"""요청 폼(모달)과 채널 글 등록 제안 판정. Slack 호출 없이 데이터만 다룬다."""

# Slack plain_text_input의 최대 길이
MAX_INPUT_LENGTH = 3000


def request_form(categories: dict[str, str], body: str = "") -> dict:
    """요청 등록 모달. body를 주면 '일감 내용'을 미리 채운다."""
    body_input: dict = {"type": "plain_text_input", "action_id": "v", "multiline": True}
    if body:
        body_input["initial_value"] = body[:MAX_INPUT_LENGTH]
    return {
        "type": "modal",
        "callback_id": "request_form",
        "title": {"type": "plain_text", "text": "요청 등록"},
        "submit": {"type": "plain_text", "text": "등록"},
        "blocks": [
            {"type": "input", "block_id": "category",
             "label": {"type": "plain_text", "text": "카테고리"},
             "element": {"type": "static_select", "action_id": "v", "options": [
                 {"text": {"type": "plain_text", "text": label}, "value": value}
                 for value, label in categories.items()]}},
            {"type": "input", "block_id": "title",
             "label": {"type": "plain_text", "text": "일감 제목"},
             "element": {"type": "plain_text_input", "action_id": "v"}},
            {"type": "input", "block_id": "body",
             "label": {"type": "plain_text", "text": "일감 내용"},
             "element": body_input},
            {"type": "input", "block_id": "assignee",
             "label": {"type": "plain_text", "text": "담당자"},
             "element": {"type": "users_select", "action_id": "v"}},
        ],
    }


def should_suggest(event: dict, request_channel_id: str) -> bool:
    """요청 채널에 사람이 쓴 새 글이면 등록 제안을 띄운다(스레드 답글·봇·수정·삭제 제외)."""
    if event.get("channel") != request_channel_id:
        return False
    if event.get("subtype") or event.get("bot_id"):
        return False
    thread_ts = event.get("thread_ts")
    if thread_ts and thread_ts != event.get("ts"):
        return False
    return bool(event.get("text", "").strip())
