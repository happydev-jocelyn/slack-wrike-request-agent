"""설정 저장 시 연결 확인: 입력한 이름·ID를 Wrike에서 찾아 ID를 채우고, 문제와 해결 방법을 알려 준다."""
from dataclasses import dataclass, replace

from request_agent.flows import Status, TrackerError
from request_agent.resolve import (
    REQUEST_STATUSES, Named, find, looks_like_id, missing_statuses, resolve_statuses)
from request_agent.settings import Settings
from request_agent.wrike_setup import WrikeSetup

# fix 값 → 그 해결 방법이 다루는 설정 항목
FIX_FIELDS = {"create_project": "project", "create_request_folder": "request_folder",
              "create_work_folder": "work_folder", "create_workflow": "workflow",
              "add_statuses": "statuses"}


@dataclass(frozen=True)
class Problem:
    # wrike_token, wrike, project, request_folder, work_folder, ticket_type, workflow, statuses
    field: str
    message: str
    candidates: tuple[Named, ...] = ()  # 이름이 겹칠 때 고를 후보
    fix: str | None = None  # 관리자가 누를 수 있는 해결 방법(FIX_FIELDS의 키)


@dataclass(frozen=True)
class SetupReport:
    settings: Settings
    problems: tuple[Problem, ...]
    account: str = ""  # Wrike 토큰 주인 이름
    # (항목 이름, 찾은 Wrike 항목). 홈 탭에 이름과 ID를 함께 보여 준다
    found: tuple[tuple[str, Named], ...] = ()

    @property
    def ok(self) -> bool:
        return not self.problems


def _resolve(label: str, field: str, items: list[Named], query: str,
             fix: str | None, found_list: list[tuple[str, Named]]) -> tuple[str, Problem | None]:
    found = find(items, query)
    if found.kind == "one":
        found_list.append((label, found.items[0]))
        return found.items[0].id, None
    if found.kind == "many":
        return "", Problem(field, f"{label} '{query}'이(가) 여러 개입니다. 하나를 고르세요.",
                           candidates=found.items)
    return "", Problem(field, f"{label} '{query}'을(를) 찾지 못했습니다.",
                       fix=None if looks_like_id(query) else fix)


def check_settings(settings: Settings, setup: WrikeSetup) -> SetupReport:
    try:
        account = setup.check()
    except TrackerError as e:
        return SetupReport(settings, (Problem("wrike_token", f"Wrike 연결 실패: {e}"),))

    problems: list[Problem] = []
    found_list: list[tuple[str, Named]] = []
    ids: dict[str, str] = {}
    status_ids: dict[Status, str] = {}
    try:
        ids["project_id"], p = _resolve("프로젝트", "project", setup.projects(),
                                        settings.project, "create_project", found_list)
        problems += [p] if p else []
        if ids["project_id"]:
            folders = setup.folders(ids["project_id"])
            for field, label in (("request_folder", "요청 폴더"), ("work_folder", "전체 일감 폴더")):
                ids[f"{field}_id"], p = _resolve(label, field, folders, getattr(settings, field),
                                                 f"create_{field}", found_list)
                problems += [p] if p else []

        ids["ticket_type_id"] = ""
        if settings.ticket_type.strip():
            ids["ticket_type_id"], p = _resolve("티켓 유형", "ticket_type", setup.item_types(),
                                                settings.ticket_type, None, found_list)
            problems += [p] if p else []

        workflows = setup.workflows()
        ids["workflow_id"], p = _resolve("요청 워크플로", "workflow", [w for w, _ in workflows],
                                         settings.workflow, "create_workflow", found_list)
        problems += [p] if p else []
        if ids["workflow_id"]:
            statuses = next(s for w, s in workflows if w.id == ids["workflow_id"])
            resolved = resolve_statuses(statuses, settings.status_names)
            status_ids = {s: f.items[0].id for s, f in resolved.items() if f.kind == "one"}
            found_list += [(f"상태: {s.value}", f.items[0])
                           for s, f in resolved.items() if f.kind == "one"]
            if missing := missing_statuses(resolved, settings.status_names):
                names = ", ".join(name for name, _ in missing)
                if ids["workflow_id"] in settings.created_workflow_ids:
                    problems.append(Problem("statuses", f"요청 워크플로에 상태가 없습니다: {names}",
                                            fix="add_statuses"))
                else:  # 앱이 만들지 않은 워크플로는 업무용일 수 있어 바꾸지 않는다
                    problems.append(Problem(
                        "statuses", f"요청 워크플로에 상태가 없습니다: {names}. 앱은 직접 만든 "
                        "전용 워크플로에만 상태를 추가합니다. Wrike에서 추가하거나 다른 이름으로 "
                        "새 전용 워크플로를 만들어 주세요."))
            for status, found in resolved.items():
                name = settings.status_names[status]
                if found.kind == "many":
                    problems.append(Problem(f"status:{status.name}",
                                            f"상태 '{name}'이(가) 여러 개입니다. 하나를 고르세요.",
                                            candidates=found.items))
                elif found.kind == "none" and looks_like_id(name):
                    problems.append(Problem(f"status:{status.name}",
                                            f"상태 ID '{name}'이(가) 요청 워크플로에 없습니다."))
    except TrackerError as e:
        problems.append(Problem("wrike", f"Wrike 조회 실패: {e}"))

    return SetupReport(replace(settings, **ids, status_ids=status_ids), tuple(problems), account,
                       tuple(found_list))


