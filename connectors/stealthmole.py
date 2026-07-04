"""StealthMole 커넥터 — 다크웹/위협 인텔 OSINT → NewsEvent(저신뢰) → 온톨로지.

파이프: GM(정부위협) · RM(랜섬웨어) · LM(기업위협) 동기 검색
        → NewsEvent(confidence=0.25, source="stealthmole")
        + mentions→ Region 키워드 링킹(GDELT 별칭 사전 재사용).

# 가드레일 (절대)
- 개인정보 모듈 CL·CDS·CB·CDF·DT: 함수 자체를 만들지 않음.
- 응답에 이메일/비밀번호 패턴이 포함된 레코드는 DB 저장 없이 skip.
- 키·토큰 값 출력 금지. NDA 상세(엔드포인트 목록·쿼터 수치) public 커밋 금지.

# 미구현 (1단계)
# TODO: TT 비동기 모듈 — /tt/search/{indicator}/target + polling 필요.
#       indicator별 1회 차감 구조라 별도 폴링 루프 설계 후 2단계에서 구현.

인증: 요청마다 새 JWT(HS256, nonce=uuid4, iat=현재UTC초) — JWT 재사용 시 401.
속도 제한: 호출 간 2초 강제(코드 레벨, 우회 아님).
에러 처리: 401 → JWT 재생성 1회 재시도, 426(쿼터) → 백오프·건너뜀.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from pathlib import Path
from typing import Optional

import httpx
import jwt as pyjwt  # PyJWT
from dotenv import load_dotenv

from connectors.gdelt import REGION_ALIASES
from ontology.model import KADIZ_REGION, NEWS_MAX_CONFIDENCE, NewsEvent
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

# ── 환경변수 ─────────────────────────────────────────────────────────────────
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_ACCESS_KEY = os.environ.get("STEALTHMOLE_ACCESS_KEY", "")
_SECRET_KEY = os.environ.get("STEALTHMOLE_SECRET_KEY", "")
BASE_URL = os.environ.get("STEALTHMOLE_BASE_URL", "")  # NDA: 값은 .env에서만

TIMEOUT = 20
SM_MIN_REQUEST_INTERVAL = 2.0  # 예의상 호출 간 최소 2초

# ── 검색 상수 ─────────────────────────────────────────────────────────────────
SM_DEFAULT_LIMIT = 20  # 사이클당 모듈별 기본 limit

# 항공·공역 관련 키워드(설정 가능). 비우면 전체 최신 목록.
# GM/LM은 title 기반 위협 게시글, RM은 victim/sector 기반 랜섬웨어 피해.
SM_AVIATION_KEYWORDS: list[str] = [
    "aviation",
    "airport",
    "airline",
    "defense",
    "Korea",
    "military",
    "aerospace",
    "airspace",
]

# ── 개인정보 패턴 (방어적 skip) ───────────────────────────────────────────────
# CL·CDS·CB·CDF 모듈 응답이 혼입되더라도 저장 전 제거.
_PII_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9_.+-]{2,}@[a-zA-Z0-9-]{2,}\.[a-zA-Z]{2,}", re.IGNORECASE
)
_PII_PASSWORD_RE = re.compile(
    r"(?i)(?:password|passwd|pwd)\s*[=:]\s*\S+", re.IGNORECASE
)

# ── 속도 제한 상태 ────────────────────────────────────────────────────────────
_last_request_ts = 0.0


def _rate_limit_guard() -> None:
    """직전 요청 이후 SM_MIN_REQUEST_INTERVAL(2초)이 안 지났으면 그만큼 sleep."""
    global _last_request_ts
    now = time.monotonic()
    elapsed = now - _last_request_ts
    if _last_request_ts > 0 and elapsed < SM_MIN_REQUEST_INTERVAL:
        wait = SM_MIN_REQUEST_INTERVAL - elapsed
        time.sleep(wait)
    _last_request_ts = time.monotonic()


# ── JWT 인증 ──────────────────────────────────────────────────────────────────


def _make_jwt() -> str:
    """요청마다 새 JWT(nonce=uuid4, iat=현재UTC초). JWT 재사용 시 401."""
    payload = {
        "access_key": _ACCESS_KEY,
        "nonce": str(uuid.uuid4()),
        "iat": int(time.time()),
    }
    return pyjwt.encode(payload, _SECRET_KEY, algorithm="HS256")


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_make_jwt()}"}


# ── 개인정보 레코드 검사 ──────────────────────────────────────────────────────


def _has_pii(record: dict) -> bool:
    """레코드 직렬화 텍스트에 이메일·비밀번호 패턴이 있으면 True(skip 대상)."""
    text = str(record)
    return bool(_PII_EMAIL_RE.search(text) or _PII_PASSWORD_RE.search(text))


# ── 지역 별칭 링킹 (gdelt 사전 재사용) ──────────────────────────────────────


def _match_region_aliases(text: str) -> list[str]:
    """텍스트에서 KADIZ 별칭 매칭 → 매칭 리스트. gdelt.REGION_ALIASES 재사용."""
    if not text:
        return []
    low = text.lower()
    return [a for a in REGION_ALIASES["KADIZ"] if a.lower() in low]


# ── NewsEvent ID ──────────────────────────────────────────────────────────────


def _sm_news_id(module: str, record_id: str) -> str:
    """StealthMole NewsEvent PK 규약: sm-{모듈}-{id}."""
    return f"sm-{module}-{record_id}"


def _sm_source_url(module: str, record_id: str, proof_url: str) -> str:
    """proof_url이 무료권한 메시지면 내부 URI, 아니면 원본 URL."""
    if not proof_url or proof_url.strip().lower().startswith("not supported"):
        return f"stealthmole://{module}/{record_id}"
    return proof_url


# ── 단일 HTTP 호출 ────────────────────────────────────────────────────────────


def _get(
    client: httpx.Client,
    path: str,
    params: dict,
    *,
    retry_on_401: bool = True,
) -> Optional[dict]:
    """StealthMole API 1회 호출. 401 시 JWT 재생성 1회 재시도, 426 시 None 반환."""
    if not BASE_URL:
        print("[stealthmole] STEALTHMOLE_BASE_URL 미설정 — 건너뜀")
        return None
    _rate_limit_guard()
    try:
        resp = client.get(
            f"{BASE_URL}{path}",
            params=params,
            headers=_auth_headers(),
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as e:
        print(f"[stealthmole] 연결 오류({path}): {e!r}")
        return None

    print(f"[stealthmole] HTTP {resp.status_code} {path} params={params}")

    if resp.status_code == 200:
        try:
            return resp.json()
        except Exception as e:
            print(f"[stealthmole] JSON 파싱 실패: {e!r}")
            return None

    if resp.status_code == 401 and retry_on_401:
        # JWT 재사용·만료 → 재생성 후 1회 재시도
        print("[stealthmole] 401 → JWT 재생성 후 1회 재시도")
        time.sleep(1.0)
        return _get(client, path, params, retry_on_401=False)

    if resp.status_code == 426:
        print(f"[stealthmole] 426 쿼터 초과({path}) — 이번 사이클 건너뜀(우회 안 함)")
        return None

    print(f"[stealthmole] 비정상 응답({resp.status_code}): {resp.text[:120]!r}")
    return None


# ── GM 매핑 ───────────────────────────────────────────────────────────────────


def gm_record_to_news(record: dict) -> Optional[NewsEvent]:
    """GM 레코드 1건 → NewsEvent. 개인정보 패턴 포함 시 None(skip)."""
    if _has_pii(record):
        print(f"[stealthmole][GM] PII 패턴 감지 → skip id={record.get('id')}")
        return None
    record_id = str(record.get("id", ""))
    proof_url = record.get("proof_url", "") or ""
    source_url = _sm_source_url("gm", record_id, proof_url)
    title = record.get("title") or ""
    author = record.get("author") or ""
    ts = int(record.get("detection_datetime") or int(time.time()))
    aliases = _match_region_aliases(title)
    return NewsEvent(
        id=_sm_news_id("gm", record_id),
        source="stealthmole",
        source_url=source_url,
        ts=ts,
        title=title,
        summary="",
        confidence=min(0.25, NEWS_MAX_CONFIDENCE),
        entities=aliases,
        attrs={
            "module": "gm",
            "author": author,
            "sm_id": record_id,
        },
    )


# ── RM 매핑 ───────────────────────────────────────────────────────────────────


def rm_record_to_news(record: dict) -> Optional[NewsEvent]:
    """RM 레코드 1건 → NewsEvent. 개인정보 패턴 포함 시 None(skip)."""
    if _has_pii(record):
        print(f"[stealthmole][RM] PII 패턴 감지 → skip id={record.get('id')}")
        return None
    record_id = str(record.get("id", ""))
    proof_url = record.get("proof_url", "") or ""
    source_url = _sm_source_url("rm", record_id, proof_url)
    victim = record.get("victim") or ""
    attack_group = record.get("attack_group") or ""
    sector = record.get("sector") or ""
    country = record.get("country") or ""
    site = record.get("site") or ""
    ts = int(record.get("detection_datetime") or int(time.time()))
    title = f"{victim} — {attack_group}" if victim or attack_group else "(RM 레코드)"
    summary_parts = [p for p in [sector, country] if p]
    summary = " / ".join(summary_parts)
    aliases = _match_region_aliases(title + " " + summary)
    return NewsEvent(
        id=_sm_news_id("rm", record_id),
        source="stealthmole",
        source_url=source_url,
        ts=ts,
        title=title,
        summary=summary,
        confidence=min(0.25, NEWS_MAX_CONFIDENCE),
        entities=aliases,
        attrs={
            "module": "rm",
            "attack_group": attack_group,
            "site": site,
            "country": country,
            "sm_id": record_id,
        },
    )


# ── LM 매핑 ───────────────────────────────────────────────────────────────────


def lm_record_to_news(record: dict) -> Optional[NewsEvent]:
    """LM 레코드 1건 → NewsEvent. 개인정보 패턴 포함 시 None(skip)."""
    if _has_pii(record):
        print(f"[stealthmole][LM] PII 패턴 감지 → skip id={record.get('id')}")
        return None
    record_id = str(record.get("id", ""))
    proof_url = record.get("proof_url", "") or ""
    source_url = _sm_source_url("lm", record_id, proof_url)
    title = record.get("title") or ""
    author = record.get("author") or ""
    ts = int(record.get("detection_datetime") or int(time.time()))
    aliases = _match_region_aliases(title)
    return NewsEvent(
        id=_sm_news_id("lm", record_id),
        source="stealthmole",
        source_url=source_url,
        ts=ts,
        title=title,
        summary="",
        confidence=min(0.25, NEWS_MAX_CONFIDENCE),
        entities=aliases,
        attrs={
            "module": "lm",
            "author": author,
            "sm_id": record_id,
        },
    )


# ── 모듈별 검색 ───────────────────────────────────────────────────────────────


def _search_module(
    client: httpx.Client,
    module: str,
    query: str = "",
    limit: int = SM_DEFAULT_LIMIT,
) -> list[dict]:
    """GM·RM·LM 공통 /{module}/search 호출 → data 리스트."""
    data = _get(
        client,
        f"/{module}/search",
        {"query": query, "limit": limit},
    )
    if data is None:
        return []
    records = data.get("data") or []
    total = data.get("totalCount", "?")
    print(f"[stealthmole][{module.upper()}] totalCount={total} 반환={len(records)}건")
    return records


def fetch_gm(
    client: httpx.Client,
    query: str = "",
    limit: int = SM_DEFAULT_LIMIT,
) -> list[dict]:
    """GM(정부위협) 검색 → 원시 레코드 리스트."""
    return _search_module(client, "gm", query, limit)


def fetch_rm(
    client: httpx.Client,
    query: str = "",
    limit: int = SM_DEFAULT_LIMIT,
) -> list[dict]:
    """RM(랜섬웨어 피해) 검색 → 원시 레코드 리스트."""
    return _search_module(client, "rm", query, limit)


def fetch_lm(
    client: httpx.Client,
    query: str = "",
    limit: int = SM_DEFAULT_LIMIT,
) -> list[dict]:
    """LM(기업위협) 검색 → 원시 레코드 리스트."""
    return _search_module(client, "lm", query, limit)


# ── ingest ────────────────────────────────────────────────────────────────────


def ingest(store: LocalOntologyStore) -> dict[str, int]:
    """1 ingest 사이클: GM·RM·LM 각 1호출(항공 키워드) → NewsEvent + mentions 링크.

    각 모듈: 빈 쿼리로 최신 limit=20건. 지역 별칭 매칭 시 mentions→KADIZ Region.
    반환: {module: write_count, ...} 딕셔너리.
    """
    counts: dict[str, int] = {"gm": 0, "rm": 0, "lm": 0}

    with httpx.Client() as client:
        # GM
        for record in fetch_gm(client):
            nv = gm_record_to_news(record)
            if nv is None:
                continue
            mentions = [("Region", KADIZ_REGION.id)] if nv.entities else []
            store.write_newsevent(nv, mentions=mentions)
            counts["gm"] += 1

        # RM (2초 간격은 _get 내 _rate_limit_guard가 자동 강제)
        for record in fetch_rm(client):
            nv = rm_record_to_news(record)
            if nv is None:
                continue
            mentions = [("Region", KADIZ_REGION.id)] if nv.entities else []
            store.write_newsevent(nv, mentions=mentions)
            counts["rm"] += 1

        # LM
        for record in fetch_lm(client):
            nv = lm_record_to_news(record)
            if nv is None:
                continue
            mentions = [("Region", KADIZ_REGION.id)] if nv.entities else []
            store.write_newsevent(nv, mentions=mentions)
            counts["lm"] += 1

    return counts


def main() -> None:
    db_path = os.environ.get("SKAI_DB", DEFAULT_DB)
    store = LocalOntologyStore(db_path)
    store.write_region(KADIZ_REGION)
    counts = ingest(store)
    total = sum(counts.values())
    print(
        f"[stealthmole] NewsEvent write: GM={counts['gm']} RM={counts['rm']} LM={counts['lm']} "
        f"합계={total} 누적={store.counts()}"
    )


if __name__ == "__main__":
    main()
