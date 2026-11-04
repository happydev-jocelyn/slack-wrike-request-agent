"""문서화된 Wrike API v4 응답 형태를 흉내 내는 메모리 서버. 테스트와 로컬 실행 관찰에 쓴다.

실행: python -m tests.fake_wrike [포트]  → http://127.0.0.1:<포트>/api/v4
"""
import itertools
import json
import re
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TOKEN = "fake-token"


class FakeWrike:
    def __init__(self) -> None:
        self.tasks: dict[str, dict] = {}
        self.comments: list[dict] = []
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.contacts = [{"id": "KUAAAAA1", "firstName": "담당", "lastName": "자",
                          "profiles": [{"email": "assignee@example.com"}]}]
        self._ids = (f"IEAAAAAAKQ{n:06d}" for n in itertools.count(1))
        self._setup_ids = (f"IEAAAAAAI4{n:06d}" for n in itertools.count(1))
        # 설정 화면용: 루트 아래 프로젝트 하나, 그 아래 폴더 하나, 기본 워크플로 하나
        self.folders: dict[str, dict] = {
            "IEAAAAAAI4ROOT00": {"id": "IEAAAAAAI4ROOT00", "title": "Root", "scope": "WsRoot",
                                 "parent": None},
            "REQFOLDER": {"id": "REQFOLDER", "title": "요청", "scope": "WsFolder",
                          "parent": "IEAAAAAAI4PROJ01"},
            "WORKFOLDER": {"id": "WORKFOLDER", "title": "전체 일감", "scope": "WsFolder",
                           "parent": "IEAAAAAAI4PROJ01"},
            "IEAAAAAAI4PROJ01": {"id": "IEAAAAAAI4PROJ01", "title": "운영", "scope": "WsFolder",
                                 "parent": "IEAAAAAAI4ROOT00", "project": {}},
        }
        self.workflows: list[dict] = [{"id": "IEAAAAAAK4DEF001", "name": "Default Workflow",
                                       "customStatuses": [
                                           {"id": "IEAAAAAAJMDEF001", "name": "Active",
                                            "group": "Active", "hidden": False}]}]
        self.item_types = [{"id": "IEAAAAAAPITEM001", "title": "티켓"}]

    def _new_task(self, title: str, parent: str, form: dict[str, str]) -> dict:
        task_id = next(self._ids)
        created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        task = {"id": task_id, "title": title, "description": "", "scope": "WsTask",
                "parentIds": [parent], "responsibleIds": [], "customStatusId": "",
                "status": "Active", "createdDate": created,
                "permalink": f"https://www.wrike.com/open.htm?id={task_id[-6:]}"}
        self.tasks[task_id] = task
        self._update(task, form)
        return task

    def _list(self, folder_id: str, form: dict[str, str]) -> list[dict]:
        tasks = [t for t in self.tasks.values()
                 if folder_id in t["parentIds"] and t["scope"] != "RbTask"]
        if "status" in form:
            tasks = [t for t in tasks if t["status"] in json.loads(form["status"])]
        if "createdDate" in form:
            start = json.loads(form["createdDate"])["start"]
            tasks = [t for t in tasks if t["createdDate"] >= start]
        return [{**t, "briefDescription": t["description"][:120]} for t in tasks]

    @staticmethod
    def _update(task: dict, form: dict[str, str]) -> None:
        if "description" in form:
            task["description"] = form["description"]
        if "customStatus" in form:
            task["customStatusId"] = form["customStatus"]
        for key in ("responsibles", "addResponsibles"):
            if key in form:
                added = set(json.loads(form[key]))
                task["responsibleIds"] = sorted(set(task["responsibleIds"]) | added)

    def handle(self, method: str, path: str, form: dict[str, str],
               headers: dict[str, str]) -> tuple[int, dict]:
        self.requests.append((method, path, form))
        if headers.get("Authorization") != f"Bearer {TOKEN}":
            return 401, {"error": "not_authorized", "errorDescription": "Access token is invalid"}

        if method == "GET" and path == "/contacts":
            return 200, {"kind": "contacts",
                         "data": self.contacts[:1] if form.get("me") else self.contacts}
        if result := self._handle_setup(method, path, form):
            return result
        if m := re.fullmatch(r"/folders/(\w+)/tasks", path):
            if method == "POST":
                return 200, {"kind": "tasks", "data": [self._new_task(form["title"], m[1], form)]}
            return 200, {"kind": "tasks", "data": self._list(m[1], form)}
        if re.fullmatch(r"/custom_item_types/\w+/instantiate", path):
            task = self._new_task(form["title"], form["parentId"], {})
            return 200, {"kind": "workItems", "data": [{"relatedType": "Task", "id": task["id"]}]}
        if m := re.fullmatch(r"/tasks/(\w+)/comments", path):
            self.comments.append({"taskId": m[1], "text": form["text"]})
            return 200, {"kind": "comments", "data": [self.comments[-1]]}
        if m := re.fullmatch(r"/tasks/(\w+)", path):
            task = self.tasks.get(m[1])
            if task is None:
                return 404, {"error": "resource_not_found", "errorDescription": "Task not found"}
            if method == "PUT":
                self._update(task, form)
            elif method == "DELETE":
                task["scope"] = "RbTask"  # Wrike는 삭제한 항목을 휴지통으로 옮긴다
            return 200, {"kind": "tasks", "data": [task]}
        return 400, {"error": "invalid_request", "errorDescription": f"{method} {path}"}

    def _handle_setup(self, method: str, path: str,
                      form: dict[str, str]) -> tuple[int, dict] | None:
        """설정 화면이 쓰는 폴더·워크플로·티켓 유형 API."""
        if path == "/folders" and method == "GET":
            folders = list(self.folders.values())
            if form.get("project") == "true":
                folders = [f for f in folders if "project" in f]
            return 200, {"kind": "folders", "data": folders}
        if m := re.fullmatch(r"/folders/(\w+)/folders", path):
            if method == "POST":
                folder = {"id": next(self._setup_ids), "title": form["title"],
                          "scope": "WsFolder", "parent": m[1]}
                if "project" in form:
                    folder["project"] = json.loads(form["project"])
                self.folders[folder["id"]] = folder
                return 200, {"kind": "folders", "data": [folder]}
            children = [f for f in self.folders.values() if f["parent"] == m[1]]
            return 200, {"kind": "folders", "data": [self.folders[m[1]], *children]}
        if path == "/workflows":
            if method == "POST":
                workflow = {"id": next(self._setup_ids), "name": form["name"],
                            "customStatuses": []}
                self.workflows.append(workflow)
                return 200, {"kind": "workflows", "data": [workflow]}
            return 200, {"kind": "workflows", "data": self.workflows}
        if (m := re.fullmatch(r"/workflows/(\w+)", path)) and method == "PUT":
            workflow = next(w for w in self.workflows if w["id"] == m[1])
            status = json.loads(form["customStatus"])
            workflow["customStatuses"].append({**status, "id": next(self._setup_ids)})
            return 200, {"kind": "workflows", "data": [workflow]}
        if path == "/custom_item_types":
            return 200, {"kind": "customItemTypes", "data": self.item_types}
        return None

    def transport(self, method: str, url: str, form: dict[str, str],
                  headers: dict[str, str]) -> tuple[int, dict]:
        path = urllib.parse.urlparse(url).path.removeprefix("/api/v4")
        return self.handle(method, path, form, headers)


def serve(port: int) -> None:
    wrike = FakeWrike()

    class Handler(BaseHTTPRequestHandler):
        def _dispatch(self) -> None:
            url = urllib.parse.urlparse(self.path)
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            form = dict(urllib.parse.parse_qsl(raw or url.query))
            status, body = wrike.handle(self.command, url.path.removeprefix("/api/v4"),
                                        form, dict(self.headers))
            print(self.command, url.path, form, "->", status, flush=True)
            payload = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)

        do_GET = do_POST = do_PUT = do_DELETE = _dispatch

        def log_message(self, format: str, *args: object) -> None:
            pass

    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()


if __name__ == "__main__":
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
