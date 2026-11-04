"""워크스페이스별 설정과 저장소. 토큰·API 키는 암호화해 저장한다.

일감 데이터는 저장하지 않는다(정본은 Wrike). 이 저장소에는 설정만 들어간다.
"""
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Protocol

from request_agent.flows import Status

DEFAULT_CATEGORIES = {"feature": "기능", "bug": "버그", "improvement": "개선", "etc": "기타"}
SECRET_FIELDS = ("wrike_token", "llm_api_key")


class Cipher(Protocol):
    def encrypt(self, text: str) -> str: ...
    def decrypt(self, token: str) -> str: ...


@dataclass(frozen=True)
class Settings:
    # Wrike 연결
    wrike_token: str = ""
    wrike_base_url: str = "https://www.wrike.com/api/v4"
    # 관리자가 입력한 이름 또는 ID
    project: str = ""
    request_folder: str = "요청"
    work_folder: str = "전체 일감"
    ticket_type: str = ""
    workflow: str = "요청 처리"
    status_names: dict[Status, str] = field(default_factory=lambda: {
        Status.REVIEWING: "검토중", Status.CONFIRMED: "확정", Status.REJECTED: "반려"})
    # 연결 확인으로 찾은 ID
    project_id: str = ""
    request_folder_id: str = ""
    work_folder_id: str = ""
    ticket_type_id: str = ""
    workflow_id: str = ""
    status_ids: dict[Status, str] = field(default_factory=dict)
    # 앱이 만든 전용 워크플로. 상태 추가는 여기에만 한다(기존 업무 워크플로 보호)
    created_workflow_ids: tuple[str, ...] = ()
    # Slack
    request_channel_id: str = ""
    approver_ids: tuple[str, ...] = ()
    categories: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_CATEGORIES))
    # AI 의견
    ai_opinion: bool = True
    llm_provider: str = "anthropic"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""

    def is_complete(self) -> bool:
        """등록·승인을 처리할 수 있을 만큼 Wrike 설정이 채워졌는지."""
        return bool(self.wrike_token and self.request_folder_id and self.work_folder_id
                    and len(self.status_ids) == len(Status))

    def llm_env(self) -> dict[str, str]:
        """llm.make_advisor가 읽는 형태."""
        return {"AI_OPINION": "on" if self.ai_opinion else "off",
                "LLM_PROVIDER": self.llm_provider, "LLM_BASE_URL": self.llm_base_url,
                "LLM_API_KEY": self.llm_api_key, "LLM_MODEL": self.llm_model}

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        """홈 탭에 저장한 설정이 없을 때 쓰는 환경변수 설정(이전 방식과 호환)."""
        statuses = {status: env.get(f"WRIKE_STATUS_{status.name}", "") for status in Status}
        categories = dict(pair.split(":", 1)
                          for pair in env.get("CATEGORIES", "").split(",") if ":" in pair)
        return replace(
            cls(),
            wrike_token=env.get("WRIKE_TOKEN", ""),
            wrike_base_url=env.get("WRIKE_BASE_URL", cls.wrike_base_url),
            request_folder_id=env.get("WRIKE_REQUEST_FOLDER_ID", ""),
            work_folder_id=env.get("WRIKE_WORK_FOLDER_ID", ""),
            ticket_type_id=env.get("WRIKE_TICKET_TYPE_ID", ""),
            request_channel_id=env.get("REQUEST_CHANNEL_ID", ""),
            status_ids={s: i for s, i in statuses.items() if i},
            approver_ids=tuple(filter(None, env.get("APPROVER_IDS", "").split(","))),
            categories=categories or dict(DEFAULT_CATEGORIES),
            ai_opinion=env.get("AI_OPINION", "on").lower() != "off",
            llm_provider=env.get("LLM_PROVIDER", "anthropic"),
            llm_base_url=env.get("LLM_BASE_URL", ""),
            llm_api_key=env.get("LLM_API_KEY", ""),
            llm_model=env.get("LLM_MODEL", ""),
        )


def is_admin(user: str, admins: set[str]) -> bool:
    return user in admins


def _to_json(settings: Settings, cipher: Cipher) -> str:
    data = asdict(settings)
    for name in ("status_names", "status_ids"):
        data[name] = {status.name: value for status, value in data[name].items()}
    for name in SECRET_FIELDS:
        data[name] = cipher.encrypt(data[name]) if data[name] else ""
    return json.dumps(data, ensure_ascii=False)


def _from_json(raw: str, cipher: Cipher) -> Settings:
    data = json.loads(raw)
    known = {f.name for f in fields(Settings)}
    data = {k: v for k, v in data.items() if k in known}  # 옛 버전 필드는 버린다
    for name in ("status_names", "status_ids"):
        if name in data:
            data[name] = {Status[key]: value for key, value in data[name].items()}
    for name in SECRET_FIELDS:
        if data.get(name):
            data[name] = cipher.decrypt(data[name])
    for name in ("approver_ids", "created_workflow_ids"):
        if name in data:
            data[name] = tuple(data[name])
    return Settings(**data)


class SettingsStore:
    """SQLite 파일 하나에 워크스페이스(team_id)별 설정을 둔다."""

    def __init__(self, path: Path, cipher: Cipher) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.cipher = path, cipher
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS settings "
                       "(team_id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """트랜잭션을 커밋(또는 롤백)하고 연결을 닫는다."""
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def load(self, team_id: str) -> Settings | None:
        with self._connect() as db:
            row = db.execute("SELECT data FROM settings WHERE team_id = ?",
                             (team_id,)).fetchone()
        return _from_json(row[0], self.cipher) if row else None

    def teams(self) -> list[str]:
        with self._connect() as db:
            return [row[0] for row in db.execute("SELECT team_id FROM settings")]

    def save(self, team_id: str, settings: Settings) -> None:
        with self._connect() as db:
            db.execute("INSERT INTO settings (team_id, data) VALUES (?, ?) "
                       "ON CONFLICT(team_id) DO UPDATE SET data = excluded.data",
                       (team_id, _to_json(settings, self.cipher)))


class FernetCipher:
    """SETTINGS_KEY(Fernet 키)로 암호화한다. 키 만들기: python -m request_agent.settings genkey"""

    def __init__(self, key: str) -> None:
        # SETTINGS_KEY 없이(환경변수 설정 방식) 돌릴 때는 cryptography 없이도 뜨도록 여기서 읽는다
        from cryptography.fernet import Fernet

        self._fernet = Fernet(key.encode())

    def encrypt(self, text: str) -> str:
        return self._fernet.encrypt(text.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()


if __name__ == "__main__":
    import sys

    if sys.argv[1:] == ["genkey"]:
        from cryptography.fernet import Fernet

        print(Fernet.generate_key().decode())
