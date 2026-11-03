# 설치와 실행

앱은 회사 PC 같은 아무 컴퓨터에서 실행하면 됩니다. Slack과는 Socket Mode로 연결하므로 공인 IP나 열어 둘 포트가 필요 없고, 밖으로 나가는 HTTPS 연결(Slack, Wrike)만 되면 됩니다. 컴퓨터가 꺼져 있는 동안에는 버튼과 ✅가 동작하지 않습니다.

> Docker로 실행하는 방법(`docker compose up -d`, 재부팅 후 자동 재시작)은 M3에서 추가됩니다. 지금은 아래 Python 실행 방법을 씁니다. 1단계의 Docker Desktop 설치는 미리 해 두어도 됩니다.

## 1. 준비물

- Python 3.10 이상 (`python3 --version`으로 확인)
- (M3부터) Docker Desktop
  - macOS·Windows: https://www.docker.com/products/docker-desktop/ 에서 설치하고, 설정에서 "Start Docker Desktop when you sign in"을 켭니다.
  - 회사 규모에 따라 Docker Desktop 유료 구독이 필요할 수 있으니 회사 정책을 확인하세요.
- Slack 워크스페이스에 앱을 설치할 권한(Enterprise는 조직 관리자 승인이 필요할 수 있습니다)
- Wrike 계정과 API 토큰을 발급할 권한

## 2. Slack 앱 만들기

1. https://api.slack.com/apps → **Create New App** → **From a manifest** → 워크스페이스 선택.
2. 이 저장소의 [manifest.json](../manifest.json) 내용을 그대로 붙여 넣고 만듭니다. (권한·이벤트·단축키·Socket Mode가 모두 들어 있습니다.)
3. **Basic Information → App-Level Tokens → Generate Token**: 이름은 아무거나, scope는 `connections:write`. 만들어진 `xapp-…` 토큰이 `SLACK_APP_TOKEN`입니다.
4. **Install App → Install to Workspace**. 설치 후 보이는 `xoxb-…` 토큰이 `SLACK_BOT_TOKEN`입니다.
5. 승인자의 Slack 사용자 ID 확인: Slack에서 그 사람 프로필 → ⋮ → **멤버 ID 복사** (`U`로 시작).
6. Enterprise 조직 단위로 설치했다면 요청 채널이 있는 워크스페이스 ID(`T`로 시작)를 `TEAM_ID`에 넣습니다.

토큰은 비밀번호와 같습니다. 채팅·문서·git에 올리지 마세요.

## 3. Wrike 준비

1. **토큰**: Wrike → 프로필 → **Apps & Integrations → API** → 앱 만들기 → **permanent access token** 발급. 이 토큰은 발급한 사람의 권한으로 동작합니다.
2. **폴더 두 개**: '요청'(티켓이 들어갈 곳)과 '전체 일감'(확정된 일감이 들어갈 곳). 폴더 링크의 `id=` 숫자는 API ID가 아니므로 아래처럼 바꿉니다.

   ```bash
   curl -s -H "Authorization: Bearer $WRIKE_TOKEN" \
     "https://www.wrike.com/api/v4/folders?permalink=https://www.wrike.com/open.htm?id=<숫자>"
   ```
   응답의 `data[0].id`가 폴더 ID입니다.
3. **전용 요청 워크플로**: 기존 업무 워크플로와 별개로 '검토중·확정·반려' 세 상태를 가진 워크플로를 Wrike에서 만들고, 요청 폴더가 그 워크플로를 쓸 수 있게 합니다. 상태 ID는 다음으로 확인합니다.

   ```bash
   curl -s -H "Authorization: Bearer $WRIKE_TOKEN" https://www.wrike.com/api/v4/workflows
   ```
   해당 워크플로의 `customStatuses[].id`를 각각 `WRIKE_STATUS_REVIEWING/CONFIRMED/REJECTED`에 넣습니다.
