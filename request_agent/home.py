"""앱 홈 탭 화면과 설정 입력창(모달). Slack 호출 없이 Block Kit 데이터만 만든다."""
from dataclasses import dataclass, replace
from datetime import datetime

from request_agent.flows import Status
from request_agent.settings import SECRET_FIELDS, Settings
from request_agent.setup_check import SetupReport

REGIONS = {"www": "https://www.wrike.com/api/v4", "app-eu": "https://app-eu.wrike.com/api/v4",
           "app-us2": "https://app-us2.wrike.com/api/v4"}
PROVIDERS = {"anthropic": "Claude (Anthropic)", "openai-compatible": "OpenAI 호환 API"}
FIX_LABELS = {"create_project": "프로젝트 만들기", "create_request_folder": "요청 폴더 만들기",
              "create_work_folder": "전체 일감 폴더 만들기", "create_workflow": "워크플로 만들기",
              "add_statuses": "상태 추가"}
FIELD_LABELS = {"wrike_token": "Wrike 토큰", "wrike": "Wrike", "project": "프로젝트",
                "request_folder": "요청 폴더", "work_folder": "전체 일감 폴더",
                "ticket_type": "티켓 유형", "workflow": "요청 워크플로", "statuses": "상태",
                **{f"status:{s.name}": f"상태: {s.value}" for s in Status}}
TEXT_INPUTS = ("project", "request_folder", "work_folder", "ticket_type", "workflow",
               "llm_base_url", "llm_model")


@dataclass(frozen=True)
class AppStatus:
    checked_at: datetime
    wrike_ok: bool | None  # None: 아직 설정되지 않음
    message: str


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _option(value: str, label: str) -> dict:
    return {"text": {"type": "plain_text", "text": label}, "value": value}


def _status_line(status: AppStatus) -> str:
    wrike = {True: "정상", False: f"오류({status.message})", None: "설정 필요"}[status.wrike_ok]
    return (f":large_green_circle: *실행 중* · 마지막 확인 "
            f"{status.checked_at.strftime('%Y-%m-%d %H:%M')} · Wrike 연결: {wrike}")


def home_view(admin: bool, settings: Settings | None, report: SetupReport | None,
              status: AppStatus) -> dict:
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": "요청 에이전트"}},
              _section(_status_line(status)),
              _section("요청 채널의 *요청 등록* 버튼, 채널에 쓴 글의 등록 제안, 메시지 ⋯ 메뉴의 "
                       "*Wrike 요청으로 등록*, `/request` 로 요청을 등록합니다. "
                       "승인자는 요청 메시지에 :white_check_mark: 확정 · :x: 반려를 누릅니다.")]
    if not admin:
        return {"type": "home", "blocks": blocks}

    s = settings or Settings()
    channel = f"<#{s.request_channel_id}>" if s.request_channel_id else "미설정"
    approvers = ", ".join(f"<@{u}>" for u in s.approver_ids) or "미설정"
    ai = (f"켜짐 · {PROVIDERS.get(s.llm_provider, s.llm_provider)}"
          f"{' · ' + s.llm_model if s.llm_model else ''}" if s.ai_opinion else "꺼짐")
    blocks += [
        {"type": "divider"},
        _section("*설정* (관리자만 보입니다)\n"
                 f"• Wrike 토큰: {'설정됨' if s.wrike_token else '없음'} · 프로젝트: {s.project or '미설정'}\n"
                 f"• 요청 폴더: {s.request_folder} · 전체 일감 폴더: {s.work_folder} · "
                 f"요청 워크플로: {s.workflow}\n"
                 f"• 요청 채널: {channel} · 승인자: {approvers}\n"
                 f"• AI 의견: {ai} · LLM 키: {'설정됨' if s.llm_api_key else '없음'}"),
        {"type": "actions", "elements": [
            {"type": "button", "action_id": "open_settings", "style": "primary",
             "text": {"type": "plain_text", "text": "설정 편집"}},
            {"type": "button", "action_id": "recheck_settings",
             "text": {"type": "plain_text", "text": "연결 다시 확인"}}]},
    ]
    if report is not None:
        blocks += _report_blocks(report)
    return {"type": "home", "blocks": blocks}


