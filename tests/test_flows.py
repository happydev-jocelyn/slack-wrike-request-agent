"""등록·승인·반려 흐름 판정 테스트 (AC3, AC7~AC10)."""
import unittest

from request_agent.flows import (
    ChatError, Request, Status, Ticket, TrackerError, WorkItem, decide, slack_escape,
    submit_request)

APPROVER = "U_APPROVER"
OTHER = "U_OTHER"
REQ = Request(category="기능", title="로그인 오류", body="재현 방법…",
              assignee="U_ASSIGNEE", requester="U_REQ")


class FakeTracker:
    def __init__(self, fail_create: bool = False, fail_work_item: bool = False) -> None:
        self.fail_create = fail_create
        self.fail_work_item = fail_work_item
        self.fail_link = self.fail_comment = self.fail_revert = False
        self.slack_links: dict[str, str] = {}
        self.tickets: dict[str, Ticket] = {}
        self.work_items: list[WorkItem] = []
        self.comments: list[tuple[str, str]] = []

    def create_ticket(self, req: Request) -> Ticket:
        if self.fail_create:
            raise TrackerError("토큰이 유효하지 않습니다")
        ticket = Ticket(id=f"T{len(self.tickets) + 1}", title=req.title,
                        permalink="https://wrike/t1", status=Status.REVIEWING)
        self.tickets[ticket.id] = ticket
        return ticket

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        return self.tickets.get(ticket_id)

    def set_status(self, ticket_id: str, status: Status | None) -> None:
        if self.fail_revert and status is Status.REVIEWING:
            raise TrackerError("네트워크 오류")
        t = self.tickets[ticket_id]
        self.tickets[ticket_id] = Ticket(t.id, t.title, t.permalink, status)

    def create_work_item(self, ticket: Ticket) -> WorkItem:
        if self.fail_work_item:
            raise TrackerError("폴더를 찾을 수 없습니다")
        item = WorkItem(id=f"W{len(self.work_items) + 1}", permalink="https://wrike/w1")
        self.work_items.append(item)
        return item

    def comment(self, ticket_id: str, text: str) -> None:
        if self.fail_comment:
            raise TrackerError("댓글 실패")
        self.comments.append((ticket_id, text))

    def add_slack_link(self, ticket_id: str, url: str) -> None:
        if self.fail_link:
            raise TrackerError("권한 없음")
        self.slack_links[ticket_id] = url

    def delete_ticket(self, ticket_id: str) -> None:
        del self.tickets[ticket_id]


class FakeChat:
    def __init__(self, fail_post: bool = False) -> None:
        self.fail_post = fail_post
        self.posted: list[tuple[Request, Ticket]] = []
        self.replies: list[str] = []
        self.private: list[tuple[str, str]] = []

    def post_request(self, req: Request, ticket: Ticket) -> str:
        if self.fail_post:
            raise ChatError("invalid_blocks")
        self.posted.append((req, ticket))
        return "https://slack/archives/C1/p1"

    def retract_request(self) -> None:
        self.posted.pop()

    def reply(self, text: str) -> None:
        self.replies.append(text)

    def notify(self, user: str, text: str) -> None:
        self.private.append((user, text))


class SubmitTest(unittest.TestCase):
    def test_submit_creates_ticket_and_posts_once(self) -> None:
        tracker, chat = FakeTracker(), FakeChat()
        submit_request(REQ, tracker, chat)
        self.assertEqual(len(tracker.tickets), 1)
        self.assertEqual(len(chat.posted), 1)
        self.assertEqual(chat.posted[0][1].status, Status.REVIEWING)
        self.assertEqual(tracker.slack_links, {"T1": "https://slack/archives/C1/p1"})

    def test_submit_wrike_failure_posts_nothing(self) -> None:
        tracker, chat = FakeTracker(fail_create=True), FakeChat()
        submit_request(REQ, tracker, chat)
        self.assertEqual(chat.posted, [])
        self.assertEqual(len(chat.private), 1)
        user, text = chat.private[0]
        self.assertEqual(user, REQ.requester)
        self.assertIn("토큰이 유효하지 않습니다", text)

    def test_slack_post_failure_deletes_ticket(self) -> None:
        tracker, chat = FakeTracker(), FakeChat(fail_post=True)
        self.assertIsNone(submit_request(REQ, tracker, chat))
        self.assertEqual(tracker.tickets, {})  # 메시지 없이 티켓만 남지 않는다
        self.assertEqual(chat.private[0][0], REQ.requester)
        self.assertIn("invalid_blocks", chat.private[0][1])

    def test_slack_link_failure_rolls_back(self) -> None:
        tracker, chat = FakeTracker(), FakeChat()
        tracker.fail_link = True
        self.assertIsNone(submit_request(REQ, tracker, chat))
        self.assertEqual(tracker.tickets, {})
        self.assertEqual(chat.posted, [])
        self.assertEqual(chat.replies, [])
        self.assertEqual(chat.private[0][0], REQ.requester)
        self.assertIn("권한 없음", chat.private[0][1])


