"""ontology/geo.py — 공용 지오 헬퍼 (거리·지오펜스 포함 판정).

P5 룰(로이터링 변위/경로 비율, dropout 민감구역 판정)과 correlation(시공간 버킷의
공간 겹침)이 함께 쓰는 계산을 한 곳에 둔다(SSOT). copilot/tools.py는 자체 사설 헬퍼를
이미 갖고 있어 건드리지 않는다(기존 테스트 보존) — 이 모듈은 P5 신규 코드용.
"""

from __future__ import annotations

import math
from typing import Optional

EARTH_RADIUS_KM = 6371.0088


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
