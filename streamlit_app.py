"""팀 프로젝트를 위한 팀 편성 웹 앱 (Streamlit + Supabase + OpenRouter)."""
from __future__ import annotations

import os
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from dotenv import load_dotenv

from tf import ai, db, logic
from tf.logic import NO, PENDING, YES
from tf.roster import RosterError, parse_roster

load_dotenv(Path(__file__).with_name(".env"))

REFRESH_SEC = 3  # 다른 사람의 변경을 다시 그리는 간격(5초 이하)
KST = ZoneInfo("Asia/Seoul")

st.set_page_config(page_title="팀 편성", page_icon="👥", layout="wide")


def secret(name: str) -> str | None:
    value = os.environ.get(name)
    if value:
        return value
    try:
        return st.secrets.get(name)
    except Exception:  # secrets.toml이 없을 때
        return None


def models() -> list[str]:
    raw = secret("OPENROUTER_MODELS")
    names = [m.strip() for m in raw.split(",") if m.strip()] if raw else []
    for m in ai.DEFAULT_MODELS:
        if len(names) < 3 and m not in names:
            names.append(m)
    return names[:3]


missing = [k for k in ("DATABASE_URL", "ADMIN_PASSWORD") if not secret(k)]
if missing:
    # 값 자체는 출력하지 않고 있음/없음만 보여 준다.
    st.error("설정이 빠졌음: " + ", ".join(f"{k} 없음" for k in missing))
    st.stop()


@st.cache_resource(show_spinner="DB에 연결하는 중…")
def get_pool():
    pool = db.make_pool(secret("DATABASE_URL"))
    db.ensure_schema(pool)
    db.ensure_admin(pool, secret("ADMIN_PASSWORD"))
    return pool


try:
    pool = get_pool()
except Exception as e:
    st.error(f"DB에 연결할 수 없음({type(e).__name__}). DATABASE_URL과 Supabase 프로젝트 상태를 확인하세요.")
    st.stop()

ss = st.session_state


# ---------------------------------------------------------------- 공용

def set_result(message: str) -> None:
    ss["result"] = message


def show_result() -> None:
    msg = ss.get("result")
    with st.container(key="result"):
        if msg:
            style = {"승인": st.success, "대기": st.info, "거절": st.warning, "막힘": st.error}
            style.get(msg.split(":")[0], st.info)(msg)


def safely(action):
    """단추 콜백에서 DB 오류가 나도 앱이 멈추지 않게 한다(오류 내용에 비밀 값은 넣지 않음)."""
    def run(*args):
        try:
            action(*args)
        except Exception as e:
            traceback.print_exc()
            set_result(f"막힘: 처리 중 오류가 남({type(e).__name__}). 다시 시도해 주세요")
    return run


def kst(ts: datetime) -> str:
    return ts.astimezone(KST).strftime("%m/%d %H:%M:%S")


def member_line(s: logic.Student) -> str:
    return f"**{s.name}** · {s.roles_label}"


def render_teams(state: logic.State, me: int | None = None, can_apply: bool = False, show_apply: bool = False):
    if not state.team_ids:
        st.info("아직 팀이 없음. 관리자가 명단을 올리면 팀이 만들어짐")
        return
    my_team = state.team_of(me) if me else None
    ncol = min(4, len(state.team_ids))
    cols = st.columns(ncol)
    for i, t in enumerate(state.team_ids):
        ms = state.team_students(t)
        waiting = sum(1 for a in state.apps if a.team == t and a.status == PENDING)
        with cols[i % ncol]:
            with st.container(key=f"team_{t}", border=True):
                badge = " · :blue-background[내 팀]" if t == my_team else ""
                st.markdown(f"#### 팀{t}{badge}")
                st.caption(f"{len(ms)}/{logic.MAX_SIZE}명" + (f" · 대기 중인 지원 {waiting}건" if waiting else ""))
                if ms:
                    for s in ms:
                        st.markdown(member_line(s))
                    if logic.unfilled_required(ms):
                        st.caption(f"아직 없음: {logic.missing_required_text(ms)}")
                else:
                    st.caption("아직 팀원이 없음")
                if show_apply:
                    st.button(
                        "지원하기", key=f"apply_{t}", disabled=not can_apply,
                        on_click=safely(do_apply), args=(t,), use_container_width=True,
                    )


