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

from anomaly.isr_satellites import is_signal_promotable_pass
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


def _pass_time_delta(a_ts: int, p_start: int, p_end: int) -> int:
    """이상징후 시각과 통과창 [p_start,p_end]의 시간차(초). 창 안이면 0.

    음수 = 이상징후가 통과 진입보다 이름, 양수 = 통과 이탈보다 늦음(가장 가까운 창 경계 기준).
    "왜 상관인가"의 시간 근접도를 UI가 표시하도록 correlated_with 사유에 싣는다.
    """
    if a_ts < p_start:
        return a_ts - p_start
    if a_ts > p_end:
        return a_ts - p_end
    return 0


def _regions_containing(regions: list, lat, lon) -> set[str]:
    """점을 포함하는 모든 Region id (OpArea·ADIZ 중첩 시 여러 개)."""
    if lat is None or lon is None:
        return set()
    return {r.id for r in regions if point_in_region(lat, lon, r)}


def correlate(store, anomalies: list, now: Optional[int] = None) -> list[dict]:
    """주어진 이상징후들을 다른 이상징후·뉴스·위성통과와 시공간 버킷으로 상관 → 링크 영속.

    반환: 이번에 주장(assert)된 correlated_with 엣지 리스트 {src_id, dst_type, dst_id, reason}
    (reason = 시간차·공간관계 사유 dict). 재실행에도 멱등(같은 링크 upsert). copilot·injector 공용.
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
        # ISR 허용목록 게이트(기본 on): 허용목록 밖 실 위성(ISS·밝은 위성)의 통과와는
        # 상관을 만들지 않는다 — 이 게이트가 없던 시절 OrbitPass 홍수가 시공간 버킷과
        # 곱해져 한 이상징후에 correlated_with 수십 건이 붙어 상관이 노이즈가 됐다.
        # 합성(synthetic)은 우회(replay 데모의 은닉 정황 서사 유지). 판정 SSOT=isr_satellites.
        for p in passes:
            if not is_signal_promotable_pass(p.source, p.satellite_ref):
                continue  # 허용목록 밖 실 위성 → 상관 생성 금지(표시는 유지)
            preg = region_map.get(p.region_ref)
            if preg is None or not point_in_region(a.lat, a.lon, preg):
                continue
            if _overlaps(p.start_ts, p.end_ts, a.ts - W, a.ts + W):
                reason = {
                    "kind": "anomaly_orbitpass",
                    "dt_s": _pass_time_delta(a.ts, p.start_ts, p.end_ts),
                    "region": preg.id,
                    "max_elevation": p.max_elevation,
                    "norad_id": p.satellite_ref,
                }
                store.link(
                    "Anomaly", a.id, CORRELATED_WITH, "OrbitPass", p.id, attrs=reason
                )
                edges.append(
                    {
                        "src_id": a.id,
                        "dst_type": "OrbitPass",
                        "dst_id": p.id,
                        "reason": reason,
                    }
                )

        # ── 이상징후 ↔ 뉴스: 같은 Region 언급 + 시간 근접(콜사인 비의존) ──
        for n in news:
            if abs(a.ts - n.ts) > NEWS_CORRELATION_WINDOW_SECONDS:
                continue
            mentioned = {
                m["id"] for m in store.query_mentions(n.id) if m["type"] == "Region"
            }
            shared = a_region_ids & mentioned
            if shared:
                reason = {
                    "kind": "anomaly_news",
                    "dt_s": a.ts - n.ts,  # 음수 = 이상징후가 기사보다 이름
                    "shared_regions": sorted(shared),
                }
                store.link(
                    "Anomaly", a.id, CORRELATED_WITH, "NewsEvent", n.id, attrs=reason
                )
                edges.append(
                    {
                        "src_id": a.id,
                        "dst_type": "NewsEvent",
                        "dst_id": n.id,
                        "reason": reason,
                    }
                )

        # ── 이상징후 ↔ 이상징후: 시간 ±창 + 공간 근접(정준방향으로 1개만 저장) ──
        for b in all_anoms:
            if b.id == a.id:
                continue
            if abs(a.ts - b.ts) > W:
                continue
            if a.lat is None or b.lat is None:
                continue
            dist = haversine_km(a.lat, a.lon, b.lat, b.lon)
            if dist > SPATIAL_CORRELATION_KM:
                continue
            src_id, dst_id = sorted([a.id, b.id])  # 대칭 → 정준방향 1개
            reason = {
                "kind": "anomaly_anomaly",
                "gap_s": abs(a.ts - b.ts),  # 대칭 관계라 절대 시간차
                "distance_km": round(dist, 1),
            }
            store.link(
                "Anomaly", src_id, CORRELATED_WITH, "Anomaly", dst_id, attrs=reason
            )
            edges.append(
                {
                    "src_id": src_id,
                    "dst_type": "Anomaly",
                    "dst_id": dst_id,
                    "reason": reason,
                }
            )

    return edges


def correlate_all(store, now: Optional[int] = None) -> list[dict]:
    """store의 모든 이상징후를 상관(주입기·폴러가 탐지 직후 호출). 반환: 엣지 리스트."""
    return correlate(store, store.query_anomalies(), now=now)
