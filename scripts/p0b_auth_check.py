"""
p0b_auth_check.py — Foundry 토큰 인증 최소 확인 스크립트
읽기 전용. 토큰·전체 hostname 출력 금지.
"""

import os
import sys
from pathlib import Path

# .env 로드
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    print("[ERROR] python-dotenv 미설치")
    sys.exit(1)

import foundry_sdk

# ── 환경변수 수집 ───────────────────────────────────────────────────────────
raw_token = os.environ.get("FOUNDRY_TOKEN", "")
raw_hostname = os.environ.get("FOUNDRY_HOSTNAME", "")

if not raw_token:
    print("[FAIL] FOUNDRY_TOKEN 미설정")
    sys.exit(1)
if not raw_hostname:
    print("[FAIL] FOUNDRY_HOSTNAME 미설정")
    sys.exit(1)


# ── hostname 정규화 ──────────────────────────────────────────────────────────
# SDK는 스킴 없이 bare hostname 또는 https:// 둘 다 받는지 실측으로 확인.
# 일단 https:// 제거 후 시도, 실패 시 원본으로 재시도.
def strip_scheme(h: str) -> str:
    for prefix in ("https://", "http://"):
        if h.startswith(prefix):
            return h[len(prefix) :].rstrip("/")
    return h.rstrip("/")


hostname_bare = strip_scheme(raw_hostname)
has_scheme = raw_hostname.startswith(("https://", "http://"))


# 마스킹: 앞 3자 + ... + TLD 도메인 끝
def mask_hostname(h: str) -> str:
    parts = h.split(".")
    if len(parts) >= 2:
        return f"{h[:3]}…{'.' + '.'.join(parts[-2:])}"
    return f"{h[:3]}…"


masked = mask_hostname(hostname_bare)
print(
    f"[INFO] hostname 형식 진단: {'https:// 스킴 포함 → 제거 후 사용' if has_scheme else 'bare hostname (스킴 없음)'}"
)
print(f"[INFO] hostname 마스킹: {masked}")
print(
    f"[INFO] SDK: foundry-platform-sdk {foundry_sdk.__version__ if hasattr(foundry_sdk, '__version__') else 'loaded'}"
)


# ── 인증 시도 함수 ───────────────────────────────────────────────────────────
def try_auth(hostname: str, label: str):
    print(f"\n[TRY] hostname={label}")
    try:
        client = foundry_sdk.FoundryClient(
            auth=foundry_sdk.UserTokenAuth(raw_token),
            hostname=hostname,
        )
        # 가장 가벼운 읽기: 온톨로지 목록
        # SDK가 ('data', [OntologyV2, ...]) 튜플 형태로 반환함
        raw = list(client.ontologies.Ontology.list())
        if raw and isinstance(raw[0], tuple) and raw[0][0] == "data":
            ontologies = raw[0][1]
        else:
            ontologies = raw
        print(f"[OK] 인증 성공 — 접근 가능 ontology 수: {len(ontologies)}")
        for ont in ontologies[:5]:
            rid = getattr(ont, "rid", "?")
            name = getattr(ont, "display_name", None) or getattr(ont, "api_name", "?")
            short_rid = str(rid)[-12:] if rid != "?" else "?"
            print(f"  - {name}  (rid …{short_rid})")
        if len(ontologies) > 5:
            print(f"  … 외 {len(ontologies) - 5}개")
        return True, None, len(ontologies)
    except Exception as e:
        return False, e, 0


# ── 1차 시도: bare hostname ──────────────────────────────────────────────────
ok, err1, ont_count = try_auth(hostname_bare, mask_hostname(hostname_bare))

if not ok:
    # ── 2차 시도: https:// 포함 (SDK가 스킴 요구할 수도) ─────────────────────
    hostname_with_scheme = f"https://{hostname_bare}"
    ok2, err2, ont_count = try_auth(
        hostname_with_scheme, f"https://{mask_hostname(hostname_bare)}"
    )

    if ok2:
        print(f"\n[RESULT] hostname 형식: https:// 스킴 포함이 필요함")
    else:
        # 에러 분류
        print("\n[RESULT] 인증 실패")
        for label, err in [("bare", err1), ("https://", err2)]:
            msg = str(err)
            code = None
            if hasattr(err, "status_code"):
                code = err.status_code
            elif hasattr(err, "status"):
                code = err.status
            # HTTP status 추출 시도
            import re

            m = re.search(r"\b(401|403|404|429|500)\b", msg)
            if m:
                code = int(m.group(1))

            if code == 401:
                diagnosis = "토큰 무효 또는 만료 (401 Unauthorized) — 토큰 재발급 필요"
            elif code == 403:
                diagnosis = "권한 부족 (403 Forbidden) — 온톨로지 read 스코프 확인"
            elif code == 404:
                diagnosis = "경로 또는 hostname 오류 (404) — hostname 값 재확인"
            elif (
                "Name or service not known" in msg
                or "nodename nor servname" in msg
                or "getaddrinfo" in msg
            ):
                diagnosis = "DNS 실패 — hostname 오타 또는 네트워크 문제"
            elif "SSLError" in msg or "CERTIFICATE" in msg:
                diagnosis = "TLS/SSL 오류 — hostname 형식 또는 네트워크 문제"
            elif "ConnectionRefused" in msg or "Connection refused" in msg:
                diagnosis = "연결 거부 — hostname이 Foundry 서버를 가리키지 않음"
            elif "timed out" in msg.lower() or "timeout" in msg.lower():
                diagnosis = "타임아웃 — 방화벽/VPN 차단 또는 hostname 오류"
            else:
                diagnosis = f"알 수 없는 오류: {msg[:120]}"

            print(f"  [{label}] {diagnosis}")
        sys.exit(2)
else:
    print(f"\n[RESULT] hostname 형식: bare hostname (스킴 없음) 동작")