# ---------------------------------------------------------------- 로그인

def logout():
    for k in ("user", "result"):
        ss.pop(k, None)


def login_page():
    st.title("👥 팀 프로젝트 팀 편성")
    st.caption("학번과 비밀번호로 로그인하세요.")
    with st.form("login_form"):
        no = st.text_input("학번", key="login_student_no")
        pw = st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인", key="login_submit", type="primary")
    if submitted:
        user = db.authenticate(pool, no.strip(), pw)
        if user:
            ss["user"] = user
            ss.pop("result", None)
            st.rerun()
        else:
            st.error("학번 또는 비밀번호가 틀림")


def whoami():
    u = ss["user"]
    with st.container(key="whoami"):
        c1, c2 = st.columns([6, 1])
        role = "관리자" if u["is_admin"] else "학생"
        c1.markdown(f"### 👤 {u['name']}  \n{role} · 학번 {u['no']}")
        c2.button("로그아웃", key="logout", on_click=logout)


# ---------------------------------------------------------------- 학생

def do_apply(team: int):
    set_result(db.apply(pool, ss["user"]["id"], team).message)


def do_vote(applicant_no: str, choice: str):
    set_result(db.vote(pool, ss["user"]["id"], applicant_no, choice).message)


@st.fragment(run_every=REFRESH_SEC)
def student_view():
    sid = ss["user"]["id"]
    state = db.load_state(pool)
    show_result()
    if sid not in state.students:
        st.warning("명단이 바뀌어 이 계정이 없음. 로그아웃한 뒤 다시 로그인하세요.")
        return
    my_team = state.team_of(sid)
    pending = state.pending_of(sid)

    if state.closed:
        st.warning("모집이 마감됨. 지원과 투표를 할 수 없음")
    if my_team:
        st.success(f"내 팀: 팀{my_team}")
    elif pending:
        st.info(f"팀{pending.team}에 지원함 — 결과를 기다리는 중")
    else:
        st.info("아직 팀이 없음")

    if my_team:
        waiting = [a for a in state.apps if a.team == my_team and a.status == PENDING]
        st.subheader("내 팀에 들어온 지원")
        if not waiting:
            st.caption("대기 중인 지원이 없음")
        for a in waiting:
            s = state.students[a.student]
            votes = state.votes.get(a.id, {})
            members = state.members.get(my_team, [])
            yes = sum(1 for m in members if votes.get(m) == YES)
            with st.container(border=True):
                st.markdown(member_line(s))
                st.caption(f"찬성 {yes}/{len(members)}명")
                mine = votes.get(sid)
                if mine:
                    st.markdown(f"내 투표: **{mine}**")
                elif not state.closed:
                    c1, c2, _ = st.columns([1, 1, 4])
                    c1.button("찬성", key=f"vote_yes_{s.no}", type="primary",
                              on_click=safely(do_vote), args=(s.no, YES))
                    c2.button("거부", key=f"vote_no_{s.no}", on_click=safely(do_vote), args=(s.no, NO))

    st.subheader("팀 목록")
    can_apply = not state.closed and my_team is None and pending is None
    render_teams(state, me=sid, can_apply=can_apply, show_apply=True)

    mine = [a for a in state.apps if a.student == sid]
    if mine:
        with st.expander("내 지원 기록"):
            for a in reversed(mine):
                st.markdown(f"- 팀{a.team}: **{a.status}**" + (f" — {a.reason}" if a.reason else ""))


# ---------------------------------------------------------------- 관리자

