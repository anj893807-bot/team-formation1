# 팀 프로젝트 팀 편성 웹 앱

Streamlit + Supabase(Postgres) + OpenRouter로 만든 팀 편성 앱. 학생은 팀에 지원하고 팀원이 투표하며, 마감 뒤 남은 학생은 AI가 배정하고 앱이 규칙으로 검사한다.

작성자: <!-- TODO: 이름 -->

## 1. 배포 주소
<!-- TODO: https://xxxx.streamlit.app -->

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
<!-- TODO: 실제로 쓴 모델로 고칠 것 (Secrets의 OPENROUTER_MODELS 또는 tf/ai.py의 DEFAULT_MODELS) -->
1차 `dots-studio/dots-3-note-preview:free`, 2차 `nvidia/nemotron-3.5-lightning:free`, 3차 `google/gemma-4-31b-it:free`

## 5. 오픈라우터 AI 모델 호출 실패 사례
<!-- TODO: 배포한 앱에서 실제로 겪은 사례와 화면(img/…png). 관리자 화면의 "AI 호출 기록"에 실패 사유와 시각이 남음 -->

## 6. 클로드가 잘못된 추론이나 답변을 한 사례
<!-- TODO: 실제 사례, 알아챈 방법, 근거(화면 또는 커밋 해시) -->

## 7. 앱에서 발견한 문제점이나 개선 필요 사항
<!-- TODO: 연습용 시나리오의 몇 단계에서 무엇을 발견했는지와 화면 -->

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
