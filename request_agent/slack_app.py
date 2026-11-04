"""Slack 배선: 요청 입구·폼 → Wrike 티켓, 승인자 이모지 → 확정·반려, 홈 탭 설정.

앱은 일감 데이터를 저장하지 않는다. 요청 메시지의 metadata에 Wrike 티켓 ID를 담고,
이모지가 눌리면 그 메시지를 다시 읽어 티켓을 찾는다. 설정은 워크스페이스별로
홈 탭에서 저장하고(SETTINGS_KEY가 있을 때), 없으면 환경변수 설정을 쓴다.
"""
import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from functools import cache
from pathlib import Path

from slack_bolt import Ack, App, BoltContext, Respond
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from request_agent.flows import Request, Status, Ticket, TrackerError, decide, submit_request
from request_agent.forms import request_form, should_suggest
from request_agent.home import AppStatus, home_view, parse_settings_form, settings_form
from request_agent.llm import make_advisor
from request_agent.review import Advisor, AdvisorError, post_opinion
from request_agent.settings import FernetCipher, Settings, SettingsStore, is_admin
from request_agent.setup_check import SetupReport, apply_fix, check_settings
from request_agent.wrike import WrikeClient, WrikeConfig
from request_agent.wrike_setup import WrikeSetup

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("request-agent")

ADMIN_IDS = set(filter(None, os.environ.get("ADMIN_IDS", "").split(",")))
DATA_DIR = Path(os.environ.get("DATA_DIR", "data"))
HEARTBEAT_SECONDS = 300
REACTIONS = {os.environ.get("APPROVE_EMOJI", "white_check_mark"): Status.CONFIRMED,
             os.environ.get("REJECT_EMOJI", "x"): Status.REJECTED}
BUTTON_EVENT = "request_button"
TICKET_EVENT = "request_ticket"
CHOOSABLE_FIELDS = ("project", "request_folder", "work_folder", "ticket_type", "workflow")
STATUS_FIELD = "status:"  # 상태 후보 고르기의 block 이름 접두사

app = App(token=os.environ["SLACK_BOT_TOKEN"])
store = (SettingsStore(DATA_DIR / "settings.db", FernetCipher(os.environ["SETTINGS_KEY"]))
         if os.environ.get("SETTINGS_KEY") else None)
env_settings = Settings.from_env(os.environ)  # 홈 탭에 저장한 설정이 없을 때 쓴다
ticket_locks: dict[str, threading.Lock] = {}
ticket_locks_guard = threading.Lock()


@cache
def email_of(user: str) -> str | None:
    profile = app.client.users_info(user=user)["user"]["profile"]
    return profile.get("email")


@cache
def name_of(user: str) -> str:
    profile = app.client.users_info(user=user)["user"]["profile"]
    return profile.get("display_name") or profile.get("real_name") or user


@dataclass(frozen=True)
class Runtime:
    """워크스페이스 하나의 현재 설정과 그 설정으로 만든 클라이언트."""
    settings: Settings
    wrike: WrikeClient | None  # 설정이 덜 됐으면 None
    advisor: Advisor | None


runtimes: dict[str, Runtime] = {}
# 마지막 연결 확인 결과(관리자 홈 탭에 표시). 문제가 남은 결과의 설정은 저장 전 대기 상태다
reports: dict[str, SetupReport] = {}
statuses: dict[str, AppStatus] = {}  # 마지막 상태 확인(홈 탭 상태 줄)
state_lock = threading.Lock()


def load_settings(team_id: str) -> Settings:
    return (store.load(team_id) if store else None) or env_settings


def build_runtime(settings: Settings) -> Runtime:
    wrike = None
    if settings.is_complete():
        wrike = WrikeClient(WrikeConfig(
            base_url=settings.wrike_base_url, token=settings.wrike_token,
            request_folder_id=settings.request_folder_id,
            work_folder_id=settings.work_folder_id, status_ids=settings.status_ids,
            ticket_type_id=settings.ticket_type_id or None), email_of=email_of)
    try:
        advisor = make_advisor(settings.llm_env())
    except AdvisorError as e:  # SDK 미설치: AI 의견만 끈다
        log.warning("AI 의견을 끕니다: %s", e)
        advisor = None
    return Runtime(settings, wrike, advisor)


