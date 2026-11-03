"""OpenAI 호환 LLM 연결 계약 테스트와 제공자 선택 테스트 (AC17)."""
import json
import unittest

from request_agent.claude import ClaudeAdvisor
from request_agent.llm import ConfigErrorAdvisor, OpenAICompatibleAdvisor, make_advisor
from request_agent.review import AdvisorError

OK_BODY = {"choices": [{"message": {"role": "assistant",
                                    "content": '{"similar": [], "comment": "없음"}'}}]}


class RecordingTransport:
    def __init__(self, status: int = 200, body: object = OK_BODY,
                 error: Exception | None = None) -> None:
        self.status, self.body, self.error = status, body, error
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url: str, payload: dict, headers: dict) -> tuple[int, object]:
        self.calls.append((url, payload, headers))
        if self.error:
            raise self.error
        return self.status, self.body


def make(transport: RecordingTransport) -> OpenAICompatibleAdvisor:
    return OpenAICompatibleAdvisor(base_url="https://llm.example.com/v1/", api_key="k",
                                   model="gpt-x", transport=transport)


class OpenAICompatibleTest(unittest.TestCase):
    def test_request_shape(self) -> None:
        transport = RecordingTransport()
        self.assertEqual(make(transport).advise("시스템", "프롬프트"),
                         {"similar": [], "comment": "없음"})

        url, payload, headers = transport.calls[0]
        self.assertEqual(url, "https://llm.example.com/v1/chat/completions")
        self.assertEqual(headers["Authorization"], "Bearer k")
        self.assertEqual(payload["model"], "gpt-x")
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        system, user = payload["messages"]
        self.assertEqual(system["role"], "system")
        self.assertIn("시스템", system["content"])
        self.assertIn("JSON", system["content"])  # json_object 모드는 지시문에 JSON이 있어야 한다
        self.assertEqual(user, {"role": "user", "content": "프롬프트"})
        json.dumps(payload)  # 직렬화할 수 없는 값을 넣지 않았는지 확인

    def test_errors_become_advisor_error(self) -> None:
        cases = [
            RecordingTransport(status=401, body={"error": {"message": "bad key"}}),
            RecordingTransport(error=OSError("connection refused")),
            RecordingTransport(body={"choices": [{"message": {"content": "not json"}}]}),
            RecordingTransport(body={"choices": [{"message": {"content": "[1]"}}]}),
            RecordingTransport(body={"unexpected": True}),
        ]
        for transport in cases:
            with self.assertRaises(AdvisorError):
                make(transport).advise("s", "p")


class MakeAdvisorTest(unittest.TestCase):
    def test_off(self) -> None:
        self.assertIsNone(make_advisor({"AI_OPINION": "off", "LLM_PROVIDER": "anthropic"}))

    def test_openai_compatible(self) -> None:
        advisor = make_advisor({"LLM_PROVIDER": "openai-compatible", "LLM_API_KEY": "k",
                                "LLM_BASE_URL": "https://llm.example.com/v1",
                                "LLM_MODEL": "gpt-x"})
        self.assertIsInstance(advisor, OpenAICompatibleAdvisor)
        assert isinstance(advisor, OpenAICompatibleAdvisor)  # 타입 좁히기
        self.assertEqual(advisor.model, "gpt-x")

    def assert_config_error(self, env: dict[str, str], reason: str) -> None:
        advisor = make_advisor(env)
        self.assertIsInstance(advisor, ConfigErrorAdvisor)
        assert advisor is not None  # 타입 좁히기
        with self.assertRaisesRegex(AdvisorError, reason):  # 조용히 꺼지지 않고 매번 실패를 알린다
            advisor.advise("s", "p")

    def test_openai_compatible_requires_settings(self) -> None:
        self.assert_config_error({"LLM_PROVIDER": "openai-compatible", "LLM_MODEL": "m"},
                                 "LLM_BASE_URL")

    def test_base_url_must_be_http(self) -> None:
        self.assert_config_error({"LLM_PROVIDER": "openai-compatible", "LLM_MODEL": "m",
                                  "LLM_BASE_URL": "file:///etc/passwd"}, "http")

    def test_unknown_provider(self) -> None:
        self.assert_config_error({"LLM_PROVIDER": "nope"}, "LLM_PROVIDER")

    def test_anthropic_default_uses_model_setting(self) -> None:
        sentinel = object()
        advisor = make_advisor({"LLM_MODEL": "claude-sonnet-5-5"}, claude_client=sentinel)
        self.assertIsInstance(advisor, ClaudeAdvisor)
        assert isinstance(advisor, ClaudeAdvisor)  # 타입 좁히기
        self.assertEqual(advisor.model, "claude-sonnet-5-5")
        self.assertIs(advisor.client, sentinel)


if __name__ == "__main__":
    unittest.main()
