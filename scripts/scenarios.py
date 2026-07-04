"""scripts/scenarios.py — 선언적 합성 시나리오 (P5, DR-0007 결정 5).

라이브 KADIZ엔 이상징후가 상시 뜨지 않으므로(재현성), 라벨된 합성 시나리오를 **선언적
dict 목록**으로 정의한다. 각 시나리오는 `now` 앵커에 상대적(dt 오프셋)이라, 평가 하네스와
P6 스냅샷 재생이 같은 정의를 재사용해 언제든 동일 결과를 낸다(P4 발견 #5 now 앵커링).

각 시나리오:
  id · desc · labels(ground-truth 양성 유형 집합, 정상=set()) · tracks · passes · news
  · weather · mirror(dropout 교차 판정용 미러 데이터).

track pattern: line(직선·gap 없음) / gapline(직선+중간 gap→dropout) / circle(선회→로이터링).
모든 관측·객체는 source="synthetic"(provenance 유지 = validate_provenance 통과).
"""

from __future__ import annotations

import math
from typing import Optional

from anomaly.crosscheck import SyntheticMirrorSource
from ontology.custody import rebuild_tracks
from ontology.model import (
    KADIZ_REGION,
    OPAREA_WEST_REGION,
    Aircraft,
    NewsEvent,
    Observation,
    OrbitPass,
    Satellite,
    WeatherState,
)

# ── 유형 상수(라벨) — rules.py의 유형 문자열과 일치해야 함 ──
T_DROPOUT = "adsb_dropout"
T_LOITERING = "loitering"
T_MILITARY = "military_approach"
T_SATELLITE = "satellite_proximity"
T_SQUAWK = "emergency_squawk"
T_MANEUVER = "rapid_maneuver"


# ── 관측 패턴 확장기 (dt 오프셋 → ts) ─────────────────────────────────────────
def _line(p: dict, now: int) -> list[dict]:
    """직선 궤적. 균등 간격(gap 없음). 변위/경로 비율 높음 → 로이터링 아님."""
    n = p["n"]
    ts0, ts1 = now + p["dt_start"], now + p["dt_end"]
    step = (ts1 - ts0) / max(1, n - 1)
    out = []
    for i in range(n):
        out.append(
            {
                "ts": int(ts0 + i * step),
                "lat": p["lat"] + i * p.get("dlat", 0.0),
                "lon": p["lon"] + i * p.get("dlon", 0.0),
                "squawk": p.get("squawk"),
            }
        )
    return out


def _gapline(p: dict, now: int) -> list[dict]:
    """직선 궤적에서 중간 구간을 제거해 gap(>임계)을 만든다 → dropout 신호."""
    obs = _line(p, now)
    lo, hi = p["gap_from"], p["gap_to"]
    return [o for i, o in enumerate(obs) if not (lo <= i <= hi)]


def _circle(p: dict, now: int) -> list[dict]:
    """원형 궤적(마지막≈처음). 변위≈0, 경로 김 → 로이터링."""
    n = p["n"]
    ts0, ts1 = now + p["dt_start"], now + p["dt_end"]
    step = (ts1 - ts0) / max(1, n - 1)
    out = []
    for i in range(n):
        ang = 2 * math.pi * i / (n - 1)
        out.append(
            {
                "ts": int(ts0 + i * step),
                "lat": p["clat"] + p["r_deg"] * math.sin(ang),
                "lon": p["clon"] + p["r_deg"] * math.cos(ang),
                "squawk": p.get("squawk"),
            }
        )
    return out


def _climb(p: dict, now: int) -> list[dict]:
    """직선 궤적 + 스텝당 고도(dalt_m)·속도(dvel_mps) 변화 → 급기동(급상승/급강하) 신호.

    수직 변화율 = dalt_m / step(초). dalt_m 음수 = 급강하. 각 관측이 고유 alt/velocity를
    들고 나오며(다른 패턴은 트랙 레벨 상수 alt를 씀), apply_scenario가 이를 관측에 싣는다.
    """
    n = p["n"]
    ts0, ts1 = now + p["dt_start"], now + p["dt_end"]
    step = (ts1 - ts0) / max(1, n - 1)
    alt0 = p.get("alt0", 3000.0)
    dalt = p.get("dalt_m", 0.0)  # 스텝당 고도 변화(m). 음수 = 강하
    vel0 = p.get("vel0", 220.0)
    dvel = p.get("dvel_mps", 0.0)  # 스텝당 속도 변화(m/s)
    out = []
    for i in range(n):
        out.append(
            {
                "ts": int(ts0 + i * step),
                "lat": p["lat"] + i * p.get("dlat", 0.0),
                "lon": p["lon"] + i * p.get("dlon", 0.0),
                "squawk": p.get("squawk"),
                "alt": alt0 + i * dalt,
                "velocity": vel0 + i * dvel,
            }
        )
    return out


