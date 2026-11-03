# Tasks: Slack 요청 → Wrike 등록 에이전트           (specs/1-request-agent/tasks.md)

> spec: specs/1-request-agent/spec.md
> 테스트 실행: `python3 -m unittest discover -s tests -t .` (표의 `파일::이름`은 unittest 테스트 메서드를 가리킨다)
> 범례: pure = 순수 로직 테스트 · edge = 계약 테스트(가짜 Wrike·Claude) + 런타임 · glue = 실행 관찰(/verify) · ∥ = 병렬 가능
> 실제 Wrike는 남편 토큰이 있어야 검증 가능 → M4 게이트에서만 확인. 그 전엔 가짜 Wrike 서버로 대체하고 그렇게 보고한다.

## 검증 계약 (AC → 이걸로 증명)
| AC | 증명 테스트 / 검증 | 계층 |
|----|--------------------|------|
| AC1 | `/verify`: test02 #watercooler에서 버튼 → 폼 4필드 열림 | glue |
| AC2 | `tests/test_wrike_client.py::test_create_ticket` (요청 본문 형태) + `/verify`: 제출 → 가짜 Wrike에 티켓 1건, 채널 메시지 1건 | edge + glue |
| AC15 | `tests/test_forms.py::test_prefill_body` + `/verify`: /request·전역 단축키·메시지 단축키로 폼 열림, 메시지 단축키는 내용 미리 채움 | pure + glue |
| AC16 | `tests/test_forms.py::test_should_suggest` (스레드·봇·수정·다른 채널 제외) + `/verify`: 채널 글 → 작성자에게만 안내, [등록] → 미리 채운 폼, 안내 사라짐 | pure + glue |
| AC3 | `tests/test_flows.py::test_submit_wrike_failure_posts_nothing` | pure |
| AC4 | `tests/test_review.py::test_candidates_filter_90d_active` + `test_parse_opinion` + `/verify`: 스레드·Wrike 댓글 양쪽 | pure + glue |
| AC5 | `tests/test_review.py::test_no_similar_message` | pure |
| AC6 | `tests/test_flows.py::test_ai_failure_does_not_block` | pure |
| AC7 | `tests/test_flows.py::test_approve_creates_task_once` + `tests/test_wrike_client.py::test_create_task_and_link` + `/verify` | pure + edge + glue |
| AC8 | `tests/test_flows.py::test_reject` | pure |
| AC9 | `tests/test_flows.py::test_non_approver_ignored` | pure |
| AC10 | `tests/test_flows.py::test_already_decided_noop` (확정·반려·삭제, 중복 ✅) | pure |
| AC11 | `tests/test_settings.py::test_admin_only` + `/verify`: 홈 탭 관리자/비관리자 | pure + glue |
| AC12 | `tests/test_settings.py::test_token_encrypted_roundtrip` + `test_token_not_rendered` + `tests/test_resolve.py` (ID 그대로 확인 · 이름→ID: 1건·0건→만들기·여러 건→선택 · 전용 워크플로 이름/ID 확인·없으면 생성·빠진 상태만 추가·기존 워크플로 미변경·권한 없음 처리) + `/verify`: 저장 시 찾기 결과·만들기 버튼 | pure + edge + glue |
| AC13 | `/verify`: 홈 탭 상태 줄 갱신 | glue |
| AC14 | `/verify`: `docker compose up -d` → 실행, 컨테이너 재생성 후 설정 유지, `restart: unless-stopped` 확인 | glue |

## 마일스톤
현재: M1 + 입구 확장(AC15·AC16) 구현, 테스트 33개 통과. master 요청으로 M1 상태를 문서와 함께 GitHub에 올림(feature/1-request-agent). 입구 확장의 test02 실행 관찰과 게이트 재리뷰는 남음
다음: M1 게이트 사람 확인 → M2

### M1: 핵심 흐름 (등록 → 승인/반려) — 가짜 Wrike
- 다룸: Wrike 클라이언트(티켓·일감·댓글·상태), 등록·승인·반려 흐름, 권한·상태 판정. 설정은 환경변수 임시값.  · 관련 AC: AC1, AC2, AC3, AC7, AC8, AC9, AC10, AC15, AC16
- 병렬: ∥ Wrike 클라이언트 계약 테스트 / 흐름 판정 로직
- [ ] 게이트 M1: 관련 AC green + test02 실행 관찰 → /gate-review → 사람 확인
- 수정 기록:
  - `1 | Opus 5.5 | spec 지적 3건(티켓에 Slack 링크 누락, 네트워크 오류·반쯤 만든 티켓 미처리, 일감 생성 실패 시 확정 고착)과 99자 초과 줄 수정, 판단 사항 일부(Ack 타입·HTTPError 닫기·상태 역맵 1회 생성·채널 변수 이름) 반영 | 코드 기준 해소. spec: 링크 추가 실패·댓글 실패·되돌리기 실패 경로, 반려 댓글 테스트 누락 지적`
  - `2 | Opus 5.5 | 링크 실패 시 메시지·티켓 되돌리기, 스레드 답글을 댓글보다 먼저·댓글 실패는 안내만, 되돌리기 실패 시 복구 안내, 정리 실패 시 남은 티켓 ID 안내, 테스트 4건 추가 | spec 지적 없음(이중 장애 안내 처리는 spec 리스크에 명시), 코드 기준 위반 1건(빈 줄)·판단 1건(docstring) 즉시 수정 → 해소`

### M2: AI 의견
- 다룸: 후보 추리기(진행 중·90일), Claude 호출, 의견 파싱, 스레드·댓글 게시, 실패 시 계속 진행.  · 관련 AC: AC4, AC5, AC6
- [ ] 게이트 M2: 관련 AC green + 실행 관찰(실제 Claude 1회) → /gate-review → 사람 확인

### M3: 설정 홈 탭 + 운영
- 다룸: 워크스페이스별 설정 저장(토큰 암호화), 관리자 전용 홈 탭, 연결 테스트, 상태 표시, Dockerfile·compose, 문서.  · 관련 AC: AC11, AC12, AC13, AC14
- 문서(master 요청): `README.md`(사용법) · `docs/install.md`(로컬 Docker Desktop 설치부터 Slack 앱 생성·토큰·실행까지. M1 시점엔 Python 실행, M3에서 Docker 절 추가) · `docs/development.md`(구조·테스트·변경 방법·가짜 Wrike로 개발·확장 가이드: 채널 여러 개(채널별 승인자·Wrike 폴더), Wrike 직접 상태 변경 동기화 등) · `.claude/`(issues·spec·tasks 포함)도 함께 저장소에 올린다. 시크릿·`.slack/apps.dev.json`은 제외.
- 병렬: ∥ 설정 저장·암호화 / Docker·README
- [ ] 게이트 M3: 관련 AC green + docker compose 실행 관찰 → /gate-review → 사람 확인

### M4: 실제 Wrike 연동 확인 (남편 계정)
- 다룸: 남편 토큰·폴더·티켓 유형·상태로 AC2·AC7·AC8·AC12 실계정 재확인, 차이 수정.  · 관련 AC: AC2, AC7, AC8, AC12
- [ ] 게이트 M4: 실계정 관찰 결과 → 사람 확인

## 완료 정의 (DoD)
- 전체 AC 테스트 green · /verify 통과 · README·docs 갱신 · /gate-review · /code-review
- 그 뒤 git 저장소 생성·커밋·push. 원격: https://github.com/happydev-jocelyn/slack-wrike-request-agent (public, 비어 있음). master가 "다 되면 깃에 올리라"고 승인함(2026-11-04) — DoD 통과 후 push.
