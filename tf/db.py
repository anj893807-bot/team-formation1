"""Supabase(Postgres) 계층.

- 연결은 풀 하나를 앱 전체에서 다시 쓴다(질의마다 새로 연결하지 않음).
- 쓰기 작업은 트랜잭션 안에서 advisory lock으로 한 줄로 세운 뒤,
  상태를 읽어 tf.logic의 결정을 그대로 반영한다.
- 그래도 두 요청이 겹치면 tf_members의 유일성 제약이 먼저 도착한 학생만 남긴다.
"""
from __future__ import annotations

import json
from pathlib import Path

import bcrypt
import psycopg
from psycopg_pool import ConnectionPool

from tf import logic
from tf.logic import APPROVED, BLOCKED, PENDING, REJECTED, ROLES, Application, Decision, State, Student

SCHEMA = Path(__file__).resolve().parent.parent / "schema.sql"
WRITE_LOCK = 73110  # pg_advisory_xact_lock 키
STUDENT_PASSWORD = "1234"


def make_pool(database_url: str) -> ConnectionPool:
    # prepare_threshold=None: Supabase 연결 풀러(트랜잭션 모드)에서도 동작하게 한다.
    return ConnectionPool(
        conninfo=database_url,
        min_size=1,
        max_size=5,
        kwargs={"prepare_threshold": None, "connect_timeout": 10},
        check=ConnectionPool.check_connection,
        open=True,
    )


def hash_password(pw: str, rounds: int = 10) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds)).decode()


def ensure_schema(pool: ConnectionPool) -> None:
    with pool.connection() as conn:
        conn.execute("select pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
        conn.execute(SCHEMA.read_text(encoding="utf-8"))


def ensure_admin(pool: ConnectionPool, password: str) -> None:
    """관리자 계정을 만들거나 비밀번호를 ADMIN_PASSWORD로 갱신한다."""
    with pool.connection() as conn:
        row = conn.execute(
            "select password_hash from tf_students where student_no = 'admin'"
        ).fetchone()
        if row and bcrypt.checkpw(password.encode(), row[0].encode()):
            return
        conn.execute(
            """insert into tf_students (student_no, name, password_hash, is_admin)
               values ('admin', '관리자', %s, true)
               on conflict (student_no) do update
               set password_hash = excluded.password_hash, is_admin = true""",
            (hash_password(password, 12),),
        )


def authenticate(pool: ConnectionPool, student_no: str, password: str) -> dict | None:
    if not student_no or not password:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            "select id, student_no, name, password_hash, is_admin from tf_students where student_no = %s",
            (student_no,),
        ).fetchone()
    if not row or not bcrypt.checkpw(password.encode(), row[3].encode()):
        return None
    return {"id": row[0], "no": row[1], "name": row[2], "is_admin": row[4]}


# ---------------------------------------------------------------- 상태 읽기

def _load_state(conn: psycopg.Connection) -> State:
    subs: dict[int, list[str]] = {}
    for sid, role in conn.execute("select student_id, role from tf_student_sub_roles"):
        subs.setdefault(sid, []).append(role)
    students = {}
    for sid, no, name, main in conn.execute(
        "select id, student_no, name, main_role from tf_students where not is_admin order by student_no"
    ):
        ordered = tuple(sorted(subs.get(sid, []), key=ROLES.index))
        students[sid] = Student(sid, no, name, main, ordered)
    team_ids = [r[0] for r in conn.execute("select id from tf_teams order by id")]
    members: dict[int, list[int]] = {t: [] for t in team_ids}
    for t, sid in conn.execute("select team_id, student_id from tf_members order by team_id, slot_no"):
        members[t].append(sid)
    apps = [
        Application(*r)
        for r in conn.execute("select id, team_id, student_id, status, reason from tf_applications order by id")
    ]
    votes: dict[int, dict[int, str]] = {}
    for aid, voter, vote in conn.execute("select application_id, voter_id, vote from tf_votes"):
        votes.setdefault(aid, {})[voter] = vote
    closed = conn.execute("select 1 from tf_settings where key = 'closed'").fetchone() is not None
    return State(students, team_ids, members, apps, votes, closed)


def load_state(pool: ConnectionPool) -> State:
    with pool.connection() as conn:
        return _load_state(conn)


def _lock(conn: psycopg.Connection) -> None:
    conn.execute("select pg_advisory_xact_lock(%s)", (WRITE_LOCK,))


