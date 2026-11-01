"""팀 편성 규칙 (DB와 화면에 의존하지 않는 순수 함수).

DB 계층(tf/db.py)은 트랜잭션 안에서 상태를 읽어 State를 만들고,
여기 있는 decide_* 함수의 결정을 그대로 DB에 반영한다.
테스트(tests/)는 같은 함수를 메모리 상태로 돌린다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

PL = "기획자"
WP = "웹 프로그래머"
ROLES = [PL, WP, "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자"]
REQUIRED = (PL, WP)
MIN_SIZE, MAX_SIZE = 2, 4

PENDING, APPROVED, REJECTED, BLOCKED = "대기", "승인", "거절", "막힘"
YES, NO = "찬성", "거부"

ROLE_ABBR = {
    PL: "PL", WP: "WP", "프론트엔드 디자이너": "FD",
    "테스터": "TT", "보고서 작성자": "RW", "발표자": "PT",
}


@dataclass(frozen=True)
class Student:
    id: int
    no: str
    name: str
    main: str
    subs: tuple[str, ...] = ()

    def can(self, role: str) -> bool:
        return self.main == role or role in self.subs

    @property
    def roles_label(self) -> str:
        if self.subs:
            return f"주: {self.main} / 보조: {', '.join(self.subs)}"
        return f"주: {self.main}"


@dataclass
class Application:
    id: int
    team: int
    student: int
    status: str = PENDING
    reason: str | None = None


@dataclass
class State:
    students: dict[int, Student]
    team_ids: list[int]
    members: dict[int, list[int]]  # 팀 번호 -> 팀원 id (자리 순서)
    apps: list[Application] = field(default_factory=list)  # 지원 순서
    votes: dict[int, dict[int, str]] = field(default_factory=dict)  # 지원 id -> {투표자 id: 찬성/거부}
    closed: bool = False

    def team_of(self, sid: int) -> int | None:
        for t, ids in self.members.items():
            if sid in ids:
                return t
        return None

    def pending_of(self, sid: int) -> Application | None:
        for a in self.apps:
            if a.student == sid and a.status == PENDING:
                return a
        return None

    def app(self, app_id: int) -> Application | None:
        return next((a for a in self.apps if a.id == app_id), None)

    def team_students(self, t: int) -> list[Student]:
        return [self.students[i] for i in self.members.get(t, [])]

    def unassigned(self) -> list[Student]:
        placed = {i for ids in self.members.values() for i in ids}
        return [s for s in self.students.values() if s.id not in placed]


# ---------------------------------------------------------------- 팀 규칙

def unfilled_required(members: list[Student]) -> int:
    """아직 채우지 못한 필수 포함 역할 수(0~2).

    기획자와 웹 프로그래머는 서로 다른 학생이 맡아야 하므로,
    한 학생이 둘 다 할 수 있어도 하나로만 센다(크기 2의 이분 매칭).
    """
    pl = [s for s in members if s.can(PL)]
    wp = [s for s in members if s.can(WP)]
    if any(a.id != b.id for a in pl for b in wp):
        return 0
    if pl or wp:
        return 1
    return 2


def missing_required_text(members: list[Student]) -> str:
    pl = any(s.can(PL) for s in members)
    wp = any(s.can(WP) for s in members)
    if not pl and not wp:
        return f"{PL}와 {WP}"
    if not pl:
        return PL
    if not wp:
        return WP
    return f"{PL}와 {WP}를 맡을 서로 다른 학생"


def join_violation(members: list[Student], applicant: Student) -> str | None:
    """applicant가 팀에 들어가면 어기게 되는 규칙. 없으면 None."""
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m.main == applicant.main for m in members):
        return "같은 주 역할의 팀원이 있음"
    after = members + [applicant]
    if MAX_SIZE - len(after) < unfilled_required(after):
        return f"필수 포함 역할({missing_required_text(after)})을 맡을 학생의 자리가 모자람"
    return None


def team_errors(name: str, members: list[Student]) -> list[str]:
    errors = []
    if not MIN_SIZE <= len(members) <= MAX_SIZE:
        errors.append(f"{name}의 인원이 {len(members)}명임(2~4명이어야 함)")
    mains = [m.main for m in members]
    for role in sorted(set(mains)):
        if mains.count(role) > 1:
            errors.append(f"{name}에 주 역할 '{role}'이 {mains.count(role)}명임")
    if members and unfilled_required(members):
        errors.append(f"{name}에 {missing_required_text(members)}가 없음")
    return errors


# ---------------------------------------------------------------- 지원과 투표

@dataclass
class Decision:
    status: str  # 승인 / 대기 / 거절 / 막힘
    reason: str | None = None

    @property
    def message(self) -> str:
        return f"{self.status}: {self.reason}" if self.reason else self.status


def decide_apply(state: State, sid: int, team: int) -> Decision:
    if state.closed:
        return Decision(BLOCKED, "모집이 마감됨")
    if team not in state.team_ids:
        return Decision(BLOCKED, "없는 팀임")
    if state.team_of(sid) is not None:
        return Decision(BLOCKED, "이미 팀이 있음")
    if state.pending_of(sid) is not None:
        return Decision(BLOCKED, "결과를 기다리는 지원이 있음")
    members = state.team_students(team)
    violation = join_violation(members, state.students[sid])
    if violation:
        return Decision(BLOCKED, violation)
    if not members:
        return Decision(APPROVED, f"팀{team}에 들어감(투표할 팀원이 없음)")
    return Decision(PENDING, f"팀{team}의 팀원 {len(members)}명의 투표를 기다림")


def decide_vote(state: State, app_id: int, voter: int, vote: str) -> Decision:
    """투표를 반영한 뒤 그 지원의 지금 상태를 돌려준다.

    status가 막힘이면 투표를 기록하지 않는다. 그 밖에는 투표를 기록하고,
    status가 승인이면 지원자를 팀에 넣고, 거절이면 지원을 거절한다.
    """
    a = state.app(app_id)
    if a is None:
        return Decision(BLOCKED, "없는 지원임")
    if state.closed:
        return Decision(BLOCKED, "모집이 마감됨")
    if a.status != PENDING:
        return Decision(BLOCKED, f"이미 {a.status}된 지원임")
    members = state.members.get(a.team, [])
    if voter not in members:
        return Decision(BLOCKED, "이 팀의 팀원이 아님")
    if voter in state.votes.get(app_id, {}):
        return Decision(BLOCKED, "이미 투표함")

    votes = {**state.votes.get(app_id, {}), voter: vote}
    if vote == NO:
        return Decision(REJECTED, "팀원이 거부함")
    if all(votes.get(m) == YES for m in members):
        # 승인 직전 재검사: 앞의 지원이 먼저 승인되어 팀이 바뀌었을 수 있다.
        violation = join_violation(state.team_students(a.team), state.students[a.student])
        if violation:
            return Decision(REJECTED, f"승인 직전 검사에서 걸림 - {violation}")
        return Decision(APPROVED, f"팀{a.team}의 팀원이 됨")
    left = sum(1 for m in members if m not in votes)
    return Decision(PENDING, f"팀원 {left}명의 투표를 기다림")


def decide_close(state: State) -> tuple[list[int], list[int]]:
    """마감 때 거절할 지원 id들과 팀에서 뺄 학생 id들(1명뿐인 팀)."""
    rejected = [a.id for a in state.apps if a.status == PENDING]
    removed = [ids[0] for ids in state.members.values() if len(ids) == 1]
    return rejected, removed


# ---------------------------------------------------------------- 배정안 검사와 탐색

def validate_assignment(
    teams: dict[int, list[Student]],
    unassigned: list[Student],
    pairs: list[tuple[str, int]],
) -> list[str]:
    """배정안((학번, 팀 번호) 목록)을 2.5절 4항의 다섯 가지로 검사한다. 오류가 없으면 []."""
    errors: list[str] = []
    by_no = {s.no: s for s in unassigned}
    seen: dict[str, int] = {}
    for no, t in pairs:
        if no not in by_no:
            errors.append(f"팀이 없는 학생이 아닌 학번 {no}을 배정함")
            continue
        if t not in teams:
            errors.append(f"{by_no[no].name}을 없는 팀({t})에 배정함")
            continue
        if no in seen and seen[no] != t:
            errors.append(f"{by_no[no].name}을 두 팀에 배정함(한 학생은 한 팀에만)")
        seen[no] = t
    missing = [s.name for s in unassigned if s.no not in seen]
    if missing:
        errors.append("배정되지 않은 학생이 있음: " + ", ".join(missing))
    if errors:
        return errors
    result = {t: list(ms) for t, ms in teams.items()}
    for no, t in seen.items():
        result[t].append(by_no[no])
    for t in sorted(result):
        errors += team_errors(f"팀{t}", result[t])
    return errors


def solve(teams: dict[int, list[Student]], unassigned: list[Student]) -> dict[int, int] | None:
    """규칙을 모두 지키는 배정을 모든 경우를 따져 찾는다. {학생 id: 팀 번호} 또는 None."""
    cur = {t: list(ms) for t, ms in teams.items()}
    order = sorted(
        unassigned,
        key=lambda s: sum(1 for t in cur if not any(m.main == s.main for m in cur[t])),
    )
    plan: dict[int, int] = {}

    def feasible(left: int) -> bool:
        need = 0
        for ms in cur.values():
            if len(ms) > MAX_SIZE or MAX_SIZE - len(ms) < unfilled_required(ms):
                return False
            need += max(0, MIN_SIZE - len(ms))
        return need <= left

    def go(i: int) -> bool:
        if i == len(order):
            return all(not team_errors("", ms) for ms in cur.values())
        s = order[i]
        for t in sorted(cur, key=lambda t: len(cur[t])):
            ms = cur[t]
            if len(ms) >= MAX_SIZE or any(m.main == s.main for m in ms):
                continue
            ms.append(s)
            plan[s.id] = t
            if feasible(len(order) - i - 1) and go(i + 1):
                return True
            ms.pop()
            del plan[s.id]
        return False

    if not feasible(len(order)):
        return None
    return dict(plan) if go(0) else None


def explain_infeasible(teams: dict[int, list[Student]], unassigned: list[Student]) -> list[str]:
    """규칙을 지키는 배정이 없을 때 관리자에게 보여 줄 이유."""
    reasons = []
    n_teams, total = len(teams), sum(len(m) for m in teams.values()) + len(unassigned)
    if total < MIN_SIZE * n_teams:
        reasons.append(f"학생이 {total}명이라 팀 {n_teams}개를 모두 2명 이상으로 채울 수 없음")
    for t in sorted(teams):
        ms = teams[t]
        if not unfilled_required(ms):
            continue
        room = MAX_SIZE - len(ms)
        for role in REQUIRED:
            if any(m.can(role) for m in ms):
                continue
            cands = [s for s in unassigned if s.can(role) and not any(m.main == s.main for m in ms)]
            if room == 0 or not cands:
                reasons.append(f"팀{t}에 {role}를 맡을 학생이 없음")
        if room < unfilled_required(ms):
            reasons.append(f"팀{t}에 남은 자리({room})가 채워야 할 필수 포함 역할 수보다 적음")
    for role in REQUIRED:
        need = sum(1 for ms in teams.values() if not any(m.can(role) for m in ms))
        have = sum(1 for s in unassigned if s.can(role))
        if need > have:
            reasons.append(f"{role}가 없는 팀이 {need}개인데, 맡을 수 있는 남은 학생은 {have}명임")
    if not reasons:
        reasons.append("모든 경우를 따져 보았으나 인원, 주 역할 중복, 필수 포함 역할을 함께 지키는 배정이 없음")
    return reasons
