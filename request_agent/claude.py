"""Claude API로 AI 의견을 받는다. review.Advisor 포트를 구현한다."""
import json
from typing import Any

from request_agent.review import OPINION_SCHEMA, AdvisorError

try:
    import anthropic
except ImportError:  # AI 의견을 끈 설치에서는 SDK 없이도 동작한다
    anthropic = None

DEFAULT_MODEL = "claude-opus-5-5"
LLM_TIMEOUT_SECONDS = 60.0
# 거절(refusal) 시 Anthropic이 권장하는 모델로 서버에서 다시 시도한다
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class ClaudeAdvisor:
    def __init__(self, model: str = DEFAULT_MODEL, client: Any = None,
                 api_key: str | None = None, base_url: str | None = None) -> None:
        if client is None:
            if anthropic is None:
                raise AdvisorError("anthropic 패키지가 설치되어 있지 않습니다")
            # api_key가 없으면 SDK가 ANTHROPIC_API_KEY 환경변수 등에서 찾는다
            try:
                # base_url은 사내 프록시 등을 거칠 때만 쓴다
                client = anthropic.Anthropic(api_key=api_key, base_url=base_url,
                                             timeout=LLM_TIMEOUT_SECONDS, max_retries=2)
            except anthropic.AnthropicError as e:
                raise AdvisorError(f"Claude 클라이언트를 만들지 못했습니다: {e}") from e
        self.client, self.model = client, model

    def advise(self, system: str, prompt: str) -> dict[str, Any]:
        # SDK 오류는 모두 AdvisorError로 바꾼다. 인증 정보가 없으면 SDK가 요청 시점에
        # TypeError("Could not resolve authentication method")를 내므로 함께 잡는다.
        api_errors: tuple[type[Exception], ...] = (TypeError,)
        if anthropic:
            api_errors += (anthropic.AnthropicError,)
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=4000,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=system,
                messages=[{"role": "user", "content": prompt}],
                # 짧은 비교 작업이라 effort는 low, 응답은 스키마에 맞는 JSON으로 받는다
                output_config={"effort": "low",
                               "format": {"type": "json_schema", "schema": OPINION_SCHEMA}},
            )
        except api_errors as e:
            status = getattr(e, "status_code", None)
            reason = (f"HTTP {status}" if status
                      else "인증 정보 없음" if isinstance(e, TypeError) else "연결 실패")
            raise AdvisorError(f"Claude API를 호출하지 못했습니다({reason})") from e

        if response.stop_reason == "refusal":
            raise AdvisorError("Claude가 이 요청에 대한 의견을 거절했습니다")
        if response.stop_reason == "max_tokens":
            raise AdvisorError("Claude 응답이 길이 제한에 걸렸습니다")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise AdvisorError("Claude 응답을 해석하지 못했습니다") from e
        if not isinstance(data, dict):
            raise AdvisorError("Claude 응답 형식이 예상과 다릅니다")
        return data