def _add_member(conn: psycopg.Connection, team: int, sid: int) -> None:
    used = {r[0] for r in conn.execute("select slot_no from tf_members where team_id = %s", (team,))}
    free = [n for n in range(1, logic.MAX_SIZE + 1) if n not in used]
    if not free:
        raise psycopg.errors.UniqueViolation("팀이 꽉 참")
    conn.execute(
        "insert into tf_members (team_id, slot_no, student_id) values (%s, %s, %s)",
        (team, free[0], sid),
    )


# ---------------------------------------------------------------- 지원과 투표

def apply(pool: ConnectionPool, sid: int, team: int) -> Decision:
    try:
        with pool.connection() as conn:
            _lock(conn)
            state = _load_state(conn)
            d = logic.decide_apply(state, sid, team)
            if d.status == APPROVED:
                conn.execute(
                    """insert into tf_applications (team_id, student_id, status, reason, decided_at)
                       values (%s, %s, '승인', %s, now())""",
                    (team, sid, d.reason),
                )
                _add_member(conn, team, sid)
            elif d.status == PENDING:
                conn.execute(
                    "insert into tf_applications (team_id, student_id) values (%s, %s)", (team, sid)
                )
            return d
    except psycopg.errors.UniqueViolation:
        return Decision(BLOCKED, "자리가 이미 찼거나 이미 처리됨(먼저 도착한 요청만 기록함)")


def vote(pool: ConnectionPool, voter: int, applicant_no: str, choice: str) -> Decision:
    try:
        with pool.connection() as conn:
            _lock(conn)
            state = _load_state(conn)
            team = state.team_of(voter)
            app = next(
                (a for a in state.apps
                 if a.status == PENDING and a.team == team and state.students[a.student].no == applicant_no),
                None,
            )
            if app is None:
                return Decision(BLOCKED, "투표할 수 있는 지원이 없음")
            d = logic.decide_vote(state, app.id, voter, choice)
            if d.status == BLOCKED:
                return d
            conn.execute(
                "insert into tf_votes (application_id, voter_id, vote) values (%s, %s, %s)",
                (app.id, voter, choice),
            )
            if d.status in (APPROVED, REJECTED):
                conn.execute(
                    "update tf_applications set status = %s, reason = %s, decided_at = now() where id = %s",
                    (d.status, d.reason, app.id),
                )
            if d.status == APPROVED:
                _add_member(conn, app.team, app.student)
            return d
    except psycopg.errors.UniqueViolation:
        return Decision(BLOCKED, "이미 투표했거나 자리가 이미 참")


# ---------------------------------------------------------------- 관리자

def reset(pool: ConnectionPool, rows: list[dict], team_count: int) -> Decision:
    """명단과 팀 수로 처음 상태를 만든다. 관리자 계정과 AI 호출 기록은 남긴다."""
    hashes = [hash_password(STUDENT_PASSWORD) for _ in rows]
    with pool.connection() as conn:
        _lock(conn)
        conn.execute("delete from tf_votes")
        conn.execute("delete from tf_applications")
        conn.execute("delete from tf_members")
        conn.execute("delete from tf_teams")
        conn.execute("delete from tf_students where not is_admin")
        conn.execute("delete from tf_settings where key <> 'keepalive'")
        with conn.cursor() as cur:
            cur.executemany(
                "insert into tf_teams (id, name) values (%s, %s)",
                [(t, f"팀{t}") for t in range(1, team_count + 1)],
            )
            for r, h in zip(rows, hashes):
                cur.execute(
                    """insert into tf_students (student_no, name, main_role, password_hash)
                       values (%s, %s, %s, %s) returning id""",
                    (r["student_no"], r["name"], r["main_role"], h),
                )
                sid = cur.fetchone()[0]
                for role in r["sub_roles"]:
                    cur.execute(
                        "insert into tf_student_sub_roles (student_id, role) values (%s, %s)", (sid, role)
                    )
    return Decision(APPROVED, f"명단 {len(rows)}명과 팀 {team_count}개로 처음 상태를 만듦")


