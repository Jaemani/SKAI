"""ontology/geo.py — 공용 지오 헬퍼 (거리·지오펜스 포함 판정).

P5 룰(로이터링 변위/경로 비율, dropout 민감구역 판정)과 correlation(시공간 버킷의
공간 겹침)이 함께 쓰는 계산을 한 곳에 둔다(SSOT). copilot/tools.py는 자체 사설 헬퍼를
이미 갖고 있어 건드리지 않는다(기존 테스트 보존) — 이 모듈은 P5 신규 코드용.
"""

from __future__ import annotations

import math
from typing import Optional

EARTH_RADIUS_KM = 6371.0088

Bbox = tuple[float, float, float, float]  # (lamin, lomin, lamax, lomax)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 위경도 점 사이 대권거리(km). 로이터링 변위·상관 공간거리에 쓴다."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def path_length_km(path: list[list[float]]) -> float:
    """경로 [[lat, lon], ...]의 총 길이(km) = 인접 구간 대권거리 합."""
    total = 0.0
    for a, b in zip(path, path[1:]):
        total += haversine_km(a[0], a[1], b[0], b[1])
    return total


def region_bbox(region) -> tuple[float, float, float, float]:
    """Region 폴리곤 → (lamin, lomin, lamax, lomax) 경계상자."""
    lats = [p[0] for p in region.geo]
    lons = [p[1] for p in region.geo]
    return min(lats), min(lons), max(lats), max(lons)


def point_in_bbox(
    lat: Optional[float], lon: Optional[float], bbox: tuple[float, float, float, float]
) -> bool:
    """점이 경계상자 안인가. lat/lon None이면 False(보수적)."""
    if lat is None or lon is None:
        return False
    lamin, lomin, lamax, lomax = bbox
    return lamin <= lat <= lamax and lomin <= lon <= lomax


def point_in_region(lat: Optional[float], lon: Optional[float], region) -> bool:
    """점이 Region bbox 안인가."""
    return point_in_bbox(lat, lon, region_bbox(region))


def region_of_point(regions: list, lat: Optional[float], lon: Optional[float]):
    """점을 포함하는 Region을 반환(없으면 None).

    여러 Region이 겹치면(OpArea ⊂ ADIZ) **가장 작은 bbox**를 우선 — 더 구체적인
    구역(작전구역)을 반환한다.
    """
    if lat is None or lon is None:
        return None
    matches = [r for r in regions if point_in_region(lat, lon, r)]
    if not matches:
        return None

    def _area(r) -> float:
        lamin, lomin, lamax, lomax = region_bbox(r)
        return (lamax - lamin) * (lomax - lomin)

    return min(matches, key=_area)


def union_bbox(regions: list) -> Optional[Bbox]:
    """여러 Region을 감싸는 최소 경계상자 (lamin, lomin, lamax, lomax). 빈 리스트면 None.

    dropout 경계이탈 판정의 "관심영역 경계"로 쓴다. 민감구역이 KADIZ(ADIZ)와 그 내부
    OpArea처럼 **중첩**돼 있어도, union은 가장 바깥(KADIZ) 경계가 된다 — OpArea 안쪽
    경계에서 KADIZ 내부로 이동하는 기체를 커버리지 이탈로 오판하지 않는다(내부 경계는
    커버리지 경계가 아님). 데모에선 union = KADIZ_BBOX = 실제 폴러 fetch bbox.
    """
    boxes = [region_bbox(r) for r in regions]
    if not boxes:
        return None
    lamins, lomins, lamaxs, lomaxs = zip(*boxes)
    return min(lamins), min(lomins), max(lamaxs), max(lomaxs)


# 경계 근접 시 "바깥으로 향함"을 인정할 heading 성분 임계. dot>이 값이면 외향(0=직교/평행은
# 제외 = 보수적: 경계와 나란히 나는 기체는 이탈로 보지 않음). 부동소수 평행(≈0) 방어용 ε.
_OUTWARD_EPS = 1e-9


def heading_exits_bbox(
    lat: Optional[float],
    lon: Optional[float],
    heading: Optional[float],
    bbox: Optional[Bbox],
    margin_deg: float,
) -> bool:
    """점이 bbox 경계에서 margin_deg 이내 AND 기수(heading)가 그 경계 **바깥**을 향하는가.

    dropout 경계이탈(커버리지 이탈) 억제용. heading은 진북 기준 시계방향(도) — ADS-B
    true_track과 동일 규약(0=북,90=동,180=남,270=서). 근접한 각 경계의 외향 법선을 합해
    기수 벡터와 내적 → 양수(외향 성분 존재)면 이탈로 본다. 경계와 나란하거나 안쪽을 향하면
    False(보수적 — 억제는 명확한 외향만). lat/lon/heading/bbox 중 하나라도 None이면 False.

    주의: margin_deg를 위/경도에 동일 적용한다(경도 1°는 위도 1°보다 물리적으로 짧다 —
    37°N에서 ≈89km vs 111km). 커버리지 이탈 휴리스틱엔 충분한 근사(억제가 경도축에서
    약간 더 관대해질 뿐, 오탐 방향이 아니라 benign 억제 방향이라 안전).
    """
    if lat is None or lon is None or heading is None or bbox is None:
        return False
    lamin, lomin, lamax, lomax = bbox
    near_north = (lamax - lat) <= margin_deg
    near_south = (lat - lamin) <= margin_deg
    near_east = (lomax - lon) <= margin_deg
    near_west = (lon - lomin) <= margin_deg
    if not (near_north or near_south or near_east or near_west):
        return False
    # 외향 법선(동, 북) 성분 누적. 코너(두 경계 동시 근접)면 두 법선의 합이 대각 외향.
    out_e = (1.0 if near_east else 0.0) - (1.0 if near_west else 0.0)
    out_n = (1.0 if near_north else 0.0) - (1.0 if near_south else 0.0)
    if out_e == 0.0 and out_n == 0.0:
        return False  # 마주보는 두 경계 동시 근접(bbox가 2·margin보다 좁음) → 판정 유보
    h = math.radians(heading)
    head_e, head_n = math.sin(h), math.cos(h)  # heading 벡터(동, 북)
    return (head_e * out_e + head_n * out_n) > _OUTWARD_EPS
