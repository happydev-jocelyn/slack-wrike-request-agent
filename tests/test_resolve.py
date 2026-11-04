"""설정의 이름·ID를 Wrike 항목으로 찾는 판정 테스트 (AC11, AC12)."""
import unittest

from request_agent.flows import Status
from request_agent.resolve import (
    REQUEST_STATUSES, Named, find, looks_like_id, missing_statuses, resolve_statuses)

ITEMS = [Named("IEAAAAAAI4AAAAA1", "요청"), Named("IEAAAAAAI4AAAAA2", "전체 일감"),
         Named("IEAAAAAAI4AAAAA3", "요청"), Named("IEAAAAAAI4AAAAA4", "기타")]


class FindTest(unittest.TestCase):
    def test_id_is_checked_as_is(self) -> None:
        self.assertTrue(looks_like_id("IEAAAAAAI4AAAAA2"))
        self.assertFalse(looks_like_id("전체 일감"))
        found = find(ITEMS, "IEAAAAAAI4AAAAA2")
        self.assertEqual(found.kind, "one")
        self.assertEqual(found.items[0].name, "전체 일감")
        self.assertEqual(find(ITEMS, "IEAAAAAAI4ZZZZZZ").kind, "none")

    def test_name_one_none_many(self) -> None:
        self.assertEqual(find(ITEMS, " 전체 일감 ").kind, "one")
        self.assertEqual(find(ITEMS, "없는 폴더").kind, "none")
        many = find(ITEMS, "요청")
        self.assertEqual(many.kind, "many")
        self.assertEqual(len(many.items), 2)

    def test_name_match_ignores_case(self) -> None:
        self.assertEqual(find([Named("IEAAAAAAI4AAAAA9", "Requests")], "requests").kind, "one")


class StatusTest(unittest.TestCase):
    STATUSES = [Named("IEAAAAAAJMAAAAA1", "검토중"), Named("IEAAAAAAJMAAAAA2", "확정")]

    def test_resolve_by_name_or_id(self) -> None:
        names = {Status.REVIEWING: "검토중", Status.CONFIRMED: "IEAAAAAAJMAAAAA2",
                 Status.REJECTED: "반려"}
        resolved = resolve_statuses(self.STATUSES, names)
        self.assertEqual(resolved[Status.REVIEWING].kind, "one")
        self.assertEqual(resolved[Status.CONFIRMED].items[0].name, "확정")
        self.assertEqual(resolved[Status.REJECTED].kind, "none")

    def test_missing_statuses_get_groups(self) -> None:
        names = {status: name for status, (name, _) in REQUEST_STATUSES.items()}
        missing = missing_statuses(resolve_statuses(self.STATUSES, names), names)
        self.assertEqual(missing, [("반려", "Cancelled")])

    def test_missing_skips_ids(self) -> None:
        names = {Status.REVIEWING: "검토중", Status.CONFIRMED: "확정",
                 Status.REJECTED: "IEAAAAAAJMZZZZZZ"}
        # ID로 지정한 상태는 만들 수 없으므로 추가 대상에서 뺀다
        self.assertEqual(missing_statuses(resolve_statuses(self.STATUSES, names), names), [])


if __name__ == "__main__":
    unittest.main()
