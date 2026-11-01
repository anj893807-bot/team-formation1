"""실제 Postgres로 DB 계층을 시험한다. TEST_DATABASE_URL이 있을 때만 돈다(테이블을 지우므로 운영 DB에 쓰지 말 것)."""
import os

import pytest

from tests.sim import DATA
from tf import db, logic
from tf.logic import APPROVED, BLOCKED, NO, PENDING, REJECTED, YES
from tf.roster import parse_roster

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL 없음")


@pytest.fixture(scope="module")
def pool():
    p = db.make_pool(URL)
    db.ensure_schema(p)
    db.ensure_schema(p)  # 두 번 돌려도 됨
    db.ensure_admin(p, "test-admin")
    yield p
    p.close()


def start(pool, name, teams):
    rows = parse_roster((DATA / name).read_bytes())
    assert db.reset(pool, rows, teams).status == APPROVED
    state = db.load_state(pool)
    ids = {s.name.split("(")[0]: s for s in state.students.values()}
    return ids


def test_login(pool):
    ids = start(pool, "쉬움.csv", 3)
    assert db.authenticate(pool, "admin", "test-admin")["is_admin"]
    assert db.authenticate(pool, "admin", "wrong") is None
    assert db.authenticate(pool, ids["학생1"].no, "1234")["name"].startswith("학생1")
    assert db.authenticate(pool, ids["학생1"].no, "") is None
    with pool.connection() as conn:
        h = conn.execute("select password_hash from tf_students where student_no = %s", (ids["학생1"].no,)).fetchone()[0]
    assert h.startswith("$2") and "1234" not in h


def test_hard_scenario_through_db(pool):
    s = start(pool, "어려움.csv", 4)

    def ap(who, t):
        return db.apply(pool, s[who].id, t).status

    def vo(voter, who, v):
        return db.vote(pool, s[voter].id, s[who].no, v).status

    assert ap("학생1", 1) == APPROVED
    assert ap("학생4", 1) == PENDING and vo("학생1", "학생4", YES) == APPROVED
    assert ap("학생7", 1) == PENDING
    assert vo("학생1", "학생7", YES) == PENDING and vo("학생4", "학생7", YES) == APPROVED
    assert ap("학생2", 2) == APPROVED
    assert ap("학생8", 2) == PENDING and vo("학생2", "학생8", YES) == APPROVED
    assert ap("학생5", 2) == PENDING and ap("학생6", 2) == PENDING
    assert vo("학생2", "학생5", YES) == PENDING and vo("학생8", "학생5", YES) == APPROVED
    assert vo("학생2", "학생6", YES) == PENDING and vo("학생8", "학생6", YES) == PENDING
    assert vo("학생5", "학생6", YES) == REJECTED
    assert vo("학생5", "학생6", YES) == BLOCKED  # 이미 처리됨
    assert ap("학생11", 3) == APPROVED
    assert ap("학생15", 3) == PENDING and vo("학생11", "학생15", YES) == APPROVED
    assert ap("학생14", 3) == BLOCKED
    assert ap("학생10", 4) == APPROVED
    assert ap("학생13", 1) == PENDING and vo("학생1", "학생13", YES) == PENDING
    assert vo("학생1", "학생13", NO) == BLOCKED  # 투표는 바꿀 수 없음
    assert db.confirm(pool).status == BLOCKED  # 마감 전
    assert db.close(pool).status == APPROVED
    assert db.close(pool).status == BLOCKED
    assert ap("학생3", 4) == BLOCKED

    state = db.load_state(pool)
    teams = {t: state.team_students(t) for t in state.team_ids}
    un = state.unassigned()
    assert len(un) == 8
    plan = logic.solve(teams, un)
    db.save_proposal(pool, plan, "test")
    assert db.confirm(pool).status == APPROVED
    state = db.load_state(pool)
    assert state.unassigned() == []
    for t in state.team_ids:
        assert logic.team_errors(f"팀{t}", state.team_students(t)) == []


def test_db_rejects_fifth_member_and_double_membership(pool):
    import psycopg
    s = start(pool, "쉬움.csv", 3)
    with pytest.raises(psycopg.errors.CheckViolation):
        with pool.connection() as conn:
            conn.execute("insert into tf_members values (1, 5, %s)", (s["학생1"].id,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with pool.connection() as conn:
            conn.execute("insert into tf_members values (1, 1, %s)", (s["학생1"].id,))
            conn.execute("insert into tf_members values (2, 1, %s)", (s["학생1"].id,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        with pool.connection() as conn:
            conn.execute("insert into tf_members values (1, 1, %s)", (s["학생1"].id,))
            conn.execute("insert into tf_members values (1, 1, %s)", (s["학생2"].id,))
