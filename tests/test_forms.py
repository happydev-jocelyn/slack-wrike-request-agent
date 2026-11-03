"""요청 폼 미리 채우기와 채널 글 등록 제안 판정 테스트 (AC15, AC16)."""
import unittest

from request_agent.forms import request_form, should_suggest

CHANNEL = "C_REQ"
CATEGORIES = {"bug": "버그", "etc": "기타"}


def body_element(view: dict) -> dict:
    block = next(b for b in view["blocks"] if b["block_id"] == "body")
    return block["element"]


class RequestFormTest(unittest.TestCase):
    def test_empty_form_has_four_fields(self) -> None:
        view = request_form(CATEGORIES)
        self.assertEqual([b["block_id"] for b in view["blocks"]],
                         ["category", "title", "body", "assignee"])
        self.assertNotIn("initial_value", body_element(view))

    def test_prefill_body(self) -> None:
        view = request_form(CATEGORIES, body="로그인이 안 돼요")
        self.assertEqual(body_element(view)["initial_value"], "로그인이 안 돼요")

    def test_prefill_is_truncated_to_input_limit(self) -> None:
        view = request_form(CATEGORIES, body="가" * 5000)
        self.assertEqual(len(body_element(view)["initial_value"]), 3000)


class ShouldSuggestTest(unittest.TestCase):
    def event(self, **overrides: object) -> dict:
        return {"type": "message", "channel": CHANNEL, "user": "U1", "text": "요청이요",
                "ts": "1.1", **overrides}

    def test_should_suggest(self) -> None:
        self.assertTrue(should_suggest(self.event(), CHANNEL))

    def test_skips_other_channel(self) -> None:
        self.assertFalse(should_suggest(self.event(channel="C_OTHER"), CHANNEL))

    def test_skips_thread_reply(self) -> None:
        self.assertFalse(should_suggest(self.event(thread_ts="1.0"), CHANNEL))

    def test_thread_parent_is_still_suggested(self) -> None:
        self.assertTrue(should_suggest(self.event(thread_ts="1.1"), CHANNEL))

    def test_skips_bot_and_subtypes(self) -> None:
        self.assertFalse(should_suggest(self.event(bot_id="B1"), CHANNEL))
        for subtype in ("message_changed", "message_deleted", "channel_join", "bot_message"):
            self.assertFalse(should_suggest(self.event(subtype=subtype), CHANNEL))

    def test_file_share_is_suggested(self) -> None:
        self.assertTrue(should_suggest(self.event(subtype="file_share"), CHANNEL))

    def test_skips_empty_text(self) -> None:
        self.assertFalse(should_suggest(self.event(text="  "), CHANNEL))


if __name__ == "__main__":
    unittest.main()
