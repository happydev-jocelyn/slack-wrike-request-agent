"""요청 등록·승인·반려 흐름. Slack·Wrike는 포트(Protocol)로만 다룬다."""
from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class Status(Enum):
    REVIEWING = "검토중"
    CONFIRMED = "확정"
    REJECTED = "반려"


@dataclass(frozen=True)
class Request:
    category: str  # 표시 이름
    title: str
    body: str
    assignee: str  # Slack 사용자 ID
    requester: str  # Slack 사용자 ID


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    permalink: str
    status: Status | None  # 전용 워크플로의 세 상태가 아니면 None


@dataclass(frozen=True)
class WorkItem:
    id: str
    permalink: str


class TrackerError(Exception):
    """Wrike 호출 실패. 메시지는 사용자에게 그대로 보여 줄 수 있어야 한다."""


class Tracker(Protocol):
    def create_ticket(self, req: Request) -> Ticket: ...
    def get_ticket(self, ticket_id: str) -> Ticket | None: ...  # 삭제됐으면 None
    def set_status(self, ticket_id: str, status: Status) -> None: ...
    def create_work_item(self, ticket: Ticket) -> WorkItem: ...
    def comment(self, ticket_id: str, text: str) -> None: ...
    def add_slack_link(self, ticket_id: str, url: str) -> None: ...
    def delete_ticket(self, ticket_id: str) -> None: ...


class Chat(Protocol):
    def post_request(self, req: Request, ticket: Ticket) -> str: ...  # 메시지 링크
    def retract_request(self) -> None: ...  # 방금 게시한 요청 메시지 삭제
    def reply(self, text: str) -> None: ...  # 요청 메시지 스레드에 답글
    def notify(self, user: str, text: str) -> None: ...  # 그 사용자에게만 보이는 안내


def submit_request(req: Request, tracker: Tracker, chat: Chat) -> Ticket | None:
    """Wrike에 검토중 티켓을 만들고 채널에 알린다. Wrike가 실패하면 아무것도 게시하지 않는다."""
    try:
        ticket = tracker.create_ticket(req)
    except TrackerError as e:
        chat.notify(req.requester, f"요청을 Wrike에 등록하지 못했습니다: {e}")
        return None
    message_url = chat.post_request(req, ticket)
    try:
        tracker.add_slack_link(ticket.id, message_url)
    except TrackerError as e:
        # 등록을 통째로 되돌려 티켓·메시지가 반쪽으로 남지 않게 한다
        chat.retract_request()
        reason = f"요청을 Wrike에 등록하지 못했습니다: {e}"
        try:
            tracker.delete_ticket(ticket.id)
        except TrackerError:
            reason += f" (Wrike에 남은 티켓을 직접 삭제해 주세요: {ticket.permalink})"
        chat.notify(req.requester, reason)
        return None
    return ticket


def decide(decision: Status, user: str, ticket_id: str, approvers: set[str],
           tracker: Tracker, chat: Chat, user_name: str = "") -> bool:
    """승인자의 확정·반려를 Wrike에 반영한다. Wrike의 현재 상태가 검토중일 때만 바꾼다.

    user_name은 Wrike 댓글에 남길 승인자 이름이다(Slack 멘션은 Wrike에서 보이지 않는다).
    확정·반려를 끝까지 처리했으면 True를 돌려준다. 실패해 처리를 끝내지 못했으면 False이며,
    되돌리기까지 실패한 경우 티켓이 확정으로 남을 수 있다(스레드에 복구 방법을 안내한다).
    """
    if user not in approvers:
        chat.notify(user, "확정·반려 권한이 없습니다. 승인자만 처리할 수 있습니다.")
        return False

    try:
        return _apply_decision(decision, user, ticket_id, tracker, chat, user_name or user)
    except TrackerError as e:
        chat.reply(f"Wrike 처리에 실패했습니다: {e}. 잠시 뒤 다시 눌러 주세요.")
        return False


def _apply_decision(decision: Status, user: str, ticket_id: str, tracker: Tracker,
                    chat: Chat, user_name: str) -> bool:
    ticket = tracker.get_ticket(ticket_id)
    if ticket is None:
        chat.reply("Wrike에서 삭제된 요청이라 처리하지 않았습니다.")
        return False
    if ticket.status is not Status.REVIEWING:
        current = ticket.status.value if ticket.status else "검토중이 아닌 상태"
        chat.reply(f"이미 처리된 요청입니다(현재: {current}). 변경하지 않았습니다.")
        return False

    # 상태를 먼저 바꿔 중복 처리를 막고, 일감 생성이 실패하면 검토중으로 되돌린다
    tracker.set_status(ticket_id, decision)
    if decision is Status.REJECTED:
        chat.reply(f"<@{user}> 님이 반려했습니다.")
        _comment(tracker, chat, ticket_id, f"{user_name} 반려")
        return True
    try:
        work = tracker.create_work_item(ticket)
    except TrackerError as e:
        try:
            tracker.set_status(ticket_id, Status.REVIEWING)
        except TrackerError:
            chat.reply(f"일감을 만들지 못했고({e}) 티켓이 확정 상태로 남았습니다. "
                       f"Wrike에서 검토중으로 되돌린 뒤 다시 눌러 주세요: {ticket.permalink}")
            return False
        raise
    chat.reply(f"<@{user}> 님이 확정했습니다. Wrike 일감: {work.permalink}")
    _comment(tracker, chat, ticket_id, f"{user_name} 확정 · 일감: {work.permalink}")
    return True


def _comment(tracker: Tracker, chat: Chat, ticket_id: str, text: str) -> None:
    """처리는 끝났으므로 댓글 실패는 스레드에 알리기만 한다."""
    try:
        tracker.comment(ticket_id, text)
    except TrackerError as e:
        chat.reply(f"Wrike 티켓에 처리 기록 댓글을 남기지 못했습니다: {e}")