def runtime(team_id: str) -> Runtime:
    with state_lock:
        if team_id not in runtimes:
            runtimes[team_id] = build_runtime(load_settings(team_id))
        return runtimes[team_id]


def team_of(body: dict, context: BoltContext) -> str:
    """이벤트·액션의 워크스페이스 ID(조직 단위 설치에서도 찾는다)."""
    event = body.get("event", {})
    return (context.team_id or body.get("team_id") or (body.get("team") or {}).get("id")
            or event.get("team") or event.get("user_team") or "")


class SlackChat:
    """flows.Chat 포트 구현. reply는 thread_ts가 있는 요청 메시지 스레드에 단다."""

    def __init__(self, client: WebClient, channel: str, thread_ts: str | None = None) -> None:
        self.client, self.channel, self.thread_ts = client, channel, thread_ts

    def post_request(self, req: Request, ticket: Ticket) -> str:
        res = self.client.chat_postMessage(
            channel=self.channel,
            text=f"새 요청: {req.title}",
            metadata={"event_type": TICKET_EVENT, "event_payload": {"ticket_id": ticket.id}},
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": (
                f"*새 요청* · 상태: *{Status.REVIEWING.value}*\n"
                f"[{req.category}] *<{ticket.permalink}|{req.title}>*\n{req.body}\n"
                f"담당자: <@{req.assignee}> · 요청자: <@{req.requester}>\n"
                f"_승인자는 :{approve_emoji()}: 확정 · :{reject_emoji()}: 반려_")}}],
        )
        self.thread_ts = res["ts"]  # 이후 reply는 이 메시지 스레드에 단다
        link = self.client.chat_getPermalink(channel=self.channel, message_ts=res["ts"])
        return link["permalink"]

    def retract_request(self) -> None:
        if self.thread_ts:
            self.client.chat_delete(channel=self.channel, ts=self.thread_ts)
            self.thread_ts = None

    def reply(self, text: str) -> None:
        self.client.chat_postMessage(channel=self.channel, thread_ts=self.thread_ts, text=text)

    def notify(self, user: str, text: str) -> None:
        try:
            self.client.chat_postEphemeral(channel=self.channel, user=user, text=text)
        except SlackApiError:
            # 요청 채널 멤버가 아니면(단축키·다른 채널에서 등록) 앱 DM으로 보낸다
            self.client.chat_postMessage(channel=user, text=text)


def approve_emoji() -> str:
    return next(k for k, v in REACTIONS.items() if v is Status.CONFIRMED)


def reject_emoji() -> str:
    return next(k for k, v in REACTIONS.items() if v is Status.REJECTED)


def channel_message(channel: str, ts: str) -> dict | None:
    """채널의 특정 메시지를 metadata까지 포함해 읽는다."""
    res = app.client.conversations_history(channel=channel, latest=ts, inclusive=True,
                                           limit=1, include_all_metadata=True)
    messages = res["messages"]
    return messages[0] if messages and messages[0]["ts"] == ts else None


def ensure_button(channel: str) -> None:
    """채널에 참여하고, 요청 등록 버튼 메시지가 최근 메시지에 없으면 게시한다."""
    app.client.conversations_join(channel=channel)
    history = app.client.conversations_history(
        channel=channel, limit=200, include_all_metadata=True)
    if any(m.get("metadata", {}).get("event_type") == BUTTON_EVENT for m in history["messages"]):
        return
    app.client.chat_postMessage(
        channel=channel, text="요청 등록",
        metadata={"event_type": BUTTON_EVENT, "event_payload": {}},
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": (
                "*요청 등록*\n버튼을 눌러 요청을 등록하세요. 승인자가 확정하면 Wrike 일감으로 등록됩니다.")}},
            {"type": "actions", "elements": [{
                "type": "button", "action_id": "open_request_form", "style": "primary",
                "text": {"type": "plain_text", "text": "요청 등록"}}]},
        ])
    log.info("요청 등록 버튼 게시: %s", channel)


