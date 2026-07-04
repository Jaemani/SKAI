"""anomaly/rules.py — 룰 기반 이상징후 후보 탐지 (architecture.md §3).

룰은 설명가능·빠르고 결정적이다. 여기서 후보만 만들고, 설명·신뢰도는 explainer가,
생성(evidence 링크 강제)은 actions.create_anomaly가 맡는다(관심사 분리).

P2 범위 = 비상 스쿽 1종(ontology.md §4):
  Observation.squawk ∈ {7500, 7600, 7700} → CreateAnomaly involves Aircraft
squawk는 **문자열 비교**(P0A gotcha 2 — "7700"은 str). 정상 코드는 걸리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from anomaly.crosscheck import CrossCheckSource, NullCrossCheckSource
from anomaly.military_db import classify_military
from ontology.geo import haversine_km, path_length_km, region_of_point
from ontology.model import (
    ANOMALY_WINDOW_SECONDS,
    SENSITIVE_CLASSIFICATIONS,
    Observation,
)

# 비상 스쿽 코드 → 의미 (ICAO 표준 트랜스폰더 비상 코드)
EMERGENCY_SQUAWKS = {
    "7500": "불법 간섭(하이재킹)",
    "7600": "무선 통신 두절",
    "7700": "일반 비상",
}

ANOMALY_TYPE_EMERGENCY_SQUAWK = "emergency_squawk"


@dataclass
class AnomalyCandidate:
    """룰이 만든 이상징후 후보 (아직 Anomaly 아님).

    observation = 근거 증거 객체(evidence). provenance는 이미 완비돼 있다
    (store가 write 시 강제). explainer가 이 컨텍스트로 설명·신뢰도를 만들고,
    actions.create_anomaly가 dedup·evidence 강제·링크 저장을 수행한다.
    """

    type: str
    aircraft_ref: str
    observation: Observation  # 근거(evidence)
    signal: dict[str, Any] = field(default_factory=dict)  # squawk, 의미, callsign 등

    @property
    def ts(self) -> int:
        return self.observation.ts

    @property
    def window(self) -> int:
        """dedup 시간창 버킷. 같은 (기체, 유형, 창)은 1개 Anomaly로 합쳐진다."""
        return self.observation.ts // ANOMALY_WINDOW_SECONDS

    @property
    def anomaly_id(self) -> str:
        """dedup 자연키. 같은 기체·유형이 같은 시간창에 재등장해도 동일 id."""
        return f"anomaly-{self.type}-{self.aircraft_ref}-{self.window}"


def detect_emergency_squawk(observations: list[Observation]) -> list[AnomalyCandidate]:
    """비상 스쿽 후보 탐지. squawk ∈ {7500,7600,7700}인 관측만 (문자열 비교).

    정상 스쿽(예: "1200","3647")·None은 걸리지 않는다.
    """
    out: list[AnomalyCandidate] = []
    for o in observations:
        # dict의 `in`은 키 검사 → None/정상코드는 통과 못 함. str 비교(gotcha 2).
        if o.squawk in EMERGENCY_SQUAWKS:
            out.append(
                AnomalyCandidate(
                    type=ANOMALY_TYPE_EMERGENCY_SQUAWK,
                    aircraft_ref=o.aircraft_ref,
                    observation=o,
                    signal={
                        "squawk": o.squawk,
                        "meaning": EMERGENCY_SQUAWKS[o.squawk],
                    },
                )
            )
    return out


# ══════════════════════════════════════════════════════════════════════════════
# P5 확장 룰 — dropout / 로이터링 / 군용기 접근 / 위성 근접
#
# 비상 스쿽(단일 관측 신호)과 달리 이 룰들은 Track·Region·교차소스·궤도 같은
# **더 많은 컨텍스트**를 본다. 그래서 근거(evidence)·주체(involves)가 유형마다 다르다
# (dropout=Observation/Aircraft, 위성 근접=OrbitPass/Satellite). AnomalyCandidate는
# Observation 1건에 묶여 있어 이들을 못 담으므로, 유형-무관 AnomalyDraft를 도입한다.
# 비상 스쿽 경로(AnomalyCandidate)는 그대로 둔다(기존 테스트·배선 보존).
# ══════════════════════════════════════════════════════════════════════════════

ANOMALY_TYPE_ADSB_DROPOUT = "adsb_dropout"
ANOMALY_TYPE_LOITERING = "loitering"
ANOMALY_TYPE_MILITARY_APPROACH = "military_approach"
ANOMALY_TYPE_SATELLITE_PROXIMITY = "satellite_proximity"

# ── 임계 상수 (튜닝 지점 분리) ──
# dropout: 교차 확인 여부로 신뢰도 이분(단정 금지 — CLAUDE.md). 상세는 crosscheck.py.
DROPOUT_UNCONFIRMED_CONFIDENCE = 0.42  # 미확인 = 저신뢰 후보(단정 금지 문구)
DROPOUT_CONFIRMED_CONFIDENCE = 0.72  # 교차 확인 = 상향
# 로이터링: 지속 ≥ 임계 AND 변위/경로 비율 낮음(원형·반복). 경로가 너무 짧으면 판정 유보.
LOITERING_MIN_SECONDS = 10 * 60  # 지속 임계(기본 10분)
LOITERING_MIN_PATH_KM = 15.0  # 경로 최소 길이(짧으면 정지·노이즈 → 유보)
LOITERING_MAX_RATIO = 0.35  # 변위/경로길이 ≤ 이 값 = 선회·반복
LOITERING_CONFIDENCE = 0.6  # 행태 휴리스틱(중간 신뢰도)
# 군용기 접근: 명시 is_military 플래그(합성)엔 이 값, 사전 휴리스틱엔 military_db 신뢰도.
MILITARY_EXPLICIT_CONFIDENCE = 0.55
# 위성 근접: over 민감구역 AND now±창 AND 최대앙각 임계(near-overhead=실제 근접).
SAT_PROXIMITY_WINDOW = 60 * 60  # now ± 이 창의 통과만
SAT_PROXIMITY_MIN_ELEV = 70.0  # 최대앙각 임계(천정 근접만 → 노이즈 차단)
SAT_PROXIMITY_CONFIDENCE = 0.4  # 정황(저신뢰)


@dataclass
class AnomalyDraft:
    """유형-무관 이상징후 초안 (P5). 룰이 사실·신뢰도·근거/주체를 확정해 담는다.

    AnomalyCandidate가 Observation 1건에 묶인 것과 달리, evidence/involves를
    (dst_type, dst_id) 튜플 리스트로 들고 있어 어떤 근거 객체 타입도 담을 수 있다
    (dropout=Observation, 위성 근접=OrbitPass/Satellite). actions.create_from_draft가
    dedup·evidence 강제·링크 저장을 수행한다. confidence는 룰이 정한다(explainer는 서술만).
    """

    type: str
    ts: int
    lat: Optional[float]
    lon: Optional[float]
    dedup_key: str  # 자연키 재료(icao24 또는 norad_id)
    evidence: list[tuple[str, str]]  # [(dst_type, dst_id), ...] — ≥1 필수
    involves: list[tuple[str, str]]  # [(dst_type, dst_id), ...]
    confidence: float  # 룰이 확정한 신뢰도
    signal: dict[str, Any] = field(default_factory=dict)  # 설명 재료
    dedup_window: int = ANOMALY_WINDOW_SECONDS

    @property
    def anomaly_id(self) -> str:
        """dedup 자연키. 같은 (유형, 키, 시간창)은 1개 Anomaly로 합쳐진다."""
        return f"anomaly-{self.type}-{self.dedup_key}-{self.ts // self.dedup_window}"


def _overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """두 시간구간 [a_start,a_end] ∩ [b_start,b_end] ≠ ∅ ?"""
    return a_start <= b_end and a_end >= b_start


def detect_adsb_dropout(
    tracks: list,
    latest_obs_by_ac: dict[str, Observation],
    sensitive_regions: list,
    now: int,
    crosscheck: Optional[CrossCheckSource] = None,
) -> list[AnomalyDraft]:
    """ADS-B dropout 후보. Track.has_gap AND 마지막 위치가 민감 Region 내 + 교차 판정.

    CLAUDE.md 기술기준: 단일 소스 결측은 단정하지 않는다. crosscheck로 2차 소스에 부재를
    질의해 — 미확인(None)=저신뢰(0.4대) 후보, 부재 교차 확인(True)=상향(0.7대),
    여전히 관측(False)=우리 쪽 결측은 센서 아티팩트 → **dropout 아님(생성 안 함)**.
    """
    crosscheck = crosscheck or NullCrossCheckSource()
    out: list[AnomalyDraft] = []
    for track in tracks:
        if not track.has_gap:
            continue
        last = latest_obs_by_ac.get(track.aircraft_ref)
        if last is None:
            continue
        region = region_of_point(sensitive_regions, last.lat, last.lon)
        if region is None:  # 민감구역 밖의 gap은 dropout 후보 아님
            continue
        window = (last.ts, now)
        confirmed = crosscheck.confirm_absence(track.aircraft_ref, window)
        if confirmed is False:
            # 2차 소스가 여전히 관측 중 → 우리 결측은 아티팩트, dropout 단정 안 함.
            continue
        confidence = (
            DROPOUT_CONFIRMED_CONFIDENCE
            if confirmed is True
            else DROPOUT_UNCONFIRMED_CONFIDENCE
        )
        out.append(
            AnomalyDraft(
                type=ANOMALY_TYPE_ADSB_DROPOUT,
                ts=last.ts,
                lat=last.lat,
                lon=last.lon,
                dedup_key=track.aircraft_ref,
                evidence=[("Observation", last.id)],
                involves=[("Aircraft", track.aircraft_ref)],
                confidence=confidence,
                signal={
                    "region": region.name,
                    "cross_confirmed": confirmed,  # None(미확인) | True(부재확인)
                    "last_seen_ts": last.ts,
                    "is_synthetic": last.source == "synthetic",
                },
            )
        )
    return out


def detect_loitering(
    tracks: list,
    latest_obs_by_ac: dict[str, Observation],
    now: int,
) -> list[AnomalyDraft]:
    """로이터링 후보. Track 지속 ≥ 임계 AND 변위/경로길이 비율 낮음(원형·반복 패턴).

    직선 통과(변위≈경로)는 제외. 경로가 너무 짧으면(정지·노이즈) 판정 유보.
    근거는 마지막 관측(Observation), 주체는 Aircraft.
    """
    out: list[AnomalyDraft] = []
    for track in tracks:
        duration = track.end_ts - track.start_ts
        if duration < LOITERING_MIN_SECONDS:
            continue
        path = track.path or []
        if len(path) < 3:
            continue
        plen = path_length_km(path)
        if plen < LOITERING_MIN_PATH_KM:  # 거의 정지 → 로이터링 판정 유보(노이즈 차단)
            continue
        disp = haversine_km(path[0][0], path[0][1], path[-1][0], path[-1][1])
        ratio = disp / plen if plen > 0 else 1.0
        if ratio > LOITERING_MAX_RATIO:  # 변위가 크면 통과 중 → 로이터링 아님
            continue
        last = latest_obs_by_ac.get(track.aircraft_ref)
        if last is None:  # evidence로 쓸 관측이 없으면 생성 불가
            continue
        out.append(
            AnomalyDraft(
                type=ANOMALY_TYPE_LOITERING,
                ts=track.end_ts,
                lat=last.lat,
                lon=last.lon,
                dedup_key=track.aircraft_ref,
                evidence=[("Observation", last.id)],
                involves=[("Aircraft", track.aircraft_ref)],
                confidence=LOITERING_CONFIDENCE,
                signal={
                    "duration_min": round(duration / 60, 1),
                    "ratio": round(ratio, 3),
                    "path_km": round(plen, 1),
                    "is_synthetic": last.source == "synthetic",
                },
            )
        )
    return out


def detect_military_approach(
    latest_observations: list[Observation],
    aircraft_map: dict,
    oparea_regions: list,
    now: int,
) -> list[AnomalyDraft]:
    """군용기 접근 후보. is_military(저신뢰 판정) AND Observation within OpArea Region.

    is_military는 관측 소스 플래그(합성 명시) 또는 military_db 휴리스틱(콜사인·대역)으로
    **저신뢰** 판정한다(단정 금지 — 대역은 국가 할당). 근거=Observation, 주체=Aircraft.
    """
    out: list[AnomalyDraft] = []
    for o in latest_observations:
        ac = aircraft_map.get(o.aircraft_ref)
        callsign = ac.callsign if ac else None
        # 휴리스틱 우선 판정(콜사인·대역). 걸리면 그 신뢰도·근거를 쓴다.
        is_mil, mil_conf, reason = classify_military(o.aircraft_ref, callsign)
        if not is_mil and ac is not None and ac.is_military:
            # 관측 소스가 명시적으로 is_military → 저신뢰 정황(합성 주입 등).
            is_mil, mil_conf, reason = (
                True,
                MILITARY_EXPLICIT_CONFIDENCE,
                ("관측 소스 is_military 플래그"),
            )
        if not is_mil:
            continue
        region = region_of_point(oparea_regions, o.lat, o.lon)
        if region is None:  # OpArea 밖이면 접근 이상징후 아님
            continue
        out.append(
            AnomalyDraft(
                type=ANOMALY_TYPE_MILITARY_APPROACH,
                ts=o.ts,
                lat=o.lat,
                lon=o.lon,
                dedup_key=o.aircraft_ref,
                evidence=[("Observation", o.id)],
                involves=[("Aircraft", o.aircraft_ref)],
                confidence=mil_conf,
                signal={
                    "callsign": callsign,
                    "mil_reason": reason,
                    "region": region.name,
                    "is_synthetic": o.source == "synthetic",
                },
            )
        )
    return out


def detect_satellite_proximity(
    orbitpasses: list,
    region_map: dict,
    now: int,
    satellite_map: Optional[dict] = None,
    window: int = SAT_PROXIMITY_WINDOW,
) -> list[AnomalyDraft]:
    """위성 근접 승격 후보. OrbitPass over 민감 Region during now±창 AND 최대앙각 임계.

    P4 발견 #3: 상관 문장으로만 남기지 않고 저신뢰 Anomaly로 승격(evidenced_by OrbitPass,
    involves Satellite). near-overhead(최대앙각≥임계)만 = 실제 근접(전역 통과 나열 아님).
    """
    satellite_map = satellite_map or {}
    out: list[AnomalyDraft] = []
    for p in orbitpasses:
        preg = region_map.get(p.region_ref)
        if preg is None or preg.classification not in SENSITIVE_CLASSIFICATIONS:
            continue
        if (
            p.max_elevation < SAT_PROXIMITY_MIN_ELEV
        ):  # 스치는 통과 제외(near-overhead만)
            continue
        if not _overlaps(p.start_ts, p.end_ts, now - window, now + window):
            continue
        # 위치 = 지상궤적 중점(없으면 판정 불가 → 스킵)
        if not p.ground_track:
            continue
        mid = p.ground_track[len(p.ground_track) // 2]
        sat = satellite_map.get(p.satellite_ref)
        out.append(
            AnomalyDraft(
                type=ANOMALY_TYPE_SATELLITE_PROXIMITY,
                ts=p.start_ts,
                lat=mid[0],
                lon=mid[1],
                dedup_key=p.satellite_ref,
                evidence=[("OrbitPass", p.id)],
                involves=[("Satellite", p.satellite_ref)],
                confidence=SAT_PROXIMITY_CONFIDENCE,
                signal={
                    "sat_name": sat.name if sat else p.satellite_ref,
                    "norad_id": p.satellite_ref,
                    "max_elevation": p.max_elevation,
                    "region": preg.name,
                    "start_ts": p.start_ts,
                    "end_ts": p.end_ts,
                    "is_synthetic": p.source == "synthetic",
                },
            )
        )
    return out
