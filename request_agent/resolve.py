"""설정에 넣은 이름 또는 ID를 Wrike 항목(프로젝트·폴더·워크플로·상태·티켓 유형)으로 찾는다."""
import re
from dataclasses import dataclass
from typing import Literal

from request_agent.flows import Status

# Wrike API v4 ID는 대문자·숫자 16자다
WRIKE_ID = re.compile(r"[A-Z0-9]{16}")

# 전용 요청 워크플로의 기본 상태 이름과 Wrike 상태 그룹
REQUEST_STATUSES: dict[Status, tuple[str, str]] = {
    Status.REVIEWING: ("검토중", "Active"),
    Status.CONFIRMED: ("확정", "Completed"),
    Status.REJECTED: ("반려", "Cancelled"),
}


@dataclass(frozen=True)
class Named:
    id: str
    name: str


@dataclass(frozen=True)
class Found:
    kind: Literal["one", "none", "many"]
    items: tuple[Named, ...] = ()


def looks_like_id(value: str) -> bool:
    return bool(WRIKE_ID.fullmatch(value.strip()))


def find(items: list[Named], query: str) -> Found:
    """ID면 ID로, 아니면 이름(앞뒤 공백·대소문자 무시)으로 찾는다."""
    query = query.strip()
    if looks_like_id(query):
        hits = [item for item in items if item.id == query]
    else:
        hits = [item for item in items if item.name.strip().casefold() == query.casefold()]
    if not hits:
        return Found("none")
    return Found("one" if len(hits) == 1 else "many", tuple(hits))


def resolve_statuses(statuses: list[Named], names: dict[Status, str]) -> dict[Status, Found]:
    return {status: find(statuses, name) for status, name in names.items()}


def missing_statuses(resolved: dict[Status, Found],
                     names: dict[Status, str]) -> list[tuple[str, str]]:
    """워크플로에 추가해야 할 (상태 이름, 그룹). ID로 지정했는데 없는 상태는 만들 수 없어 뺀다."""
    return [(names[status].strip(), REQUEST_STATUSES[status][1])
            for status, found in resolved.items()
            if found.kind == "none" and not looks_like_id(names[status])]
