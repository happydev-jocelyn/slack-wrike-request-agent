"""Slack 배선: 요청 버튼·폼 → Wrike 티켓, 승인자 이모지 → 확정·반려.

앱은 일감 데이터를 저장하지 않는다. 요청 메시지의 metadata에 Wrike 티켓 ID를 담고,
이모지가 눌리면 그 메시지를 다시 읽어 티켓을 찾는다.
"""
import logging
import os
import threading
from datetime import datetime, timezone
from functools import cache

from slack_bolt import Ack, App, Respond
from slack_bolt.adapter.socket_mode import SocketModeHandler
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from request_agent.flows import Request, Status, Ticket, decide, submit_request
from request_agent.forms import request_form, should_suggest
from request_agent.llm import make_advisor
from request_agent.review import Advisor, AdvisorError, post_opinion
from request_agent.wrike import WrikeClient, WrikeConfig

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("request-agent")

# M1: 설정은 환경변수로 받는다(M3에서 홈 탭 설정으로 바꾼다).
CHANNEL_NAME = os.environ.get("REQUEST_CHANNEL", "watercooler")
TEAM_ID = os.environ.get("TEAM_ID")  # 조직 단위 설치일 때 워크스페이스 지정용
APPROVER_IDS = set(filter(None, os.environ.get("APPROVER_IDS", "").split(",")))
REACTIONS = {os.environ.get("APPROVE_EMOJI", "white_check_mark"): Status.CONFIRMED,
             os.environ.get("REJECT_EMOJI", "x"): Status.REJECTED}
# 카테고리 선택지. "값:표시이름"을 쉼표로 구분한다.
CATEGORIES = dict(pair.split(":", 1) for pair in os.environ.get(
    "CATEGORIES", "feature:기능,bug:버그,improvement:개선,etc:기타").split(","))
BUTTON_EVENT = "request_button"
TICKET_EVENT = "request_ticket"
request_channel_id = ""  # 시작할 때 CHANNEL_NAME으로 찾아 채운다

app = App(token=os.environ["SLACK_BOT_TOKEN"])
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


wrike = WrikeClient(WrikeConfig(
    base_url=os.environ.get("WRIKE_BASE_URL", "https://www.wrike.com/api/v4"),
    token=os.environ["WRIKE_TOKEN"],
    request_folder_id=os.environ["WRIKE_REQUEST_FOLDER_ID"],
    work_folder_id=os.environ["WRIKE_WORK_FOLDER_ID"],
    status_ids={Status.REVIEWING: os.environ["WRIKE_STATUS_REVIEWING"],
                Status.CONFIRMED: os.environ["WRIKE_STATUS_CONFIRMED"],
                Status.REJECTED: os.environ["WRIKE_STATUS_REJECTED"]},
    ticket_type_id=os.environ.get("WRIKE_TICKET_TYPE_ID") or None,
), email_of=email_of)


def load_advisor() -> Advisor | None:
    """설정이 잘못됐거나 SDK가 없으면 경고 후 AI 의견을 끈다(등록·승인은 그대로 동작)."""
    try:
        return make_advisor(os.environ)
    except AdvisorError as e:
        log.warning("AI 의견을 끕니다: %s", e)
        return None


advisor = load_advisor()


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


def find_channel_id(name: str) -> str:
    kwargs = {"team_id": TEAM_ID} if TEAM_ID else {}
    for page in app.client.conversations_list(types="public_channel", limit=200, **kwargs):
        for ch in page["channels"]:
            if ch["name"] == name:
                return ch["id"]
    raise SystemExit(f"채널 #{name}을 찾지 못했습니다")


def channel_message(channel: str, ts: str) -> dict | None:
    """채널의 특정 메시지를 metadata까지 포함해 읽는다."""
    res = app.client.conversations_history(channel=channel, latest=ts, inclusive=True,
                                           limit=1, include_all_metadata=True)
    messages = res["messages"]
    return messages[0] if messages and messages[0]["ts"] == ts else None


def ensure_button(channel: str) -> None:
    """요청 등록 버튼 메시지가 최근 메시지에 없으면 게시한다(채널 고정은 관리자가 한다)."""
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
    log.info("요청 등록 버튼 게시: #%s", CHANNEL_NAME)