def require_admin() -> bool:
    if not ss.get("user", {}).get("is_admin"):
        set_result("막힘: 관리자만 할 수 있음")
        return False
    return True


def do_start():
    if not require_admin():
        return
    f = ss.get("admin_roster_file")
    if f is None:
        set_result("막힘: 명단 CSV 파일을 먼저 올려야 함")
        return
    try:
        rows = parse_roster(f.getvalue())
    except RosterError as e:
        set_result(f"막힘: {e}")
        return
    count = int(ss.get("admin_team_count") or 0)
    if count < 1:
        set_result("막힘: 팀 수는 1 이상이어야 함")
        return
    set_result(db.reset(pool, rows, count).message)


def do_close():
    if require_admin():
        set_result(db.close(pool).message)


def do_confirm():
    if require_admin():
        set_result(db.confirm(pool).message)


def run_assign(box):
    """AI 배정. 몇 차 모델을 기다리는지 assign_status 안에 보여 준다."""
    if not require_admin():
        return
    state = db.load_state(pool)
    if not state.closed:
        set_result("막힘: 모집을 먼저 마감해야 함")
        return
    if db.get_setting(pool, "confirmed"):
        set_result("막힘: 이미 배정안을 확정함")
        return
    teams = {t: state.team_students(t) for t in state.team_ids}
    unassigned = state.unassigned()
    db.set_setting(pool, "proposal", None)

    # 규칙을 지키는 배정이 아예 없으면 AI를 부르지 않는다(2.5절 8항).
    fallback = logic.solve(teams, unassigned)
    if fallback is None:
        reasons = logic.explain_infeasible(teams, unassigned)
        db.set_setting(pool, "assign_status", "규칙을 지키는 배정이 없음\n" + "\n".join(f"- {r}" for r in reasons))
        set_result("막힘: 규칙을 지키는 배정이 없음 - " + "; ".join(reasons))
        return
    if not unassigned:
        db.save_proposal(pool, {}, "배정할 학생이 없음")
        db.set_setting(pool, "assign_status", "팀이 없는 학생이 없음. 확정하면 지금 팀 구성을 그대로 씀")
        set_result("승인: 배정할 학생이 없음(지금 팀 구성이 규칙을 지킴)")
        return

    names = models()
    with box:
        ph = st.empty()

    def progress(attempt, model, elapsed, attempts):
        lines = [f"- {a.attempt}차 `{a.model}`: {a.outcome} ({a.elapsed:.0f}초) — {a.detail}" for a in attempts]
        with ph.container():
            st.info(f"⏳ AI가 생각하는 중 — **{attempt}차 모델** `{model}`의 응답을 기다리는 중 "
                    f"({elapsed:.0f}초 / {ai.MODEL_TIMEOUT:.0f}초)")
            if lines:
                st.markdown("\n".join(lines))

    def record(a: ai.Attempt):
        db.add_ai_log(pool, a.attempt, a.model, a.outcome, a.detail, a.elapsed)

    pairs, model, attempts = ai.assign(teams, unassigned, names, secret("OPENROUTER_API_KEY"), progress, record)
    history = "\n".join(f"- {a.attempt}차 `{a.model}`: {a.outcome} ({a.elapsed:.0f}초) — {a.detail}" for a in attempts)
    if pairs is not None:
        by_no = {s.no: s.id for s in unassigned}
        plan = {by_no[no]: t for no, t in pairs}
        source = f"{attempts[-1].attempt}차 모델 {model}"
    else:
        plan = fallback
        source = "앱이 스스로 찾은 배정(3차 모델까지 실패)"
        db.add_ai_log(pool, 0, "앱 배정", "성공", "모든 경우를 따져 규칙을 지키는 배정을 찾음", 0.0)
    db.save_proposal(pool, plan, source)
    db.set_setting(pool, "assign_status", f"배정안을 만듦: {source} (검사 다섯 가지 통과)\n{history}")
    set_result(f"승인: 배정안을 만듦 - {source}")


