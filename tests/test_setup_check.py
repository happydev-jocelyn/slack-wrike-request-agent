"""설정 저장 시 연결 확인 테스트 (AC12): 이름·ID 찾기, 만들기, 여러 개면 고르기."""
import unittest
from dataclasses import replace

from request_agent.flows import Status
from request_agent.settings import Settings
from request_agent.setup_check import apply_fix, check_settings
from request_agent.wrike_setup import WrikeSetup
from tests.fake_wrike import TOKEN, FakeWrike


def make() -> tuple[WrikeSetup, FakeWrike]:
    wrike = FakeWrike()
    return WrikeSetup("https://www.wrike.com/api/v4", TOKEN, wrike.transport), wrike


BASE = replace(Settings(), wrike_token=TOKEN, project="운영")


class CheckSettingsTest(unittest.TestCase):
    def test_bad_token_stops_early(self) -> None:
        setup, _ = make()
        setup.token = "wrong"
        report = check_settings(BASE, setup)
        self.assertFalse(report.ok)
        self.assertEqual([p.field for p in report.problems], ["wrike_token"])

    def test_resolves_names_and_reports_missing_workflow(self) -> None:
        setup, _ = make()
        report = check_settings(BASE, setup)
        self.assertEqual(report.account, "담당 자")
        self.assertEqual(report.settings.project_id, "IEAAAAAAI4PROJ01")
        self.assertEqual(report.settings.request_folder_id, "REQFOLDER")
        self.assertEqual(report.settings.work_folder_id, "WORKFOLDER")
        self.assertFalse(report.ok)
        [problem] = report.problems
        self.assertEqual((problem.field, problem.fix), ("workflow", "create_workflow"))

    def test_create_workflow_then_ok(self) -> None:
        setup, wrike = make()
        report = check_settings(BASE, setup)
        report = apply_fix(report.settings, setup, "create_workflow")
        # 새 전용 워크플로에 세 상태가 추가되고 상태 ID가 채워진다
        self.assertTrue(report.ok, report.problems)
        workflow = wrike.workflows[-1]
        self.assertEqual(workflow["name"], "요청 처리")
        self.assertEqual([(s["name"], s["group"]) for s in workflow["customStatuses"]],
                         [("검토중", "Active"), ("확정", "Completed"), ("반려", "Cancelled")])
        self.assertEqual(set(report.settings.status_ids), set(Status))
        self.assertTrue(report.settings.is_complete())
        # 기존 업무 워크플로는 건드리지 않는다
        self.assertEqual(len(wrike.workflows[0]["customStatuses"]), 1)

    def test_missing_statuses_added_only_to_app_created_workflow(self) -> None:
        setup, wrike = make()
        wrike.workflows.append({"id": "IEAAAAAAK4REQ001", "name": "요청 처리", "customStatuses": [
            {"id": "IEAAAAAAJMREQ001", "name": "검토중", "group": "Active", "hidden": False}]})
        # 앱이 만들지 않은 워크플로(업무용일 수 있음)에는 상태 추가 버튼을 주지 않는다
        [problem] = check_settings(BASE, setup).problems
        self.assertIsNone(problem.fix)
        report = apply_fix(check_settings(BASE, setup).settings, setup, "add_statuses")
        self.assertIn("앱이 만든", report.problems[0].message)  # 직접 요청해도 바꾸지 않는다
        self.assertEqual(len(wrike.workflows[-1]["customStatuses"]), 1)

        owned = replace(BASE, created_workflow_ids=("IEAAAAAAK4REQ001",))
        [problem] = check_settings(owned, setup).problems
        self.assertEqual(problem.fix, "add_statuses")
        report = apply_fix(check_settings(owned, setup).settings, setup, "add_statuses")
        self.assertTrue(report.ok, report.problems)
        self.assertEqual(report.settings.status_ids[Status.REVIEWING], "IEAAAAAAJMREQ001")

    def test_create_workflow_records_ownership(self) -> None:
        setup, _ = make()
        report = apply_fix(check_settings(BASE, setup).settings, setup, "create_workflow")
        self.assertEqual(report.settings.created_workflow_ids, (report.settings.workflow_id,))

    def test_partial_workflow_creation_keeps_ownership(self) -> None:
        setup, wrike = make()
        original = wrike.handle
        calls = {"put": 0}

        def fail_second_status(method: str, path: str, form: dict[str, str],
                               headers: dict[str, str]) -> tuple[int, dict]:
            if method == "PUT" and path.startswith("/workflows/"):
                calls["put"] += 1
                if calls["put"] == 2:
                    return 500, {"error": "server_error"}
            return original(method, path, form, headers)

        wrike.handle = fail_second_status  # type: ignore[method-assign]
        report = apply_fix(check_settings(BASE, setup).settings, setup, "create_workflow")
        created = wrike.workflows[-1]["id"]
        self.assertIn(created, report.settings.created_workflow_ids)
        self.assertIn("상태 일부", report.problems[0].message)
        self.assertIn("add_statuses", [p.fix for p in report.problems])  # 이어서 추가할 수 있다

        wrike.handle = original  # type: ignore[method-assign]
        report = apply_fix(report.settings, setup, "add_statuses")
        self.assertTrue(report.ok, report.problems)
        self.assertEqual(len(wrike.workflows), 2)  # 같은 이름 워크플로를 또 만들지 않았다

    def test_duplicate_status_names_need_choice(self) -> None:
        setup, wrike = make()
        wrike.workflows.append({"id": "IEAAAAAAK4REQ002", "name": "요청 처리", "customStatuses": [
            {"id": f"IEAAAAAAJMDUP00{i}", "name": name, "group": "Active", "hidden": False}
            for i, name in enumerate(["검토중", "확정", "반려", "반려"])]})
        [problem] = check_settings(BASE, setup).problems
        self.assertEqual(problem.field, "status:REJECTED")
        self.assertEqual(len(problem.candidates), 2)

    def test_found_lists_names_and_ids(self) -> None:
        setup, _ = make()
        found = dict(check_settings(BASE, setup).found)
        self.assertEqual(found["프로젝트"].id, "IEAAAAAAI4PROJ01")
        self.assertEqual(found["요청 폴더"].name, "요청")

    def test_missing_folder_and_project_offer_create(self) -> None:
        setup, wrike = make()
        report = check_settings(replace(BASE, request_folder="새 요청함"), setup)
        self.assertIn(("request_folder", "create_request_folder"),
                      [(p.field, p.fix) for p in report.problems])
        report = apply_fix(report.settings, setup, "create_request_folder")
        self.assertNotIn("request_folder", [p.field for p in report.problems])
        self.assertEqual(wrike.folders[report.settings.request_folder_id]["title"], "새 요청함")

        report = check_settings(replace(BASE, project="없는 프로젝트"), setup)
        self.assertEqual(report.problems[0].fix, "create_project")

    def test_duplicate_names_need_choice(self) -> None:
        setup, wrike = make()
        wrike.folders["IEAAAAAAI4DUP001"] = {"id": "IEAAAAAAI4DUP001", "title": "요청",
                                             "scope": "WsFolder", "parent": "IEAAAAAAI4PROJ01"}
        report = check_settings(BASE, setup)
        problem = next(p for p in report.problems if p.field == "request_folder")
        self.assertIsNone(problem.fix)
        self.assertEqual(len(problem.candidates), 2)
        # 후보를 고르면 그 ID로 다시 확인한다
        chosen = replace(report.settings, request_folder="IEAAAAAAI4DUP001")
        report = check_settings(chosen, setup)
        self.assertEqual(report.settings.request_folder_id, "IEAAAAAAI4DUP001")

    def test_ticket_type_optional(self) -> None:
        setup, _ = make()
        self.assertEqual(check_settings(BASE, setup).settings.ticket_type_id, "")
        report = check_settings(replace(BASE, ticket_type="티켓"), setup)
        self.assertEqual(report.settings.ticket_type_id, "IEAAAAAAPITEM001")
        report = check_settings(replace(BASE, ticket_type="없는 유형"), setup)
        self.assertIn("ticket_type", [p.field for p in report.problems])

    def test_permission_error_on_create_is_reported(self) -> None:
        setup, wrike = make()
        original = wrike.handle

        def forbid_workflow(method: str, path: str, form: dict[str, str],
                            headers: dict[str, str]) -> tuple[int, dict]:
            if method == "POST" and path == "/workflows":
                return 403, {"error": "access_forbidden", "errorDescription": "No permission"}
            return original(method, path, form, headers)

        wrike.handle = forbid_workflow  # type: ignore[method-assign]
        report = apply_fix(check_settings(BASE, setup).settings, setup, "create_workflow")
        problem = next(p for p in report.problems if p.field == "workflow")
        self.assertIn("access_forbidden", problem.message)


if __name__ == "__main__":
    unittest.main()