class SlackEscapeTest(unittest.TestCase):
    def test_escapes_control_characters(self) -> None:
        self.assertEqual(slack_escape("<!channel> a&b > c"), "&lt;!channel&gt; a&amp;b &gt; c")


class DecideTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tracker, self.chat = FakeTracker(), FakeChat()
        self.ticket = self.tracker.create_ticket(REQ)

    def run_decide(self, decision: Status, user: str = APPROVER) -> bool:
        return decide(decision, user, self.ticket.id, {APPROVER}, self.tracker, self.chat)

    def test_approve_creates_task_once(self) -> None:
        self.assertTrue(self.run_decide(Status.CONFIRMED))
        self.assertFalse(self.run_decide(Status.CONFIRMED))  # 같은 ✅를 다시 눌러도
        self.assertEqual(self.tracker.tickets[self.ticket.id].status, Status.CONFIRMED)
        self.assertEqual(len(self.tracker.work_items), 1)
        self.assertIn("https://wrike/w1", self.chat.replies[0])
        self.assertTrue(any("https://wrike/w1" in c for _, c in self.tracker.comments))

    def test_reject(self) -> None:
        self.run_decide(Status.REJECTED)
        self.assertEqual(self.tracker.tickets[self.ticket.id].status, Status.REJECTED)
        self.assertEqual(self.tracker.work_items, [])
        self.assertIn(APPROVER, self.chat.replies[0])
        self.assertEqual(self.tracker.comments, [(self.ticket.id, f"{APPROVER} 반려")])

    def test_non_approver_ignored(self) -> None:
        self.run_decide(Status.CONFIRMED, user=OTHER)
        self.assertEqual(self.tracker.tickets[self.ticket.id].status, Status.REVIEWING)
        self.assertEqual(self.chat.replies, [])
        self.assertEqual(self.chat.private[0][0], OTHER)

    def test_already_decided_noop(self) -> None:
        self.tracker.set_status(self.ticket.id, Status.REJECTED)
        self.run_decide(Status.CONFIRMED)
        self.assertEqual(self.tracker.tickets[self.ticket.id].status, Status.REJECTED)
        self.assertEqual(self.tracker.work_items, [])
        self.assertIn("반려", self.chat.replies[0])

    def test_status_changed_elsewhere_noop(self) -> None:
        self.tracker.set_status(self.ticket.id, None)  # 전용 워크플로 밖 상태
        self.run_decide(Status.CONFIRMED)
        self.assertEqual(self.tracker.work_items, [])
        self.assertEqual(len(self.chat.replies), 1)

    def test_work_item_failure_reverts_to_reviewing(self) -> None:
        self.tracker.fail_work_item = True
        self.assertFalse(self.run_decide(Status.CONFIRMED))
        self.assertEqual(self.tracker.tickets[self.ticket.id].status, Status.REVIEWING)
        self.assertIn("폴더를 찾을 수 없습니다", self.chat.replies[0])

        self.tracker.fail_work_item = False  # 다시 누르면 처리된다
        self.assertTrue(self.run_decide(Status.CONFIRMED))
        self.assertEqual(len(self.tracker.work_items), 1)

    def test_comment_failure_still_reports_result(self) -> None:
        self.tracker.fail_comment = True
        self.assertTrue(self.run_decide(Status.CONFIRMED))
        self.assertIn("https://wrike/w1", self.chat.replies[0])
        self.assertIn("댓글", self.chat.replies[1])

    def test_revert_failure_tells_how_to_recover(self) -> None:
        self.tracker.fail_work_item = self.tracker.fail_revert = True
        self.assertFalse(self.run_decide(Status.CONFIRMED))
        self.assertIn("검토중으로 되돌린 뒤", self.chat.replies[0])
        self.assertIn(self.ticket.permalink, self.chat.replies[0])

    def test_deleted_ticket_noop(self) -> None:
        del self.tracker.tickets[self.ticket.id]
        self.run_decide(Status.CONFIRMED)
        self.assertEqual(self.tracker.work_items, [])
        self.assertIn("삭제", self.chat.replies[0])


if __name__ == "__main__":
    unittest.main()
