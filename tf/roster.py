"""명단 CSV 읽기 (UTF-8, BOM이 있어도 됨)."""
from __future__ import annotations

import csv
import io

from tf.logic import ROLES

COLUMNS = ["student_no", "name", "main_role", "sub_roles"]


class RosterError(ValueError):
    pass


def parse_roster(data: bytes) -> list[dict]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise RosterError("CSV가 UTF-8이 아님")
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        raise RosterError("CSV가 비어 있음")
    header = [c.strip() for c in rows[0]]
    if header != COLUMNS:
        raise RosterError("첫 줄은 student_no,name,main_role,sub_roles 이어야 함")

    out, seen = [], set()
    for line_no, r in enumerate(rows[1:], start=2):
        if len(r) != 4:
            raise RosterError(f"{line_no}번째 줄의 열이 {len(r)}개임(4개여야 함)")
        no, name, main, subs = (c.strip() for c in r)
        if not no or not name:
            raise RosterError(f"{line_no}번째 줄에 학번이나 이름이 없음")
        if no.lower() == "admin":
            raise RosterError(f"{line_no}번째 줄: 학번 admin은 관리자용임")
        if no in seen:
            raise RosterError(f"{line_no}번째 줄: 학번 {no}이 중복됨")
        if main not in ROLES:
            raise RosterError(f"{line_no}번째 줄: 주 역할 '{main}'은 6개 역할이 아님")
        sub_list = [s.strip() for s in subs.split(";") if s.strip()] if subs else []
        for s in sub_list:
            if s not in ROLES:
                raise RosterError(f"{line_no}번째 줄: 보조 역할 '{s}'은 6개 역할이 아님")
        sub_list = [s for s in dict.fromkeys(sub_list) if s != main]
        if len(sub_list) > 2:
            raise RosterError(f"{line_no}번째 줄: 보조 역할이 2개를 넘음")
        seen.add(no)
        out.append({"student_no": no, "name": name, "main_role": main, "sub_roles": sub_list})
    if not out:
        raise RosterError("CSV에 학생이 없음")
    return out
