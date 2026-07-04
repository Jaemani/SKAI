"""copilot/tools.py — 툴화된 온톨로지 read (architecture.md §4: 에이전트 툴).

각 함수는 기존 OntologyStore 질의를 감싸 **사실 레코드(Fact)** 를 낸다. 핵심 규율:
  - **객체 id 보존**: 모든 Fact는 근거 객체 id(cites)를 들고 나온다 → 문장 조립 시
    citation이 사실에 이미 붙어 있다(DR-0006: citation은 조립의 부산물).
  - **지역·시간창 필터**: 질의 파서가 정한 (region, window)로 read 범위를 좁힌다.

⚠️ Foundry 개통 시 치환 지점: 지금은 LocalOntologyStore(SQLite) 질의를 감싸지만,
크리덴셜 도착 시 이 함수들의 본문만 **OSDK 타입드 read**(client.ontology.objects.<Type>
.where(...))로 바꾸면 된다. 반환 계약(Fact + cites)은 불변 → assessment·서버·프론트 무변경.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ── 사실 레코드 ────────────────────────────────────────────────────────────
@dataclass
class Fact:
    """툴 read가 낸 사실 하나. cites = 이 사실의 근거 객체 id(provenance).

    kind = flight | anomaly | satellite | weather | news (문장 조립 분기용).
    data = 문장에 쓸 값(각 kind별 필드). confidence = 소스 신뢰도 힌트
    (하드 소스 高·뉴스 低 — 문장 신뢰도 산출에 assessment가 사용).
    """

    kind: str
    cites: list[str]
    data: dict[str, Any]
    confidence: float = 1.0
    ts: int = 0


# ── 지역 포함 판정 (지오펜스) ────────────────────────────────────────────────
def _region_bbox(region) -> tuple[float, float, float, float]:
    """Region 폴리곤 → (lamin, lomin, lamax, lomax) 경계상자."""
    lats = [p[0] for p in region.geo]
    lons = [p[1] for p in region.geo]
    return min(lats), min(lons), max(lats), max(lons)


def _in_bbox(lat, lon, bbox) -> bool:
    """점이 경계상자 안인가. lat/lon None이면 판정 불가 → False(보수적)."""
    if lat is None or lon is None:
        return False
    lamin, lomin, lamax, lomax = bbox
    return lamin <= lat <= lamax and lomin <= lon <= lomax


def _region_of(store, region_id: str):
    """region_id → Region 객체(없으면 None)."""
    for r in store.query_regions():
        if r.id == region_id:
            return r
    return None


# ── 툴 ──────────────────────────────────────────────────────────────────────
def query_flights(store, region_id: str, window: tuple[int, int]) -> list[Fact]:
    """관심지역·시간창 안의 현재 항적(항공기별 최신 관측 1건).

    필터: ts ∈ window AND 관측점이 Region bbox 안(within Region 지오펜스).
    cites = [Observation.id]. Foundry: Observation.where(region, ts).iterate().
    """
    start, end = window
    region = _region_of(store, region_id)
    bbox = _region_bbox(region) if region else None
    ac_map = store.aircraft_map()
    out: list[Fact] = []
    for o in store.query_latest_observations():
        if not (start <= o.ts <= end):
            continue
        if bbox is not None and not _in_bbox(o.lat, o.lon, bbox):
            continue
        ac = ac_map.get(o.aircraft_ref)
        out.append(
            Fact(
                kind="flight",
                cites=[o.id],  # 근거 = 이 관측 객체
                confidence=0.9,  # ADS-B = 하드 소스
                ts=o.ts,
                data={
                    "icao24": o.aircraft_ref,
                    "callsign": ac.callsign if ac else None,
                    "squawk": o.squawk,
                    "lat": o.lat,
                    "lon": o.lon,
                    "alt": o.alt,
                    "source": o.source,
                    "source_url": o.source_url,
                    "is_military": ac.is_military if ac else False,
                },
            )
        )
    return out


def query_anomalies(store, region_id: str, window: tuple[int, int]) -> list[Fact]:
    """관심지역·시간창 안의 이상징후 + 그 근거 관측.

    필터: ts ∈ window AND 이상징후 위치가 Region bbox 안.
    cites = [Anomaly.id] + evidenced_by Observation id들(문장이 이상징후·근거를 함께 인용).
    """
    start, end = window
    region = _region_of(store, region_id)
    bbox = _region_bbox(region) if region else None
    out: list[Fact] = []
    for a in store.query_anomalies():
        if not (start <= a.ts <= end):
            continue
        if bbox is not None and not _in_bbox(a.lat, a.lon, bbox):
            continue
        evidence_ids = store.query_evidence_ids(a.id)
        out.append(
            Fact(
                kind="anomaly",
                cites=[a.id, *evidence_ids],  # 이상징후 + 근거 관측 모두 인용
                confidence=a.confidence,
                ts=a.ts,
                data={
                    "id": a.id,
                    "type": a.type,
                    "status": a.status,
                    "squawk": a.attrs.get("squawk") if a.attrs else None,
                    "callsign": a.attrs.get("callsign") if a.attrs else None,
                    "meaning": a.attrs.get("meaning") if a.attrs else None,
                    "is_synthetic": a.attrs.get("is_synthetic", False)
                    if a.attrs
                    else False,
                    "lat": a.lat,
                    "lon": a.lon,
                    "explanation": a.explanation,
                    "confidence": a.confidence,
                    "n_evidence": len(evidence_ids),
                    # 유형별 문장 조립을 위해 전체 attrs 전달(P5: region·sat_name 등).
                    "attrs": a.attrs or {},
                },
            )
        )
    return out


def sat_passes(store, region_id: str, window: tuple[int, int]) -> list[Fact]:
    """관심지역 상공 통과창 중 시간창과 겹치는 것.

    필터: OrbitPass.over == region AND [start_ts, end_ts]가 window와 교차.
    cites = [OrbitPass.id]. Foundry: OrbitPass.where(over=region).iterate() 후 시간 교차.
    ⚠️ OrbitPass는 미래 예측 객체 — "겹침"의 시공간 상관 정책은 assessment가 정한다
    (여기선 주어진 window와의 구간 교차만; 상관용 확장 window는 호출자가 넘긴다).
    """
    start, end = window
    sat_map = store.satellite_map()
    out: list[Fact] = []
    for p in store.query_orbitpasses():
        if p.region_ref != region_id:
            continue
        # 구간 교차: pass[start_ts, end_ts] ∩ window[start, end] ≠ ∅
        if p.start_ts > end or p.end_ts < start:
            continue
        sat = sat_map.get(p.satellite_ref)
        out.append(
            Fact(
                kind="satellite",
                cites=[p.id],
                confidence=0.85,  # sgp4 궤도 계산 = 하드 소스
                ts=p.start_ts,
                data={
                    "id": p.id,
                    "norad_id": p.satellite_ref,
                    "name": sat.name if sat else p.satellite_ref,
                    "object_type": sat.object_type if sat else None,
                    "start_ts": p.start_ts,
                    "end_ts": p.end_ts,
                    "max_elevation": p.max_elevation,
                },
            )
        )
    return out


def weather(store, region_id: str, window: tuple[int, int]) -> list[Fact]:
    """관심지역 최신 기상(공항별 1건). 시간창 밖이면 stale로 표시(투명성).

    cites = [WeatherState.id]. 기상은 "현재 상태" 객체라 window로 자르지 않고 최신을 쓰되,
    관측 시각이 window 밖이면 data.stale=True로 알린다(오래된 기상임을 문장에 명시).
    """
    start, end = window
    out: list[Fact] = []
    for w in store.query_weather_latest():
        if w.region_ref != region_id:
            continue
        out.append(
            Fact(
                kind="weather",
                cites=[w.id],
                confidence=0.9,  # METAR 실황 = 하드 소스
                ts=w.ts,
                data={
                    "id": w.id,
                    "station": w.station,
                    "flight_category": w.flight_category,
                    "ceiling_ft": w.ceiling_ft,
                    "visibility_sm": w.visibility_sm,
                    "wind_dir": w.wind_dir,
                    "wind_speed_kt": w.wind_speed_kt,
                    "conditions": w.conditions,
                    "stale": not (start <= w.ts <= end),
                },
            )
        )
    return out


def news(store, region_id: str, window: tuple[int, int], limit: int = 5) -> list[Fact]:
    """관심지역을 언급한 뉴스(저신뢰 OSINT). 최신순 상위 limit건.

    cites = [NewsEvent.id]. 뉴스는 OSINT 회고(7d 창)라 질의 시간창으로 자르지 않는다
    (window로 자르면 대개 0건 — 뉴스는 사건 이후 회고 보도). region mentions 링크 또는
    entities에 지역이 걸린 뉴스만(엔티티 링킹). confidence ≤ 0.4(확증 아님).
    """
    out: list[Fact] = []
    for n in store.query_news():
        mentions = store.query_mentions(n.id)
        mentions_region = any(
            m["type"] == "Region" and m["id"] == region_id for m in mentions
        )
        if not mentions_region and region_id not in n.entities:
            continue
        out.append(
            Fact(
                kind="news",
                cites=[n.id],
                confidence=n.confidence,  # 저신뢰(≤ 0.4)
                ts=n.ts,
                data={
                    "id": n.id,
                    "title": n.title,
                    "confidence": n.confidence,
                    "source_url": n.source_url,
                    "entities": n.entities,
                    "mentions_region": mentions_region,
                },
            )
        )
    out.sort(key=lambda f: f.ts, reverse=True)  # 최신 우선
    return out[:limit]
