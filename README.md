# 팀 프로젝트 팀 편성 웹 앱

Streamlit + Supabase(Postgres) + OpenRouter로 만든 팀 편성 앱. 학생은 팀에 지원하고 팀원이 투표하며, 마감 뒤 남은 학생은 AI가 배정하고 앱이 규칙으로 검사한다.

작성자: 안지영 (2601956)

## 1. 배포 주소
https://tf-anj893807.streamlit.app/

## 2. PRD와 구현 계획의 위치
- [docs/PRD.md](docs/PRD.md)
- [docs/구현계획.md](docs/구현계획.md)

## 3. 고른 해결 방법
| 기능 | 해결 | 방법 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | `st.fragment`로 3초마다 바뀐 부분만 다시 표시 |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원 단계에서, 필수 포함 역할을 채울 자리가 모자라게 되는 지원은 막음 |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 승인 직전에 규칙을 다시 검사(투표가 끝난 순서대로 처리) |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | `tf_members`의 `(team_id, slot_no)` 기본 키와 `student_id` 유일성 제약 ([schema.sql](schema.sql)) |
| AI 응답이 늦을 때 | 어려운 해결 | 모델마다 120초의 전체 대기 시간을 재서 끊고 다음 모델로 넘어감 (`tf/ai.py`의 `call_model`) |
| 3차 모델까지 실패 | 어려운 해결 | 검사 다섯 가지를 모두 통과하는 배정을 모든 경우를 따져 끝까지 찾음 (`tf/logic.py`의 `solve`) |
| AI 대기 중 화면 | 어려운 해결 | 몇 차 모델의 응답을 기다리는 중인지와 경과 시간을 `assign_status`에 보여 줌 |
| DB(Supabase) 일시정지 막기 | 어려운 해결 | GitHub Actions 예약 작업으로 매일 DB에 질의 ([.github/workflows/keepalive.yml](.github/workflows/keepalive.yml)) |

## 4. 쓴 모델 3개
1차 `dots-studio/dots-3-note-preview:free`, 2차 `nvidia/nemotron-3.5-lightning:free`, 3차 `google/gemma-4-31b-it:free`

## 5. 오픈라우터 AI 모델 호출 실패 사례
11/1에 1, 2, 3차 모델을 고르면서 앱의 AI 호출 코드(`tf/ai.py`의 `assign`, `call_model`)로 모델마다 한 번씩 불렀을 때 다음 실패가 났다 (화면 [img/model-test.png](img/model-test.png)).
- `nvidia/nemotron-3-ultra-550b-a55b:free`: 120초 안에 응답을 다 받지 못해 앱의 전체 대기 시간 제한이 끊음(시간 초과)
- `google/gemma-4-31b-it:free`, `google/gemma-4-26b-a4b-it:free`, `xingchen-agi/xing4.0-29b-a4b:free`: HTTP 429(사용량이 많다며 거절)
- `recursive/flint-v1.0:free`: HTTP 503
- `thinkingmachines/inkling:free`: HTTP 403(에이전트 앱에서만 쓸 수 있음)

이 결과로 실제로 배정안을 낸 모델을 1, 2차로 정했다 (커밋 `5d60ee3`의 `tf/ai.py` 주석). 배포한 앱에서 쉬움(11/1)과 어려움(11/2) 시나리오의 AI 배정은 모두 1차 모델이 검사를 통과한 배정안을 내어(26.4초, 70.6초) 2차로 넘어가지 않았다 (화면 [img/hard-ai-waiting.png](img/hard-ai-waiting.png), [img/hard-ai-log.png](img/hard-ai-log.png)).

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
### 사례 1: 없는 AI 모델을 기본 모델로 정함
- **사례:** 클로드가 처음 정한 기본 모델 3개(`nvidia/nemotron-3-super-120b-a12b:free`, `openai/gpt-oss-120b:free`, `z-ai/glm-4.5-air:free`)가 11/1 기준 오픈라우터에 하나도 없는 모델이었다. 클로드가 목록을 확인하지 않고 알고 있던 이름으로 골랐기 때문이다. 그대로 배포했다면 채점 때 AI 호출이 모두 실패했을 것이다.
- **알아챈 방법:** `.env`의 키를 확인하면서 오픈라우터 모델 목록(`/api/v1/models`)과 대조했더니 세 모델이 모두 "없음"으로 나왔다 (화면 [img/models-missing.png](img/models-missing.png)). 처음 바꾼 3개도 실제로 불러 보니 2개를 쓸 수 없었다(120초 시간 초과, HTTP 403). 그래서 다시 골랐다.
- **고친 곳:** 실제로 한 번씩 불러 검사를 통과한 배정안을 낸 모델을 1차와 2차로 정했다 (커밋 `5d60ee3`의 `tf/ai.py`, `DEFAULT_MODELS`와 주석).