# ---------- 요청 등록 ----------

def open_form(client: WebClient, trigger_id: str, team_id: str, body: str = "") -> None:
    categories = runtime(team_id).settings.categories
    client.views_open(trigger_id=trigger_id, view=request_form(categories, body))


@app.command("/request")
def open_form_from_command(ack: Ack, body: dict, client: WebClient,
                           context: BoltContext) -> None:
    ack()
    open_form(client, body["trigger_id"], team_of(body, context))


@app.action("open_request_form")
def open_form_from_button(ack: Ack, body: dict, client: WebClient, context: BoltContext) -> None:
    ack()
    open_form(client, body["trigger_id"], team_of(body, context))


@app.shortcut("request_global")
def open_form_from_global_shortcut(ack: Ack, body: dict, client: WebClient,
                                   context: BoltContext) -> None:
    ack()
    open_form(client, body["trigger_id"], team_of(body, context))


@app.shortcut("request_from_message")
def open_form_from_message_shortcut(ack: Ack, body: dict, client: WebClient,
                                    context: BoltContext) -> None:
    ack()
    open_form(client, body["trigger_id"], team_of(body, context),
              body["message"].get("text", ""))


@app.event("message")
def suggest_registration(body: dict, event: dict, client: WebClient,
                         context: BoltContext) -> None:
    """요청 채널의 새 글에 작성자에게만 보이는 등록 제안을 띄운다."""
    channel = runtime(team_of(body, context)).settings.request_channel_id
    if not channel or not should_suggest(event, channel):
        return
    client.chat_postEphemeral(
        channel=channel, user=event["user"], text="Wrike 요청으로 등록할까요?",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "이 글을 Wrike 요청으로 등록할까요?"}},
            {"type": "actions", "elements": [{
                "type": "button", "action_id": "open_request_form_from_post", "style": "primary",
                "text": {"type": "plain_text", "text": "등록"}, "value": event["ts"]}]},
        ])


@app.action("open_request_form_from_post")
def open_form_from_post(ack: Ack, body: dict, client: WebClient, respond: Respond,
                        context: BoltContext) -> None:
    ack()
    team_id = team_of(body, context)
    channel = runtime(team_id).settings.request_channel_id
    message = channel_message(channel, body["actions"][0]["value"])
    open_form(client, body["trigger_id"], team_id, (message or {}).get("text", ""))
    respond(delete_original=True)  # 제안 안내를 지운다


@app.view("request_form")
def submit_form(ack: Ack, body: dict, client: WebClient, context: BoltContext) -> None:
    ack()
    rt = runtime(team_of(body, context))
    values = body["view"]["state"]["values"]
    req = Request(
        category=rt.settings.categories[values["category"]["v"]["selected_option"]["value"]],
        title=values["title"]["v"]["value"],
        body=values["body"]["v"]["value"],
        assignee=values["assignee"]["v"]["selected_user"],
        requester=body["user"]["id"],
    )
    chat = SlackChat(client, rt.settings.request_channel_id)
    if rt.wrike is None or not rt.settings.request_channel_id:
        chat.notify(req.requester, "요청 에이전트 설정이 끝나지 않았습니다. 관리자에게 알려 주세요.")
        return
    ticket = submit_request(req, rt.wrike, chat)
    log.info("요청 등록: ticket=%s", ticket.id if ticket else "실패")
    if ticket and rt.advisor:
        opinion = post_opinion(req, ticket, rt.wrike, rt.advisor, chat,
                               datetime.now(timezone.utc))
        log.info("AI 의견: ticket=%s %s", ticket.id,
                 f"유사 {len(opinion.similar)}건" if opinion else "실패")


# ---------- 승인·반려 ----------