def _report_blocks(report: SetupReport) -> list[dict]:
    found = "\n".join(f"• {label}: {item.name} (`{item.id}`)" for label, item in report.found)
    if report.ok:
        return [_section(f":white_check_mark: *연결 확인 완료* (Wrike 계정: {report.account}). "
                         f"저장했습니다.\n{found}")]
    blocks = [_section(":warning: *연결 확인에서 해결할 것이 있습니다.* 해결할 때까지 이 설정은 "
                       "저장되지 않고, 이전 설정으로 계속 동작합니다.")]
    if found:
        blocks.append(_section(f"찾은 항목\n{found}"))
    for problem in report.problems:
        label = FIELD_LABELS.get(problem.field, problem.field)
        block = _section(f"*{label}*: {problem.message}")
        if problem.fix:
            block["accessory"] = {"type": "button", "action_id": "setup_fix",
                                  "value": problem.fix,
                                  "text": {"type": "plain_text", "text": FIX_LABELS[problem.fix]}}
        blocks.append(block)
        if problem.fix in ("create_workflow", "add_statuses"):
            blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": (
                "전용 요청 워크플로에만 만들거나 추가합니다. 기존 업무 워크플로는 바꾸지 않습니다. "
                "Wrike 워크플로 관리 권한이 필요합니다.")}]})
        if problem.candidates:
            blocks.append({"type": "actions", "block_id": f"choose:{problem.field}",
                           "elements": [{"type": "static_select", "action_id": "setup_choose",
                                         "placeholder": {"type": "plain_text", "text": "하나 고르기"},
                                         "options": [_option(c.id, f"{c.name} ({c.id})"[:75])
                                                     for c in problem.candidates[:100]]}]})
    return blocks


def _text_input(block_id: str, label: str, value: str = "", optional: bool = False,
                placeholder: str = "", hint: str = "") -> dict:
    element: dict = {"type": "plain_text_input", "action_id": "v"}
    if value:
        element["initial_value"] = value
    if placeholder:
        element["placeholder"] = {"type": "plain_text", "text": placeholder}
    block = {"type": "input", "block_id": block_id, "optional": optional, "element": element,
             "label": {"type": "plain_text", "text": label}}
    if hint:
        block["hint"] = {"type": "plain_text", "text": hint}
    return block


def _select(block_id: str, label: str, options: dict[str, str], selected: str) -> dict:
    opts = [_option(v, label_) for v, label_ in options.items()]
    element = {"type": "static_select", "action_id": "v", "options": opts}
    if selected in options:
        element["initial_option"] = _option(selected, options[selected])
    return {"type": "input", "block_id": block_id, "element": element,
            "label": {"type": "plain_text", "text": label}}


