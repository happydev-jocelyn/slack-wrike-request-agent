"""Wrike API v4 클라이언트. flows.Tracker 포트를 구현한다.

Wrike API는 요청 본문 대신 폼 파라미터를 받으며, 배열·객체 값은 JSON 문자열로 넣는다.
"""
import html
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass

from request_agent.flows import Request, Status, Ticket, TrackerError, WorkItem

# (method, url, form 파라미터, 헤더) -> (HTTP 상태, 응답 JSON)
Transport = Callable[[str, str, dict[str, str], dict[str, str]], tuple[int, dict]]


def urllib_transport(method: str, url: str, params: dict[str, str],
                     headers: dict[str, str]) -> tuple[int, dict]:
    query = urllib.parse.urlencode(params)
    data: bytes | None = query.encode()
    if method in ("GET", "DELETE"):
        data = None
        if query:
            url = f"{url}?{query}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, json.load(res)
    except urllib.error.HTTPError as e:
        with e:
            return e.code, json.loads(e.read() or b"{}")


@dataclass(frozen=True)
class WrikeConfig:
    base_url: str  # 예: https://www.wrike.com/api/v4
    token: str
    request_folder_id: str
    work_folder_id: str
    status_ids: dict[Status, str]  # 전용 요청 워크플로의 상태 ID
    ticket_type_id: str | None = None  # 사용자 정의 항목 유형(티켓). 없으면 일반 일감


class WrikeClient:
    def __init__(self, config: WrikeConfig, transport: Transport = urllib_transport,
                 email_of: Callable[[str], str | None] = lambda _: None) -> None:
        self.config = config
        self.transport = transport
        self.email_of = email_of  # Slack 사용자 ID -> 이메일 (담당자 매핑용)
        self._contacts: dict[str, str] | None = None  # 이메일 -> Wrike 사용자 ID
        self._status_by_id = {v: k for k, v in config.status_ids.items()}

    def _call(self, method: str, path: str, params: dict | None = None) -> list[dict]:
        form = {k: v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                for k, v in (params or {}).items()}
        headers = {"Authorization": f"Bearer {self.config.token}"}
        try:
            status, body = self.transport(method, self.config.base_url + path, form, headers)
        except (OSError, ValueError) as e:  # 연결 실패·타임아웃·JSON이 아닌 응답
            raise TrackerError(f"Wrike에 연결하지 못했습니다({e})") from e
        if status >= 400 or "error" in body:
            message = f"Wrike {body.get('error', status)}"
            if description := body.get("errorDescription"):
                message += f": {description}"
            raise TrackerError(message)
        return body.get("data", [])

    def _contact_id(self, slack_user: str) -> str | None:
        email = self.email_of(slack_user)
        if not email:
            return None
        if self._contacts is None:
            self._contacts = {}
            for contact in self._call("GET", "/contacts"):
                for profile in contact.get("profiles", []):
                    if profile.get("email"):
                        self._contacts[profile["email"].lower()] = contact["id"]
        return self._contacts.get(email.lower())

    def _to_ticket(self, task: dict) -> Ticket:
        return Ticket(id=task["id"], title=task["title"], permalink=task["permalink"],
                      status=self._status_by_id.get(task.get("customStatusId", "")))

    def create_ticket(self, req: Request) -> Ticket:
        lines = [f"카테고리: {req.category}", f"요청자: {self._person(req.requester)}",
                 f"담당자: {self._person(req.assignee)}", "", req.body]
        fields: dict = {
            "description": "<br>".join(html.escape(line) for line in lines),
            "customStatus": self.config.status_ids[Status.REVIEWING],
        }
        if assignee_id := self._contact_id(req.assignee):
            fields["responsibles"] = [assignee_id]
        title = f"[{req.category}] {req.title}"

        if self.config.ticket_type_id:
            # instantiate는 제목·위치만 받으므로 나머지는 생성 직후 수정한다
            path = f"/custom_item_types/{self.config.ticket_type_id}/instantiate"
            created = self._call(
                "POST", path, {"title": title, "parentId": self.config.request_folder_id})
            task_id = created[0]["id"]
            if "responsibles" in fields:
                fields["addResponsibles"] = fields.pop("responsibles")
            try:
                task = self._call("PUT", f"/tasks/{task_id}", fields)[0]
            except TrackerError as e:
                # 반쯤 만들어진 티켓을 남기지 않는다
                try:
                    self.delete_ticket(task_id)
                except TrackerError:
                    raise TrackerError(f"{e} (남은 티켓 {task_id}은 Wrike에서 직접 삭제해 주세요)") from e
                raise
        else:
            task = self._call("POST", f"/folders/{self.config.request_folder_id}/tasks",
                              {"title": title, **fields})[0]
        return self._to_ticket(task)

    def _person(self, slack_user: str) -> str:
        return self.email_of(slack_user) or f"Slack {slack_user}"

    def _get_task(self, task_id: str) -> dict | None:
        try:
            tasks = self._call("GET", f"/tasks/{task_id}")
        except TrackerError as e:
            if "resource_not_found" in str(e):
                return None
            raise
        # 휴지통으로 간 항목은 scope가 RbTask다
        return tasks[0] if tasks and tasks[0].get("scope") != "RbTask" else None

    def get_ticket(self, ticket_id: str) -> Ticket | None:
        task = self._get_task(ticket_id)
        return self._to_ticket(task) if task else None

    def set_status(self, ticket_id: str, status: Status) -> None:
        self._call("PUT", f"/tasks/{ticket_id}", {"customStatus": self.config.status_ids[status]})

    def create_work_item(self, ticket: Ticket) -> WorkItem:
        source = self._get_task(ticket.id) or {}
        params: dict = {
            "title": ticket.title,
            "description": (source.get("description", "")
                            + f"<br><br>요청 티켓: {html.escape(ticket.permalink)}"),
        }
        if source.get("responsibleIds"):
            params["responsibles"] = source["responsibleIds"]
        task = self._call("POST", f"/folders/{self.config.work_folder_id}/tasks", params)[0]
        return WorkItem(id=task["id"], permalink=task["permalink"])

    def add_slack_link(self, ticket_id: str, url: str) -> None:
        task = self._get_task(ticket_id) or {}
        link = f'Slack 요청: <a href="{html.escape(url)}">{html.escape(url)}</a>'
        description = task.get("description", "")
        self._call("PUT", f"/tasks/{ticket_id}", {"description": f"{description}<br>{link}"})

    def delete_ticket(self, ticket_id: str) -> None:
        self._call("DELETE", f"/tasks/{ticket_id}")

    def comment(self, ticket_id: str, text: str) -> None:
        self._call("POST", f"/tasks/{ticket_id}/comments", {"text": text, "plainText": "true"})
