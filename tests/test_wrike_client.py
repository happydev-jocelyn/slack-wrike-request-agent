"""Wrike 클라이언트 계약 테스트 (AC2, AC7, AC10). 요청 형태는 Wrike API v4 문서 기준."""
import json
import unittest
from datetime import datetime, timedelta, timezone

from request_agent.flows import Request, Status, TrackerError
from request_agent.wrike import WrikeClient, WrikeConfig
from tests.fake_wrike import TOKEN, FakeWrike

STATUS_IDS = {Status.REVIEWING: "IEAAAAAAJMAAAAA1", Status.CONFIRMED: "IEAAAAAAJMAAAAA2",
              Status.REJECTED: "IEAAAAAAJMAAAAA3"}
REQ = Request(category="버그", title="로그인 오류", body="재현 <방법>",
              assignee="U_ASSIGNEE", requester="U_REQ")
EMAILS = {"U_ASSIGNEE": "assignee@example.com", "U_REQ": "req@example.com"}


def make(ticket_type_id: str | None = None, token: str = TOKEN) -> tuple[WrikeClient, FakeWrike]:
    wrike = FakeWrike()
    config = WrikeConfig(base_url="https://www.wrike.com/api/v4", token=token,
                         request_folder_id="REQFOLDER", work_folder_id="WORKFOLDER",
                         status_ids=STATUS_IDS, ticket_type_id=ticket_type_id)
    return WrikeClient(config, wrike.transport, EMAILS.get), wrike


class CreateTicketTest(unittest.TestCase):
    def test_create_ticket(self) -> None:
        client, wrike = make()
        ticket = client.create_ticket(REQ)

        method, path, form = wrike.requests[-1]
        self.assertEqual((method, path), ("POST", "/folders/REQFOLDER/tasks"))
        self.assertEqual(form["title"], "[버그] 로그인 오류")
        self.assertEqual(form["customStatus"], STATUS_IDS[Status.REVIEWING])
        self.assertEqual(json.loads(form["responsibles"]), ["KUAAAAA1"])
        self.assertIn("재현 &lt;방법&gt;", form["description"])
        self.assertIn("req@example.com", form["description"])
        self.assertEqual(ticket.status, Status.REVIEWING)
        self.assertTrue(ticket.permalink.startswith("https://www.wrike.com/"))

    def test_create_ticket_from_custom_item_type(self) -> None:
        client, wrike = make(ticket_type_id="TICKETTYPE")
        ticket = client.create_ticket(REQ)

        paths = [(m, p) for m, p, _ in wrike.requests if p != "/contacts"]
        self.assertEqual(paths, [("POST", "/custom_item_types/TICKETTYPE/instantiate"),
                                 ("PUT", f"/tasks/{ticket.id}")])
        self.assertEqual(wrike.requests[-2][2]["parentId"], "REQFOLDER")
        self.assertEqual(json.loads(wrike.requests[-1][2]["addResponsibles"]), ["KUAAAAA1"])
        self.assertEqual(ticket.status, Status.REVIEWING)

    def test_unknown_assignee_email_leaves_responsibles_empty(self) -> None:
        client, wrike = make()
        client.create_ticket(Request("버그", "t", "b", assignee="U_NOBODY", requester="U_REQ"))
        self.assertNotIn("responsibles", wrike.requests[-1][2])

    def test_custom_item_type_update_failure_deletes_ticket(self) -> None:
        client, wrike = make(ticket_type_id="TICKETTYPE")
        original = wrike.handle

        def fail_put(method: str, path: str, form: dict[str, str],
                     headers: dict[str, str]) -> tuple[int, dict]:
            if method == "PUT":
                return 400, {"error": "invalid_parameter", "errorDescription": "customStatus"}
            return original(method, path, form, headers)

        wrike.handle = fail_put  # type: ignore[method-assign]
        with self.assertRaisesRegex(TrackerError, "invalid_parameter"):
            client.create_ticket(REQ)
        self.assertEqual(wrike.requests[-1][0], "DELETE")
        self.assertTrue(all(t["scope"] == "RbTask" for t in wrike.tasks.values()))

    def test_cleanup_failure_names_leftover_ticket(self) -> None:
        client, wrike = make(ticket_type_id="TICKETTYPE")
        original = wrike.handle

        def fail_writes(method: str, path: str, form: dict[str, str],
                        headers: dict[str, str]) -> tuple[int, dict]:
            if method in ("PUT", "DELETE"):
                return 500, {"error": "server_error"}
            return original(method, path, form, headers)

        wrike.handle = fail_writes  # type: ignore[method-assign]
        with self.assertRaisesRegex(TrackerError, "직접 삭제"):
            client.create_ticket(REQ)

    def test_network_error_raises_tracker_error(self) -> None:
        def down(*_: object) -> tuple[int, dict]:
            raise TimeoutError("timed out")

        client = WrikeClient(make()[0].config, down, EMAILS.get)
        with self.assertRaisesRegex(TrackerError, "연결하지 못했습니다"):
            client.create_ticket(REQ)

    def test_auth_error_raises_tracker_error(self) -> None:
        client, _ = make(token="wrong")
        with self.assertRaisesRegex(TrackerError, "not_authorized"):
            client.create_ticket(REQ)


class DecideCallsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.wrike = make()
        self.ticket = self.client.create_ticket(REQ)

    def test_create_task_and_link(self) -> None:
        self.client.set_status(self.ticket.id, Status.CONFIRMED)
        work = self.client.create_work_item(self.ticket)

        method, path, form = self.wrike.requests[-1]
        self.assertEqual((method, path), ("POST", "/folders/WORKFOLDER/tasks"))
        self.assertEqual(json.loads(form["responsibles"]), ["KUAAAAA1"])
        self.assertIn(self.ticket.permalink, form["description"])
        ticket = self.client.get_ticket(self.ticket.id)
        assert ticket is not None
        self.assertEqual(ticket.status, Status.CONFIRMED)
        self.assertNotEqual(work.id, self.ticket.id)

    def test_status_outside_workflow_is_none(self) -> None:
        self.wrike.tasks[self.ticket.id]["customStatusId"] = "IEAAAAAAJMOTHER1"
        ticket = self.client.get_ticket(self.ticket.id)
        assert ticket is not None
        self.assertIsNone(ticket.status)

    def test_deleted_ticket_is_none(self) -> None:
        self.wrike.tasks[self.ticket.id]["scope"] = "RbTask"
        self.assertIsNone(self.client.get_ticket(self.ticket.id))
        self.assertIsNone(self.client.get_ticket("IEAAAAAAKQNOPE01"))

    def test_add_slack_link(self) -> None:
        self.client.add_slack_link(self.ticket.id, "https://slack/archives/C1/p1")
        description = self.wrike.tasks[self.ticket.id]["description"]
        self.assertIn("재현 &lt;방법&gt;", description)
        self.assertIn('href="https://slack/archives/C1/p1"', description)

    def test_list_recent_items(self) -> None:
        now = datetime.now(timezone.utc)
        items = self.client.list_recent_items(now)

        lists = [(p, f) for m, p, f in self.wrike.requests if m == "GET" and p.endswith("/tasks")]
        self.assertEqual([p for p, _ in lists], ["/folders/REQFOLDER/tasks"] * 2
                         + ["/folders/WORKFOLDER/tasks"] * 2)
        self.assertEqual(json.loads(lists[0][1]["status"]), ["Active"])
        start = json.loads(lists[1][1]["createdDate"])["start"]
        self.assertEqual(start, (now - timedelta(days=90)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(json.loads(lists[0][1]["fields"]), ["briefDescription"])
        self.assertEqual({c.id for c in items}, {self.ticket.id})
        self.assertEqual(items[0].status, "Active")
        self.assertEqual(items[0].created.tzinfo, timezone.utc)

    def test_comment(self) -> None:
        self.client.comment(self.ticket.id, "홍길동 확정")
        self.assertEqual(self.wrike.comments[-1], {"taskId": self.ticket.id, "text": "홍길동 확정"})


if __name__ == "__main__":
    unittest.main()
