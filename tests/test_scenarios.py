"""부록의 연습용 시나리오 3개를 규칙 엔진으로 돌려 기대 결과와 비교한다."""
import pytest

from tests.sim import Sim
from tf import logic
from tf.logic import APPROVED, BLOCKED, NO, PENDING, REJECTED, YES


def assign_and_check(sim: Sim):
    teams = sim.teams()
    unassigned = sim.state.unassigned()
    plan = logic.solve(teams, unassigned)
    assert plan is not None, logic.explain_infeasible(teams, unassigned)
    pairs = [(sim.state.students[sid].no, t) for sid, t in plan.items()]
    assert logic.validate_assignment(teams, unassigned, pairs) == []
    return plan


def test_easy():
    s = Sim("쉬움.csv", 3)
    assert [s.apply("학생1", 1), s.apply("학생2", 2), s.apply("학생3", 3)] == [APPROVED] * 3
    assert s.apply("학생4", 1) == PENDING
    assert s.vote("학생1", "학생4", YES) == APPROVED
    assert s.apply("학생5", 2) == PENDING
    assert s.vote("학생2", "학생5", YES) == APPROVED
    assert s.apply("학생7", 1) == PENDING
    assert s.vote("학생1", "학생7", YES) == PENDING
    assert s.vote("학생4", "학생7", NO) == REJECTED
    assert s.apply("학생6", 3) == PENDING
    assert s.vote("학생3", "학생6", YES) == APPROVED
    s.close()
    assert {x.name[:3] for x in s.state.unassigned()} == {"학생7", "학생8"}
    assign_and_check(s)


def test_medium():
    s = Sim("중간.csv", 4)
    assert s.apply("학생1", 1) == APPROVED
    assert s.apply("학생5", 1) == PENDING
    assert s.vote("학생1", "학생5", YES) == APPROVED
    assert s.apply("학생2", 1) == BLOCKED  # 주 역할 중복
    assert s.apply("학생6", 2) == APPROVED
    assert s.apply("학생9", 2) == PENDING
    assert s.vote("학생6", "학생9", YES) == APPROVED
    assert s.apply("학생10", 2) == PENDING
    assert s.vote("학생6", "학생10", YES) == PENDING
    assert s.vote("학생9", "학생10", NO) == REJECTED
    assert s.apply("학생3", 3) == APPROVED
    assert s.apply("학생11", 4) == APPROVED
    assert s.apply("학생12", 4) == PENDING
    assert s.apply("학생12", 3) == BLOCKED  # 결과를 기다리는 지원이 있음
    s.close()
    app12 = [a for a in s.state.apps if a.student == s.sid("학생12")]
    assert app12[0].status == REJECTED
    assert s.state.team_of(s.sid("학생3")) is None
    assert s.state.team_of(s.sid("학생11")) is None
    assert s.apply("학생2", 3) == BLOCKED  # 마감
    assign_and_check(s)


def test_hard():
    s = Sim("어려움.csv", 4)
    assert s.apply("학생1", 1) == APPROVED
    assert s.apply("학생4", 1) == PENDING
    assert s.vote("학생1", "학생4", YES) == APPROVED
    assert s.apply("학생7", 1) == PENDING
    assert s.vote("학생1", "학생7", YES) == PENDING
    assert s.vote("학생4", "학생7", YES) == APPROVED
    assert s.apply("학생2", 2) == APPROVED
    assert s.apply("학생8", 2) == PENDING
    assert s.vote("학생2", "학생8", YES) == APPROVED
    assert s.apply("학생5", 2) == PENDING
    assert s.apply("학생6", 2) == PENDING
    assert s.vote("학생2", "학생5", YES) == PENDING
    assert s.vote("학생8", "학생5", YES) == APPROVED
    assert s.vote("학생2", "학생6", YES) == PENDING
    assert s.vote("학생8", "학생6", YES) == PENDING
    assert s.vote("학생5", "학생6", YES) == REJECTED  # 승인 직전 검사
    assert s.apply("학생11", 3) == APPROVED
    assert s.apply("학생15", 3) == PENDING
    assert s.vote("학생11", "학생15", YES) == APPROVED
    assert s.apply("학생14", 3) == BLOCKED  # 필수 포함 역할 자리
    assert s.apply("학생10", 4) == APPROVED
    assert s.apply("학생13", 1) == PENDING
    assert s.vote("학생1", "학생13", YES) == PENDING
    s.close()
    assert s.state.team_of(s.sid("학생10")) is None
    assert len(s.state.unassigned()) == 8
    assign_and_check(s)


def test_slot_examples_from_notice():
    st = logic.Student
    both = st(1, "1", "a", logic.PL, (logic.WP,))
    assert logic.unfilled_required([both]) == 1
    tt, pt = st(2, "2", "b", "테스터"), st(3, "3", "c", "발표자")
    rw = st(4, "4", "d", "보고서 작성자")
    assert logic.join_violation([tt, pt], rw).startswith("필수 포함 역할")


def test_infeasible_reports_reason():
    st = logic.Student
    full = [st(i, str(i), f"s{i}", r) for i, r in enumerate(["테스터", "발표자", "보고서 작성자", "프론트엔드 디자이너"])]
    teams = {1: full, 2: []}
    left = [st(10, "10", "p", logic.PL), st(11, "11", "w", logic.WP)]
    assert logic.solve(teams, left) is None
    reasons = logic.explain_infeasible(teams, left)
    assert any("팀1에 기획자를 맡을 학생이 없음" in r for r in reasons)


@pytest.mark.parametrize("bad", [
    [("2026307", 1), ("2026307", 2)],
    [("9999999", 1)],
])
def test_validate_rejects_bad_pairs(bad):
    s = Sim("쉬움.csv", 3)
    errs = logic.validate_assignment(s.teams(), s.state.unassigned(), bad)
    assert errs
