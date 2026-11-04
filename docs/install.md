# 설치와 실행

앱은 회사 PC 같은 아무 컴퓨터에서 실행하면 됩니다. Slack과는 Socket Mode로 연결하므로 공인 IP나 열어 둘 포트가 필요 없고, 밖으로 나가는 HTTPS 연결(Slack, Wrike, LLM)만 되면 됩니다. 컴퓨터가 꺼져 있는 동안에는 버튼과 ✅가 동작하지 않습니다.

서버(PC)에 넣는 값은 Slack 토큰 두 개, 관리자 ID, 암호화 키뿐입니다. Wrike·채널·승인자·AI 설정은 실행 후 Slack 앱 홈 탭에서 관리자가 입력합니다.

## 1. 준비물

- **Docker Desktop** (권장)
  - macOS·Windows: https://www.docker.com/products/docker-desktop/ 에서 설치합니다.
  - 설정(Settings → General)에서 **Start Docker Desktop when you sign in**을 켭니다. 그래야 PC를 다시 켰을 때 앱도 자동으로 다시 뜹니다.
  - 회사 규모에 따라 Docker Desktop 유료 구독이 필요할 수 있으니 회사 정책을 확인하세요.
  - Docker를 쓸 수 없으면 Python 3.10 이상으로 직접 실행할 수 있습니다(5-2).
- Slack 워크스페이스에 앱을 설치할 권한(Enterprise는 조직 관리자 승인이 필요할 수 있습니다)
- Wrike 계정과 API 토큰을 발급할 권한(전용 워크플로를 앱이 만들게 하려면 Wrike 워크플로 관리 권한)

## 2. Slack 앱 만들기

1. https://api.slack.com/apps → **Create New App** → **From a manifest** → 워크스페이스 선택.
2. 이 저장소의 [manifest.json](../manifest.json) 내용을 그대로 붙여 넣고 만듭니다. (권한·이벤트·단축키·홈 탭·Socket Mode가 모두 들어 있습니다.)
3. **Basic Information → App-Level Tokens → Generate Token**: 이름은 아무거나, scope는 `connections:write`. 만들어진 `xapp-…` 토큰이 `SLACK_APP_TOKEN`입니다.
4. **Install App → Install to Workspace**. 설치 후 보이는 `xoxb-…` 토큰이 `SLACK_BOT_TOKEN`입니다.
5. 관리자의 Slack 사용자 ID 확인: Slack에서 프로필 → ⋮ → **멤버 ID 복사** (`U`로 시작). `ADMIN_IDS`에 넣습니다.

토큰은 비밀번호와 같습니다. 채팅·문서·git에 올리지 마세요.

## 3. .env 만들기

```bash
git clone https://github.com/happydev-jocelyn/slack-wrike-request-agent.git
cd slack-wrike-request-agent
cp .env.example .env
```

`.env`에 `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `ADMIN_IDS`를 넣고, 암호화 키를 만들어 `SETTINGS_KEY`에 넣습니다.

```bash
docker compose build
docker compose run --rm agent python -m request_agent.settings genkey
```

출력된 한 줄을 `SETTINGS_KEY=` 뒤에 붙입니다. 이 키를 잃어버리면 저장된 설정을 읽을 수 없어 홈 탭에서 다시 입력해야 합니다.

## 4. 실행

### 4-1. Docker (권장)

```bash
docker compose up -d
```

- Docker Desktop 앱의 **Containers**에서 `agent`가 초록색(Running)이면 실행 중입니다.
- 로그 보기: `docker compose logs -f`
- 멈추기: `docker compose down` (설정은 `agent-data` 볼륨에 남습니다)
- 코드 업데이트: `git pull && docker compose up -d --build`

`restart: unless-stopped`라서 PC를 다시 켜고 Docker Desktop이 뜨면 앱도 자동으로 다시 실행됩니다.

### 4-2. Python 직접 실행 (Docker를 쓸 수 없을 때)

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
set -a; source .env; set +a
.venv/bin/python app.py
```

Windows PowerShell에서는 `.venv\Scripts\pip`, `.venv\Scripts\python`을 쓰고, `.env` 대신 `$env:SLACK_BOT_TOKEN="xoxb-…"`처럼 환경변수를 넣습니다. 이 방식은 PC를 다시 켰을 때 자동으로 실행되지 않습니다.


## 5. 홈 탭에서 설정하기 (관리자)

Slack에서 앱을 열고 **홈** 탭 → **설정 편집**을 누릅니다. 관리자(`ADMIN_IDS`)가 아니면 설정이 보이지 않습니다.

| 항목 | 넣는 값 |
|---|---|
| Wrike API 토큰 | Wrike → 프로필 → **Apps & Integrations → API** → 앱 만들기 → **permanent access token**. 이 토큰은 발급한 사람의 권한으로 동작합니다. 저장 후에는 다시 표시되지 않고, 비워 두면 기존 값을 유지합니다. |
| Wrike 지역 | 기본(www), EU, US2 |
| 프로젝트·요청 폴더·전체 일감 폴더 | **이름 또는 ID**. 기본 폴더 이름은 "요청", "전체 일감" |
| 티켓 유형 | 사용자 정의 항목 유형을 쓸 때만, 이름 또는 ID |
| 요청 워크플로·상태 3개 | 기존 업무 워크플로와 분리된 **전용 워크플로**. 기본 이름 "요청 처리", 상태 "검토중·확정·반려". 이름 또는 ID |
| 요청 채널·승인자·카테고리 | Slack에서 고릅니다. 카테고리는 `값:표시이름`을 쉼표로 구분 |
| AI 의견 | 켜기/끄기, LLM 제공자·주소·API 키·모델 (아래 표) |

