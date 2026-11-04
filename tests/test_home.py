"""홈 탭 화면 테스트 (AC11, AC12, AC13)."""
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from request_agent.flows import Status
from request_agent.home import (
    FIX_LABELS, REGIONS, AppStatus, home_view, parse_settings_form, settings_form)
from request_agent.resolve import Named
from request_agent.settings import Settings
from request_agent.setup_check import FIX_FIELDS, Problem, SetupReport

NOW = datetime(2026, 11, 4, 10, 42, tzinfo=timezone.utc)
SECRET = replace(Settings(), wrike_token="wrike-secret-token", llm_api_key="llm-secret-key",
                 project="운영", request_channel_id="C1", approver_ids=("U1",))
STATUS = AppStatus(checked_at=NOW, wrike_ok=True, message="정상")


def dump(view: dict) -> str:
    return json.dumps(view, ensure_ascii=False)


def form_values(**overrides: object) -> dict:
    """settings_form 제출 값(state.values) 형태를 만든다."""
    text = {"wrike_token": "", "project": "운영", "request_folder": "요청",
            "work_folder": "전체 일감", "ticket_type": "", "workflow": "요청 처리",
            "status_REVIEWING": "검토중", "status_CONFIRMED": "확정", "status_REJECTED": "반려",
            "categories": "bug:버그, etc:기타", "llm_base_url": "", "llm_api_key": "",
            "llm_model": ""}
    text.update({k: str(v) for k, v in overrides.items() if k in text})
    values: dict = {k: {"v": {"type": "plain_text_input", "value": v or None}}
                    for k, v in text.items()}
    values["wrike_region"] = {"v": {"selected_option": {"value": overrides.get("region", "www")}}}
    values["request_channel"] = {"v": {"selected_conversation": "C9"}}
    values["approvers"] = {"v": {"selected_users": ["U1", "U2"]}}
    values["ai_opinion"] = {"v": {"selected_option": {"value": overrides.get("ai", "on")}}}
    values["llm_provider"] = {"v": {"selected_option": {"value": "anthropic"}}}
    return values


class HomeViewTest(unittest.TestCase):
    def test_status_line(self) -> None:
        text = dump(home_view(False, SECRET, None, STATUS))
        self.assertIn("실행 중", text)
        self.assertIn("마지막 확인", text)
        self.assertIn("Wrike 연결: 정상", text)

    def test_admin_only(self) -> None:
        self.assertNotIn("open_settings", dump(home_view(False, SECRET, None, STATUS)))
        self.assertIn("open_settings", dump(home_view(True, SECRET, None, STATUS)))

    def test_token_not_rendered(self) -> None:
        for view in (home_view(True, SECRET, None, STATUS), settings_form(SECRET)):
            text = dump(view)
            self.assertNotIn("wrike-secret-token", text)
            self.assertNotIn("llm-secret-key", text)
        self.assertIn("설정됨", dump(home_view(True, SECRET, None, STATUS)))

    def test_problems_show_fix_and_choices(self) -> None:
        report = SetupReport(SECRET, (
            Problem("workflow", "요청 워크플로 '요청 처리'을(를) 찾지 못했습니다.",
                    fix="create_workflow"),
            Problem("request_folder", "여러 개",
                    candidates=(Named("IEAAAAAAI4AAAAA1", "요청"),
                                Named("IEAAAAAAI4AAAAA2", "요청")))),
                              account="담당 자")
        text = dump(home_view(True, SECRET, report, STATUS))
        self.assertIn('"value": "create_workflow"', text)
        self.assertIn("IEAAAAAAI4AAAAA2", text)
        self.assertIn("기존 업무 워크플로", text)  # 워크플로 만들기 버튼 옆 안내


class ReportTest(unittest.TestCase):
    def test_found_names_and_ids_shown(self) -> None:
        report = SetupReport(SECRET, (), account="담당 자",
                             found=(("프로젝트", Named("IEAAAAAAI4PROJ01", "운영")),))
        text = dump(home_view(True, SECRET, report, STATUS))
        self.assertIn("운영 (`IEAAAAAAI4PROJ01`)", text)
        self.assertIn("저장했습니다", text)

    def test_unsaved_warning_and_status_choice(self) -> None:
        report = SetupReport(SECRET, (Problem("status:REJECTED", "여러 개", candidates=(
            Named("IEAAAAAAJMAAAAA1", "반려"), Named("IEAAAAAAJMAAAAA2", "반려"))),))
        text = dump(home_view(True, SECRET, report, STATUS))
        self.assertIn("저장되지 않고", text)
        self.assertIn("choose:status:REJECTED", text)


class FixLabelTest(unittest.TestCase):
    def test_every_fix_has_button_label(self) -> None:
        self.assertEqual(set(FIX_LABELS), set(FIX_FIELDS))


class SettingsFormTest(unittest.TestCase):
    def test_form_has_all_fields(self) -> None:
        block_ids = {b.get("block_id") for b in settings_form(SECRET)["blocks"]}
        for field in ("wrike_token", "wrike_region", "project", "request_folder",
                      "work_folder", "ticket_type", "workflow", "status_REVIEWING",
                      "request_channel", "approvers", "categories", "ai_opinion",
                      "llm_provider", "llm_base_url", "llm_api_key", "llm_model"):
            self.assertIn(field, block_ids)

    def test_parse_keeps_secrets_when_blank(self) -> None:
        parsed = parse_settings_form(form_values(), SECRET)
        self.assertEqual(parsed.wrike_token, "wrike-secret-token")
        self.assertEqual(parsed.llm_api_key, "llm-secret-key")
        self.assertEqual(parsed.request_channel_id, "C9")
        self.assertEqual(parsed.approver_ids, ("U1", "U2"))
        self.assertEqual(parsed.categories, {"bug": "버그", "etc": "기타"})
        self.assertEqual(parsed.status_names[Status.REJECTED], "반려")

    def test_parse_replaces_secret_and_region(self) -> None:
        parsed = parse_settings_form(form_values(wrike_token=" new-token ", region="app-eu",
                                                 ai="off"), SECRET)
        self.assertEqual(parsed.wrike_token, "new-token")
        self.assertEqual(parsed.wrike_base_url, REGIONS["app-eu"])
        self.assertFalse(parsed.ai_opinion)

    def test_custom_base_url_is_kept(self) -> None:
        custom = replace(SECRET, wrike_base_url="http://127.0.0.1:8765/api/v4")
        self.assertIn("사용자 지정", dump(settings_form(custom)))
        parsed = parse_settings_form(form_values(region="custom"), custom)
        self.assertEqual(parsed.wrike_base_url, "http://127.0.0.1:8765/api/v4")

    def test_parse_resets_resolved_ids(self) -> None:
        resolved = replace(SECRET, request_folder_id="X", status_ids={Status.REVIEWING: "S"})
        parsed = parse_settings_form(form_values(), resolved)
        self.assertEqual(parsed.request_folder_id, "")  # 저장할 때마다 다시 확인한다
        self.assertEqual(parsed.status_ids, {})


if __name__ == "__main__":
    unittest.main()
