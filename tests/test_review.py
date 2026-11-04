"""AI 의견 테스트 (AC4~AC6)."""
import unittest
from datetime import datetime, timedelta, timezone

from request_agent.flows import Request, Status, Ticket, TrackerError
from request_agent.review import (
    NO_SIMILAR, AdvisorError, Candidate, Opinion, Similar, build_prompt, format_opinion,
    parse_opinion, post_opinion, select_candidates)
from tests.test_flows import FakeChat

NOW = datetime(2026, 11, 4, tzinfo=timezone.utc)
REQ = Request(category="버그", title="로그인 오류", body="비밀번호 재설정 후 로그인 실패",
              assignee="U_A", requester="U_R")
TICKET = Ticket(id="NEW", title="[버그] 로그인 오류", permalink="https://wrike/new",
                status=Status.REVIEWING)


def item(item_id: str, days_ago: int, status: str = "Completed", title: str = "") -> Candidate:
    return Candidate(id=item_id, title=title or f"일감 {item_id}", summary="요약", status=status,
                     created=NOW - timedelta(days=days_ago),
                     permalink=f"https://wrike/{item_id}")


class SelectCandidatesTest(unittest.TestCase):
    def test_candidates_filter_90d_active(self) -> None:
        items = [item("old_done", 200), item("old_active", 200, "Active"),
                 item("recent_done", 10), item("NEW", 0, "Active")]
        picked = select_candidates(items, NOW, exclude_id="NEW")
        self.assertEqual([c.id for c in picked], ["recent_done", "old_active"])

    def test_duplicates_and_limit(self) -> None:
        items = [item(f"T{i}", i) for i in range(80)] + [item("T1", 1)]
        picked = select_candidates(items, NOW, exclude_id="NEW")
        self.assertEqual(len(picked), 50)
        self.assertEqual(picked[0].id, "T0")


class ParseOpinionTest(unittest.TestCase):
    CANDIDATES = [item("A", 1), item("B", 2), item("C", 3), item("D", 4)]

    def test_parse_opinion(self) -> None:
        data = {"similar": [{"id": "A", "reason": "같은 화면"}, {"id": "X", "reason": "없는 id"},
                            {"id": "A", "reason": "중복"}, {"id": "B", "reason": "b"},
                            {"id": "C", "reason": "c"}, {"id": "D", "reason": "d"}],
                "comment": " 중복 가능성이 큽니다. "}
        opinion = parse_opinion(data, self.CANDIDATES)
        self.assertEqual([s.candidate.id for s in opinion.similar], ["A", "B", "C"])
        self.assertEqual(opinion.similar[0].reason, "같은 화면")
        self.assertEqual(opinion.comment, "중복 가능성이 큽니다.")

    def test_malformed_opinion_raises(self) -> None:
        for data in ({}, {"foo": 1}, {"similar": "ab", "comment": ""},
                     {"similar": [1], "comment": ""}, {"similar": None, "comment": ""},
                     {"similar": [], "comment": 5},
                     {"similar": [{"id": "A"}], "comment": ""}):
            with self.assertRaises(AdvisorError, msg=str(data)):
                parse_opinion(data, self.CANDIDATES)

    def test_no_similar_message(self) -> None:
        opinion = parse_opinion({"similar": [], "comment": ""}, self.CANDIDATES)
        self.assertIn(NO_SIMILAR, format_opinion(opinion, slack=True))
        self.assertIn(NO_SIMILAR, format_opinion(opinion, slack=False))


class FormatTest(unittest.TestCase):
    def test_slack_and_wrike_links(self) -> None:
        opinion = Opinion((Similar(item("A", 1, title="로그인 수정"), "같은 화면"),), "확인 필요")
        self.assertIn("<https://wrike/A|로그인 수정> — 같은 화면", format_opinion(opinion, True))
        self.assertIn("로그인 수정 (https://wrike/A)", format_opinion(opinion, False))

    def test_slack_text_is_escaped(self) -> None:
        opinion = Opinion((Similar(item("A", 1, title="a|b>c"), "<!channel> 확인"),), "<!here>")
        text = format_opinion(opinion, slack=True)
        self.assertNotIn("<!channel>", text)
        self.assertNotIn("<!here>", text)
        self.assertIn("<https://wrike/A|a|b&gt;c>", text)
        self.assertIn("<!channel> 확인", format_opinion(opinion, slack=False))  # Wrike는 평문

    def test_prompt_lists_candidates_and_request(self) -> None:
        prompt = build_prompt(REQ, [item("A", 1, "Active", "로그인 수정")])
        self.assertIn("비밀번호 재설정 후 로그인 실패", prompt)
        self.assertIn("id=A | 상태=Active | 제목=로그인 수정", prompt)
        self.assertIn("(없음)", build_prompt(REQ, []))


class FakeItems:
    def __init__(self, items: list[Candidate], fail: bool = False) -> None:
        self.items, self.fail = items, fail
        self.comments: list[tuple[str, str]] = []

    def list_recent_items(self, now: datetime) -> list[Candidate]:
        if self.fail:
            raise TrackerError("Wrike 503")
        return self.items

    def comment(self, ticket_id: str, text: str) -> None:
        self.comments.append((ticket_id, text))


class FakeAdvisor:
    def __init__(self, answer: dict | None = None) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def advise(self, system: str, prompt: str) -> dict:
        self.prompts.append(prompt)
        if self.answer is None:
            raise AdvisorError("rate limited")
        return self.answer


class PostOpinionTest(unittest.TestCase):
    def test_opinion_posted_to_thread_and_wrike(self) -> None:
        items, chat = FakeItems([item("A", 1, title="로그인 수정"), item("NEW", 0)]), FakeChat()
        advisor = FakeAdvisor({"similar": [{"id": "A", "reason": "같은 증상"}], "comment": "중복"})
        opinion = post_opinion(REQ, TICKET, items, advisor, chat, NOW)

        assert opinion is not None
        self.assertNotIn("id=NEW", advisor.prompts[0])  # 새 티켓 자신은 비교하지 않는다
        self.assertIn("<https://wrike/A|로그인 수정>", chat.replies[0])
        self.assertEqual(items.comments[0][0], "NEW")
        self.assertIn("로그인 수정 (https://wrike/A)", items.comments[0][1])

    def test_ai_failure_does_not_block(self) -> None:
        for items, advisor in ((FakeItems([]), FakeAdvisor(None)),
                               (FakeItems([], fail=True), FakeAdvisor({})),
                               (FakeItems([]), FakeAdvisor({"similar": None}))):
            chat = FakeChat()
            self.assertIsNone(post_opinion(REQ, TICKET, items, advisor, chat, NOW))
            self.assertEqual(len(chat.replies), 1)
            self.assertIn("AI 의견 생성 실패", chat.replies[0])
            self.assertEqual(items.comments, [])


if __name__ == "__main__":
    unittest.main()