@app.event("reaction_added")
def on_reaction(body: dict, event: dict, client: WebClient, context: BoltContext) -> None:
    decision = REACTIONS.get(event["reaction"])
    rt = runtime(team_of(body, context))
    item = event["item"]
    channel = rt.settings.request_channel_id
    if decision is None or rt.wrike is None or item.get("channel") != channel:
        return
    message = channel_message(channel, item["ts"])
    metadata = (message or {}).get("metadata", {})
    if metadata.get("event_type") != TICKET_EVENT:
        return

    ticket_id = metadata["event_payload"]["ticket_id"]
    with ticket_locks_guard:
        lock = ticket_locks.setdefault(ticket_id, threading.Lock())
    with lock:  # 같은 티켓에 이모지가 동시에 눌려도 한 번만 처리한다
        chat = SlackChat(client, channel, item["ts"])
        changed = decide(decision, event["user"], ticket_id, set(rt.settings.approver_ids),
                         rt.wrike, chat, user_name=name_of(event["user"]))
    log.info("%s %s: ticket=%s by %s", decision.value, "처리" if changed else "무시",
             ticket_id, event["user"])


# ---------- 홈 탭·설정 ----------

def app_status(team_id: str) -> AppStatus:
    if team_id not in statuses:  # 아직 확인한 적이 없으면 지금 확인한다
        refresh_status(team_id)
    return statuses[team_id]


def publish_home(client: WebClient, team_id: str, user: str) -> None:
    admin = is_admin(user, ADMIN_IDS)
    settings = runtime(team_id).settings if admin else None
    client.views_publish(user_id=user, view=home_view(
        admin, settings, reports.get(team_id) if admin else None, app_status(team_id)))


def pending_settings(team_id: str) -> Settings:
    """해결 중인 설정이 있으면 그것을, 없으면 지금 쓰는 설정을 돌려준다."""
    report = reports.get(team_id)
    return report.settings if report and not report.ok else runtime(team_id).settings


def save_report(client: WebClient, team_id: str, report: SetupReport, user: str) -> None:
    """확인에 문제가 없을 때만 저장해 적용하고, 문제가 있으면 대기시킨 채 홈 탭에 보여 준다."""
    assert store is not None  # 설정 화면은 저장소가 있을 때만 열린다
    with state_lock:
        reports[team_id] = report
    if not report.ok:  # 이름이 겹치거나 없는 동안에는 이전 설정으로 계속 동작한다
        publish_home(client, team_id, user)
        return
    store.save(team_id, report.settings)
    with state_lock:
        runtimes.pop(team_id, None)
    if report.settings.request_channel_id:
        try:
            ensure_button(report.settings.request_channel_id)
        except SlackApiError as e:
            log.warning("요청 채널 준비 실패: %s", e)
    refresh_status(team_id)
    publish_home(client, team_id, user)


def setup_client(settings: Settings) -> WrikeSetup:
    return WrikeSetup(settings.wrike_base_url, settings.wrike_token)


@app.event("app_home_opened")
def on_home_opened(body: dict, event: dict, client: WebClient, context: BoltContext) -> None:
    if event.get("tab") == "home":
        publish_home(client, team_of(body, context), event["user"])


def admin_only(client: WebClient, user: str) -> bool:
    """홈 탭 버튼에는 응답 주소가 없으므로 거절 안내는 앱 DM으로 보낸다."""
    if is_admin(user, ADMIN_IDS) and store is not None:
        return True
    client.chat_postMessage(channel=user, text=(
        "설정은 관리자만 바꿀 수 있습니다." if store else
        "설정 저장소가 꺼져 있습니다(SETTINGS_KEY 없음). docs/install.md를 확인해 주세요."))
    return False


@app.action("open_settings")
def open_settings(ack: Ack, body: dict, client: WebClient, context: BoltContext) -> None:
    ack()
    if admin_only(client, body["user"]["id"]):
        settings = pending_settings(team_of(body, context))
        client.views_open(trigger_id=body["trigger_id"], view=settings_form(settings))


