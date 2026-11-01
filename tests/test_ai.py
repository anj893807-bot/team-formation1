"""AI 실패 처리(전체 시간 제한, 다음 모델로 넘어감, 검사 실패) 시험. 실제 오픈라우터는 부르지 않는다."""
import json
import time

import pytest

from tests.sim import Sim
from tf import ai


class FakeResp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body


def ok(content):
    return FakeResp(200, {"choices": [{"message": {"content": content}, "finish_reason": "stop"}]})


def scenario():
    s = Sim("쉬움.csv", 3)
    for who, t in [("학생1", 1), ("학생2", 2), ("학생3", 3)]:
        s.apply(who, t)
    return s.teams(), s.state.unassigned()


def test_total_timeout_cuts_slow_model(monkeypatch):
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: time.sleep(5) or ok("{}"))
    start = time.monotonic()
    with pytest.raises(ai.AIError) as e:
        ai.call_model("m", [], "key", lambda el: None, timeout=1.5)
    assert e.value.outcome == "시간 초과"
    assert time.monotonic() - start < 3


def test_falls_through_to_third_model(monkeypatch):
    teams, un = scenario()
    good = {"assignments": []}
    # 쉬움 마감 뒤: 팀1~3에 기획자 1명씩. 남은 5명을 규칙대로 넣는 배정
    by = {s.name.split("(")[0]: s.no for s in un}
    good["assignments"] = [
        {"student_no": by["학생4"], "team": 1}, {"student_no": by["학생5"], "team": 2},
        {"student_no": by["학생6"], "team": 3}, {"student_no": by["학생7"], "team": "팀1"},
        {"student_no": by["학생8"], "team": 2},
    ]
    bad = {"assignments": [{"student_no": s.no, "team": 1} for s in un]}
    responses = iter([
        FakeResp(429, {"error": {"message": "rate limited"}}),
        ok("```json\n" + json.dumps(bad) + "\n```"),
        ok("배정안입니다: " + json.dumps(good)),
    ])
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: next(responses))
    seen = []
    pairs, model, attempts = ai.assign(teams, un, ["a", "b", "c"], "key",
                                       lambda i, m, el, at: seen.append(i), lambda a: None)
    assert [a.outcome for a in attempts] == ["오류", "검사 실패", "성공"]
    assert model == "c" and len(pairs) == 5
    assert set(seen) == {1, 2, 3}


def test_all_fail_returns_none(monkeypatch):
    teams, un = scenario()
    monkeypatch.setattr(ai.requests, "post", lambda *a, **k: ok(""))
    pairs, model, attempts = ai.assign(teams, un, ["a", "b", "c"], "key", lambda *a: None, lambda a: None)
    assert pairs is None and model is None
    assert [a.outcome for a in attempts] == ["빈 응답"] * 3
