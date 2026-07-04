"""Celestrak 커넥터 — TLE → sgp4 → KADIZ 상공 통과창(OrbitPass) → 온톨로지.

파이프: Celestrak GP(TLE) → 12h 파일 캐시 → sgp4 전파(향후 N시간) →
        지상궤적 subpoint가 KADIZ bbox를 지나는 구간 = OrbitPass
        (of→Satellite, over→Region 링크, start/end/max_elevation + 지상궤적 점열).

P0A gotcha 반영:
  - **GMST 보정 필수**: ECI→위경도 변환 시 atan2(y,x)만으론 경도 수백 도 오차. GMST를 뺀다.
  - `error_code != 0` = 위성 수명 만료 → 스킵.

GROUP 선택(2026-07 수정):
  - 과거 기본 GROUP은 stations,visual(ISS·밝은 육안관측 위성)이었다 — ISR(정찰/이미징)이
    아니어서 위성 근접 경고 스팸·상관 폭주의 근원이었다. 이제 기본 GROUP을
    isr_satellites.CELESTRAK_ISR_GROUPS(=resource, Earth Resources)로 바꾼다. 이 GROUP은
    ISR 허용목록 전량(Sentinel·KOMPSAT·WorldView·Yaogan·Gaofen 등)을 1회 fetch로 커버한다
    (근거는 anomaly/isr_satellites.py docstring). GROUP은 CELESTRAK_GROUPS 환경변수로 교체 가능.
  - **표시층 분리 결정**: 여기서 받아온 GROUP의 **모든** 통과를 OrbitPass로 write한다
    = 지도 지상궤적 레이어는 전체 카탈로그를 표시한다(주변 위성 전부가 상황인식 맥락).
    반면 이상징후 승격·correlated_with 상관은 허용목록만(anomaly/rules·correlation에서 게이트).
    즉 **표시=전체 / 신호 승격=허용목록**. 이 커넥터는 표시 데이터를 만들 뿐, 필터링하지 않는다.

캐시 규율(DR-0005, Celestrak 캐시 존중):
  - 그룹별 TLE를 data/cache/celestrak_<group>.tle 로 저장, mtime < 12h면 재fetch 안 함.

폴링 주기(architecture.md): TLE는 12h. 단 검증 실행은 1회 ingest.
"""

from __future__ import annotations

import math
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx
from sgp4.api import Satrec, jday

from anomaly.isr_satellites import CELESTRAK_ISR_GROUPS
from ontology.model import KADIZ_BBOX, KADIZ_REGION, OrbitPass, Satellite
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php"
TIMEOUT = 30

# 폴링·계산 상수 (환경변수로 교체 가능) --------------------------------------
TLE_POLL_INTERVAL = 12 * 3600  # architecture.md: TLE 12h
CACHE_TTL_SECONDS = 12 * 3600  # 캐시 신선도(= 폴링 주기). 이내면 재fetch 안 함.
# ISR 위성 허용목록을 커버하는 GROUP(SSOT=isr_satellites). 기본 resource(Earth Resources).
DEFAULT_GROUPS = ",".join(CELESTRAK_ISR_GROUPS)
HORIZON_HOURS = 12  # 향후 몇 시간의 통과를 계산할지
STEP_SECONDS = 30  # 전파 시간 간격(통과 판정·지상궤적 해상도)

EARTH_RADIUS_KM = 6371.0
CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"


# ── TLE fetch + 12h 파일 캐시 ────────────────────────────────────────────────
def _group_url(group: str) -> str:
    return f"{CELESTRAK_URL}?GROUP={group}&FORMAT=tle"