@app.view("settings_form")
def submit_settings(ack: Ack, body: dict, client: WebClient, context: BoltContext) -> None:
    user = body["user"]["id"]
    if not is_admin(user, ADMIN_IDS) or store is None:
        ack(response_action="errors", errors={"wrike_token": "관리자만 저장할 수 있습니다."})
        return
    ack()  # Wrike 확인은 몇 초 걸릴 수 있어 입력창을 먼저 닫고 결과는 홈 탭에 보여 준다
    team_id = team_of(body, context)
    settings = parse_settings_form(body["view"]["state"]["values"], pending_settings(team_id))
    save_report(client, team_id, check_settings(settings, setup_client(settings)), user)


@app.action("recheck_settings")
def recheck_settings(ack: Ack, body: dict, client: WebClient, context: BoltContext) -> None:
    ack()
    if admin_only(client, body["user"]["id"]):
        team_id = team_of(body, context)
        settings = pending_settings(team_id)
        save_report(client, team_id, check_settings(settings, setup_client(settings)),
                    body["user"]["id"])


@app.action("setup_fix")
def setup_fix(ack: Ack, body: dict, client: WebClient, context: BoltContext) -> None:
    ack()
    if admin_only(client, body["user"]["id"]):
        team_id = team_of(body, context)
        settings = pending_settings(team_id)
        fix = body["actions"][0]["value"]
        save_report(client, team_id, apply_fix(settings, setup_client(settings), fix),
                    body["user"]["id"])


@app.action("setup_choose")
def setup_choose(ack: Ack, body: dict, client: WebClient, context: BoltContext) -> None:
    ack()
    action = body["actions"][0]
    field, chosen = action["block_id"].removeprefix("choose:"), action["selected_option"]["value"]
    if admin_only(client, body["user"]["id"]):
        team_id = team_of(body, context)
        # 고른 항목의 ID를 입력값으로 바꿔 다시 확인한다
        settings = pending_settings(team_id)
        if field in CHOOSABLE_FIELDS:
            settings = replace(settings, **{field: chosen})
        elif field.startswith(STATUS_FIELD):
            status = Status[field.removeprefix(STATUS_FIELD)]
            settings = replace(settings, status_names={**settings.status_names, status: chosen})
        else:
            return
        save_report(client, team_id, check_settings(settings, setup_client(settings)),
                    body["user"]["id"])


# ---------- 상태 확인 ----------

def refresh_status(team_id: str) -> None:
    settings = runtime(team_id).settings
    now = datetime.now(timezone.utc)
    if not settings.wrike_token:
        status = AppStatus(now, None, "설정 필요")
    else:
        try:
            setup_client(settings).check()
            status = AppStatus(now, True, "정상")
        except TrackerError as e:
            status = AppStatus(now, False, str(e))
    with state_lock:
        statuses[team_id] = status


def heartbeat() -> None:
    """주기적으로 Wrike 연결을 확인하고 관리자 홈 탭을 갱신한다(시각이 멈추면 앱이 꺼진 것)."""
    while True:
        teams = set(store.teams()) if store else set()
        teams |= set(runtimes)
        for team_id in teams:
            try:
                refresh_status(team_id)
                for admin in ADMIN_IDS:
                    publish_home(app.client, team_id, admin)
            except Exception:  # 한 워크스페이스가 실패해도 다음 주기에 다시 시도한다
                log.exception("상태 확인 실패: %s", team_id)
        time.sleep(HEARTBEAT_SECONDS)


def main() -> None:
    if not ADMIN_IDS:
        log.warning("ADMIN_IDS가 비어 있어 홈 탭 설정을 열 수 있는 사람이 없습니다")
    if store is None:
        log.warning("SETTINGS_KEY가 없어 홈 탭 설정 저장을 끕니다. 환경변수 설정만 씁니다")
    if env_settings.request_channel_id:  # 환경변수 설정 방식
        ensure_button(env_settings.request_channel_id)
    threading.Thread(target=heartbeat, daemon=True).start()
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