4. (선택) 티켓을 사용자 정의 항목 유형으로 쓰면 그 유형 ID를 `WRIKE_TICKET_TYPE_ID`에 넣습니다.
5. EU 등 지역 계정이면 `WRIKE_BASE_URL`을 `https://app-eu.wrike.com/api/v4`처럼 바꿉니다.

M3에서는 이 ID들을 Slack 홈 탭에서 이름으로 입력하면 앱이 찾아 줍니다.

## 3-1. AI 의견 (선택)

Slack은 앱에 LLM을 제공하지 않으므로, 쓰려는 LLM의 API 키를 넣습니다. 요청 1건당 기존 일감 최대 50건을 함께 보내므로 호출 비용이 조금씩 듭니다.

| 쓰려는 LLM | 설정 |
|---|---|
| Claude (기본) | `LLM_PROVIDER=anthropic`, `LLM_API_KEY`(https://console.anthropic.com 에서 발급). `LLM_MODEL`을 비우면 `claude-opus-5-5` |
| OpenAI | `LLM_PROVIDER=openai-compatible`, `LLM_BASE_URL=https://api.openai.com/v1`, `LLM_API_KEY`, `LLM_MODEL` |
| Google Gemini | `LLM_PROVIDER=openai-compatible`, `LLM_BASE_URL`에 Gemini의 OpenAI 호환 주소, `LLM_API_KEY`, `LLM_MODEL` |
| 사내 프록시·로컬 모델(Ollama 등) | `LLM_PROVIDER=openai-compatible`, 그 서버의 `…/v1` 주소, 필요하면 키, `LLM_MODEL` |

- 설정이 잘못되면(제공자 이름 오타, 필수 값 누락 등) 요청마다 스레드에 "AI 의견 생성 실패: AI 설정 오류 …"가 남습니다.
- `openai-compatible`은 Chat Completions API와 JSON 응답 모드(`response_format: json_object`)를 지원해야 합니다. 지원하지 않으면 스레드에 "AI 의견 생성 실패"가 남습니다.
- 요청·일감 내용이 그 LLM 제공자로 전송됩니다. 회사 보안 정책에서 허용되는지 먼저 확인하세요.
- 끄려면 `AI_OPINION=off`. 꺼도 등록·승인은 그대로 동작합니다.

## 4. 실행 (Python)

```bash
git clone https://github.com/happydev-jocelyn/slack-wrike-request-agent.git
cd slack-wrike-request-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 값을 채웁니다
set -a; source .env; set +a
.venv/bin/python app.py
```

`⚡️ Bolt app is running!`이 보이면 연결된 것입니다. 앱은 요청 채널에 참여하고, 채널에 "요청 등록" 버튼이 없으면 게시합니다. 그 메시지를 채널에 고정해 두세요.

Windows PowerShell에서는 `.venv\Scripts\pip`, `.venv\Scripts\python`을 쓰고, `.env` 대신 `$env:SLACK_BOT_TOKEN="xoxb-…"`처럼 환경변수를 넣습니다.

## 5. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 버튼을 눌러도 반응 없음 | 앱이 실행 중인지, Socket Mode가 켜져 있는지 |
| `채널 #… 을 찾지 못했습니다` | `REQUEST_CHANNEL`(# 빼고), Enterprise면 `TEAM_ID` |
| "요청을 Wrike에 등록하지 못했습니다: Wrike not_authorized" | `WRIKE_TOKEN`, `WRIKE_BASE_URL`(지역) |
| 담당자가 Wrike에 지정되지 않음 | Slack과 Wrike 계정 이메일이 같은지 (이메일로 매칭) |
| ✅를 눌렀는데 "권한이 없습니다" | `APPROVER_IDS`에 그 사람의 멤버 ID가 있는지 |
| 스레드에 "AI 의견 생성 실패" | `LLM_API_KEY`, `LLM_MODEL`, 회사 네트워크에서 LLM 주소 접속 가능 여부 |
| AI 의견이 아예 안 달림 | 시작 로그의 "AI 의견을 끕니다" 경고(`AI_OPINION`, `LLM_PROVIDER` 설정, Claude면 `anthropic` 설치 여부) |
