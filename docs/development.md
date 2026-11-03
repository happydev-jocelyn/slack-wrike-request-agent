# 개발 가이드

## 구조

계층을 셋으로 나눕니다. 순수 로직은 Slack·Wrike를 모르고 포트(Protocol)로만 다루므로 가짜 구현으로 테스트합니다.

| 파일 | 계층 | 책임 |
|---|---|---|
| `request_agent/flows.py` | 순수 로직 | 등록(`submit_request`)·확정/반려(`decide`) 흐름, 실패 시 되돌리기. `Tracker`(Wrike)·`Chat`(Slack) 포트 정의 |
| `request_agent/forms.py` | 순수 로직 | 요청 폼(모달) 구성, 채널 글에 등록 제안을 띄울지 판정 |
| `request_agent/wrike.py` | 경계 | Wrike API v4 클라이언트(`Tracker` 구현). 폼 파라미터, 배열은 JSON 문자열 |
| `request_agent/slack_app.py` | 배선 | Bolt 핸들러(버튼·단축키·명령·이벤트), `Chat` 구현, 시작 처리 |
| `app.py` | 진입점 | `python app.py` 또는 `slack run` |
| `tests/fake_wrike.py` | 테스트 도구 | 문서화된 Wrike 응답 형태를 흉내 내는 메모리 서버 |

앱은 일감 데이터를 저장하지 않습니다. 요청 메시지의 Slack `metadata`에 Wrike 티켓 ID를 넣어 두고, 이모지가 눌리면 그 메시지를 다시 읽어 티켓을 찾습니다. 판단은 항상 Wrike의 현재 상태를 기준으로 합니다.

명세(인수조건)와 진행 상황은 [이슈 #2](https://github.com/happydev-jocelyn/slack-wrike-request-agent/issues/2)에 있습니다. 동작을 바꿀 때는 이슈의 AC부터 고치고 테스트를 맞춥니다.

## 테스트

추가 설치 없이 표준 라이브러리로 돌아갑니다.

```bash
python3 -m unittest discover -s tests -t .
```

- `tests/test_flows.py`: 흐름 판정(권한·이미 처리됨·삭제됨·실패 시 되돌리기)
- `tests/test_forms.py`: 폼 미리 채우기, 등록 제안 판정
- `tests/test_wrike_client.py`: Wrike 요청 형태 계약 테스트

## 가짜 Wrike로 실행해 보기

실제 Wrike 없이 Slack 쪽 동작을 볼 수 있습니다.

```bash
python3 -m tests.fake_wrike 8765      # 터미널 1: 요청을 콘솔에 출력
```

다른 터미널에서 `.env`의 Wrike 값을 다음처럼 두고 앱을 실행합니다.

```
WRIKE_BASE_URL=http://127.0.0.1:8765/api/v4
WRIKE_TOKEN=fake-token
WRIKE_REQUEST_FOLDER_ID=REQFOLDER
WRIKE_WORK_FOLDER_ID=WORKFOLDER
WRIKE_STATUS_REVIEWING=IEAAAAAAJMAAAAA1
WRIKE_STATUS_CONFIRMED=IEAAAAAAJMAAAAA2
WRIKE_STATUS_REJECTED=IEAAAAAAJMAAAAA3
```

[Slack CLI](https://api.slack.com/automation/cli/install)가 있으면 `slack run`으로 개발용 앱을 만들어 설치까지 자동으로 하고, 파일을 고치면 자동으로 다시 시작합니다.

## 자주 하는 변경

- **카테고리**: `CATEGORIES` 환경변수만 바꾸면 됩니다.
- **폼 필드 추가**: `forms.request_form`에 블록 추가 → `slack_app.submit_form`에서 값 읽기 → `flows.Request`에 필드 추가 → `wrike.create_ticket`의 설명(또는 Wrike 사용자 정의 필드 `customFields`)에 반영 → 테스트.
- **승인/반려 이모지**: `APPROVE_EMOJI`, `REJECT_EMOJI`.
- **Slack 권한·이벤트 추가**: `manifest.json`을 고치고 Slack 앱 설정에 반영(재설치)합니다.

## 확장 가이드

현재는 워크스페이스마다 요청 채널 하나, 승인자 목록 하나, Wrike 폴더 한 쌍입니다.

### 채널 여러 개 (팀별 요청 창구)
채널마다 승인자·Wrike 폴더·워크플로를 다르게 두려면:
1. 설정을 채널 단위로 바꿉니다: `{채널 ID: {승인자, 요청 폴더, 전체 일감 폴더, 상태 ID}}`.
2. `slack_app`의 `request_channel_id` 단일 값을 이 맵으로 바꾸고, 이벤트·이모지 처리에서 `event["channel"]`로 설정을 고릅니다.
3. 채널별 `WrikeClient`를 만들거나, `WrikeConfig`를 호출마다 넘기도록 바꿉니다.
4. 단축키·`/request`처럼 채널이 정해지지 않은 입구는 폼에 "요청 채널" 선택 칸을 추가합니다.
M3의 홈 탭 설정 저장소가 생기면 그 구조를 채널 단위로 확장하는 것이 자연스럽습니다.

### Wrike에서 직접 바꾼 상태를 Slack에 반영
지금은 Slack ✅/❌만 처리하고, Wrike에서 직접 상태를 바꾸면 Slack 메시지는 그대로입니다. 반영하려면 Wrike 웹훅(`POST /webhooks`)을 받아야 하는데, 웹훅은 공개 HTTPS 주소가 필요해 Socket Mode만으로는 안 됩니다. 대안은 주기적으로 '요청' 폴더를 조회해 상태가 바뀐 티켓의 Slack 메시지에 답글을 다는 방식입니다.

### 등록 버튼을 늘 맨 아래에
요청이 처리될 때마다 이전 버튼 메시지를 지우고(`chat.delete`) 새로 게시하면 됩니다. 채널 알림이 늘어나므로 설정으로 켜고 끄게 하는 것을 권합니다.

### 멘션으로 자연어 등록
"@봇 로그인이 안 돼요, 담당 홍길동" 같은 글을 AI로 폼 항목에 나눠 넣는 방식입니다. M2의 AI 연동 위에 얹습니다.

## 코드 규칙

- Python 3.10+, 타입 힌트, 줄 길이 99자, 주석과 문서는 한국어.
- 시크릿은 코드·로그·커밋에 넣지 않습니다(`.env`는 git 제외).
- Wrike 실패는 `TrackerError`로 올리고, 사용자에게 보여 줄 수 있는 문장으로 만듭니다.