def _cached_or_fetch(
    client: httpx.Client, group: str, cache_dir: Path, ttl: int
) -> str:
    """그룹 TLE 텍스트를 반환. 캐시가 ttl 이내면 파일에서, 아니면 fetch 후 캐시 갱신."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"celestrak_{group}.tle"
    if path.exists() and (time.time() - path.stat().st_mtime) < ttl:
        age_h = (time.time() - path.stat().st_mtime) / 3600
        print(
            f"[celestrak] 캐시 사용 group={group} (age={age_h:.1f}h < 12h) → fetch 생략"
        )
        return path.read_text(encoding="utf-8")
    resp = client.get(
        CELESTRAK_URL, params={"GROUP": group, "FORMAT": "tle"}, timeout=TIMEOUT
    )
    print(
        f"[celestrak] HTTP {resp.status_code} group={group} ({len(resp.text)} bytes) → 캐시 갱신"
    )
    resp.raise_for_status()
    path.write_text(resp.text, encoding="utf-8")
    return resp.text


def parse_tle(text: str) -> list[tuple[str, str, str]]:
    """TLE 텍스트 → (name, line1, line2) 리스트 (P0A parse 로직)."""
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    out: list[tuple[str, str, str]] = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("1 ") or lines[i].startswith("2 "):
            i += 1
            continue
        if (
            i + 2 < len(lines)
            and lines[i + 1].startswith("1 ")
            and lines[i + 2].startswith("2 ")
        ):
            out.append((lines[i], lines[i + 1], lines[i + 2]))
            i += 3
        else:
            i += 1
    return out


def norad_id_of(line1: str) -> str:
    """TLE Line1의 카탈로그 번호(컬럼 3~7)."""
    return line1[2:7].strip()


def epoch_iso_of(line1: str) -> Optional[str]:
    """TLE Line1 epoch(YYDDD.DDDD, 컬럼 19~32) → ISO8601 UTC 문자열."""
    try:
        raw = line1[18:32].strip()
        yy = int(raw[:2])
        doy = float(raw[2:])
        year = 2000 + yy if yy < 57 else 1900 + yy  # TLE 2자리 연도 규약
        dt = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=doy - 1)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def object_type_of(name: str) -> str:
    """이름 휴리스틱으로 object_type 추정 (TLE 포맷엔 명시 없음)."""
    u = name.upper()
    if "DEB" in u:
        return "DEBRIS"
    if "R/B" in u or "ROCKET" in u:
        return "ROCKET BODY"
    return "PAYLOAD"


# ── ECI → 위경도 / 앙각 (GMST 보정 핵심) ─────────────────────────────────────
def gmst_deg(jd_full: float) -> float:
    """율리우스일(jd+fr) → GMST(°). P0A와 동일한 1차 다항식."""
    return (280.46061837 + 360.98564736629 * (jd_full - 2451545.0)) % 360.0


def eci_to_subpoint(
    r_eci_km: tuple[float, float, float], gmst_degrees: float
) -> tuple[float, float, float]:
    """ECI(km) → 지상 subpoint (위도°, 경도°, 고도 km). **GMST 보정 적용**.

    경도 = atan2(y, x) - GMST (지구 자전 보정). 보정 없으면 수백 도 오차(P0A gotcha 3).
    """
    x, y, z = r_eci_km
    r = math.sqrt(x * x + y * y + z * z)
    lat = math.degrees(math.asin(max(-1.0, min(1.0, z / r))))
    lon = math.degrees(math.atan2(y, x)) - gmst_degrees
    lon = ((lon + 180.0) % 360.0) - 180.0  # [-180, 180] 정규화
    alt = r - EARTH_RADIUS_KM
    return lat, lon, alt


def elevation_deg(
    obs_lat: float,
    obs_lon: float,
    r_eci_km: tuple[float, float, float],
    gmst_degrees: float,
) -> float:
    """관측점(관심지역 중심)에서 본 위성 앙각(°). 구형 지구 근사.

    ECI를 GMST만큼 회전해 ECEF로 옮긴 뒤, 관측점 국소 상방(up)과 시선 벡터로 앙각을 구한다.
    subpoint가 bbox 중심에 가까울수록 90°(천정)에 근접 → "얼마나 머리 위인가" 지표.
    """
    g = math.radians(gmst_degrees)
    olat, olon = math.radians(obs_lat), math.radians(obs_lon)
    ox = EARTH_RADIUS_KM * math.cos(olat) * math.cos(olon)
    oy = EARTH_RADIUS_KM * math.cos(olat) * math.sin(olon)
    oz = EARTH_RADIUS_KM * math.sin(olat)
    x, y, z = r_eci_km
    # ECI → ECEF (지구 자전 보정: -GMST 회전)
    sx = x * math.cos(g) + y * math.sin(g)
    sy = -x * math.sin(g) + y * math.cos(g)
    sz = z
    rx, ry, rz = sx - ox, sy - oy, sz - oz
    rng = math.sqrt(rx * rx + ry * ry + rz * rz)
    if rng == 0:
        return 90.0
    up = (ox / EARTH_RADIUS_KM, oy / EARTH_RADIUS_KM, oz / EARTH_RADIUS_KM)
    dot = rx * up[0] + ry * up[1] + rz * up[2]
    return math.degrees(math.asin(max(-1.0, min(1.0, dot / rng))))


def region_center(bbox: dict) -> tuple[float, float]:
    """bbox 중심 (관측점). 앙각 계산 기준."""
    return (
        (bbox["lamin"] + bbox["lamax"]) / 2.0,
        (bbox["lomin"] + bbox["lomax"]) / 2.0,
    )


# ── 통과창 그룹핑 (순수 함수 — 테스트 대상) ──────────────────────────────────
def group_passes(samples: list[tuple]) -> list[dict]:
    """시간순 샘플 → 연속 in_bbox 구간(통과창) 리스트.

    samples 원소 = (ts, lat, lon, elev|None, in_bbox: bool).
    반환 원소 = {start_ts, end_ts, max_elev, track:[[lat,lon],...]}.
    """
    passes: list[dict] = []
    cur: Optional[dict] = None
    for ts, lat, lon, elev, in_bbox in samples:
        if in_bbox:
            e = elev if elev is not None else 0.0
            if cur is None:
                cur = {
                    "start_ts": ts,
                    "end_ts": ts,
                    "max_elev": e,
                    "track": [[lat, lon]],
                }
            else:
                cur["end_ts"] = ts
                cur["max_elev"] = max(cur["max_elev"], e)
                cur["track"].append([lat, lon])
        elif cur is not None:
            passes.append(cur)
            cur = None
    if cur is not None:
        passes.append(cur)
    return passes


def compute_passes_for_sat(
    sat: Satrec,
    bbox: dict,
    obs_lat: float,
    obs_lon: float,
    start_dt: datetime,
    horizon_hours: int = HORIZON_HOURS,
    step_seconds: int = STEP_SECONDS,
) -> Optional[list[dict]]:
    """한 위성의 통과창 계산. error_code≠0(첫 스텝) 위성은 None(스킵).

    중간에 error가 나면 그 지점까지의 샘플로 통과를 구성(궤도 소멸 안전).
    """
    n_steps = int(horizon_hours * 3600 / step_seconds) + 1
    samples: list[tuple] = []
    for i in range(n_steps):
        dt = start_dt + timedelta(seconds=i * step_seconds)
        jd, fr = jday(
            dt.year,
            dt.month,
            dt.day,
            dt.hour,
            dt.minute,
            dt.second + dt.microsecond / 1e6,
        )
        err, r, _v = sat.sgp4(jd, fr)
        if err != 0:
            if not samples:
                return None  # 첫 스텝부터 실패 = 만료 → 위성 스킵
            break  # 중간 소멸: 여기까지로 통과 구성
        g = gmst_deg(jd + fr)
        lat, lon, _alt = eci_to_subpoint(r, g)
        in_bbox = (
            bbox["lamin"] <= lat <= bbox["lamax"]
            and bbox["lomin"] <= lon <= bbox["lomax"]
        )
        # 앙각은 bbox 안일 때만 계산(비용 절약).
        elev = elevation_deg(obs_lat, obs_lon, r, g) if in_bbox else None
        samples.append((int(dt.timestamp()), lat, lon, elev, in_bbox))
    return group_passes(samples)


# ── ingest ───────────────────────────────────────────────────────────────────
def _groups_from_env() -> list[str]:
    raw = os.environ.get("CELESTRAK_GROUPS", DEFAULT_GROUPS)
    return [g.strip() for g in raw.replace(" ", ",").split(",") if g.strip()]


def ingest(store: LocalOntologyStore) -> tuple[int, int, int, int]:
    """1 ingest 사이클: TLE fetch(캐시) → 위성별 통과창 → Satellite/OrbitPass write.

    KADIZ 상공을 지나는 위성만 Satellite로 write(지역 관련 엔티티에 집중).

    **P3 이월 #1 수정**: 통과창은 now 이후 12h를 계산 → 폴러 반복 실행마다 신규 id가
    쌓여 과거 계산의 미래 통과가 stale로 잔존한다(2회 실행 시 99→196 배증 문제). 재계산
    전 각 위성의 **미래**(start_ts ≥ now) 통과창을 지우고(과거는 관측 이력으로 보존) 새
    계산으로 대체한다. 반환: (통과 있는 위성 수, OrbitPass 수, 스킵 위성 수, 삭제된 미래 pass 수).
    """
    groups = _groups_from_env()
    obs_lat, obs_lon = region_center(KADIZ_BBOX)
    start_dt = datetime.now(timezone.utc)
    now_ts = int(
        start_dt.timestamp()
    )  # 미래 pass 삭제 기준(이 시각 이후는 재계산 대상)

    tles: dict[str, tuple[str, str, str, str]] = {}
    with httpx.Client() as client:
        for g in groups:
            for name, l1, l2 in parse_tle(
                _cached_or_fetch(client, g, CACHE_DIR, CACHE_TTL_SECONDS)
            ):
                tles.setdefault(norad_id_of(l1), (name, l1, l2, g))  # 그룹 간 dedup

    n_sat = n_pass = n_skip = n_deleted = 0
    for nid, (name, l1, l2, group) in tles.items():
        try:
            sat = Satrec.twoline2rv(l1, l2)
        except Exception as e:
            print(f"[celestrak] TLE 파싱 실패 {name}: {e!r}")
            n_skip += 1
            continue
        passes = compute_passes_for_sat(sat, KADIZ_BBOX, obs_lat, obs_lon, start_dt)
        # 재계산 전 이 위성의 미래 통과창 선삭제(과거 보존) — 만료/무통과 위성도 정리된다.
        n_deleted += store.delete_future_orbitpasses_for(nid, now_ts)
        if passes is None:  # error_code != 0 = 만료
            n_skip += 1
            continue
        if not passes:  # 12h 내 KADIZ 통과 없음 → 이 위성은 저장하지 않음
            continue
        url = _group_url(group)
        store.write_satellite(
            Satellite(
                norad_id=nid,
                name=name,
                object_type=object_type_of(name),
                tle_epoch=epoch_iso_of(l1),
                source_url=url,
            )
        )
        n_sat += 1
        for p in passes:
            store.write_orbitpass(
                OrbitPass(
                    id=f"pass-{nid}-{p['start_ts']}",
                    satellite_ref=nid,
                    region_ref=KADIZ_REGION.id,
                    start_ts=p["start_ts"],
                    end_ts=p["end_ts"],
                    max_elevation=round(p["max_elev"], 1),
                    ground_track=p["track"],
                    source_url=url,
                )
            )
            n_pass += 1
    return n_sat, n_pass, n_skip, n_deleted


def main() -> None:
    db_path = os.environ.get("SKAI_DB", DEFAULT_DB)
    store = LocalOntologyStore(db_path)
    store.write_region(KADIZ_REGION)
    print(
        f"[celestrak] db={db_path} groups={_groups_from_env()} "
        f"horizon={HORIZON_HOURS}h step={STEP_SECONDS}s"
    )
    n_sat, n_pass, n_skip, n_deleted = ingest(store)
    print(
        f"[celestrak] 통과 위성={n_sat} OrbitPass={n_pass} 스킵={n_skip} "
        f"미래pass삭제={n_deleted} 누적={store.counts()}"
    )


if __name__ == "__main__":
    main()