_PATTERNS = {"line": _line, "gapline": _gapline, "circle": _circle, "climb": _climb}


# ── 시나리오 적용 (store에 write + Track 재구성) ──────────────────────────────
def apply_scenario(store, scenario: dict, now: int) -> Optional[SyntheticMirrorSource]:
    """시나리오를 store에 주입(관측·통과·뉴스·기상) + Track 재구성. 반환: dropout 미러.

    탐지·상관은 호출자가 scan_and_create_all(crosscheck=반환 미러)로 돌린다.
    """
    store.write_region(KADIZ_REGION)
    store.write_region(OPAREA_WEST_REGION)

    # 항공기 + 관측 + observed_as
    for tr in scenario.get("tracks", []):
        ac = Aircraft(
            icao24=tr["icao24"],
            callsign=tr.get("callsign"),
            is_military=tr.get("is_military", False),
        )
        store.write_aircraft(ac)
        obs_list = _PATTERNS[tr["pattern"]](tr["params"], now)
        for o in obs_list:
            oid = f"{tr['icao24']}-{o['ts']}"
            obs = Observation(
                id=oid,
                aircraft_ref=tr["icao24"],
                ts=o["ts"],
                lat=o["lat"],
                lon=o["lon"],
                # 관측별 alt/velocity가 있으면(climb 패턴) 그것을, 없으면 트랙 레벨 상수를 쓴다
                # (하위호환: line/gapline/circle은 관측별 값이 없어 기존 산출 그대로).
                alt=o.get("alt", tr.get("alt", 9000.0)),
                velocity=o.get("velocity", tr.get("velocity", 220.0)),
                heading=tr.get("heading", 90.0),
                squawk=o.get("squawk"),
                on_ground=False,
                source="synthetic",
                source_url=f"synthetic://skai/scenario/{scenario['id']}/{tr['icao24']}/{o['ts']}",
                attrs={"scenario": scenario["id"]},
            )
            store.write_observation(obs)
            store.link("Aircraft", tr["icao24"], "observed_as", "Observation", oid)

    # 위성 통과 (satellite_proximity 신호 + 상관 대상)
    for sp in scenario.get("passes", []):
        store.write_satellite(
            Satellite(
                norad_id=sp["norad_id"],
                name=sp["name"],
                source="synthetic",
                source_url=f"synthetic://skai/scenario/{scenario['id']}/sat/{sp['norad_id']}",
            )
        )
        store.write_orbitpass(
            OrbitPass(
                id=f"pass-{sp['norad_id']}-{now + sp['dt_start']}",
                satellite_ref=sp["norad_id"],
                region_ref=sp.get("region_ref", "KADIZ"),
                start_ts=now + sp["dt_start"],
                end_ts=now + sp["dt_end"],
                max_elevation=sp["max_elevation"],
                ground_track=sp["ground_track"],
                source="synthetic",
                source_url=f"synthetic://skai/scenario/{scenario['id']}/sat/{sp['norad_id']}",
            )
        )

    # 뉴스 (correlated_with 대상, 저신뢰 OSINT)
    for nw in scenario.get("news", []):
        nid = f"news-{scenario['id']}-{nw.get('slug', 'n')}"
        store.write_newsevent(
            NewsEvent(
                id=nid,
                source="synthetic",
                source_url=f"synthetic://skai/scenario/{scenario['id']}/news/{nw.get('slug', 'n')}",
                ts=now + nw["dt"],
                title=nw["title"],
                confidence=nw.get("confidence", 0.35),
                entities=nw.get("entities", []),
            ),
            mentions=[("Region", r) for r in nw.get("mentions_region", [])],
        )

    # 기상 (맥락)
    for wx in scenario.get("weather", []):
        store.write_weatherstate(
            WeatherState(
                id=f"wx-{wx['station']}-{now + wx['dt']}",
                region_ref=wx.get("region_ref", "KADIZ"),
                ts=now + wx["dt"],
                station=wx["station"],
                flight_category=wx.get("flight_category"),
                ceiling_ft=wx.get("ceiling_ft"),
                visibility_sm=wx.get("visibility_sm"),
                lat=wx.get("lat"),
                lon=wx.get("lon"),
                source="synthetic",
                source_url=f"synthetic://skai/scenario/{scenario['id']}/wx",
            )
        )

    rebuild_tracks(store)

    mirror = scenario.get("mirror")
    if mirror is not None:
        return SyntheticMirrorSource(
            absent=set(mirror.get("absent", [])),
            present=set(mirror.get("present", [])),
        )
    return None


