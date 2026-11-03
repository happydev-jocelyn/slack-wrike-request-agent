"""Claude 의견 클라이언트 계약 테스트 (AC4, AC6). SDK 없이 가짜 클라이언트로 요청 형태를 확인한다."""
import json
import unittest
from types import SimpleNamespace

from request_agent.claude import FALLBACK_BETA, ClaudeAdvisor
from request_agent.review import OPINION_SCHEMA, AdvisorError


class FakeMessages:
    def __init__(self, response: SimpleNamespace) -> None:
        self.response = response
        self.kwargs: dict = {}

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.kwargs = kwargs
        return self.response


def make(stop_reason: str = "end_turn", text: str = '{"similar": [], "comment": "ok"}'
         ) -> tuple[ClaudeAdvisor, FakeMessages]:
    blocks = [SimpleNamespace(type="thinking", thinking=""),
              SimpleNamespace(type="text", text=text)]
    messages = FakeMessages(SimpleNamespace(stop_reason=stop_reason, content=blocks))
    client = SimpleNamespace(beta=SimpleNamespace(messages=messages))
    return ClaudeAdvisor(client=client), messages


class ClaudeAdvisorTest(unittest.TestCase):
    def test_request_shape(self) -> None:
        advisor, messages = make()
        self.assertEqual(advisor.advise("시스템", "프롬프트"), {"similar": [], "comment": "ok"})

        kwargs = messages.kwargs
        self.assertEqual(kwargs["model"], "claude-opus-5-5")
        self.assertEqual(kwargs["betas"], [FALLBACK_BETA])
        self.assertEqual(kwargs["fallbacks"], "default")
        self.assertEqual(kwargs["system"], "시스템")
        self.assertEqual(kwargs["messages"], [{"role": "user", "content": "프롬프트"}])
        self.assertEqual(kwargs["output_config"]["format"],
                         {"type": "json_schema", "schema": OPINION_SCHEMA})
        for removed in ("temperature", "top_p", "top_k", "thinking"):
            self.assertNotIn(removed, kwargs)
        json.dumps(kwargs["output_config"])  # 직렬화 가능한 값만 보낸다

    def test_missing_credentials_raise_advisor_error(self) -> None:
        def no_auth(**_: object) -> None:
            raise TypeError("Could not resolve authentication method")

        advisor, messages = make()
        messages.create = no_auth  # type: ignore[method-assign]
        with self.assertRaisesRegex(AdvisorError, "인증 정보 없음"):
            advisor.advise("s", "p")

    def test_refusal_and_bad_output_raise(self) -> None:
        cases = (("refusal", ""), ("max_tokens", "{"), ("end_turn", "not json"),
                 ("end_turn", "[1, 2]"))
        for stop_reason, text in cases:
            advisor, _ = make(stop_reason, text)
            with self.assertRaises(AdvisorError):
                advisor.advise("s", "p")


if __name__ == "__main__":
    unittest.main()
