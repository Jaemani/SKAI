"""METAR 커넥터 — aviationweather.gov 실황 → WeatherState → 온톨로지.

파이프: aviationweather.gov/api/data/metar(ids=RKSI) → WeatherState
        (region_ref, wind·visibility·ceiling·flight_category, rawOb 원문).

P0A gotcha 반영:
  - `visib` 단위 = statute miles → 필드명 visibility_sm 로 단위 명시.
  - `clouds[*].base` 단위 = **피트** → ceiling_ft 로 명시. 최저 BKN/OVC/VV 층 = 실링.
  - `wdir`가 'VRB'(가변풍)일 수 있음 → None + attrs 기록.

폴링 주기(architecture.md): METAR 30분. 단 검증 실행은 1회 ingest.
"""

from __future__ import annotations

import os
from typing import Optional

import httpx

from ontology.model import KADIZ_REGION, WeatherState
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

METAR_URL = "https://aviationweather.gov/api/data/metar"
TIMEOUT = 20

METAR_POLL_INTERVAL = 30 * 60  # architecture.md: METAR 30분
DEFAULT_ICAOS = "RKSI"  # 인천국제공항(KADIZ 내). 환경변수 METAR_ICAOS로 교체 가능.

# 실링 판정 대상 운량 코드(하늘을 덮는 층): BKN/OVC/VV(수직시정).
_CEILING_COVERS = {"BKN", "OVC", "VV"}


def source_url_for(icaos: str) -> str:
    return f"{METAR_URL}?ids={icaos}&format=json"


def fetch_metar(client: httpx.Client, icaos: str) -> list[dict]:
    """METAR 1회 호출 → 레코드 리스트."""
    resp = client.get(
        METAR_URL, params={"ids": icaos, "format": "json"}, timeout=TIMEOUT
    )
    print(f"[metar] HTTP {resp.status_code} ids={icaos}")
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def _parse_visibility_sm(visib) -> Optional[float]:
    """visib(float 또는 '10+' 같은 문자열) → statute miles(float)."""
    if visib is None:
        return None
    if isinstance(visib, (int, float)):
        return float(visib)
    s = str(visib).strip().rstrip("+")
    try:
        return float(s)
    except ValueError:
        return None


def _parse_ceiling_ft(clouds) -> Optional[int]:
    """clouds([{cover, base(ft)}]) → 실링(최저 BKN/OVC/VV base, ft). 없으면 None(무제한)."""
    if not clouds:
        return None
    bases = [
        c.get("base")
        for c in clouds
        if c.get("cover") in _CEILING_COVERS and c.get("base") is not None
    ]
    return int(min(bases)) if bases else None


def record_to_weatherstate(rec: dict, region_ref: str, source_url: str) -> WeatherState:
    """METAR 레코드 1건 → WeatherState (순수 매핑 — 테스트 대상).

    단위: visibility_sm=statute miles, ceiling_ft=피트, wind_speed_kt=노트 (필드명 명시).
    """
    station = rec.get("icaoId", "")
    ts = int(rec.get("obsTime") or 0)

    wdir_raw = rec.get("wdir")
    wind_dir = wdir_raw if isinstance(wdir_raw, int) else None  # 'VRB' 등은 None

    return WeatherState(
        id=f"wx-{station}-{ts}",
        region_ref=region_ref,
        ts=ts,
        station=station,
        lat=rec.get("lat"),
        lon=rec.get("lon"),
        wind_dir=wind_dir,
        wind_speed_kt=(
            float(rec["wspd"]) if isinstance(rec.get("wspd"), (int, float)) else None
        ),
        visibility_sm=_parse_visibility_sm(rec.get("visib")),
        ceiling_ft=_parse_ceiling_ft(rec.get("clouds")),
        flight_category=rec.get("fltCat"),
        conditions=rec.get("rawOb", ""),
        source="metar",
        source_url=source_url,
        attrs={
            "temp_c": rec.get("temp"),
            "dewp_c": rec.get("dewp"),
            "altim_hpa": rec.get("altim"),
            "wind_dir_raw": wdir_raw,  # 'VRB' 보존(단위/특이값 추적)
            "clouds": rec.get("clouds"),
            "name": rec.get("name"),
        },
    )


def ingest(store: LocalOntologyStore) -> int:
    """1 ingest 사이클: fetch → WeatherState write(provenance 강제). 반환: write 건수."""
    icaos = os.environ.get("METAR_ICAOS", DEFAULT_ICAOS)
    url = source_url_for(icaos)
    with httpx.Client() as client:
        records = fetch_metar(client, icaos)
    n = 0
    for rec in records:
        ws = record_to_weatherstate(rec, KADIZ_REGION.id, url)
        if not ws.station or ws.ts <= 0:
            continue  # provenance(ts) 미충족 레코드 스킵
        store.write_weatherstate(ws)
        n += 1
        print(
            f"[metar] {ws.station} {ws.flight_category} 바람={ws.wind_dir}°/{ws.wind_speed_kt}kt "
            f"시정={ws.visibility_sm}sm 실링={ws.ceiling_ft}ft"
        )
    return n


def main() -> None:
    db_path = os.environ.get("SKAI_DB", DEFAULT_DB)
    store = LocalOntologyStore(db_path)
    store.write_region(KADIZ_REGION)
    n = ingest(store)
    print(f"[metar] WeatherState write={n} 누적={store.counts()}")


if __name__ == "__main__":
    main()
