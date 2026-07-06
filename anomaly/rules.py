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
from anomaly.isr_satellites import is_signal_promotable_pass
from anomaly.mil_enrich import MilEnrichmentSource, NullMilEnrichment
from anomaly.military_db import classify_military
from ontology.geo import haversine_km, path_length_km, region_of_point
from ontology.model import (
    ANOMALY_WINDOW_SECONDS,
    SENSITIVE_CLASSIFICATIONS,
    Observation,
    gap_threshold_seconds,
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
ANOMALY_TYPE_RAPID_MANEUVER = "rapid_maneuver"

# ── 임계 상수 (튜닝 지점 분리) ──
# dropout: 교차 확인 여부로 신뢰도 이분(단정 금지 — CLAUDE.md). 상세는 crosscheck.py.
DROPOUT_UNCONFIRMED_CONFIDENCE = 0.42  # 미확인 = 저신뢰 후보(단정 금지 문구)
DROPOUT_CONFIRMED_CONFIDENCE = 0.72  # 교차 확인 = 상향
# dropout = "지금 끊겨 있음". 침묵이 이 창(초) 안에서 시작한 것만 활성 후보 — 오래 전 침묵은
# 지난 일(스팸·콜드스타트 stale DB 방어). 침묵 하한은 gap_threshold_seconds()(폴 간격 인지).
DROPOUT_ACTIVE_WINDOW_SECONDS = 30 * 60
# 착륙 억제(보너스): 마지막 관측이 저고도(<이 값, m)면 착륙 추정 → 신뢰도 하향(지상 접촉은 전면 억제).
DROPOUT_LANDING_ALT_M = 1000.0
DROPOUT_LANDING_CONFIDENCE = 0.3  # 착륙 추정 시 상한(단정 금지 — 정황도 아래로)
# 로이터링: 지속 ≥ 임계 AND 변위/경로 비율 낮음(원형·반복). 경로가 너무 짧으면 판정 유보.
LOITERING_MIN_SECONDS = 10 * 60  # 지속 임계(기본 10분)
LOITERING_MIN_PATH_KM = 15.0  # 경로 최소 길이(짧으면 정지·노이즈 → 유보)
LOITERING_MAX_RATIO = 0.35  # 변위/경로길이 ≤ 이 값 = 선회·반복
LOITERING_CONFIDENCE = 0.6  # 행태 휴리스틱(중간 신뢰도)
# 군용기 접근: 명시 is_military 플래그(합성)엔 이 값, 사전 휴리스틱엔 military_db 신뢰도.
MILITARY_EXPLICIT_CONFIDENCE = 0.55
# 공개 커뮤니티 DB 플래그(adsb.fi dbFlags&1)는 콜사인·대역 휴리스틱보다 근거가 한 단계 강하나
# 여전히 커뮤니티 DB(오탐·미탐 존재) → 저신뢰 상한(≤0.65)에 둔다(DR-0013 결정 5).
MILITARY_DB_FLAG_CONFIDENCE = 0.65
# 위성 근접: over 민감구역 AND now±창 AND 최대앙각 임계(near-overhead=실제 근접).
SAT_PROXIMITY_WINDOW = 60 * 60  # now ± 이 창의 통과만
SAT_PROXIMITY_MIN_ELEV = 70.0  # 최대앙각 임계(천정 근접만 → 노이즈 차단)
SAT_PROXIMITY_CONFIDENCE = 0.4  # 정황(저신뢰)
# 급기동(rapid maneuver): 같은 Track의 연속 Observation에서 고도·속도 변화율이 임계 초과.
# 단위 — 고도=미터(OpenSky baro_altitude), 속도=m/s(OpenSky velocity). mapping.py 매핑 참조.
# 임계 근거(보수적 — 민항 정상 기동을 배제): 민항기 정상 상승/강하는 대략 1500~3000 ft/min,
# 통상적 비상강하도 6000 ft/min 부근이다. 따라서 6000 ft/min **이상**만 후보로 본다(회피·전투
# 기동·비상강하 등). 정상 순항 가속은 ~0.5 m/s² 이하 → 3 m/s²(≈0.3g) 지속을 속도 급변으로 본다.
FT_PER_M = (
    3.28084  # 미터 → 피트(고도율을 ft/min으로 서술할 때만 사용, 판정은 SI 단위로)
)
MANEUVER_VERTICAL_RATE_FPM = 6000.0  # 수직 변화율 임계(ft/min) — 근거 상단 주석
MANEUVER_VERTICAL_RATE_MPS = MANEUVER_VERTICAL_RATE_FPM / FT_PER_M / 60.0  # ≈ 30.5 m/s
MANEUVER_ACCEL_MPS2 = 3.0  # 속도 급변(가속/감속) 임계(m/s²) — ≈ 0.3g
# 비물리적 상한 — 이 이상은 기압고도 스파이크·GPS 튐 등 데이터 글리치로 보고 해당 구간을 무효화
# (런을 끊는다 = 오탐 방어). 물리적으로 항공기가 지속할 수 없는 값.
MANEUVER_MAX_VERTICAL_MPS = 150.0  # ≈ 29,500 ft/min 이상 = 물리적 불가(기압고도 글리치)
MANEUVER_MAX_GROUND_SPEED_MPS = (
    600.0  # 위치 점프 함의 지상속도 상한(≈2160 km/h) 초과 = GPS 튐
)
MANEUVER_MIN_OBSERVATIONS = 4  # 최소 관측 수(구간 ≥3 확보) — 미만이면 판정 유보(노이즈)
MANEUVER_MIN_RUN = 2  # 연속 초과 구간 ≥2(같은 방향) — 단일점 글리치 방어
MANEUVER_CONFIDENCE_BASE = 0.5  # 정황(휴리스틱, 단정 금지)
MANEUVER_CONFIDENCE_STRONG = 0.62  # 고도+속도 동시 급변 시 상향(0.5~0.65 범위 유지)


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
    """ADS-B dropout 후보 = **지금 끊겨 있음**. now−마지막관측 > 침묵 임계 AND 마지막 위치가
    민감 Region 내 AND 침묵이 활성 창 안에서 시작 + 교차 판정.

    의미(과거 오탐 폭주 교정): 과거 has_gap 이력만으로 발화하지 않는다 — **현재 관측이 신선하면
    (침묵 임계 미만) 절대 후보 아님**(매 사이클 새 관측을 받는 정상 송신 기체는 dropout 아님).
    침묵 임계 = gap_threshold_seconds()(실제 폴 간격의 3배 이상 — 60s 폴이면 180s). 침묵 시작
    (=마지막 관측 ts)이 draft.ts라 dedup이 **침묵 이벤트 단위**로 이뤄진다(같은 침묵=1회,
    복귀 후 재침묵=새 이벤트, DROPOUT dedup_window=1로 정확 앵커).

    CLAUDE.md 기술기준: 단일 소스 결측은 단정하지 않는다. crosscheck로 2차 소스에 부재를
    질의해 — 미확인(None)=저신뢰(0.4대) 후보, 부재 교차 확인(True)=상향(0.7대),
    여전히 관측(False)=우리 쪽 결측은 센서 아티팩트 → **dropout 아님(생성 안 함)**.
    착륙 억제: 지상 접촉(on_ground)이면 후보에서 제외, 저고도면 착륙 추정으로 신뢰도 하향.
    """
    crosscheck = crosscheck or NullCrossCheckSource()
    silence_threshold = gap_threshold_seconds()
    out: list[AnomalyDraft] = []
    for track in tracks:
        last = latest_obs_by_ac.get(track.aircraft_ref)
        if last is None:
            continue
        silence = now - last.ts
        # 지금 끊겨 있는가? 신선한 관측(침묵 임계 미만)은 과거 gap 이력과 무관하게 후보 아님.
        if silence <= silence_threshold:
            continue
        # 활성 창 밖(오래 전 침묵)은 지난 일 — 재발화·콜드스타트 stale DB 폭주 방어.
        if silence > DROPOUT_ACTIVE_WINDOW_SECONDS:
            continue
        region = region_of_point(sensitive_regions, last.lat, last.lon)
        if region is None:  # 민감구역 밖의 침묵은 dropout 후보 아님
            continue
        # 착륙 억제 — 지상 접촉은 dropout 아님(착륙·택싱). 저고도는 아래서 신뢰도만 하향.
        if last.on_ground:
            continue
        confirmed = crosscheck.confirm_absence(track.aircraft_ref, (last.ts, now))
        if confirmed is False:
            # 2차 소스가 여전히 관측 중 → 우리 결측은 아티팩트, dropout 단정 안 함.
            continue
        confidence = (
            DROPOUT_CONFIRMED_CONFIDENCE
            if confirmed is True
            else DROPOUT_UNCONFIRMED_CONFIDENCE
        )
        likely_landing = last.alt is not None and last.alt < DROPOUT_LANDING_ALT_M
        if likely_landing:  # 저고도 침묵 = 착륙 추정 → 상한을 저신뢰로(단정 금지 강화)
            confidence = min(confidence, DROPOUT_LANDING_CONFIDENCE)
        out.append(
            AnomalyDraft(
                type=ANOMALY_TYPE_ADSB_DROPOUT,
                ts=last.ts,  # 침묵 시작(=마지막 관측) — dedup 앵커
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
                    "silence_seconds": int(silence),
                    "likely_landing": likely_landing,
                    "is_synthetic": last.source == "synthetic",
                },
                # dedup을 침묵 시작 시각(last.ts) 정확값으로 앵커 — 같은 침묵은 1개 Anomaly,
                # 복귀 후 재침묵은 last.ts가 달라 새 이벤트로 정당하게 발화(버킷 경계 무관).
                dedup_window=1,
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
    mil_enrich: Optional[MilEnrichmentSource] = None,
) -> list[AnomalyDraft]:
    """군용기 접근 후보. is_military(저신뢰 판정) AND Observation within OpArea Region.

    is_military는 세 신호로 **저신뢰** 판정한다(단정 금지 — 대역은 국가 할당, DB는 커뮤니티).
    우선순위(강→약):
      1. **공개 커뮤니티 DB 플래그**(mil_enrich, adsb.fi dbFlags&1) — 라이브 최강 신호(0.65).
      2. **콜사인·대역 휴리스틱**(military_db) — 0.5~0.65.
      3. **관측 소스 명시 is_military 플래그**(합성 주입 등) — 0.55(불변 경로).
    근거=Observation, 주체=Aircraft. signal.mil_source가 어느 신호인지 명시해 explainer가
    출처에 맞는 caveat를 서술한다(provenance 문장 전파). 기본 mil_enrich=Null(보강 없음).
    """
    mil_enrich = mil_enrich or NullMilEnrichment()
    out: list[AnomalyDraft] = []
    for o in latest_observations:
        ac = aircraft_map.get(o.aircraft_ref)
        callsign = ac.callsign if ac else None
        # 1) 공개 DB 플래그(라이브 보강) — 콜사인·대역 휴리스틱보다 상위. 게이트 off면 항상 None.
        db_hit = mil_enrich.lookup(o.aircraft_ref)
        # 2) 콜사인·대역 휴리스틱.
        cs_mil, cs_conf, cs_reason = classify_military(o.aircraft_ref, callsign)
        if db_hit is not None:
            is_mil, mil_conf, reason, mil_source = (
                True,
                MILITARY_DB_FLAG_CONFIDENCE,
                db_hit[1],
                "db_flag",
            )
        elif cs_mil:
            is_mil, mil_conf, reason, mil_source = (
                True,
                cs_conf,
                cs_reason,
                "heuristic",
            )
        elif ac is not None and ac.is_military:
            # 3) 관측 소스가 명시적으로 is_military → 저신뢰 정황(합성 주입 등). 불변.
            is_mil, mil_conf, reason, mil_source = (
                True,
                MILITARY_EXPLICIT_CONFIDENCE,
                "관측 소스 is_military 플래그",
                "explicit",
            )
        else:
            continue
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
                    "mil_source": mil_source,  # db_flag | heuristic | explicit
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

    **ISR 허용목록 게이트(기본 on)**: 허용목록 밖 실 위성(ISS·밝은 위성 등)의 통과는
    승격하지 않는다 — 이 게이트가 없던 시절 stations/visual 그룹 통과가 전부 저신뢰
    Anomaly로 승격돼 경고 스팸이 됐다(isr_satellites.py 배경 참조). 합성(synthetic)은
    우회 → replay 데모 발화 유지. 이 판정은 isr_satellites가 SSOT.
    """
    satellite_map = satellite_map or {}
    out: list[AnomalyDraft] = []
    for p in orbitpasses:
        if not is_signal_promotable_pass(p.source, p.satellite_ref):
            continue  # 허용목록 밖 실 위성 → Anomaly 승격 금지(표시는 유지)
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


# ── 급기동(rapid maneuver) — 고도·속도 급변(임계), 근거 = Observation 시퀀스 ──────
def _longest_run(
    metrics: list[Optional[float]], threshold: float
) -> Optional[tuple[int, int]]:
    """|metric| ≥ threshold를 **같은 방향으로 연속** 만족하는 최장 런의 (구간 시작, 끝) 인덱스.

    metrics[i] = 구간 i(obs[i]→obs[i+1])의 값(수직률 또는 가속). None = 무효 구간
    (글리치·GPS 튐 → 런을 끊는다). 부호가 바뀌면(상승↔강하) 런도 끊는다(글리치 방어).
    런 길이(구간 수) ≥ MANEUVER_MIN_RUN 인 것만 후보. 반환 (i0, i1): 구간 [i0, i1] 포함
    → 근거 관측은 obs[i0 .. i1+1] (≥ MANEUVER_MIN_RUN+1 건).
    """
    best: Optional[tuple[int, int]] = None
    i, n = 0, len(metrics)
    while i < n:
        m = metrics[i]
        if m is None or abs(m) < threshold:
            i += 1
            continue
        sign = 1 if m > 0 else -1
        j = i
        while j + 1 < n:
            mj = metrics[j + 1]
            if mj is None or abs(mj) < threshold or (1 if mj > 0 else -1) != sign:
                break
            j += 1
        if (j - i + 1) >= MANEUVER_MIN_RUN and (
            best is None or (j - i) > (best[1] - best[0])
        ):
            best = (i, j)
        i = j + 1
    return best


def _maneuver_segment(obs: list[Observation]) -> Optional[dict]:
    """관측 시퀀스 → 급변 구간(있으면 dict, 없으면 None).

    구간별 수직 변화율(Δalt/Δt, m/s)·가속(Δvel/Δt, m/s²)을 구하되, 위치 점프가 함의하는
    지상속도가 비물리적(>MANEUVER_MAX_GROUND_SPEED_MPS)이거나 수직률이 비물리적
    (>MANEUVER_MAX_VERTICAL_MPS)인 구간은 데이터 글리치로 무효화한다(런에서 배제).
    반환 dict: seg(근거 관측 리스트 ≥2)·kind(vertical|speed|both)·peak_vertical_mps·peak_accel_mps2.
    """
    vrates: list[Optional[float]] = []
    accels: list[Optional[float]] = []
    for a, b in zip(obs, obs[1:]):
        dt = b.ts - a.ts
        if dt <= 0:  # 동일·역행 시각 = 무효 구간
            vrates.append(None)
            accels.append(None)
            continue
        # GPS 튐: 위치 점프가 함의하는 지상속도가 비물리적이면 구간 전체 무효.
        ground_mps = haversine_km(a.lat, a.lon, b.lat, b.lon) * 1000.0 / dt
        if ground_mps > MANEUVER_MAX_GROUND_SPEED_MPS:
            vrates.append(None)
            accels.append(None)
            continue
        if a.alt is not None and b.alt is not None:
            vr = (b.alt - a.alt) / dt
            # 비물리적 수직률(기압고도 스파이크) = 무효.
            vrates.append(None if abs(vr) > MANEUVER_MAX_VERTICAL_MPS else vr)
        else:
            vrates.append(None)
        if a.velocity is not None and b.velocity is not None:
            accels.append((b.velocity - a.velocity) / dt)
        else:
            accels.append(None)

    vrun = _longest_run(vrates, MANEUVER_VERTICAL_RATE_MPS)
    srun = _longest_run(accels, MANEUVER_ACCEL_MPS2)
    if vrun is None and srun is None:
        return None

    # 수직 런 우선(근거 구간). 같은 구간에서 속도도 급변하면 both로 상향.
    if vrun is not None:
        i0, i1 = vrun
        kind = "vertical"
        if srun is not None and not (srun[1] < i0 or srun[0] > i1):
            kind = "both"
    else:
        assert srun is not None
        i0, i1 = srun
        kind = "speed"
    seg = obs[i0 : i1 + 2]  # 구간 [i0,i1] → 관측 i0..i1+1
    peak_v = max(
        (abs(vrates[k]) for k in range(i0, i1 + 1) if vrates[k] is not None),
        default=0.0,
    )
    peak_a = max(
        (abs(accels[k]) for k in range(i0, i1 + 1) if accels[k] is not None),
        default=0.0,
    )
    return {
        "seg": seg,
        "kind": kind,
        "peak_vertical_mps": peak_v,
        "peak_accel_mps2": peak_a,
    }


def detect_rapid_maneuver(
    tracks: list,
    observations_by_ac: dict[str, list[Observation]],
    now: int,
) -> list[AnomalyDraft]:
    """급기동 후보. 같은 Track의 연속 Observation에서 고도/속도 변화율이 보수적 임계 초과.

    민항 정상 기동(상승·강하 ~1500~3000 ft/min)을 배제하도록 임계를 6000 ft/min(수직)·
    3 m/s²(가속)로 잡는다(상단 상수 주석에 근거). 노이즈 방어:
      - 최소 관측 수(MANEUVER_MIN_OBSERVATIONS) 미만이면 판정 유보.
      - 단일 구간이 아니라 **연속 ≥2 구간이 같은 방향으로** 임계 초과해야 후보(단일점 방어).
      - 비물리적 수직률(기압고도 스파이크)·지상속도(GPS 튐)는 구간을 무효화(런을 끊음).
    근거(evidence) = 급변 구간의 Observation 시퀀스(≥2), 주체(involves) = Aircraft.
    confidence는 휴리스틱(정황) — 단정하지 않는다(0.5, 고도+속도 동시면 0.62로 상향).
    """
    out: list[AnomalyDraft] = []
    for track in tracks:
        obs = observations_by_ac.get(track.aircraft_ref) or []
        if len(obs) < MANEUVER_MIN_OBSERVATIONS:
            continue
        obs = sorted(obs, key=lambda o: o.ts)
        seg = _maneuver_segment(obs)
        if seg is None:
            continue
        seg_obs = seg["seg"]
        last = seg_obs[-1]
        kind = seg["kind"]
        confidence = (
            MANEUVER_CONFIDENCE_STRONG if kind == "both" else MANEUVER_CONFIDENCE_BASE
        )
        out.append(
            AnomalyDraft(
                type=ANOMALY_TYPE_RAPID_MANEUVER,
                ts=last.ts,
                lat=last.lat,
                lon=last.lon,
                dedup_key=track.aircraft_ref,
                evidence=[("Observation", o.id) for o in seg_obs],  # 시퀀스 ≥2
                involves=[("Aircraft", track.aircraft_ref)],
                confidence=confidence,
                signal={
                    "kind": kind,  # vertical | speed | both
                    "peak_vertical_fpm": round(
                        seg["peak_vertical_mps"] * FT_PER_M * 60.0
                    ),
                    "peak_accel_mps2": round(seg["peak_accel_mps2"], 1),
                    "n_obs": len(seg_obs),
                    "is_synthetic": last.source == "synthetic",
                },
            )
        )
    return out