### 사례 2: 비밀 값 끝의 줄바꿈을 처리하지 않음
- **사례:** 클로드가 만든 DB 연결 코드(`tf/db.py`의 `make_pool`)가 `DATABASE_URL` 값을 그대로 썼다. 깃허브 Actions Secrets에 DB 주소를 붙여 넣을 때 끝에 줄바꿈이 섞였고, DB 이름이 `postgres` 대신 `"postgres\n"`이 되어 DB keepalive 작업이 실패했다. 클로드는 로컬에서 같은 스크립트가 성공한다는 것만 확인했고, 비밀 값에 공백이나 줄바꿈이 섞일 경우는 처리하지 않았다.
- **알아챈 방법:** GitHub Actions의 "DB keepalive" 첫 실행이 빨간 X로 실패했고, 로그에서 `FATAL: database "postgres⏎" does not exist`(따옴표가 다음 줄에서 닫힘)를 보고 알아챘다 (화면 [img/keepalive-fail.png](img/keepalive-fail.png)).
- **고친 곳:** 커밋 `6de84d2`에서 DB 주소, 키, 관리자 비밀번호의 앞뒤 공백과 줄바꿈을 지우고 쓰게 고쳤다. 끝에 줄바꿈을 일부러 붙인 주소로 다시 실행해 성공을 확인했다.

## 7. 앱에서 발견한 문제점이나 개선 필요 사항
### 새로 고침(F5)하면 로그아웃됨
- **발견한 곳:** 배포한 앱에서 부록 3(어려움)을 마친 뒤, 학생1(2026301)로 로그인해 확정된 팀 구성을 확인하던 중 F5를 눌렀다.
- **문제:** 새로 고침하면 로그인 화면으로 돌아가 다시 로그인해야 한다 (로그인한 화면 [img/f5-before.png](img/f5-before.png), F5 뒤 [img/f5-after.png](img/f5-after.png)).
- **원인:** 로그인 정보를 Streamlit의 `st.session_state`에만 둔다. 새로 고침하면 새 세션이 열려 이 값이 사라진다.
- **영향:** 채점 프로그램은 화면을 다시 열지 않으므로 채점에는 영향이 없다. 실제 학생은 새로 고침할 때마다 다시 로그인해야 해 불편하다.
- **개선 방법:** 로그인할 때 서명한 토큰을 브라우저 쿠키나 `st.query_params`에 두고, 새 세션에서 그 토큰을 검사해 로그인 상태를 되살린다.

### 찬성 단추를 두 번 누르면 결과 안내가 "막힘"으로 덮임
- **발견한 곳:** 부록 3(어려움) 8단계. 학생2(2026302)가 학생6의 지원에 찬성 단추를 두 번 눌렀다.
- **문제:** 첫 번째 누름은 "대기"로 처리되었다(DB에도 투표가 1건만 기록됨). 그러나 두 번째 누름의 결과인 "막힘: 이미 투표함"이 결과 안내를 덮어서, 내 투표가 받아들여졌는지 헷갈린다 (화면 [img/vote-already-voted.png](img/vote-already-voted.png)).
- **원인:** 첫 번째 투표를 처리하고 화면을 다시 그리기 전에는 찬성 단추가 아직 남아 있다. 이미 투표한 경우를 "막힘"으로 돌려준다.
- **개선 방법:** 같은 사람이 같은 지원에 다시 누르면 "막힘" 대신 그 지원의 지금 상태(예: "대기: 이미 찬성함")를 보여 준다. 또는 누르는 즉시 단추를 비활성화한다.

## 참고: 배포한 앱에서 연습용 시나리오를 해 본 결과
부록 3(어려움)을 배포한 앱에서 1~15단계까지 해 보았고, 결과 안내의 첫 단어가 모두 기대 결과와 같았다.
- 8단계: 학생6의 지원이 승인 직전 검사로 거절됨 ([img/hard-step8-rejected.png](img/hard-step8-rejected.png))
- 11단계: 학생14의 지원이 필수 포함 역할 자리로 막힘 ([img/hard-step11-blocked.png](img/hard-step11-blocked.png))
- 15단계: AI 배정 뒤 확정. 남은 학생 8명이 모두 배정되었고, 팀 4개가 모두 4명으로 규칙을 지킴 ([img/hard-confirmed.png](img/hard-confirmed.png))

---

## 실행 방법 (로컬)
```
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
copy .env.example .env        # 이름이 정확히 .env 인지 확인하고 값을 채움
streamlit run streamlit_app.py
```

## 배포 (Streamlit Community Cloud)
1. 깃허브 저장소를 **비공개**로 만들고 올림 (`.env`가 올라가지 않았는지 확인)
2. Supabase 대시보드 > Connect에서 **Session pooler** 주소를 복사해 `DATABASE_URL`로 씀 (Direct 주소는 IPv6 전용이라 Streamlit Cloud와 GitHub Actions에서 접속되지 않을 수 있음)
3. Streamlit: Workspace settings > Linked accounts > Private access "Connect here"
4. Create app: Branch `main`, Main file path `streamlit_app.py`, Advanced settings > Secrets:
   ```
   OPENROUTER_API_KEY = "sk-or-v1-..."
   DATABASE_URL = "postgresql://..."
   ADMIN_PASSWORD = "..."
   ```
5. 배포 뒤 Settings > Sharing > "This app is public and searchable" > Save changes, 시크릿 창으로 로그인 화면 확인
6. 깃허브 저장소 Settings > Secrets and variables > Actions에 `DATABASE_URL` 추가, Actions 탭에서 "DB keepalive"를 한 번 직접 실행해 확인

테이블은 앱이 처음 시작할 때 [schema.sql](schema.sql)로 자동으로 만들어지며, 관리자 계정도 `ADMIN_PASSWORD`로 만들어진다.

## 시험
```
pip install pytest
pytest -q                                  # 연습용 시나리오 3개, AI 실패 처리
TEST_DATABASE_URL="..." pytest -q          # DB 계층까지 (tf_ 테이블을 지우므로 시험용 DB에만)
```
연습용 명단은 [data/](data/)에 있다.