# 직선 KADIZ 관통 궤적(민감구역 밖 좌표) 지상궤적 헬퍼는 불필요 — 좌표를 직접 준다.
# ── 시나리오 정의 (선언적) ────────────────────────────────────────────────────
# 좌표 기준: KADIZ lat 32~39·lon 122~132(ADIZ). OpArea(서해) lat 35~37.5·lon 123.5~125.5.
SCENARIOS: list[dict] = [
    # ── 양성: ADS-B dropout (교차 확인 = 부재 확인, 신뢰도 상향) ──
    {
        "id": "dropout_confirmed",
        "desc": "서해 작전구역 내 dropout — 미러가 부재 교차 확인(신뢰도 0.7대)",
        "labels": {T_DROPOUT},
        "tracks": [
            {
                "icao24": "d1shadow",
                "callsign": "SHADOW1",
                "pattern": "gapline",
                # OpArea 내 직선(고 변위비 → 로이터링 아님) + 중간 gap → dropout
                "params": {
                    "lat": 36.1,
                    "lon": 124.0,
                    "dlat": 0.03,
                    "dlon": 0.05,
                    "n": 11,
                    "dt_start": -900,
                    "dt_end": -300,
                    "gap_from": 5,
                    "gap_to": 7,
                    "squawk": "2000",
                },
            }
        ],
        "mirror": {"absent": ["d1shadow"]},  # 2차 소스도 관측 못 함 → 부재 확인
    },
    # ── 양성: ADS-B dropout (교차 미확인, 저신뢰 후보 — 단정 금지) ──
    {
        "id": "dropout_unconfirmed",
        "desc": "KADIZ 내 dropout — 2차 소스 없음(미확인, 신뢰도 0.4대, 단정 금지)",
        "labels": {T_DROPOUT},
        "tracks": [
            {
                "icao24": "d2ghost",
                "callsign": "GHOST2",
                "pattern": "gapline",
                "params": {
                    "lat": 37.5,
                    "lon": 129.5,
                    "dlat": 0.02,
                    "dlon": 0.06,
                    "n": 11,
                    "dt_start": -800,
                    "dt_end": -250,
                    "gap_from": 4,
                    "gap_to": 6,
                    "squawk": "2000",
                },
            }
        ],
        "mirror": {},  # 미확인(None)
    },
    # ── 음성: dropout 처럼 보이나 미러가 여전히 관측(False) → dropout 아님 ──
    {
        "id": "dropout_present_mirror",
        "desc": "gap 있으나 2차 소스가 여전히 관측 → 센서 아티팩트(교차검증이 오탐 차단)",
        "labels": set(),  # 정상(음성) — 이상징후 없어야 함
        "tracks": [
            {
                "icao24": "d3relay",
                "callsign": "RELAY3",
                "pattern": "gapline",
                "params": {
                    "lat": 36.3,
                    "lon": 124.3,
                    "dlat": 0.03,
                    "dlon": 0.04,
                    "n": 11,
                    "dt_start": -900,
                    "dt_end": -300,
                    "gap_from": 5,
                    "gap_to": 7,
                    "squawk": "2000",
                },
            }
        ],
        "mirror": {"present": ["d3relay"]},  # 여전히 관측됨 → 단정 안 함
    },
    # ── 양성: 비상 스쿽 (P2 룰 — 전 유형 P/R 커버리지) ──
    {
        "id": "emergency_hijack",
        "desc": "KADIZ 내 스쿽 7500(하이재킹) 송신 → 비상 스쿽",
        "labels": {T_SQUAWK},
        "tracks": [
            {
                "icao24": "e1hijk",
                "callsign": "CES7500",
                "pattern": "line",
                "params": {
                    "lat": 35.2,
                    "lon": 125.8,
                    "dlat": 0.02,
                    "dlon": 0.02,
                    "n": 4,
                    "dt_start": -180,
                    "dt_end": 0,
                    "squawk": "7500",
                },
            }
        ],
    },
    # ── 양성: 로이터링 (원형·반복, ≥10분) ──
    {
        "id": "loitering_orbit",
        "desc": "KADIZ 내 12분 원형 선회(변위/경로 비율 낮음) → 로이터링",
        "labels": {T_LOITERING},
        "tracks": [
            {
                "icao24": "l1orbit",
                "callsign": "ORBIT3",
                "pattern": "circle",
                "params": {
                    "clat": 36.0,
                    "clon": 127.0,
                    "r_deg": 0.15,
                    "n": 13,
                    "dt_start": -720,
                    "dt_end": 0,
                    "squawk": "2000",
                },
            }
        ],
    },
    # ── 양성: 군용기 접근 (콜사인 프리픽스 휴리스틱) ──
    {
        "id": "military_callsign",
        "desc": "군 콜사인 RCH가 서해 작전구역 진입(저신뢰 휴리스틱)",
        "labels": {T_MILITARY},
        "tracks": [
            {
                "icao24": "m1reach",
                "callsign": "RCH451",  # military_db 프리픽스 매칭
                "pattern": "line",
                # OpArea 내 짧은 직선(gap 없음·짧음 → dropout/로이터링 아님)
                "params": {
                    "lat": 36.4,
                    "lon": 124.4,
                    "dlat": 0.02,
                    "dlon": 0.03,
                    "n": 6,
                    "dt_start": -300,
                    "dt_end": 0,
                    "squawk": "2000",
                },
            }
        ],
    },
    # ── 양성: 군용기 접근 (관측 소스 is_military 플래그) ──
    {
        "id": "military_flag",
        "desc": "is_military 플래그 항공기가 서해 작전구역 진입",
        "labels": {T_MILITARY},
        "tracks": [
            {
                "icao24": "m2falcon",
                "callsign": "FALCON9",
                "is_military": True,
                "pattern": "line",
                "params": {
                    "lat": 35.6,
                    "lon": 124.8,
                    "dlat": 0.03,
                    "dlon": 0.02,
                    "n": 6,
                    "dt_start": -300,
                    "dt_end": 0,
                    "squawk": "2000",
                },
            }
        ],
    },
    # ── 양성: 위성 근접 (over 민감구역 · near-overhead · now±) ──
    {
        "id": "satellite_overhead",
        "desc": "위성이 now 근방 KADIZ 상공을 최대앙각 84°로 통과 → 위성 근접",
        "labels": {T_SATELLITE},
        "passes": [
            {
                "norad_id": "90001",
                "name": "SYN-RECON-1",
                "dt_start": -120,
                "dt_end": 60,
                "max_elevation": 84.0,
                "ground_track": [[35.6, 126.6], [36.0, 127.0], [36.4, 127.4]],
            }
        ],
    },
    # ── 양성: 급기동 (급상승 — 스텝당 고도 급변, 6000 ft/min 초과) ──
    {
        "id": "rapid_climb",
        "desc": "KADIZ 내 급상승(수직률 ≈9200 ft/min, 임계 6000 초과) → 급기동",
        "labels": {T_MANEUVER},
        "tracks": [
            {
                "icao24": "r1zoom",
                "callsign": "ZOOM1",
                "pattern": "climb",
                # 8관측·30초 간격 직선 + 스텝당 +1400m(=46.7 m/s≈9186 ft/min). gap 없음·짧음·
                # 정상 스쿽·비군용·KADIZ(비 OpArea) → 급기동만 트리거.
                "params": {
                    "lat": 36.0,
                    "lon": 127.0,
                    "dlat": 0.01,
                    "dlon": 0.01,
                    "n": 8,
                    "dt_start": -210,
                    "dt_end": 0,
                    "alt0": 3000.0,
                    "dalt_m": 1400.0,
                    "squawk": "2000",
                },
            }
        ],
    },
    # ── 양성(복합): 은닉 정황 내러티브 — dropout + 위성통과 + 뉴스 상관 ──
    {
        "id": "narrative_hidden",
        "desc": "은닉 정황: ADS-B 끊긴 기체 + 위성 통과 + 뉴스 언급이 correlated_with로 묶임",
        "labels": {T_DROPOUT, T_SATELLITE},
        "tracks": [
            {
                "icao24": "nvshadow",
                "callsign": "SHADOW7",
                "pattern": "gapline",
                "params": {
                    "lat": 36.2,
                    "lon": 124.5,
                    "dlat": 0.03,
                    "dlon": 0.05,
                    "n": 11,
                    "dt_start": -900,
                    "dt_end": -300,
                    "gap_from": 5,
                    "gap_to": 7,
                    "squawk": "2000",
                },
            }
        ],
        "mirror": {"absent": ["nvshadow"]},
        "passes": [
            {
                "norad_id": "90007",
                "name": "SYN-RECON-7",
                "dt_start": -180,
                "dt_end": 0,
                "max_elevation": 82.0,
                "ground_track": [[35.9, 124.2], [36.2, 124.6], [36.5, 125.0]],
            }
        ],
        "news": [
            {
                "slug": "kadiz-incident",
                "dt": -1800,
                "title": "관측통 '서해 상공서 미식별 항적 일시 소실' 보도 — KADIZ 긴장",
                "mentions_region": ["KADIZ"],
                "entities": ["KADIZ"],
                "confidence": 0.35,
            }
        ],
        "weather": [
            {
                "station": "RKSI",
                "dt": -600,
                "flight_category": "MVFR",
                "ceiling_ft": 1200,
                "visibility_sm": 4.0,
                "lat": 37.46,
                "lon": 126.44,
            },
        ],
    },
    # ── 음성: 정상 통과 항적(직선·정상 스쿽·비군용·gap 없음) ──
    {
        "id": "normal_transit_a",
        "desc": "상용기 정상 통과(직선·정상 스쿽) → 이상징후 없어야 함",
        "labels": set(),
        "tracks": [
            {
                "icao24": "71c101",
                "callsign": "KAL123",
                "pattern": "line",
                "params": {
                    "lat": 34.4,
                    "lon": 129.2,
                    "dlat": 0.05,
                    "dlon": -0.04,
                    "n": 8,
                    "dt_start": -420,
                    "dt_end": 0,
                    "squawk": "2000",
                },
            }
        ],
    },
    {
        "id": "normal_transit_b",
        "desc": "상용기 정상 통과 2(다른 항로) → 이상징후 없어야 함",
        "labels": set(),
        "tracks": [
            {
                "icao24": "71c202",
                "callsign": "AAR456",
                "pattern": "line",
                "params": {
                    "lat": 38.0,
                    "lon": 123.0,
                    "dlat": -0.04,
                    "dlon": 0.06,
                    "n": 8,
                    "dt_start": -420,
                    "dt_end": 0,
                    "squawk": "1200",
                },
            }
        ],
    },
    {
        "id": "normal_transit_c",
        "desc": "상용기 정상 통과 3(짧은 직선) → 이상징후 없어야 함",
        "labels": set(),
        "tracks": [
            {
                "icao24": "71c303",
                "callsign": "JJA789",
                "pattern": "line",
                "params": {
                    "lat": 33.5,
                    "lon": 126.0,
                    "dlat": 0.03,
                    "dlon": 0.05,
                    "n": 6,
                    "dt_start": -300,
                    "dt_end": 0,
                    "squawk": "3647",
                },
            }
        ],
    },
    # ── 음성: 정상 상승(민항 정상 상승률 ≈2600 ft/min < 임계) → 급기동 아님 ──
    {
        "id": "normal_climb",
        "desc": "상용기 정상 상승(수직률 ≈2600 ft/min, 임계 6000 미만) → 이상징후 없어야 함",
        "labels": set(),
        "tracks": [
            {
                "icao24": "71c404",
                "callsign": "KAL999",
                "pattern": "climb",
                # 스텝당 +400m(=13.3 m/s≈2625 ft/min) — 민항 정상 상승률, 급기동 임계 미달.
                "params": {
                    "lat": 34.8,
                    "lon": 128.5,
                    "dlat": 0.02,
                    "dlon": 0.02,
                    "n": 8,
                    "dt_start": -210,
                    "dt_end": 0,
                    "alt0": 2000.0,
                    "dalt_m": 400.0,
                    "squawk": "1200",
                },
            }
        ],
    },
]


def scenario_by_id(scenario_id: str) -> Optional[dict]:
    for s in SCENARIOS:
        if s["id"] == scenario_id:
            return s
    return None
