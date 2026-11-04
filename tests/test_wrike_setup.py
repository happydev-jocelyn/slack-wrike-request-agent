"""설정 화면용 Wrike 조회·생성 계약 테스트 (AC12). 요청 형태는 Wrike API v4 문서 기준."""
import json
import unittest

from request_agent.flows import TrackerError
from request_agent.resolve import Named
from request_agent.wrike_setup import WrikeSetup
from tests.fake_wrike import TOKEN, FakeWrike


def make(token: str = TOKEN) -> tuple[WrikeSetup, FakeWrike]:
    wrike = FakeWrike()
    return WrikeSetup("https://www.wrike.com/api/v4", token, wrike.transport), wrike


class WrikeSetupTest(unittest.TestCase):
    def test_check(self) -> None:
        setup, wrike = make()
        self.assertEqual(setup.check(), "담당 자")
        self.assertEqual(wrike.requests[-1], ("GET", "/contacts", {"me": "true"}))
        with self.assertRaisesRegex(TrackerError, "not_authorized"):
            make(token="wrong")[0].check()

    def test_lists(self) -> None:
        setup, wrike = make()
        self.assertEqual(setup.projects(), [Named("IEAAAAAAI4PROJ01", "운영")])
        self.assertEqual(wrike.requests[-1], ("GET", "/folders", {"project": "true"}))
        self.assertEqual({f.name for f in setup.folders("IEAAAAAAI4PROJ01")},
                         {"요청", "전체 일감"})  # 부모 자신은 빠진다
        workflow, statuses = setup.workflows()[0]
        self.assertEqual(workflow.name, "Default Workflow")
        self.assertEqual(statuses, [Named("IEAAAAAAJMDEF001", "Active")])
        self.assertEqual(setup.item_types(), [Named("IEAAAAAAPITEM001", "티켓")])

    def test_create(self) -> None:
        setup, wrike = make()
        project = setup.create_project("새 프로젝트")
        method, path, form = wrike.requests[-1]
        self.assertEqual((method, path), ("POST", "/folders/IEAAAAAAI4ROOT00/folders"))
        self.assertEqual(json.loads(form["project"]), {})
        folder = setup.create_folder(project.id, "요청")
        self.assertEqual(wrike.requests[-1][1], f"/folders/{project.id}/folders")
        self.assertEqual(folder.name, "요청")

        workflow = setup.create_workflow("요청 처리")
        self.assertEqual(wrike.requests[-1], ("POST", "/workflows", {"name": "요청 처리"}))
        setup.add_status(workflow.id, "검토중", "Active")
        method, path, form = wrike.requests[-1]
        self.assertEqual((method, path), ("PUT", f"/workflows/{workflow.id}"))
        self.assertEqual(json.loads(form["customStatus"]),
                         {"name": "검토중", "group": "Active", "hidden": False})
        self.assertNotIn("id", json.loads(form["customStatus"]))  # id가 없어야 추가로 처리된다


if __name__ == "__main__":
    unittest.main()
