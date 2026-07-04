"""anomaly/correlation.py — 교차소스 상관 엔진 (P5, ontology.md §2 correlated_with).

시공간 버킷(±창 · 공간 겹침)으로 이상징후를 다른 이상징후·뉴스·위성통과와 잇고,
그 관계를 **온톨로지 링크로 영속**한다: Anomaly —correlated_with→ Anomaly/NewsEvent/
OrbitPass. 이것이 "은닉 정황" 내러티브(ontology.md §2 깊이 증명 질의)의 그래프 백본이다.

핵심 규율(DR-0007 결정 4):
  - 뉴스↔이상징후는 **콜사인 링킹에 의존하지 않는다**(군용 인시던트엔 상용 콜사인이 없음
    — P3 발견 #2). 대신 "같은 Region 언급 + 시간 근접"의 시공간 버킷으로 잇는다.
  - correlated_with는 정황(확증 아님) — 신뢰도를 올리지 않고 관계만 남긴다.

이 모듈이 상관 로직의 유일한 소스다. copilot은 여기서 만든 링크를 **읽기만** 한다
(assessment.py의 중복 계산 제거 — 로직 SSOT).
"""

from __future__ import annotations

from typing import Optional

from ontology.geo import haversine_km, point_in_region

# 시공간 버킷 창.
CORRELATION_WINDOW_SECONDS = 60 * 60  # ±60분 (이상징후↔이상징후·↔통과)
# 뉴스는 사건 이후 회고 보도(7d 창)라 더 넓은 시간창 + 같은 Region 언급으로 잇는다.
NEWS_CORRELATION_WINDOW_SECONDS = 24 * 3600  # ±24시간
# 이상징후↔이상징후 공간 근접 임계(km). 같은 관심지역 규모 안의 상관만.
SPATIAL_CORRELATION_KM = 300.0

CORRELATED_WITH = "correlated_with"


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and a_end >= b_start


def _regions_containing(regions: list, lat, lon) -> set[str]:
    """점을 포함하는 모든 Region id (OpArea·ADIZ 중첩 시 여러 개)."""
    if lat is None or lon is None:
        return set()
    return {r.id for r in regions if point_in_region(lat, lon, r)}


def correlate(store, anomalies: list, now: Optional[int] = None) -> list[dict]:
    """주어진 이상징후들을 다른 이상징후·뉴스·위성통과와 시공간 버킷으로 상관 → 링크 영속.

    반환: 이번에 주장(assert)된 correlated_with 엣지 리스트 {src_id, dst_type, dst_id}
    (INSERT OR IGNORE라 재실행에도 멱등). copilot·injector가 공용으로 호출한다.
    """
    if not anomalies:
        return []
    regions = store.query_regions()
    region_map = {r.id: r for r in regions}
    passes = store.query_orbitpasses()
    news = store.query_news()
    all_anoms = store.query_anomalies()
    W = CORRELATION_WINDOW_SECONDS
    edges: list[dict] = []

    for a in anomalies:
        a_region_ids = _regions_containing(regions, a.lat, a.lon)

        # ── 이상징후 ↔ 위성통과: 이상징후 점이 통과 Region 안 + 시간 겹침 ──
        for p in passes:
            preg = region_map.get(p.region_ref)
            if preg is None or not point_in_region(a.lat, a.lon, preg):
                continue
            if _overlaps(p.start_ts, p.end_ts, a.ts - W, a.ts + W):
                store.link("Anomaly", a.id, CORRELATED_WITH, "OrbitPass", p.id)
                edges.append({"src_id": a.id, "dst_type": "OrbitPass", "dst_id": p.id})

        # ── 이상징후 ↔ 뉴스: 같은 Region 언급 + 시간 근접(콜사인 비의존) ──
        for n in news:
            if abs(a.ts - n.ts) > NEWS_CORRELATION_WINDOW_SECONDS:
                continue
            mentioned = {
                m["id"] for m in store.query_mentions(n.id) if m["type"] == "Region"
            }
            if a_region_ids & mentioned:
                store.link("Anomaly", a.id, CORRELATED_WITH, "NewsEvent", n.id)
                edges.append({"src_id": a.id, "dst_type": "NewsEvent", "dst_id": n.id})

        # ── 이상징후 ↔ 이상징후: 시간 ±창 + 공간 근접(정준방향으로 1개만 저장) ──
        for b in all_anoms:
            if b.id == a.id:
                continue
            if abs(a.ts - b.ts) > W:
                continue
            if a.lat is None or b.lat is None:
                continue
            if haversine_km(a.lat, a.lon, b.lat, b.lon) > SPATIAL_CORRELATION_KM:
                continue
            src_id, dst_id = sorted([a.id, b.id])  # 대칭 → 정준방향 1개
            store.link("Anomaly", src_id, CORRELATED_WITH, "Anomaly", dst_id)
            edges.append({"src_id": src_id, "dst_type": "Anomaly", "dst_id": dst_id})

    return edges


def correlate_all(store, now: Optional[int] = None) -> list[dict]:
    """store의 모든 이상징후를 상관(주입기·폴러가 탐지 직후 호출). 반환: 엣지 리스트."""
    return correlate(store, store.query_anomalies(), now=now)
