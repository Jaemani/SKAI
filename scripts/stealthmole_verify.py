"""
StealthMole API 키 유효성 + 허용 모듈/쿼터 확인 스크립트.

제약:
- 키·토큰 값 출력 금지
- 개인정보 모듈(CL/CDS/CB/CDF) 호출 금지
- /user/quotas 는 차감 없음
- GM 검색은 1회만 (limit=1)
"""

import os
import sys
import uuid
import time
import json
from pathlib import Path

import httpx
import jwt  # PyJWT
from dotenv import load_dotenv

# ── 환경변수 로드 ──────────────────────────────────────────────
load_dotenv(Path(__file__).parent.parent / ".env")

ACCESS_KEY = os.environ.get("STEALTHMOLE_ACCESS_KEY", "")
SECRET_KEY = os.environ.get("STEALTHMOLE_SECRET_KEY", "")

if not ACCESS_KEY or not SECRET_KEY:
    print("[FAIL] .env에 STEALTHMOLE_ACCESS_KEY / STEALTHMOLE_SECRET_KEY 없음")
    sys.exit(1)

BASE_URL = "https://hackathon.stealthmole.com"


# ── JWT 생성 헬퍼 (요청마다 새 nonce) ─────────────────────────
def make_jwt() -> str:
    payload = {
        "access_key": ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
        "iat": int(time.time()),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def headers() -> dict:
    return {"Authorization": f"Bearer {make_jwt()}"}


# ── 1. /user/quotas (차감 없음) ────────────────────────────────
print("=== /user/quotas 조회 ===")
try:
    resp = httpx.get(f"{BASE_URL}/user/quotas", headers=headers(), timeout=15)
    print(f"상태코드: {resp.status_code}")

    if resp.status_code == 200:
        data = resp.json()
        print("[KEY-OK] 인증 성공\n")
        # 허용 모듈 목록 출력 (수치 포함, 키 값 제외)
        quotas = data if isinstance(data, list) else data.get("data", data)
        print("허용 모듈 목록:")
        for item in quotas if isinstance(quotas, list) else [quotas]:
            # 키/토큰 필드는 출력하지 않음
            safe = {
                k: v
                for k, v in (item.items() if isinstance(item, dict) else {}.items())
                if k.lower() not in ("access_key", "secret_key", "token")
            }
            print(f"  {json.dumps(safe, ensure_ascii=False)}")
    else:
        print(f"[FAIL({resp.status_code})] 응답: {resp.text[:300]}")
        sys.exit(1)

except Exception as e:
    print(f"[FAIL] 요청 예외: {e}")
    sys.exit(1)

# ── 2. GM 검색 1건 (정부 위협 목록 — 개인정보 아님) ────────────
print("\n=== GM /gm/search?query=&limit=1 ===")
try:
    resp_gm = httpx.get(
        f"{BASE_URL}/gm/search",
        params={"query": "", "limit": 1},
        headers=headers(),
        timeout=15,
    )
    print(f"상태코드: {resp_gm.status_code}")

    if resp_gm.status_code == 200:
        gm_data = resp_gm.json()
        items = gm_data.get("data", [])
        if items:
            sample = items[0]
            print("GM 응답 실측 필드(첫 번째 레코드):")
            for k, v in sample.items():
                # proof_url 값은 그대로 보여도 무방 (공개 URL), 값만 type 표시
                print(f"  {k}: {type(v).__name__} = {repr(v)[:120]}")
        else:
            print("data 배열 비어있음 (쿼터 있으나 현재 결과 없음)")
        # totalCount / cursor 등 메타 출력
        meta = {k: v for k, v in gm_data.items() if k != "data"}
        if meta:
            print(f"  메타: {json.dumps(meta, ensure_ascii=False)}")
    else:
        print(f"GM 호출 실패 — 상태코드 {resp_gm.status_code}: {resp_gm.text[:300]}")

except Exception as e:
    print(f"[FAIL] GM 요청 예외: {e}")
