"""오픈라우터로 남은 학생의 배정안을 받는다.

- 모델 1차, 2차, 3차를 차례로 부른다. 앞 모델이 실패하면 다음 모델로 넘어간다.
- 실패: 응답 없음/120초 넘음(전체 대기 시간), HTTP 오류(429 등), 빈 응답, JSON 형식 오류, 규칙 검사 실패.
- 120초는 요청을 보낸 때부터 응답을 다 받을 때까지의 전체 시간이다.
  requests의 timeout은 소켓 한 번 읽기만 재므로, 별도 스레드로 보내고 바깥에서 시계로 끊는다.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import requests

from tf import logic
from tf.logic import MAX_SIZE, MIN_SIZE, PL, WP, Student

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL_TIMEOUT = 120.0
# 2026-11-01 실제 호출 결과로 고름. 1, 2차는 검사를 통과한 배정안을 냈음(58초, 78초).
# 3차는 그날 429였으나 다른 제공사라 1, 2차와 함께 막힐 가능성이 낮음.
# 바꾸려면 Secrets의 OPENROUTER_MODELS에 쉼표로 넣는다(커밋 없이 바뀜).
DEFAULT_MODELS = [
    "dots-studio/dots-3-note-preview:free",
    "nvidia/nemotron-3.5-lightning:free",
    "google/gemma-4-31b-it:free",
]


class AIError(Exception):
    def __init__(self, outcome: str, detail: str):
        super().__init__(f"{outcome}: {detail}")
        self.outcome = outcome
        self.detail = detail


@dataclass
class Attempt:
    attempt: int
    model: str
    outcome: str
    detail: str
    elapsed: float


def _student_line(s: Student) -> str:
    subs = ", ".join(s.subs) if s.subs else "없음"
    return f'- 학번 {s.no}, 이름 {s.name}, 주 역할 {s.main}, 보조 역할 {subs}'


def build_messages(teams: dict[int, list[Student]], unassigned: list[Student]) -> list[dict]:
    team_text = []
    for t in sorted(teams):
        ms = teams[t]
        body = "\n".join(_student_line(s) for s in ms) if ms else "- (팀원 없음)"
        team_text.append(f"팀{t} ({len(ms)}명, 남은 자리 {MAX_SIZE - len(ms)})\n{body}")
    rules = f"""규칙:
1. 팀이 없는 학생만 팀에 넣는다. 이미 팀에 있는 학생은 옮기지 않는다.
2. 팀이 없는 학생은 빠짐없이, 한 팀에만 배정한다.
3. 배정 뒤 모든 팀은 {MIN_SIZE}~{MAX_SIZE}명이어야 한다(빈 팀 없음).
4. 한 팀 안에서 주 역할이 같은 학생이 2명 이상이면 안 된다(보조 역할은 중복을 따지지 않음).
5. 모든 팀에는 {PL}를 맡을 학생과 {WP}를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 한다.
   주 역할이나 보조 역할로 맡을 수 있다. 한 학생이 둘 다 할 수 있어도 한 역할만 맡는다."""
    user = f"""학생 팀 프로젝트의 팀 배정을 도와줘.

{rules}

지금 팀 구성:
{chr(10).join(team_text)}

팀이 없는 학생:
{chr(10).join(_student_line(s) for s in unassigned)}