class PartialFixError(TrackerError):
    """만들기가 일부만 됐을 때. 그때까지 바뀐 설정을 담는다."""

    def __init__(self, settings: Settings, cause: TrackerError) -> None:
        super().__init__(str(cause))
        self.settings = settings


def apply_fix(settings: Settings, setup: WrikeSetup, fix: str) -> SetupReport:
    """관리자가 누른 해결 방법(만들기)을 실행하고 다시 확인한다. 실패하면 그 이유를 문제에 담는다."""
    try:
        settings = _run_fix(settings, setup, fix)
    except PartialFixError as e:
        # 워크플로는 만들어졌으니 앱 소유로 남기고, 빠진 상태는 '상태 추가'로 이어서 만든다
        report = check_settings(e.settings, setup)
        note = Problem(FIX_FIELDS[fix], f"워크플로는 만들었지만 상태 일부를 만들지 못했습니다: {e}")
        return replace(report, problems=(note, *report.problems))
    except TrackerError as e:
        report = check_settings(settings, setup)
        field = FIX_FIELDS[fix]
        failed = Problem(field, f"만들지 못했습니다: {e}", fix=fix)
        others = tuple(p for p in report.problems if p.field != field)
        return replace(report, problems=(failed, *others))
    return check_settings(settings, setup)


def _run_fix(settings: Settings, setup: WrikeSetup, fix: str) -> Settings:
    if fix == "create_project":
        return replace(settings, project=setup.create_project(settings.project.strip()).id)
    if fix in ("create_request_folder", "create_work_folder"):
        field = FIX_FIELDS[fix]
        folder = setup.create_folder(settings.project_id, getattr(settings, field).strip())
        return replace(settings, **{field: folder.id})
    if fix == "create_workflow":
        workflow = setup.create_workflow(settings.workflow.strip())
        settings = replace(settings, workflow=workflow.id,
                           created_workflow_ids=(*settings.created_workflow_ids, workflow.id))
        try:
            for status, name in settings.status_names.items():
                if not looks_like_id(name):
                    setup.add_status(workflow.id, name.strip(), REQUEST_STATUSES[status][1])
        except TrackerError as e:
            raise PartialFixError(settings, e) from e
        return settings
    if fix == "add_statuses":
        if settings.workflow_id not in settings.created_workflow_ids:
            raise TrackerError("앱이 만든 전용 워크플로에만 상태를 추가합니다")
        statuses = next(s for w, s in setup.workflows() if w.id == settings.workflow_id)
        resolved = resolve_statuses(statuses, settings.status_names)
        for name, group in missing_statuses(resolved, settings.status_names):
            setup.add_status(settings.workflow_id, name, group)
        return settings
    raise ValueError(f"알 수 없는 해결 방법: {fix}")
