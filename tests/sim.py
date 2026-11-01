"""메모리 위에서 tf/db.py와 같은 순서로 결정을 반영하는 시뮬레이터 (테스트 전용)."""
from __future__ import annotations

import csv
import io
from pathlib import Path

from tf import logic
from tf.logic import APPROVED, PENDING, REJECTED, Application, State, Student

DATA = Path(__file__).resolve().parent.parent / "data"


def parse_roster_file(name: str):
    from tf.roster import parse_roster
    return parse_roster((DATA / name).read_bytes())


class Sim:
    def __init__(self, roster_name: str, team_count: int):
        rows = parse_roster_file(roster_name)
        students = {}
        for i, r in enumerate(rows, start=1):
            students[i] = Student(i, r["student_no"], r["name"], r["main_role"], tuple(r["sub_roles"]))
        teams = list(range(1, team_count + 1))
        self.state = State(students, teams, {t: [] for t in teams})
        self.next_app = 1

    def sid(self, short: str) -> int:
        """'학생7' -> id"""
        for s in self.state.students.values():
            if s.name.split("(")[0] == short:
                return s.id
        raise KeyError(short)

    def apply(self, who: str, team: int) -> str:
        sid = self.sid(who)
        d = logic.decide_apply(self.state, sid, team)
        if d.status == APPROVED:
            self.state.apps.append(Application(self.next_app, team, sid, APPROVED))
            self.state.members[team].append(sid)
        elif d.status == PENDING:
            self.state.apps.append(Application(self.next_app, team, sid, PENDING))
        self.next_app += 1
        return d.status

    def vote(self, voter: str, applicant: str, vote: str) -> str:
        vid, aid = self.sid(voter), self.sid(applicant)
        app = self.state.pending_of(aid)
        if app is None:
            return logic.BLOCKED
        d = logic.decide_vote(self.state, app.id, vid, vote)
        if d.status == logic.BLOCKED:
            return d.status
        self.state.votes.setdefault(app.id, {})[vid] = vote
        if d.status in (APPROVED, REJECTED):
            app.status, app.reason = d.status, d.reason
        if d.status == APPROVED:
            self.state.members[app.team].append(app.student)
        return d.status

    def close(self):
        rejected, removed = logic.decide_close(self.state)
        for a in self.state.apps:
            if a.id in rejected:
                a.status = REJECTED
        for t, ids in self.state.members.items():
            self.state.members[t] = [i for i in ids if i not in removed]
        self.state.closed = True

    def teams(self):
        return {t: self.state.team_students(t) for t in self.state.team_ids}


def roster_bytes(text: str) -> bytes:
    return ("﻿" + text).encode("utf-8")


__all__ = ["Sim", "roster_bytes", "io", "csv"]