def settings_form(s: Settings) -> dict:
    """관리자 설정 입력창. 토큰·키는 값을 채우지 않고, 비워 두면 기존 값을 유지한다."""
    regions = {"www": "기본 (www)", "app-eu": "EU", "app-us2": "US2"}
    region = next((k for k, url in REGIONS.items() if url == s.wrike_base_url), "custom")
    if region == "custom":  # WRIKE_BASE_URL로 지정한 주소(사내 프록시·개발용 가짜 서버)
        regions["custom"] = f"사용자 지정 ({s.wrike_base_url})"[:75]
    keep = "변경할 때만 입력 (비우면 기존 값 유지)"
    channel: dict[str, object] = {"type": "conversations_select", "action_id": "v",
                                  "filter": {"include": ["public"]}}
    if s.request_channel_id:
        channel["initial_conversation"] = s.request_channel_id
    approvers: dict[str, object] = {"type": "multi_users_select", "action_id": "v"}
    if s.approver_ids:
        approvers["initial_users"] = list(s.approver_ids)
    name_or_id = "이름 또는 ID"
    blocks = [
        _section("*Wrike*"),
        _text_input("wrike_token", "Wrike API 토큰", optional=bool(s.wrike_token),
                    placeholder=keep if s.wrike_token else "permanent access token"),
        _select("wrike_region", "Wrike 지역", regions, region),
        _text_input("project", "프로젝트", s.project, hint=name_or_id),
        _text_input("request_folder", "요청 폴더", s.request_folder, hint=name_or_id),
        _text_input("work_folder", "전체 일감 폴더", s.work_folder, hint=name_or_id),
        _text_input("ticket_type", "티켓 유형 (사용자 정의 항목 유형)", s.ticket_type,
                    optional=True, hint="쓰지 않으면 비워 두세요. 이름 또는 ID"),
        _text_input("workflow", "요청 워크플로", s.workflow,
                    hint="기존 업무 워크플로와 분리된 전용 워크플로. 이름 또는 ID"),
        *[_text_input(f"status_{status.name}", f"상태: {status.value}",
                      s.status_names.get(status, status.value), hint=name_or_id)
          for status in Status],
        _section("*Slack*"),
        {"type": "input", "block_id": "request_channel", "element": channel,
         "label": {"type": "plain_text", "text": "요청 채널"}},
        {"type": "input", "block_id": "approvers", "element": approvers,
         "label": {"type": "plain_text", "text": "승인자"}},
        _text_input("categories", "카테고리",
                    ", ".join(f"{k}:{v}" for k, v in s.categories.items()),
                    hint="값:표시이름 을 쉼표로 구분"),
        _section("*AI 의견*"),
        _select("ai_opinion", "AI 의견", {"on": "켜기", "off": "끄기"},
                "on" if s.ai_opinion else "off"),
        _select("llm_provider", "LLM 제공자", PROVIDERS, s.llm_provider),
        _text_input("llm_base_url", "LLM 주소", s.llm_base_url, optional=True,
                    hint="OpenAI 호환은 필수(…/v1까지). Claude는 사내 프록시를 쓸 때만"),
        _text_input("llm_api_key", "LLM API 키", optional=True,
                    placeholder=keep if s.llm_api_key else "API 키"),
        _text_input("llm_model", "LLM 모델", s.llm_model, optional=True,
                    hint="Claude는 비우면 claude-opus-5-5"),
    ]
    return {"type": "modal", "callback_id": "settings_form",
            "title": {"type": "plain_text", "text": "요청 에이전트 설정"},
            "submit": {"type": "plain_text", "text": "저장하고 확인"},
            "close": {"type": "plain_text", "text": "취소"}, "blocks": blocks}


def parse_settings_form(values: dict, current: Settings) -> Settings:
    """입력창 제출 값으로 새 설정을 만든다. 찾은 ID는 비워 두고 저장 후 다시 확인한다."""
    def text(block_id: str) -> str:
        return (values[block_id]["v"].get("value") or "").strip()

    def selected(block_id: str) -> str:
        return values[block_id]["v"]["selected_option"]["value"]

    secrets = {field: text(field) or getattr(current, field) for field in SECRET_FIELDS}
    categories = dict(pair.strip().split(":", 1)
                      for pair in text("categories").split(",") if ":" in pair)
    return replace(
        current, **secrets, **{field: text(field) for field in TEXT_INPUTS},
        wrike_base_url=REGIONS.get(selected("wrike_region"), current.wrike_base_url),
        status_names={status: text(f"status_{status.name}") for status in Status},
        request_channel_id=values["request_channel"]["v"]["selected_conversation"],
        approver_ids=tuple(values["approvers"]["v"]["selected_users"]),
        categories={k.strip(): v.strip() for k, v in categories.items()} or current.categories,
        ai_opinion=selected("ai_opinion") == "on",
        llm_provider=selected("llm_provider"),
        project_id="", request_folder_id="", work_folder_id="", ticket_type_id="",
        workflow_id="", status_ids={},
    )