def open_form(client: WebClient, trigger_id: str, body: str = "") -> None:
    client.views_open(trigger_id=trigger_id, view=request_form(CATEGORIES, body))


@app.command("/request")
def open_form_from_command(ack: Ack, body: dict, client: WebClient) -> None:
    ack()
    open_form(client, body["trigger_id"])


@app.action("open_request_form")
def open_form_from_button(ack: Ack, body: dict, client: WebClient) -> None:
    ack()
    open_form(client, body["trigger_id"])


@app.shortcut("request_global")
def open_form_from_global_shortcut(ack: Ack, body: dict, client: WebClient) -> None:
    ack()
    open_form(client, body["trigger_id"])


@app.shortcut("request_from_message")
def open_form_from_message_shortcut(ack: Ack, body: dict, client: WebClient) -> None:
    ack()
    open_form(client, body["trigger_id"], body["message"].get("text", ""))


@app.event("message")
def suggest_registration(event: dict, client: WebClient) -> None:
    """요청 채널의 새 글에 작성자에게만 보이는 등록 제안을 띄운다."""
    if not should_suggest(event, request_channel_id):
        return
    client.chat_postEphemeral(
        channel=request_channel_id, user=event["user"], text="Wrike 요청으로 등록할까요?",
        blocks=[
            {"type": "section", "text": {"type": "mrkdwn", "text": "이 글을 Wrike 요청으로 등록할까요?"}},
            {"type": "actions", "elements": [{
                "type": "button", "action_id": "open_request_form_from_post", "style": "primary",
                "text": {"type": "plain_text", "text": "등록"}, "value": event["ts"]}]},
        ])


@app.action("open_request_form_from_post")
def open_form_from_post(ack: Ack, body: dict, client: WebClient, respond: Respond) -> None:
    ack()
    message = channel_message(request_channel_id, body["actions"][0]["value"])
    open_form(client, body["trigger_id"], (message or {}).get("text", ""))
    respond(delete_original=True)  # 제안 안내를 지운다


@app.view("request_form")
def submit_form(ack: Ack, body: dict, client: WebClient) -> None:
    ack()
    values = body["view"]["state"]["values"]
    req = Request(
        category=CATEGORIES[values["category"]["v"]["selected_option"]["value"]],
        title=values["title"]["v"]["value"],
        body=values["body"]["v"]["value"],
        assignee=values["assignee"]["v"]["selected_user"],
        requester=body["user"]["id"],
    )
    chat = SlackChat(client, request_channel_id)
    ticket = submit_request(req, wrike, chat)
    log.info("요청 등록: ticket=%s", ticket.id if ticket else "실패")
    if ticket and advisor:
        opinion = post_opinion(req, ticket, wrike, advisor, chat, datetime.now(timezone.utc))
        log.info("AI 의견: ticket=%s %s", ticket.id,
                 f"유사 {len(opinion.similar)}건" if opinion else "실패")


@app.event("reaction_added")
def on_reaction(event: dict, client: WebClient) -> None:
    decision = REACTIONS.get(event["reaction"])
    item = event["item"]
    if decision is None or item.get("channel") != request_channel_id:
        return
    message = channel_message(request_channel_id, item["ts"])
    metadata = (message or {}).get("metadata", {})
    if metadata.get("event_type") != TICKET_EVENT:
        return

    ticket_id = metadata["event_payload"]["ticket_id"]
    with ticket_locks_guard:
        lock = ticket_locks.setdefault(ticket_id, threading.Lock())
    with lock:  # 같은 티켓에 이모지가 동시에 눌려도 한 번만 처리한다
        chat = SlackChat(client, request_channel_id, item["ts"])
        changed = decide(decision, event["user"], ticket_id, APPROVER_IDS, wrike, chat,
                         user_name=name_of(event["user"]))
    log.info("%s %s: ticket=%s by %s", decision.value, "처리" if changed else "무시",
             ticket_id, event["user"])


def main() -> None:
    global request_channel_id
    request_channel_id = find_channel_id(CHANNEL_NAME)
    app.client.conversations_join(channel=request_channel_id)
    ensure_button(request_channel_id)
    SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"]).start()