def close(pool: ConnectionPool) -> Decision:
    with pool.connection() as conn:
        _lock(conn)
        state = _load_state(conn)
        if not state.team_ids:
            return Decision(BLOCKED, "명단을 먼저 올려야 함")
        if state.closed:
            return Decision(BLOCKED, "이미 마감됨")
        rejected, removed = logic.decide_close(state)
        if rejected:
            conn.execute(
                "update tf_applications set status = '거절', reason = '모집 마감', decided_at = now() where id = any(%s)",
                (rejected,),
            )
        if removed:
            conn.execute("delete from tf_members where student_id = any(%s)", (removed,))
        conn.execute("insert into tf_settings (key, value) values ('closed', now()::text)")
        conn.execute("delete from tf_settings where key in ('proposal', 'assign_status')")
    return Decision(
        APPROVED, f"모집을 마감함(대기 지원 {len(rejected)}건 거절, 1명뿐인 팀에서 {len(removed)}명 뺌)"
    )


def get_setting(pool: ConnectionPool, key: str) -> str | None:
    with pool.connection() as conn:
        row = conn.execute("select value from tf_settings where key = %s", (key,)).fetchone()
    return row[0] if row else None


def set_setting(pool: ConnectionPool, key: str, value: str | None) -> None:
    with pool.connection() as conn:
        if value is None:
            conn.execute("delete from tf_settings where key = %s", (key,))
        else:
            conn.execute(
                """insert into tf_settings (key, value) values (%s, %s)
                   on conflict (key) do update set value = excluded.value""",
                (key, value),
            )


def save_proposal(pool: ConnectionPool, plan: dict[int, int], source: str) -> None:
    set_setting(pool, "proposal", json.dumps({"plan": {str(k): v for k, v in plan.items()}, "source": source}))


def get_proposal(pool: ConnectionPool) -> dict | None:
    raw = get_setting(pool, "proposal")
    if not raw:
        return None
    data = json.loads(raw)
    return {"plan": {int(k): v for k, v in data["plan"].items()}, "source": data["source"]}


def confirm(pool: ConnectionPool) -> Decision:
    with pool.connection() as conn:
        _lock(conn)
        state = _load_state(conn)
        if not state.closed:
            return Decision(BLOCKED, "모집을 먼저 마감해야 함")
        row = conn.execute("select value from tf_settings where key = 'proposal'").fetchone()
        if not row:
            return Decision(BLOCKED, "확정할 배정안이 없음(AI 배정을 먼저 해야 함)")
        plan = {int(k): v for k, v in json.loads(row[0])["plan"].items()}
        teams = {t: state.team_students(t) for t in state.team_ids}
        unassigned = state.unassigned()
        pairs = [(state.students[sid].no, t) for sid, t in plan.items() if sid in state.students]
        errors = logic.validate_assignment(teams, unassigned, pairs)
        if errors:
            return Decision(BLOCKED, "배정안이 지금 팀 구성과 맞지 않음 - " + "; ".join(errors))
        for sid, t in plan.items():
            _add_member(conn, t, sid)
        conn.execute("delete from tf_settings where key = 'proposal'")
        conn.execute(
            """insert into tf_settings (key, value) values ('confirmed', now()::text)
               on conflict (key) do update set value = excluded.value"""
        )
        conn.execute(
            """insert into tf_settings (key, value) values ('assign_status', '배정안을 확정함')
               on conflict (key) do update set value = excluded.value"""
        )
    return Decision(APPROVED, f"배정안을 확정함({len(plan)}명 배정)")


# ---------------------------------------------------------------- AI 기록

def add_ai_log(pool: ConnectionPool, attempt: int, model: str, outcome: str, detail: str, elapsed: float) -> None:
    with pool.connection() as conn:
        conn.execute(
            """insert into tf_ai_logs (attempt, model, outcome, detail, elapsed_sec)
               values (%s, %s, %s, %s, %s)""",
            (attempt, model, outcome, detail[:2000], round(elapsed, 1)),
        )


def recent_ai_logs(pool: ConnectionPool, limit: int = 30) -> list[tuple]:
    with pool.connection() as conn:
        return conn.execute(
            """select created_at, attempt, model, outcome, detail, elapsed_sec
               from tf_ai_logs order by id desc limit %s""",
            (limit,),
        ).fetchall()


def touch(pool: ConnectionPool) -> None:
    """DB 일시정지를 막기 위한 가벼운 질의(외부 예약 작업이 부름)."""
    with pool.connection() as conn:
        conn.execute("select count(*) from tf_students")
        conn.execute(
            """insert into tf_settings (key, value) values ('keepalive', now()::text)
               on conflict (key) do update set value = excluded.value"""
        )