**저장하고 확인**을 누르면 앱이 Wrike에서 이름을 찾아 ID를 채우고, 찾은 항목의 이름과 ID를 홈 탭에 보여 줍니다. 해결할 문제가 남아 있으면 그 설정은 저장하지 않고, 이전 설정으로 계속 동작합니다.

- 프로젝트·폴더가 없으면 **만들기** 버튼이 나옵니다. 눌렀을 때만 만듭니다.
- 전용 워크플로가 없으면 **워크플로 만들기**(세 상태 포함) 버튼이 나옵니다. 앱이 만든 워크플로에 상태가 빠졌으면 **상태 추가** 버튼이 나옵니다. 앱이 만들지 않은 워크플로는 업무용일 수 있어 바꾸지 않으므로, 상태가 빠졌으면 Wrike에서 직접 추가하세요. Wrike 워크플로 관리 권한이 없으면 실패 이유가 표시되니, Wrike 관리자가 만든 워크플로를 이름 또는 ID로 지정하세요.
- 같은 이름이 여러 개면 목록에서 하나를 고릅니다.
- 설정이 끝나면 앱이 요청 채널에 참여하고 "요청 등록" 버튼을 게시합니다. 그 메시지를 채널에 고정해 두세요.

홈 탭 맨 위에는 "실행 중 · 마지막 확인 시각 · Wrike 연결 상태"가 보입니다. 앱이 5분마다 갱신하므로, 시각이 오래 멈춰 있으면 앱이 꺼진 것입니다(조직 단위로 여러 워크스페이스에 설치했다면 홈 탭을 열 때 갱신됩니다).

"요청 등록" 버튼 메시지는 채널에 고정해 두세요. 고정돼 있으면 앱이 다시 시작하거나 설정을 저장해도 버튼을 또 게시하지 않습니다.

### AI 의견 설정

Slack은 앱에 LLM을 제공하지 않으므로, 쓰려는 LLM의 API 키를 넣습니다. 요청 1건당 기존 일감 최대 50건을 함께 보내므로 호출 비용이 조금씩 듭니다.

| 쓰려는 LLM | 설정 |
|---|---|
| Claude (기본) | 제공자 Claude, API 키(https://console.anthropic.com 에서 발급). 모델을 비우면 `claude-opus-5-5` |
| OpenAI | 제공자 OpenAI 호환, 주소 `https://api.openai.com/v1`, API 키, 모델 |
| Google Gemini | 제공자 OpenAI 호환, 주소에 Gemini의 OpenAI 호환 주소, API 키, 모델 |
| 사내 프록시·로컬 모델(Ollama 등) | 제공자 OpenAI 호환, 그 서버의 `…/v1` 주소, 필요하면 키, 모델 |

- 설정이 잘못되면 요청마다 스레드에 "AI 의견 생성 실패: AI 설정 오류 …"가 남습니다.
- OpenAI 호환은 Chat Completions API와 JSON 응답 모드(`response_format: json_object`)를 지원해야 합니다.
- 요청·일감 내용이 그 LLM 제공자로 전송됩니다. 회사 보안 정책에서 허용되는지 먼저 확인하세요.
- 꺼도 등록·승인은 그대로 동작합니다.

### 환경변수로 설정하는 방식 (선택)

홈 탭 대신 `.env`에 Wrike·채널·AI 설정을 넣을 수도 있습니다(`.env.example`의 주석 처리된 항목). 홈 탭에 저장한 설정이 있으면 그쪽이 우선합니다. 이때 Wrike ID는 직접 찾아야 합니다.

```bash
# 폴더 링크의 id= 숫자를 API ID로
curl -s -H "Authorization: Bearer $WRIKE_TOKEN" \
  "https://www.wrike.com/api/v4/folders?permalink=https://www.wrike.com/open.htm?id=<숫자>"
# 워크플로와 상태 ID
curl -s -H "Authorization: Bearer $WRIKE_TOKEN" https://www.wrike.com/api/v4/workflows
```

## 6. 문제 해결

| 증상 | 확인할 것 |
|---|---|
| 버튼을 눌러도 반응 없음 | 앱이 실행 중인지(Docker Desktop의 Containers, 홈 탭 마지막 확인 시각), Socket Mode가 켜져 있는지 |
| 홈 탭에 설정이 안 보임 | `ADMIN_IDS`에 내 멤버 ID가 있는지 |
| "설정 저장소가 꺼져 있습니다" | `.env`의 `SETTINGS_KEY` |
| "요청 에이전트 설정이 끝나지 않았습니다" | 홈 탭 연결 확인 결과에 남은 문제 |
| 홈 탭 "Wrike 연결: 오류(not_authorized)" | Wrike 토큰, Wrike 지역 |
| 담당자가 Wrike에 지정되지 않음 | Slack과 Wrike 계정 이메일이 같은지 (이메일로 매칭) |
| ✅를 눌렀는데 "권한이 없습니다" | 홈 탭 설정의 승인자 |
| 스레드에 "AI 의견 생성 실패" | LLM API 키·모델, 회사 네트워크에서 LLM 주소 접속 가능 여부 |
| AI 의견이 아예 안 달림 | AI 의견 켜기, 시작 로그의 "AI 의견을 끕니다" 경고(Claude면 `anthropic` 설치 여부) |