@st.fragment(run_every=REFRESH_SEC)
def admin_overview():
    state = db.load_state(pool)
    confirmed = db.get_setting(pool, "confirmed")
    phase = "확정됨" if confirmed else ("마감됨" if state.closed else ("모집 중" if state.team_ids else "명단 없음"))
    st.caption(f"학생 {len(state.students)}명 · 팀 {len(state.team_ids)}개 · 상태: **{phase}**")
    st.subheader("팀이 없는 학생")
    with st.container(key="unassigned", border=True):
        un = state.unassigned()
        if un:
            for s in un:
                st.markdown(member_line(s))
        else:
            st.caption("없음")
    st.subheader("팀 목록")
    render_teams(state)
    waiting = [a for a in state.apps if a.status == PENDING]
    if waiting:
        st.subheader("대기 중인 지원")
        for a in waiting:
            s = state.students[a.student]
            votes = state.votes.get(a.id, {})
            st.markdown(f"- {s.name} → 팀{a.team} (찬성 {sum(v == YES for v in votes.values())}/"
                        f"{len(state.members.get(a.team, []))})")


def admin_page():
    show_result()
    state = db.load_state(pool)

    left, right = st.columns([2, 3], gap="large")
    with left:
        st.subheader("1. 명단 올리기")
        st.file_uploader("명단 CSV (UTF-8)", type=["csv"], key="admin_roster_file")
        st.number_input("팀 수", min_value=1, max_value=10, value=3, step=1, key="admin_team_count")
        st.button("명단으로 처음 상태 만들기", key="admin_start", on_click=safely(do_start))
        st.caption("지원, 투표, 팀원, 마감 표시를 모두 지우고 새로 시작함(관리자 계정은 남김)")

        st.subheader("2. 모집 마감")
        st.button("모집 마감", key="admin_close", on_click=safely(do_close))

        st.subheader("3. AI 배정")
        clicked = st.button("AI 배정", key="admin_assign")
        box = st.container(key="assign_status", border=True)
        if clicked:
            try:
                run_assign(box)
            except Exception as e:
                traceback.print_exc()
                set_result(f"막힘: 배정 중 오류가 남({type(e).__name__})")
            st.rerun()
        with box:
            status = db.get_setting(pool, "assign_status")
            if status:
                head, _, rest = status.partition("\n")
                (st.error if head.startswith("규칙을 지키는 배정이 없음") else st.success)(head)
                if rest:
                    st.markdown(rest)
            else:
                st.caption("마감한 뒤 AI 배정을 누르면 여기에 진행 상황이 보임")

        proposal = db.get_proposal(pool)
        if proposal:
            st.markdown(f"**배정안** ({proposal['source']})")
            rows = [
                {"이름": state.students[sid].name, "주 역할": state.students[sid].main,
                 "보조 역할": ", ".join(state.students[sid].subs) or "없음", "배정된 팀": f"팀{t}"}
                for sid, t in sorted(proposal["plan"].items(), key=lambda x: (x[1], x[0]))
                if sid in state.students
            ]
            if rows:
                st.table(rows)
        st.button("배정안 확정", key="admin_confirm", type="primary", on_click=safely(do_confirm))

        with st.expander("AI 호출 기록 (한국 시각)"):
            logs = db.recent_ai_logs(pool)
            if not logs:
                st.caption("기록 없음")
            for ts, attempt, model, outcome, detail, el in logs:
                label = f"{attempt}차" if attempt else "앱"
                st.markdown(f"- {kst(ts)} · {label} `{model}` · **{outcome}** ({el}초) — {detail}")

    with right:
        admin_overview()


# ---------------------------------------------------------------- 시작

if "user" not in ss:
    login_page()
else:
    whoami()
    if ss["user"]["is_admin"]:
        admin_page()
    else:
        student_view()