팀이 없는 학생 {len(unassigned)}명을 모두 배정해. 규칙을 하나씩 확인한 뒤,
설명 없이 다음 JSON 하나만 출력해. team은 팀 번호(정수)야.
{{"assignments": [{{"student_no": "학번", "team": 1}}]}}"""
    return [
        {"role": "system", "content": "너는 규칙을 정확히 지키는 팀 배정 도우미다. 요청한 JSON만 출력한다."},
        {"role": "user", "content": user},
    ]


def parse_pairs(content: str) -> list[tuple[str, int]]:
    text = re.sub(r"```(?:json)?", "", content)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise AIError("형식 오류", "응답에 JSON이 없음")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise AIError("형식 오류", f"JSON을 읽을 수 없음({e.msg})")
    items = data.get("assignments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise AIError("형식 오류", "assignments 목록이 없음")
    pairs = []
    for it in items:
        try:
            no = str(it["student_no"]).strip()
            team = int(re.sub(r"\D", "", str(it["team"])))
        except (KeyError, TypeError, ValueError):
            raise AIError("형식 오류", f"항목을 읽을 수 없음: {str(it)[:80]}")
        pairs.append((no, team))
    return pairs


def call_model(
    model: str,
    messages: list[dict],
    api_key: str,
    on_tick: Callable[[float], None],
    timeout: float = MODEL_TIMEOUT,
) -> str:
    """모델 하나를 불러 응답 본문을 돌려준다. timeout은 전체 대기 시간."""
    box: dict = {}

    def worker():
        try:
            box["resp"] = requests.post(
                OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Title": "Team Formation App",
                },
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": 0.2,
                    "max_tokens": 8000,
                    "reasoning": {"effort": "low"},
                },
                timeout=(10, timeout + 5),
            )
        except Exception as e:  # 연결 실패 등
            box["err"] = e

    th = threading.Thread(target=worker, daemon=True)
    start = time.monotonic()
    th.start()
    while th.is_alive():
        elapsed = time.monotonic() - start
        if elapsed >= timeout:
            # 스레드는 버리고(daemon) 다음 모델로 넘어간다.
            raise AIError("시간 초과", f"{timeout:.0f}초 안에 응답을 다 받지 못함")
        on_tick(elapsed)
        th.join(min(1.0, timeout - elapsed))

    if "err" in box:
        raise AIError("오류", type(box["err"]).__name__)
    resp = box["resp"]
    if resp.status_code != 200:
        msg = ""
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        raise AIError("오류", f"HTTP {resp.status_code} {msg}".strip()[:300])
    try:
        data = resp.json()
    except ValueError:
        raise AIError("오류", "응답이 JSON이 아님")
    if data.get("error"):
        raise AIError("오류", str(data["error"].get("message", data["error"]))[:300])
    choices = data.get("choices") or []
    content = (choices[0].get("message", {}).get("content") if choices else None) or ""
    if not content.strip():
        reason = choices[0].get("finish_reason") if choices else None
        raise AIError("빈 응답", f"내용이 비어 있음(finish_reason={reason})")
    return content


def assign(
    teams: dict[int, list[Student]],
    unassigned: list[Student],
    models: list[str],
    api_key: str | None,
    on_progress: Callable[[int, str, float, list[Attempt]], None],
    on_attempt: Callable[[Attempt], None],
) -> tuple[list[tuple[str, int]] | None, str | None, list[Attempt]]:
    """검사를 통과한 배정안, 그 배정안을 낸 모델 이름, 시도 기록을 돌려준다.

    세 모델이 모두 실패하면 배정안과 모델 이름은 None이다.
    """
    messages = build_messages(teams, unassigned)
    attempts: list[Attempt] = []
    for i, model in enumerate(models[:3], start=1):
        start = time.monotonic()
        on_progress(i, model, 0.0, attempts)
        try:
            if not api_key:
                raise AIError("오류", "OPENROUTER_API_KEY가 없음")
            content = call_model(model, messages, api_key, lambda el: on_progress(i, model, el, attempts))
            pairs = parse_pairs(content)
            errors = logic.validate_assignment(teams, unassigned, pairs)
            if errors:
                raise AIError("검사 실패", "; ".join(errors))
        except AIError as e:
            a = Attempt(i, model, e.outcome, e.detail, time.monotonic() - start)
            attempts.append(a)
            on_attempt(a)
            continue
        a = Attempt(i, model, "성공", f"{len(pairs)}명 배정, 검사 다섯 가지 통과", time.monotonic() - start)
        attempts.append(a)
        on_attempt(a)
        return pairs, model, attempts
    return None, None, attempts
