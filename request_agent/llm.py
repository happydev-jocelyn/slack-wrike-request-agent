"""LLM 제공자 선택과 OpenAI 호환 Chat Completions 연결. review.Advisor 포트를 구현한다."""
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

from request_agent.claude import DEFAULT_MODEL, LLM_TIMEOUT_SECONDS, ClaudeAdvisor
from request_agent.review import Advisor, AdvisorError

# (url, JSON 본문, 헤더) -> (HTTP 상태, 응답 JSON)
Transport = Callable[[str, dict[str, Any], dict[str, str]], tuple[int, Any]]

# json_object 모드는 스키마를 강제하지 않으므로 형식을 지시문으로 알려 준다
JSON_FORMAT_HINT = (
    '반드시 JSON 객체 하나로만 답합니다: '
    '{"similar": [{"id": "기존 일감 id", "reason": "겹치는 이유"}], "comment": "의견"}'
)


def urllib_transport(url: str, payload: dict[str, Any],
                     headers: dict[str, str]) -> tuple[int, Any]:
    """JSON을 POST한다. HTTP 오류도 예외 없이 (상태, 본문)으로 돌려준다."""
    data = json.dumps(payload, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        # 주소는 운영자 설정이며 make_advisor가 http/https만 허용한다
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT_SECONDS) as res:  # nosec B310
            return res.status, json.load(res)
    except urllib.error.HTTPError as e:
        with e:
            try:
                return e.code, json.loads(e.read() or b"{}")
            except ValueError:  # JSON이 아닌 오류 본문도 상태 코드로 보고한다
                return e.code, {}


class ConfigErrorAdvisor:
    """설정이 잘못됐을 때 요청마다 그 이유로 실패해 스레드에 알린다(조용히 꺼지지 않게)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def advise(self, system: str, prompt: str) -> dict[str, Any]:
        raise AdvisorError(f"AI 설정 오류: {self.reason}")


class OpenAICompatibleAdvisor:
    """OpenAI 호환 API(OpenAI·Gemini·Azure·사내 프록시·로컬 모델 등)로 의견을 받는다."""

    def __init__(self, base_url: str, api_key: str, model: str,
                 transport: Transport = urllib_transport) -> None:
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key, self.model, self.transport = api_key, model, transport

    def advise(self, system: str, prompt: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": f"{system}\n{JSON_FORMAT_HINT}"},
                         {"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            status, body = self.transport(self.url, payload, headers)
        except (OSError, ValueError) as e:  # 연결 실패·타임아웃·JSON이 아닌 응답
            raise AdvisorError(f"LLM API에 연결하지 못했습니다({e})") from e
        if status >= 400:
            raise AdvisorError(f"LLM API를 호출하지 못했습니다(HTTP {status})")
        try:
            data = json.loads(body["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise AdvisorError("LLM 응답을 해석하지 못했습니다") from e
        if not isinstance(data, dict):
            raise AdvisorError("LLM 응답 형식이 예상과 다릅니다")
        return data


def make_advisor(env: Mapping[str, str], claude_client: Any = None) -> Advisor | None:
    """환경 설정으로 의견 생성기를 고른다.

    AI_OPINION=off이면 None. 설정이 잘못됐으면 요청마다 실패를 알리는 ConfigErrorAdvisor.
    Claude SDK가 설치되지 않았으면 AdvisorError(호출자가 AI를 끈다).
    """
    if env.get("AI_OPINION", "on").lower() == "off":
        return None
    provider = env.get("LLM_PROVIDER", "anthropic")
    model, api_key = env.get("LLM_MODEL", ""), env.get("LLM_API_KEY", "")
    base_url = env.get("LLM_BASE_URL", "")
    if base_url and not base_url.startswith(("https://", "http://")):
        return ConfigErrorAdvisor("LLM_BASE_URL은 http:// 또는 https://로 시작해야 합니다")
    if provider == "anthropic":
        return ClaudeAdvisor(model=model or DEFAULT_MODEL, client=claude_client,
                             api_key=api_key or None, base_url=base_url or None)
    if provider == "openai-compatible":
        missing = [name for name in ("LLM_BASE_URL", "LLM_MODEL") if not env.get(name)]
        if missing:
            return ConfigErrorAdvisor(
                f"openai-compatible에는 {', '.join(missing)} 설정이 필요합니다")
        return OpenAICompatibleAdvisor(base_url, api_key, model)
    return ConfigErrorAdvisor(f"알 수 없는 LLM_PROVIDER입니다: {provider}")
