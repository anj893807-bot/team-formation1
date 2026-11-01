"""Supabase DB 일시정지를 막기 위해 가벼운 질의를 보낸다.

GitHub Actions(.github/workflows/keepalive.yml)가 매일 실행한다.
DATABASE_URL은 환경 변수로 받으며, 값은 출력하지 않는다.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tf import db  # noqa: E402

url = os.environ.get("DATABASE_URL")
if not url:
    print("DATABASE_URL: 없음")
    sys.exit(1)

pool = db.make_pool(url)
db.touch(pool)
pool.close()
print("DB 질의 완료")
