"""설정 화면용 Wrike 조회·생성: 연결 확인, 프로젝트·폴더·워크플로·티켓 유형."""
from request_agent.flows import TrackerError
from request_agent.resolve import Named
from request_agent.wrike import Transport, urllib_transport, wrike_call


class WrikeSetup:
    def __init__(self, base_url: str, token: str,
                 transport: Transport = urllib_transport) -> None:
        self.base_url, self.token, self.transport = base_url, token, transport

    def _call(self, method: str, path: str, params: dict | None = None) -> list[dict]:
        return wrike_call(self.transport, self.base_url, self.token, method, path, params)

    def check(self) -> str:
        """토큰이 유효한지 확인하고 토큰 주인의 이름을 돌려준다."""
        me = self._call("GET", "/contacts", {"me": "true"})
        if not me:
            raise TrackerError("Wrike 토큰 사용자 정보를 받지 못했습니다")
        return f"{me[0].get('firstName', '')} {me[0].get('lastName', '')}".strip()

    def projects(self) -> list[Named]:
        projects = self._call("GET", "/folders", {"project": "true"})
        return [Named(f["id"], f["title"]) for f in projects]

    def folders(self, parent_id: str) -> list[Named]:
        """parent_id 아래의 폴더(자기 자신 제외)."""
        return [Named(f["id"], f["title"])
                for f in self._call("GET", f"/folders/{parent_id}/folders")
                if f["id"] != parent_id]

    def workflows(self) -> list[tuple[Named, list[Named]]]:
        return [(Named(w["id"], w["name"]),
                 [Named(s["id"], s["name"]) for s in w.get("customStatuses", [])
                  if not s.get("hidden")])
                for w in self._call("GET", "/workflows")]

    def item_types(self) -> list[Named]:
        return [Named(t["id"], t["title"]) for t in self._call("GET", "/custom_item_types")]

    def create_project(self, title: str) -> Named:
        """계정 루트에 프로젝트를 만든다."""
        root = next((f["id"] for f in self._call("GET", "/folders")
                     if f.get("scope") == "WsRoot"), None)
        if root is None:
            raise TrackerError("Wrike 루트 폴더를 찾지 못했습니다")
        created = self._call("POST", f"/folders/{root}/folders", {"title": title, "project": {}})
        return Named(created[0]["id"], created[0]["title"])

    def create_folder(self, parent_id: str, title: str) -> Named:
        created = self._call("POST", f"/folders/{parent_id}/folders", {"title": title})
        return Named(created[0]["id"], created[0]["title"])

    def create_workflow(self, name: str) -> Named:
        created = self._call("POST", "/workflows", {"name": name})
        return Named(created[0]["id"], created[0]["name"])

    def add_status(self, workflow_id: str, name: str, group: str) -> None:
        """새 상태를 추가한다(Wrike는 group만 주고 id를 빼면 추가로 처리한다)."""
        self._call("PUT", f"/workflows/{workflow_id}",
                   {"customStatus": {"name": name, "group": group, "hidden": False}})
