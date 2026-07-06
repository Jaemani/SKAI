"""온톨로지 객체 정의 — ontology.md §1 미러.

여기 dataclass는 ontology.md의 Object Type을 그대로 옮긴 것이다(척추).
Foundry Ontology가 뚫리면 이 스키마를 Object Type으로 정의하고 store_foundry가
같은 dataclass를 write/read 한다. store_local(SQLite)은 "보험"으로 동일 스키마를 미러한다.

Event = 온톨로지 이전의 공통 중간표현(data-sources.md §정규화 스키마).
모든 소스를 Event로 정규화한 뒤 mapping.py가 온톨로지 객체로 매핑한다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Optional

# ──────────────────────────────────────────────
# 상수 — 관심지역(KADIZ). 데모는 1곳 고정, 좌표는 교체 쉽게 상수로 분리.
# (data-sources.md "관심지역 시드": lat 32~39, lon 122~132)
# ──────────────────────────────────────────────
KADIZ_BBOX = {"lamin": 32.0, "lomin": 122.0, "lamax": 39.0, "lomax": 132.0}

# 데모 작전구역(OpArea) 소구역 — KADIZ 내부 서해(황해) 상공 1곳(P5, DR-0007 결정 2).
# classification="OpArea"는 ontology.md Region에 이미 정의된 값 → 스키마 변경 아님, 데이터 추가.
# 군용기 접근 룰(is_military + Observation within OpArea)과 위성 근접 룰의 민감구역으로 쓰인다.
OPAREA_WEST_BBOX = {"lamin": 35.0, "lomin": 123.5, "lamax": 37.5, "lomax": 125.5}

# Track custody: 한 항공기의 연속 관측 간격이 이 값(초)을 넘으면 has_gap=True.
# 이 값은 **빠른 폴 가정**의 base다 — 실효 임계는 gap_threshold_seconds()가 실제 폴 간격에
# 맞춰 상향한다(느린 폴에서 매 관측이 gap으로 오판되는 것 방지). ADS-B dropout·custody 공용.
GAP_THRESHOLD_SECONDS = 90
# 침묵/gap 임계 = max(base, k×실제 폴 간격). k=3: 관측 하나를 놓쳐도(정상 지연) gap으로 보지
# 않되, 연속 3주기 침묵이면 끊긴 것으로 본다. 폴 간격은 SKAI_POLL_INTERVAL로 주입(poller와 동일 소스).
DROPOUT_POLL_MULTIPLIER = 3


def poll_interval_seconds() -> Optional[int]:
    """현재 폴 간격(초). SKAI_POLL_INTERVAL(하위호환 POLL_INTERVAL) 환경변수, 없으면 None.

    poller(connectors.opensky.main)가 읽는 것과 같은 환경변수 — 임계가 실제 관측 리듬을 따르게
    한다. 값이 없거나 파싱 불가면 None(호출자가 base로 폴백). 하한 1s(0·음수 방어).
    """
    raw = os.environ.get("SKAI_POLL_INTERVAL") or os.environ.get("POLL_INTERVAL")
    if not raw:
        return None
    try:
        return max(1, int(raw))
    except ValueError:
        return None


def gap_threshold_seconds() -> int:
    """gap/침묵 판정 임계(초) = max(GAP_THRESHOLD_SECONDS, k×실제 폴 간격).

    폴 간격 미상(테스트·단발 실행)이면 base GAP_THRESHOLD_SECONDS를 그대로 쓴다(기존 동작 불변).
    60s 폴이면 180s가 되어, 60~75s 간격의 정상 관측을 dropout으로 오판하지 않는다.
    """
    poll = poll_interval_seconds()
    if poll is None:
        return GAP_THRESHOLD_SECONDS
    return max(GAP_THRESHOLD_SECONDS, DROPOUT_POLL_MULTIPLIER * poll)


# Anomaly 상태 (ontology.md §1). candidate=룰 후보, confirmed/dismissed=사람 승인,
# resolved=반증 증거 기반 자동 해소(복귀 관측으로 침묵이 끝남 — actions.scan_and_resolve).
# resolved는 사람 결정(confirmed/dismissed)과 달리 폴러가 자동 전이하며, 근거 없는 상태 전이
# 금지 원칙의 연장으로 복귀 관측을 evidenced_by 링크로 남긴다(store.resolve_anomaly).
ANOMALY_STATUSES = ("candidate", "confirmed", "dismissed", "resolved")

# Anomaly dedup 시간창(초): 같은 (기체, 유형)은 이 창 안에서 1개만 생성.
# 비상 스쿽은 수 분간 지속되며 매 사이클 같은 관측이 재등장 → 중복 Anomaly 방지.
ANOMALY_WINDOW_SECONDS = 600

# 뉴스(NewsEvent) 신뢰도 상한 — 뉴스는 확증 아님(저신뢰 증거).
# DR-0005: NewsEvent.confidence ≤ 0.4, 하드 소스(항적·궤도)로 교차검증한다.
NEWS_MAX_CONFIDENCE = 0.4

# 증거 객체 id 접두어 → 온톨로지 객체 타입. SituationAssessment의 cites 링크가
# 어떤 Object Type을 가리키는지 id만으로 역추적하는 데 쓴다(문장별 cites → 링크 타입 결정).
# id 규약은 각 dataclass docstring이 SSOT — 여기 표는 그 규약의 역인덱스일 뿐이다.
CITE_ID_PREFIXES = (
    ("anomaly-", "Anomaly"),
    ("pass-", "OrbitPass"),
    ("wx-", "WeatherState"),
    ("news-", "NewsEvent"),
    ("track-", "Track"),
)


def cite_object_type(cite_id: str) -> str:
    """cites 객체 id → 온톨로지 Object Type 이름. 접두어 규약으로 판별.

    Observation id는 f"{icao24}-{ts}" 자연키라 전용 접두어가 없다 → 위 표에 안 걸리면
    Observation으로 본다(관측이 가장 흔한 증거 객체). SituationAssessment의 cites→ 링크
    dst_type 결정에 쓴다. tools가 사실을 만들 때 타입을 이미 알므로 assessment는 그 타입을
    우선 쓰고, 이 함수는 폴백/검증용이다.
    """
    for prefix, obj_type in CITE_ID_PREFIXES:
        if cite_id.startswith(prefix):
            return obj_type
    return "Observation"


def bbox_to_polygon(bbox: dict) -> list[list[float]]:
    """bbox(dict) → 닫힌 폴리곤 [[lat, lon], ...] (Leaflet 좌표 순서)."""
    lamin, lomin = bbox["lamin"], bbox["lomin"]
    lamax, lomax = bbox["lamax"], bbox["lomax"]
    return [
        [lamin, lomin],
        [lamin, lomax],
        [lamax, lomax],
        [lamax, lomin],
        [lamin, lomin],
    ]


# ──────────────────────────────────────────────
# Object Types (ontology.md §1)
# ──────────────────────────────────────────────
@dataclass
class Region:
    """관심지역/지오펜스. classification: ADIZ / OpArea / civil."""

    id: str  # PK
    name: str
    classification: str
    geo: list[list[float]]  # 폴리곤 [[lat, lon], ...]


@dataclass
class Aircraft:
    """실세계 항공기 (엔티티 해소 대상). icao24 = PK."""

    icao24: str
    callsign: Optional[str] = None
    registration: Optional[str] = None
    operator_ref: Optional[str] = None
    type: Optional[str] = None
    is_military: bool = False


@dataclass
class Observation:
    """ADS-B 상태벡터 = 증거 객체.

    provenance(source·source_url·ts)는 store 레벨에서 write 시 강제된다
    (누락 write는 거부 = ProvenanceError). ontology.md §3 "근거 없는 객체 거부"의
    선행 구현.
    """

    id: str  # f"{icao24}-{ts}" — (icao24, ts) 자연 dedup 키
    aircraft_ref: str  # → Aircraft.icao24
    ts: int  # 관측 Unix 시각 (last_contact)
    lat: float
    lon: float
    alt: Optional[float] = None
    velocity: Optional[float] = None
    heading: Optional[float] = None
    squawk: Optional[str] = None  # str! ("7700" 문자열 비교 — P0A gotcha 2)
    on_ground: bool = False
    # ── provenance (필수) ──
    source: str = ""
    source_url: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)


@dataclass
class Track:
    """한 항공기의 시계열 경로(custody). Observation을 icao24로 묶어 재구성."""

    id: str  # f"track-{icao24}"
    aircraft_ref: str
    start_ts: int
    end_ts: int
    path: list[list[float]]  # [[lat, lon], ...] 시간순
    has_gap: bool = False


@dataclass
class Event:
    """온톨로지 이전 공통 중간표현 (data-sources.md §정규화 스키마).

    kind: "aircraft" | "satellite" | "weather" | "news".
    citation은 source + source_url + fetched_at로 항상 역추적 가능.
    """

    id: str
    source: str
    source_url: str
    fetched_at: int
    kind: str
    ts: int
    lat: Optional[float] = None
    lon: Optional[float] = None
    alt: Optional[float] = None
    attrs: dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0


@dataclass
class Anomaly:
    """파생 이상징후 (ontology.md §1). 룰 후보 → explainer 설명·신뢰도 → CreateAnomaly.

    provenance 백본: evidence(evidenced_by) 링크 없이는 생성 불가하다
    (ontology.md §3 "근거 없는 Anomaly는 Action이 거부"). 강제는 store.validate_evidence로
    write_anomaly에서 집행 = 어떤 경로로도 근거 없는 Anomaly는 저장 불가.

    status: candidate(룰 후보) → confirmed/dismissed(사람 승인 = human-on-the-loop).
    geo는 이상징후 위치(점) lat/lon. Region의 폴리곤과 달리 단일 좌표.
    """

    id: str  # PK, f"anomaly-{type}-{aircraft_ref}-{window}" (dedup 자연키)
    type: str  # ontology.md §4 유형 ("emergency_squawk" 등)
    ts: int  # 탐지 기준 관측 시각
    confidence: float  # 0~1 (explainer 산출)
    status: str = "candidate"
    lat: Optional[float] = None  # 이상징후 위치(점)
    lon: Optional[float] = None
    explanation: str = ""  # explainer가 생성한 설명문
    explainer_backend: str = ""  # template | claude_cli | aip_logic
    created_at: int = 0  # Anomaly 생성 Unix 시각
    attrs: dict[str, Any] = field(default_factory=dict)  # squawk, is_synthetic 등


# ──────────────────────────────────────────────
# P3 융합 객체 (ontology.md §1) — 위성·기상·뉴스·주체
# 4종 소스가 같은 온톨로지에 시공간 정렬되도록, 각 소스를 아래 객체로 매핑한다.
# ──────────────────────────────────────────────
@dataclass
class Satellite:
    """위성 (ontology.md §1). norad_id = PK (엔티티 해소 대상).

    Celestrak TLE에서 카탈로그번호·이름·궤도 epoch을 추출. OrbitPass가 —of→ 로 참조.
    """

    norad_id: str  # PK (TLE 카탈로그 번호)
    name: str
    operator_ref: Optional[str] = None  # → Operator.id (satop). P3은 미배선(스텁)
    object_type: Optional[str] = None  # PAYLOAD / ROCKET BODY / DEBRIS (이름 휴리스틱)
    tle_epoch: Optional[str] = None  # TLE epoch (ISO8601 UTC 문자열)
    source: str = "celestrak"
    source_url: str = ""


@dataclass
class OrbitPass:
    """관심지역 상공 통과창 (ontology.md §1). 위성 지상궤적이 Region bbox를 지나는 구간.

    of→ Satellite, over→ Region 링크로 위성-지역 시공간 상관을 표현(P5 correlated_with 토대).
    max_elevation = 통과 중 Region 중심에서 본 최대 앙각(°). ground_track = 지도 레이어용 점열.
    provenance(source_url = TLE 질의 URL)는 파생객체지만 citation을 위해 보존한다.
    """

    id: str  # PK, f"pass-{norad_id}-{start_ts}" (통과 시작시각 자연키)
    satellite_ref: str  # → Satellite.norad_id
    region_ref: str  # → Region.id
    start_ts: int  # 통과 진입 Unix 시각
    end_ts: int  # 통과 이탈 Unix 시각
    max_elevation: float  # Region 중심 기준 최대 앙각(°)
    ground_track: list[list[float]] = field(default_factory=list)  # [[lat, lon], ...]
    source: str = "celestrak"
    source_url: str = ""


@dataclass
class WeatherState:
    """지역 기상 (ontology.md §1). METAR 실황 → ISR 임무 가용성 컨텍스트.

    단위는 필드명에 명시(P0A gotcha 4: visib=statute miles, ceiling=피트). 혼용 금지.
    provenance(source·source_url·ts)는 증거 객체처럼 write 시 강제된다.
    """

    id: str  # PK, f"wx-{station}-{ts}"
    region_ref: str  # → Region.id (공항이 속한 관심지역)
    ts: int  # 관측 Unix 시각 (obsTime)
    station: str  # 공항 ICAO (RKSI 등)
    lat: Optional[float] = None
    lon: Optional[float] = None
    wind_dir: Optional[int] = None  # 풍향(°, 진북). 가변풍(VRB)은 None + attrs 기록
    wind_speed_kt: Optional[float] = None  # 풍속(노트)
    visibility_sm: Optional[float] = None  # 시정(statute miles — 단위 명시)
    ceiling_ft: Optional[int] = None  # 실링(피트, 최저 BKN/OVC). None = 무제한
    flight_category: Optional[str] = None  # VFR / MVFR / IFR / LIFR
    conditions: str = ""  # rawOb 원문 METAR (citation)
    # ── provenance (필수) ──
    source: str = ""
    source_url: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)  # temp, dewp, altim, clouds 등


@dataclass
class NewsEvent:
    """OSINT/뉴스 = 증거 객체(저신뢰) (ontology.md §1).

    confidence ≤ NEWS_MAX_CONFIDENCE(0.4) — 뉴스는 확증이 아니라 정황이다(DR-0005).
    mentions→ Region/Aircraft 링크로 뉴스↔실체를 잇는다(엔티티 링킹).
    entities = 매칭된 별칭/키워드(설명·감사용). provenance는 증거 객체처럼 강제.
    """

    id: str  # PK, f"news-{url 해시}"
    source: str  # "gdelt"
    source_url: str  # 기사 원문 URL (citation PK)
    ts: int  # 기사 시각 (seendate 파싱)
    title: str
    summary: str = ""
    lat: Optional[float] = None  # geo? (GDELT artlist는 대개 없음)
    lon: Optional[float] = None
    confidence: float = 0.3  # 저신뢰 (≤ NEWS_MAX_CONFIDENCE 로 clamp)
    entities: list[str] = field(default_factory=list)  # 매칭된 지역 별칭 등
    attrs: dict[str, Any] = field(
        default_factory=dict
    )  # domain, language, sourcecountry


@dataclass
class Operator:
    """귀속용 주체 (ontology.md §1). airline / airforce / satop.

    P3은 스키마·저장 경로만 확보(NewsEvent mentions→Operator 확장 여지). exact-match 링킹만
    허용(DR-0005: NER 파이프라인은 범위 밖).
    """

    id: str  # PK
    name: str
    kind: str  # airline / airforce / satop
    country: Optional[str] = None


# ──────────────────────────────────────────────
# P4 산출 인텔 객체 (ontology.md §1) — SituationAssessment
# 자연어 질의 → 병렬 read → 문장별 cites가 강제된 지역 요약. Q&A가 아니라
# "산출 인텔 객체 생성"(GenerateSituationAssessment 액션)임을 구조로 보인다(DR-0006).
# ──────────────────────────────────────────────
@dataclass
class AssessmentSentence:
    """SituationAssessment를 이루는 한 문장.

    DR-0006 조립 강제의 최소 단위: **각 문장이 근거 객체 id(cites)를 갖고 태어난다**.
    citation은 LLM 생성이 아니라 사실→문장 조립의 부산물이라, cites 없는 문장은 애초에
    만들어지지 않는다. write_assessment가 문장 단위로 다시 검증(cites=[]면 거부).

    cites = 근거 객체 id 리스트 (Observation/Anomaly/OrbitPass/WeatherState/NewsEvent).
    kind = summary | anomaly | satellite | weather | news | correlation (섹션 분류·UI용).
    confidence = 이 문장 하나의 신뢰도(하드 소스 高·뉴스 低 — 문장별로 다르다).
    """

    text: str
    cites: list[str]  # 근거 객체 id (빈 리스트면 Assessment 진입 거부)
    confidence: float
    kind: str = ""


@dataclass
class SituationAssessment:
    """산출 인텔 객체 (ontology.md §1). 지역+시간창에 대한 근거 강제 요약.

    aggregates→ Anomaly, cites→ Observation/NewsEvent/OrbitPass/WeatherState 링크로
    provenance 그래프를 이룬다(ontology.md §0 스멜테스트 4). 채팅 답변 = 이 객체의 뷰.
    window_start/end = 파싱된 시간창(투명성 위해 응답에 노출). produced_by = 서술 백엔드.
    """

    id: str  # PK, f"assess-{region}-{created_at}"
    region_ref: str  # → Region.id
    window_start: int  # 질의 시간창 시작 Unix 시각
    window_end: int  # 질의 시간창 끝 Unix 시각
    query: str  # 원 자연어 질의(감사)
    summary: str  # 헤드라인 요약문(문장들의 첫 줄)
    sentences: list[AssessmentSentence]  # 문장별 cites 강제
    confidence: float  # 종합 신뢰도(0~1)
    produced_by: str  # 서술 백엔드: template | claude(폴백 시 template)
    created_at: int  # 생성 Unix 시각
    window_label: str = ""  # "최근 30분" 등 사람이 읽는 창 라벨(투명성)
    attrs: dict[str, Any] = field(default_factory=dict)


# 데모 고정 관심지역 객체 (상수). 좌표 교체 시 KADIZ_BBOX만 수정.
KADIZ_REGION = Region(
    id="KADIZ",
    name="한국 방공식별구역 (KADIZ)",
    classification="ADIZ",
    geo=bbox_to_polygon(KADIZ_BBOX),
)

# 데모 OpArea 소구역 (KADIZ 내부 서해 상공). 군용기 접근·위성 근접 룰의 민감구역.
OPAREA_WEST_REGION = Region(
    id="OPAREA-WEST",
    name="서해 작전구역 (데모 OpArea)",
    classification="OpArea",
    geo=bbox_to_polygon(OPAREA_WEST_BBOX),
)

# 민감구역 분류 (dropout·위성 근접 룰이 "민감 Region 내"를 판정할 때 사용).
SENSITIVE_CLASSIFICATIONS = ("ADIZ", "OpArea")
