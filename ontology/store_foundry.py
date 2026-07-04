"""FoundryOntologyStore + HybridStore — Foundry 하이브리드 저장 어댑터 (DR-0009).

## 무엇 (DR-0009 결정 + P7 §10 실측 확장)
Foundry 온톨로지에 **11 Object Type**이 구축돼 있다(Aircraft·Observation·Region·Anomaly·
Operator·Track·Satellite·OrbitPass·WeatherState·NewsEvent·SituationAssessment). P7 §9~§10
재검증으로 D-2·D-3·D-5·D-6이 해소돼(신규 7타입 PK 파라미터·set-alert Modify화·composed_of·
self-link 해제) **write 배선이 가능**해졌다. 이 어댑터는 그 실측(P7 §10-7 B목록)을 코드로 옮긴다.

- **FoundryOntologyStore**: Aircraft·Observation·Operator·Satellite·OrbitPass·Track·
  WeatherState·NewsEvent를 Foundry에 write(create 액션)/read(생성 OSDK 0.9.0 타입드 클래스, DR-0012
  #4 — 구 저수준 dict SDK에서 전환). SituationAssessment는
  스칼라만 write(문장 cites는 스키마에 없음 → 로컬 권위본과 짝).
- **HybridStore**: 위 8종 → Foundry(정보소재), Region·Anomaly·산출 인텔 문장·provenance
  MANY-MANY 링크 → LocalOntologyStore. `SKAI_STORE=foundry`로 활성화(미설정이면 순수 로컬).

## 링크 (P7 §10-3 실측)
- **FK 링크는 객체 write의 FK 파라미터로 자동 형성**: observed_as(aircraftIcao24)·
  operated_by(operatorRef)·of(satelliteNoradId)·Track→Aircraft(aircraftIcao24)·
  WeatherState→Region(regionId)·SituationAssessment→Region(regionId). 별도 link() 불필요.
- **composed_of(Observation↔Track)**: edit-observation의 `trackId`로만 채운다(custody 확정 후
  귀속, create엔 파라미터 없음 — P7 §10-4). HybridStore.link(composed_of)가 이 경로로 라우팅.
- **over(OrbitPass→Region)**: §15 E-2.1로 regionId FK 링크 형성(구 미형성 해소) → write_orbitpass의
  regionId가 이제 OrbitPass.region traverse 가능. **within(Observation→Region)**: §16에서 write 시점
  KADIZ bbox 지오펜스 판정(point_in_bbox)으로 regionId FK를 채워 Observation.region traverse 형성 완료.
- **MANY-MANY provenance 링크(evidenced_by·involves·correlated_with·mentions·aggregates·cites)**:
  Foundry측이 불안정/오배선(P7 §9-4·§10-2) → **로컬 링크 테이블이 권위본**.
- **Anomaly는 dual-write**(P7 §12-6 → §15 E부: 클린 실행 해소): 사용자가 create-anomaly 규칙의
  가짜 에러 원인(링크가 신규 객체가 아닌 `anomalies` 입력 파라미터에 연결되던 것)을 고쳐 **이제
  create-anomaly EXECUTE가 ApplyActionFailed 없이 깔끔히 성공**한다(§15 실측: err=None, evidenced_by/
  involves/correlatedWith 엣지 안정 형성, evidence 없으면 INVALID 거부 유지). 아래 §12 에러 흡수
  (_create_anomaly_absorbing)는 **방어용으로 유지**하나 정상 경로에선 더는 발동하지 않는다(§15 라이브
  확인). Foundry엔 스칼라 + 단일 observations(첫 근거)·단일 aircraft(첫 involves) 엣지만 밀고,
  correlatedWith는 §17에서 Optional 강등돼 이제 파라미터 자체를 생략(구 present-only placeholder 폐기),
  **전체 근거·involves·correlated_with는 로컬 권위본**(링크 파라미터가 단수). confirm/dismiss 상태
  전이는 confirm-anomaly/dismiss-anomaly 액션으로 dual 동기.

## 스키마 잔여 이슈 (§17 실측 갱신 — 코드로 못 고침, Ontology Manager UI 대응)
1. E-4 리네임 **완료**: 11개 create-* 액션의 PK 파라미터가 `newParameter`→실 PK명(icao24/obsId/
   anomalyId/…)으로 리네임됨. 코드도 동기(§15). 단 **edit-observation만 `newParameter`(required) 잔존**
   → composed_of 경로(_set_observation_track)는 여전히 newParameter를 보낸다.
2. E-3 신규 속성 채움 파라미터 **§17에서 전부 신설·배선 완료**: create-observation.attrsJson(→
   write_observation), create-orbit-pass.groundTrackJson(→write_orbitpass), create-weather-state.station
   (→write_weatherstate; read는 station 속성 우선·PK 복원 폴백), create-situation-assessment.sentencesJson
   (→write_assessment 문장 cites). Anomaly.createdAt·explainerBackend는 §15에서 이미 배선. → **속성 손실
   갭 종결**(구 객체는 read 폴백으로 하위호환). Foundry는 여전히 write 소재, 문장 read 권위본은 로컬.
3. write_anomaly: create-anomaly **클린 실행**(§15 ApplyActionFailed 해소). _create_anomaly_absorbing은
   방어용 유지(정상 경로 미발동). correlatedWith는 §17에서 **Optional 강등** → placeholder 생략(엣지는
   여전히 로컬 권위본; 다중 근거·involves·correlated_with는 파라미터 단수 한계로 로컬).
4. set-region-alert-level은 OSDK 0.8.0에 **포함됨**(§17 실측, 0.5.0·0.6.0 누락→0.7.0/0.8.0 해소). live=OSDK
   36액션 정합(editrack 중복 삭제, delete-orbit-pass 포함). edit-aircraft.isMilitary도 boolean으로 정정(§17).

## provenance
write_observation·write_weatherstate·write_newsevent는 백엔드 무관하게 store.validate_provenance로
source·source_url·ts를 강제한다(누락 write 거부). = 환각방지 백본은 Foundry에서도 동일 적용.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Optional, Sequence

from ontology.geo import point_in_bbox
from ontology.model import (
    KADIZ_BBOX,
    NEWS_MAX_CONFIDENCE,
    Aircraft,
    Anomaly,
    NewsEvent,
    Observation,
    Operator,
    OrbitPass,
    Region,
    Satellite,
    SituationAssessment,
    Track,
    WeatherState,
)
from ontology.store import validate_evidence, validate_provenance
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

# 사용자 온톨로지 rid (P0B §8-2 실측, OSDK 내장값과 동일).
DEFAULT_ONT_RID = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"

# 액션 API name (2026-07-04 P7 §10 introspection).
ACTION_CREATE_AIRCRAFT = "create-aircraft"
ACTION_CREATE_OBSERVATION = "create-observation"
ACTION_EDIT_OBSERVATION = "edit-observation"
ACTION_CREATE_OPERATOR = "create-operator"
ACTION_CREATE_SATELLITE = "create-satellite"
ACTION_CREATE_ORBIT_PASS = "create-orbit-pass"
ACTION_CREATE_TRACK = "create-track"
ACTION_CREATE_WEATHER_STATE = "create-weather-state"
ACTION_CREATE_NEWS_EVENT = "create-news-event"
ACTION_CREATE_SITUATION_ASSESSMENT = "create-situation-assessment"
ACTION_SET_REGION_ALERT_LEVEL = "set-region-alert-level"
ACTION_DELETE_ORBIT_PASS = "delete-orbit-pass"
ACTION_CREATE_ANOMALY = "create-anomaly"
ACTION_CONFIRM_ANOMALY = "confirm-anomaly"
ACTION_DISMISS_ANOMALY = "dismiss-anomaly"
# B2 staged human review (방법 B) — AIP 산출 explanation을 본 속성에 즉시 쓰지 않고
# 제안(proposedExplanation)→사람 승인(explanation←proposed) 2단계로 분리. OSDK 0.11.0 실측:
#   propose-explanation: params {anomaly(target), proposedExplanation, reviewStatus}
#   approve-explanation: params {anomaly(target), proposedExplanation(opt)} — 규칙이 복사+approved
#   reject-explanation : params {anomaly(target)} — 규칙이 reviewStatus=rejected
ACTION_PROPOSE_EXPLANATION = "propose-explanation"
ACTION_APPROVE_EXPLANATION = "approve-explanation"
ACTION_REJECT_EXPLANATION = "reject-explanation"

# create-*가 실세계 mention/근거 객체를 못 받을 때 required object 파라미터에 넣는 placeholder.
# P7 §9-4·§10-6 실측: 존재하지 않는 ref도 present-only로 EXECUTE 통과(링크는 안 맺힘). 권위 링크는
# 로컬에 별도 저장하므로 이 placeholder는 "required 충족"만 담당하고 그래프 의미는 없다.
_ABSENT_REF = "none"

# KADIZ bbox 튜플(lamin, lomin, lamax, lomax) — geo.point_in_bbox 시그니처에 맞게 파생 변환.
# SSOT는 model.KADIZ_BBOX(dict); 여기서 중복 정의 금지.
_KADIZ_BBOX: tuple[float, float, float, float] = (
    KADIZ_BBOX["lamin"],
    KADIZ_BBOX["lomin"],
    KADIZ_BBOX["lamax"],
    KADIZ_BBOX["lomax"],
)
# Foundry Region PK (라이브 read 확인 — §16).
_KADIZ_REGION_PK = "KADIZ"


class FoundryUnsupportedError(NotImplementedError):
    """Foundry에 아직 배선 못 한 Object Type/메서드 호출.

    HybridStore가 라우팅을 잘못했거나, FoundryOntologyStore를 단독으로 (로컬 위임 없이)
    쓰면서 미배선 객체를 건드릴 때 난다. 잔여 갭은 이 파일 상단 참조.
    """


def _unix_to_iso(ts: int) -> str:
    """int Unix 초 → ISO8601 UTC 문자열 (Foundry timestamp 타입 파라미터용)."""
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()


def _iso_to_unix(v) -> int:
    """Foundry timestamp(ISO8601 문자열 또는 datetime) → int Unix 초."""
    if v is None:
        return 0
    if isinstance(v, (int, float)):
        return int(v)
    if isinstance(v, datetime):
        return int(v.timestamp())
    s = str(v).replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).timestamp())
    except ValueError:
        return 0


def _iso_param(v) -> str:
    """Foundry에서 읽은 timestamp를 다시 Foundry timestamp 파라미터로 보낼 때 정규화(round-trip)."""
    return _unix_to_iso(_iso_to_unix(v))


def _wind_str(weather: WeatherState) -> str:
    """model의 wind_dir/wind_speed_kt를 Foundry WeatherState.wind(단일 문자열)로 합성.

    Foundry는 방향/속도를 분해하지 않고 "200/8"류 문자열 1개로 보관한다. 가변풍(dir=None)은 "VRB".
    둘 다 없으면 required 파라미터 충족용 "VRB"를 낸다(빈 문자열 회피).
    """
    d, s = weather.wind_dir, weather.wind_speed_kt
    if d is not None and s is not None:
        return f"{int(d):03d}/{int(round(s))}"
    if s is not None:
        return f"VRB/{int(round(s))}"
    return "VRB"


def _station_from_weather_id(weather_id: str) -> str:
    """weatherId PK(f"wx-{station}-{ts}")에서 station 복원.

    Foundry WeatherState에 station 속성이 없어(스키마 갭) PK에서 되읽는다. 형식이 다르면 "".
    """
    if not weather_id or not weather_id.startswith("wx-"):
        return ""
    rest = weather_id[3:]
    # 뒤에서 첫 '-'까지가 ts → 그 앞이 station(공항 ICAO는 '-' 없음).
    return rest.rsplit("-", 1)[0] if "-" in rest else rest


def _sentences_json(assessment: SituationAssessment) -> str:
    """SituationAssessment.sentences → sentencesJson(문장별 cites 보존, DR-0006 provenance).

    0.8.0(§17)에서 create-situation-assessment에 sentencesJson 파라미터가 신설돼, 문장별 근거
    cites(사실→문장 조립의 부산물)를 Foundry 스파인에도 감사 가능한 형태로 보존한다. read
    권위본은 여전히 로컬(문장 객체·aggregates/cites 링크)이나, Foundry측 dual-write가 완성된다.
    """
    return json.dumps(
        [
            {
                "text": s.text,
                "cites": list(s.cites),
                "confidence": s.confidence,
                "kind": s.kind,
            }
            for s in assessment.sentences
        ],
        ensure_ascii=False,
    )


def _parse_wind(wind: Optional[str]) -> tuple[Optional[int], Optional[float]]:
    """Foundry wind 문자열("200/8"/"VRB/8") → (wind_dir, wind_speed_kt). 실패 시 (None, None)."""
    if not wind or "/" not in wind:
        return None, None
    d_str, s_str = wind.split("/", 1)
    d = None if d_str.upper().startswith("VRB") else _safe_int(d_str)
    return d, _safe_float(s_str)


def _safe_int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _safe_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _warn(msg: str) -> None:
    print(f"[store_foundry] {msg}", file=sys.stderr)


def _first_ref_of_type(
    items: Sequence, default_type: str, want_type: str
) -> Optional[str]:
    """evidence/involves 원소 중 want_type의 첫 대상 id를 고른다(없으면 None).

    문자열 원소는 default_type으로 간주(하위호환: evidence=[obs_id]). 튜플/리스트 원소는
    (dst_type, dst_id) 타입드 근거(P5: ("OrbitPass", pass_id) 등). create-anomaly의 단일
    링크 파라미터(observations·aircraft·newsEvents·orbitPasses)를 채울 때 쓴다.
    """
    for it in items:
        if isinstance(it, (tuple, list)):
            dst_type, dst_id = it[0], it[1]
        else:
            dst_type, dst_id = default_type, it
        if dst_type == want_type:
            return dst_id
    return None


class FoundryOntologyStore:
    """Foundry 어댑터 (write=create 액션[저수준 _pf], read=OSDK 0.9.0 타입드 클래스).

    ## read 경로 (DR-0012 #4 전환)
    외부 read API(query_*·get_observation·counts)는 생성 OSDK(`skai_osdk_sdk` 0.9.0)의 타입드
    ObjectSet를 경유한다: `client.ontology.objects.<Type>.get(pk)`/`.iterate()`/`.count()`. 속성은
    dict.get가 아니라 타입드 어트리뷰트(snake_case: is_military·aircraft_icao24·attrs_json…). §17에서
    신설된 속성(attrsJson·station·groundTrackJson·sentencesJson)도 전부 타입드 속성으로 노출됨(실측).
    구 저수준 dict SDK read 사유(발행 OSDK가 stale·Observation 없음, P7 §4)는 0.9.0(11객체+36액션)
    에서 소멸. write는 아래 `_pf`(저수준 Action.apply)를 그대로 유지(경로 무변경). 잔여 저수준 read는
    write 내부 read-back 전용(_set_observation_track·delete·_anomaly_written_ok — 아래 §write-내부 참조).

    지원(Foundry 소재): Aircraft·Observation·Operator·Satellite·OrbitPass·Track·WeatherState·
    NewsEvent(객체) + SituationAssessment(스칼라 스파인). 그 밖의 Protocol 메서드는
    FoundryUnsupportedError를 던진다 — HybridStore가 그것들을 LocalOntologyStore로 라우팅하므로
    정상 흐름에선 호출되지 않는다.
    """

    def __init__(
        self,
        token: Optional[str] = None,
        hostname: Optional[str] = None,
        ont_rid: str = DEFAULT_ONT_RID,
    ):
        # foundry_sdk는 메인 .venv(3.14)엔 없다 → 반드시 lazy import.
        # (이 스토어를 실제로 만들 때만 필요; 모듈 import 자체는 SDK 없이 통과해야 유닛 테스트 가능)
        import foundry_sdk

        token = token or os.environ.get("FOUNDRY_TOKEN")
        hostname = hostname or os.environ.get("FOUNDRY_HOSTNAME")
        if not token or not hostname:
            raise RuntimeError(
                "FOUNDRY_TOKEN·FOUNDRY_HOSTNAME 미설정 — .env 확인 "
                "(FoundryOntologyStore는 크리덴셜 필수)."
            )
        self.ont = ont_rid
        # write = 저수준 _pf(Action.apply). DR-0012 범위(write 무변경) 밖이라 그대로 유지.
        self._pf = foundry_sdk.FoundryClient(
            auth=foundry_sdk.UserTokenAuth(token), hostname=hostname
        )
        # read = 생성 OSDK(skai_osdk_sdk 0.9.0) 타입드 클래스(DR-0012 #4). OSDK도 메인 .venv(3.14)엔
        # 없어 lazy import(모듈 import 자체는 SDK 없이 통과해야 유닛 테스트 가능). rid는 온톨로지 고정.
        import skai_osdk_sdk

        self._osdk = skai_osdk_sdk.FoundryClient(
            auth=foundry_sdk.UserTokenAuth(token), hostname=hostname, rid=ont_rid
        )
        # 프로세스 내 client-side dedup: 같은 세션 내 이중 write 방지.
        # 세션 간(크로스런) ObjectAlreadyExists는 write 메서드에서 catch·skip.
        self._written_aircraft: dict[str, str] = {}  # icao24 → foundry pk
        self._written_obs: set[str] = set()  # obs.id(자연키)
        # 신규 7타입 공용 dedup: {kind: {pk, ...}}
        self._written_other: dict[str, set[str]] = {}

    # ── 내부: 액션 apply ──────────────────────────
    def _apply(self, action: str, parameters: dict):
        resp = self._pf.ontologies.Action.apply(
            self.ont, action, parameters=parameters, options={"returnEdits": "ALL"}
        )
        # 신규 객체 PK 회수(returnEdits)
        edits = getattr(resp, "edits", None)
        modified = getattr(edits, "edits", None) if edits is not None else None
        if modified:
            for e in modified:
                pk = getattr(e, "primary_key", None)
                if pk:
                    return pk
        return None

    @staticmethod
    def _is_already_exists(e: Exception) -> bool:
        """ObjectAlreadyExists 계열 예외 판별 (dedup: 크래시 없이 skip용).

        실 Foundry는 크로스런 PK 중복을 `ConflictError`로 던지고, errorName은 메시지 JSON에
        `"errorName": "ObjectAlreadyExists"`로만 실린다(타입명은 ConflictError, 구분자 없는
        연결형). 그래서 `already_exists`(밑줄)·`already exists`(공백)만 보던 기존 매칭이 실
        예외를 놓쳐 write_aircraft/observation의 크로스런 dedup이 작동하지 않았다(재인제스트가
        전부 실패). errorName 연결형 `objectalreadyexists`를 부분매칭에 추가한다.
        주의: `LinkAlreadyExists`(create-anomaly 링크 tombstone)는 여기서 매칭하지 않는다 —
        그건 객체 중복이 아니라 실패이므로 read-back 판정(§12) 경로로 흘러야 한다.
        """
        name = type(e).__name__
        msg = str(e).lower()
        return (
            "ObjectAlreadyExists" in name
            or "objectalreadyexists" in msg
            or "already_exists" in msg
            or "already exists" in msg
        )

    def _create_object(self, kind: str, pk: str, action: str, params: dict) -> None:
        """PK 프로세스내 dedup + create 액션 apply + ObjectAlreadyExists skip (신규 7타입 공용).

        기존 write_aircraft/write_observation의 인라인 dedup 패턴을 신규 타입으로 일반화한 것.
        """
        seen = self._written_other.setdefault(kind, set())
        if pk in seen:
            return  # 프로세스 내 dedup
        try:
            self._apply(action, params)
            seen.add(pk)
        except Exception as e:
            if self._is_already_exists(e):
                # 크로스런 dedup: 같은 PK가 이미 Foundry에 있음 → skip.
                _warn(f"{action}: {pk} 이미 존재 (skip)")
                seen.add(pk)
            else:
                raise

    # ── write (Foundry) ───────────────────────────
    def write_aircraft(self, aircraft: Aircraft) -> None:
        if aircraft.icao24 in self._written_aircraft:
            return  # 프로세스 내 dedup
        params: dict = {
            "callsign": aircraft.callsign or aircraft.icao24,
            "registration": aircraft.registration or aircraft.icao24,
            "isMilitary": bool(aircraft.is_military),
            # icao24 = PK (E-4 리네임: 구 newParameter → 실 PK명, 2026-07-04 §15).
            "icao24": aircraft.icao24,
        }
        if aircraft.type:
            params["type"] = aircraft.type
        if aircraft.operator_ref:
            params["operatorRef"] = aircraft.operator_ref  # FK → operated_by(Operator)
        try:
            pk = self._apply(ACTION_CREATE_AIRCRAFT, params)
            self._written_aircraft[aircraft.icao24] = pk or aircraft.icao24
        except Exception as e:
            if self._is_already_exists(e):
                # 크로스런 dedup: 같은 icao24가 이미 Foundry에 있음 → skip.
                _warn(f"write_aircraft: {aircraft.icao24} 이미 존재 (skip)")
                self._written_aircraft[aircraft.icao24] = aircraft.icao24
            else:
                raise

    def write_observation(self, obs: Observation) -> None:
        # provenance 강제(백엔드 무관) — 누락이면 ProvenanceError로 거부.
        validate_provenance(obs)
        if obs.id in self._written_obs:
            return  # 프로세스 내 dedup
        params: dict = {
            "sourceUrl": obs.source_url,
            "source": obs.source,
            "ts": _unix_to_iso(obs.ts),
            "lat": float(obs.lat),
            "lon": float(obs.lon),
            "onGround": bool(obs.on_ground),
            # obsId = PK (E-4 리네임: 구 newParameter, §15)
            "obsId": obs.id,
            # aircraftIcao24 = FK → observed_as 링크 자동 형성 (§7-2)
            "aircraftIcao24": obs.aircraft_ref,
        }
        # optional 텔레메트리: None이면 파라미터 생략 (§7-1 갭4 해소, required=False 확인됨).
        if obs.alt is not None:
            params["alt"] = float(obs.alt)
        if obs.velocity is not None:
            params["velocity"] = float(obs.velocity)
        if obs.heading is not None:
            params["heading"] = float(obs.heading)
        if obs.squawk:
            params["squawk"] = obs.squawk
        # KADIZ 지오펜스: bbox 안이면 regionId FK 포함 → Observation.region(within, E-2.2 배선).
        # regionId=required=False(§15 introspection + §16 라이브 확인) — 밖이면 생략.
        if point_in_bbox(obs.lat, obs.lon, _KADIZ_BBOX):
            params["regionId"] = _KADIZ_REGION_PK
        # attrs(origin_country 등) → attrsJson (§17/0.8.0: create-observation.attrsJson 신설 →
        #   구 "attrs 저장 불가" 갭 해소). 비어있으면 생략(optional).
        if obs.attrs:
            params["attrsJson"] = json.dumps(obs.attrs, ensure_ascii=False)
        # ⚠️ create-observation에 trackId(opt) 파라미터도 §17에서 신설됐으나, composed_of 귀속은
        #   custody 확정(track 세그먼트 종료) 후에야 정해진다 — write_observation 시점엔 track이
        #   미상이므로 여기선 채우지 않고 edit-observation 경로(_set_observation_track)를 유지한다(§10-4).
        try:
            self._apply(ACTION_CREATE_OBSERVATION, params)
            self._written_obs.add(obs.id)
        except Exception as e:
            if self._is_already_exists(e):
                # 크로스런 dedup: 같은 obsId가 이미 Foundry에 있음 → skip.
                _warn(f"write_observation: {obs.id} 이미 존재 (skip)")
                self._written_obs.add(obs.id)
            else:
                raise

    def write_operator(self, operator: Operator) -> None:
        # create-operator: name·kind·country 전부 required(P7 §10 introspection).
        params = {
            "name": operator.name or operator.id,
            "kind": operator.kind or "unknown",
            "country": operator.country
            or "unknown",  # req; model Optional → 비어있으면 placeholder
            "operatorId": operator.id,  # PK (E-4 리네임, §15)
        }
        self._create_object("Operator", operator.id, ACTION_CREATE_OPERATOR, params)

    def write_satellite(self, satellite: Satellite) -> None:
        # create-satellite: name·objectType·operatorRef·tleEpoch 전부 required.
        params = {
            "name": satellite.name or satellite.norad_id,
            "objectType": satellite.object_type or "UNKNOWN",
            "operatorRef": satellite.operator_ref or "UNKNOWN",
            # tleEpoch=timestamp(req). model은 ISO 문자열 or None → None이면 현재시각으로 대체.
            "tleEpoch": satellite.tle_epoch or _unix_to_iso(int(time.time())),
            "noradId": satellite.norad_id,  # PK (E-4 리네임, §15)
        }
        self._create_object(
            "Satellite", satellite.norad_id, ACTION_CREATE_SATELLITE, params
        )

    def write_orbitpass(self, orbit_pass: OrbitPass) -> None:
        # create-orbit-pass: satelliteNoradId(FK→of)·regionId(FK→over)·startTs·endTs·maxElevation req.
        # E-2.1(§15): over(OrbitPass→Region)가 regionId FK 링크로 형성됨(구 미형성) → regionId가 이제
        #   그래프 traverse 가능. §17/0.8.0: create-orbit-pass.groundTrackJson 신설 → ground_track(지도
        #   궤적 점열)을 이제 Foundry에 직접 보존(구 "저장 불가·로컬 폴백" 갭 해소).
        params = {
            "satelliteNoradId": orbit_pass.satellite_ref,  # FK → OrbitPass.satellite(of)
            "regionId": orbit_pass.region_ref,  # FK → OrbitPass.region(over, E-2.1 해소)
            "startTs": _unix_to_iso(orbit_pass.start_ts),
            "endTs": _unix_to_iso(orbit_pass.end_ts),
            "maxElevation": float(orbit_pass.max_elevation),
            "passId": orbit_pass.id,  # PK (E-4 리네임, §15)
        }
        if orbit_pass.ground_track:
            params["groundTrackJson"] = json.dumps(orbit_pass.ground_track)
        self._create_object(
            "OrbitPass", orbit_pass.id, ACTION_CREATE_ORBIT_PASS, params
        )

    def write_track(self, track: Track) -> None:
        # create-track: aircraftIcao24(FK→Track.aircraft)·startTs·endTs·hasGap·pathJson req.
        params = {
            "aircraftIcao24": track.aircraft_ref,  # FK → Track.aircraft
            "startTs": _unix_to_iso(track.start_ts),
            "endTs": _unix_to_iso(track.end_ts),
            "hasGap": bool(track.has_gap),
            "pathJson": json.dumps(track.path),
            "trackId": track.id,  # PK (E-4 리네임, §15)
        }
        self._create_object("Track", track.id, ACTION_CREATE_TRACK, params)

    def write_weatherstate(self, weather: WeatherState) -> None:
        # provenance 강제(뉴스·기상은 증거 객체) — source/source_url/ts 누락이면 ProvenanceError.
        validate_provenance(weather)
        # create-weather-state: 대부분 required. Foundry 매핑:
        #   conditions ← model.flight_category(VFR/MVFR..), rawText ← model.conditions(원문 METAR).
        #   station: §17/0.8.0에서 create-weather-state.station(opt) 파라미터가 신설돼(구 부재) 이제
        #   station을 직접 write(PK 복원 꼼수 불필요). read는 station 속성 우선·PK 복원 폴백 유지.
        params = {
            "regionId": weather.region_ref,  # FK → WeatherState.region
            "ts": _unix_to_iso(weather.ts),
            "wind": _wind_str(weather),
            "visibilitySm": float(weather.visibility_sm)
            if weather.visibility_sm is not None
            else 0.0,
            # ceilingFt=req. model의 None(무제한)을 0.0으로 보내면 의미 왜곡 → 큰 값으로 표기.
            "ceilingFt": float(weather.ceiling_ft)
            if weather.ceiling_ft is not None
            else 99999.0,
            "conditions": weather.flight_category or "UNKNOWN",
            "rawText": weather.conditions or weather.id,
            "source": weather.source,
            "sourceUrl": weather.source_url,
            "weatherId": weather.id,  # PK (E-4 리네임, §15)
        }
        if weather.station:
            params["station"] = weather.station
        self._create_object(
            "WeatherState", weather.id, ACTION_CREATE_WEATHER_STATE, params
        )

    def write_newsevent(self, news: NewsEvent, mentions: Sequence[tuple] = ()) -> None:
        # provenance 강제 + confidence 상한 clamp(DR-0005).
        validate_provenance(news)
        confidence = min(news.confidence, NEWS_MAX_CONFIDENCE)
        # create-news-event: aircraft·operators·regions는 E-2.4(§15)로 **Optional 강등** 확인
        #   (구 required present-only 마찰 해소). mentions=[(dst_type, dst_id)]에서 타입별 첫 ref만
        #   채우고, 없으면 파라미터 자체를 생략(placeholder 불필요).
        #   ⚠️ Foundry MANY-MANY mention 링크는 불안정(§9-4) → 권위 링크는 HybridStore가 로컬에 저장.
        m_by_type: dict[str, str] = {}
        for dst_type, dst_id in mentions:
            m_by_type.setdefault(dst_type, dst_id)
        params = {
            "source": news.source,
            "url": news.source_url,  # Foundry url = model source_url(citation PK)
            "ts": _unix_to_iso(news.ts),
            "title": news.title or news.id,
            "summary": news.summary or "",
            "entitiesJson": json.dumps(news.entities, ensure_ascii=False),
            "confidence": float(confidence),
            "lat": float(news.lat) if news.lat is not None else 0.0,
            "lon": float(news.lon) if news.lon is not None else 0.0,
            "newsId": news.id,  # PK (E-4 리네임, §15)
        }
        # mention 파라미터는 실 ref가 있을 때만(Optional화, E-2.4) — 없으면 생략.
        if "Aircraft" in m_by_type:
            params["aircraft"] = m_by_type["Aircraft"]
        if "Operator" in m_by_type:
            params["operators"] = m_by_type["Operator"]
        if "Region" in m_by_type:
            params["regions"] = m_by_type["Region"]
        self._create_object("NewsEvent", news.id, ACTION_CREATE_NEWS_EVENT, params)

    def write_assessment(self, assessment: SituationAssessment) -> None:
        """SituationAssessment 스칼라 + 문장 cites를 Foundry에 write(AIP 스파인).

        §17/0.8.0: create-situation-assessment.sentencesJson 파라미터가 신설돼(구 부재) 문장별
        cites(DR-0006 provenance 백본)를 이제 Foundry 스파인에도 보존한다 → dual의 Foundry측 완성.
        read 권위본은 여전히 로컬(문장 객체·aggregates/cites 링크; HybridStore가 dual-write). create-
        situation-assessment의 anomalies·newsEvents·observations·orbitPasses(전부 여전히 required
        object)는 present-only placeholder로 충족(실 provenance 링크는 로컬 권위본).
        """
        params = {
            "regionId": assessment.region_ref,  # FK → SituationAssessment.region
            "windowStart": _unix_to_iso(assessment.window_start),
            "windowEnd": _unix_to_iso(assessment.window_end),
            "summary": assessment.summary or assessment.id,
            "confidence": float(assessment.confidence),
            "producedBy": assessment.produced_by or "template",
            "createdAt": _unix_to_iso(assessment.created_at),
            # 문장별 cites(§17 신설) — Foundry 스파인에 provenance 감사기록 보존.
            "sentencesJson": _sentences_json(assessment),
            # required object 파라미터(present-only) — 실 provenance 링크는 로컬 권위본.
            "anomalies": _ABSENT_REF,
            "newsEvents": _ABSENT_REF,
            "observations": _ABSENT_REF,
            "orbitPasses": _ABSENT_REF,
            "assessmentId": assessment.id,  # PK (E-4 리네임, §15)
        }
        self._create_object(
            "SituationAssessment",
            assessment.id,
            ACTION_CREATE_SITUATION_ASSESSMENT,
            params,
        )

    def set_region_alert_level(self, region_id: str, alert_level: str) -> None:
        """Region.alertLevel 전이(set-region-alert-level Modify 액션, P7 §10-1 D-2 해소).

        OSDK 0.5.0엔 이 액션이 누락됐으나(§10-6) 저수준 SDK Action.apply로는 정상 실행된다.
        """
        self._apply(
            ACTION_SET_REGION_ALERT_LEVEL,
            {"region": region_id, "alertLevel": alert_level},
        )

    def link(
        self, src_type: str, src_id: str, link_type: str, dst_type: str, dst_id: str
    ) -> None:
        # observed_as: write_observation의 aircraftIcao24 FK로 자동 형성(§7-2) → no-op.
        if link_type == "observed_as":
            return
        # composed_of: Observation.trackId(FK)를 edit-observation으로 채워 Track에 귀속(P7 §10-4/§10-5).
        #   custody.py는 link("Track", track.id, "composed_of", "Observation", obs.id)로 호출.
        if link_type == "composed_of":
            obs_id, track_id = (
                (dst_id, src_id) if dst_type == "Observation" else (src_id, dst_id)
            )
            self._set_observation_track(obs_id, track_id)
            return
        raise FoundryUnsupportedError(
            f"FoundryOntologyStore.link: {link_type}는 Foundry 미배선 "
            "(observed_as·composed_of만 처리, MANY-MANY provenance 링크는 로컬 권위본)."
        )

    def _set_observation_track(self, obs_id: str, track_id: str) -> None:
        """edit-observation으로 기존 Observation의 trackId를 세팅(composed_of).

        edit-observation은 텔레메트리(alt·heading·squawk·velocity)까지 전부 required라, 현재 Foundry
        값을 되읽어 재공급한다(P7 §10 introspection: create는 optional이나 edit는 required).
        """
        d = self._get_object("Observation", obs_id)
        if not d:
            _warn(f"composed_of: Observation {obs_id} 미존재 → trackId 세팅 skip")
            return
        params = {
            "Observation": obs_id,
            "lat": float(d.get("lat") or 0.0),
            "lon": float(d.get("lon") or 0.0),
            "alt": float(d.get("alt") or 0.0),
            "heading": float(d.get("heading") or 0.0),
            "velocity": float(d.get("velocity") or 0.0),
            "squawk": d.get("squawk") or "0000",
            "onGround": bool(d.get("onGround")),
            "source": d.get("source") or "",
            "sourceUrl": d.get("sourceUrl") or "",
            "ts": _iso_param(d.get("ts")),
            # ⚠️ edit-observation은 E-4 리네임에서 제외됨 — 유일하게 newParameter(required) 잔존(§15
            #   실측). create-*와 달리 여기서만 newParameter를 보낸다(리네임 금지, 보내야 required 충족).
            "newParameter": obs_id,
            "trackId": track_id,  # FK → composed_of
        }
        self._apply(ACTION_EDIT_OBSERVATION, params)

    def delete_future_orbitpasses_for(self, satellite_ref: str, now_ts: int) -> int:
        """한 위성의 미래 통과창(start_ts >= now_ts)을 Foundry에서 삭제(재계산 전 정리).

        LocalOntologyStore.delete_future_orbitpasses_for의 Foundry판(P7 §10-7 B-3). of/over 링크는
        FK/속성이라 객체 삭제로 함께 사라진다. 반환: 삭제된 pass 수.
        """
        deleted = 0
        seen = self._written_other.setdefault("OrbitPass", set())
        for d in self._list_objects("OrbitPass"):
            if d.get("satelliteNoradId") != satellite_ref:
                continue
            if _iso_to_unix(d.get("startTs")) < now_ts:
                continue
            pid = d.get("passId")
            if not pid:
                continue
            try:
                self._apply(ACTION_DELETE_ORBIT_PASS, {"OrbitPass": pid})
                deleted += 1
                seen.discard(pid)  # 재작성 허용(dedup 캐시에서 제거)
            except Exception as e:
                _warn(f"delete-orbit-pass {pid} 실패: {e!r}")
        return deleted

    # ── read (Foundry, 생성 OSDK 0.9.0 타입드 클래스 — DR-0012 #4) ──
    # 외부 read API(query_*·get_observation·counts)는 전부 OSDK 타입드 경유. 속성은 dict.get가
    # 아니라 타입드 어트리뷰트(snake_case). ObjectSet API: `.get(pk)`·`.iterate()`·`.count()`.
    def _osdk_iter(self, object_type: str):
        """OSDK ObjectSet 전체를 타입드 객체로 지연 순회(제너레이터). object_type=OSDK API명."""
        return getattr(self._osdk.ontology.objects, object_type).iterate()

    def _osdk_get(self, object_type: str, pk: str):
        """OSDK 타입드 단건 조회(PK) → 타입드 객체 or None(미존재)."""
        return getattr(self._osdk.ontology.objects, object_type).get(pk)

    # ── write-내부 저수준 read (경로 무변경, DR-0012 범위 밖) ──
    # 아래 두 헬퍼는 write/정리 경로의 read-back 전용으로만 남긴다: _set_observation_track(edit-
    # observation의 required 필드 재공급)·delete_future_orbitpasses_for(삭제 대상 스캔)·
    # _anomaly_written_ok(Anomaly 존재확인). write 로직과 얽혀 있어 저수준 dict를 유지한다.
    def _list_objects(self, object_type: str) -> list[dict]:
        return list(self._pf.ontologies.OntologyObject.list(self.ont, object_type))

    def _get_object(self, object_type: str, pk: str) -> Optional[dict]:
        try:
            return self._pf.ontologies.OntologyObject.get(self.ont, object_type, pk)
        except Exception:
            return None

    # ── OSDK 타입드 객체 → model dataclass 변환 (속성=snake_case 어트리뷰트) ──
    @staticmethod
    def _obj_to_aircraft(o) -> Aircraft:
        return Aircraft(
            icao24=o.icao24,
            callsign=o.callsign,
            registration=o.registration,
            operator_ref=o.operator_ref,
            type=o.type,
            is_military=bool(o.is_military),
        )

    @staticmethod
    def _obj_to_obs(o) -> Observation:
        return Observation(
            id=o.obs_id,
            aircraft_ref=o.aircraft_icao24 or "",
            ts=_iso_to_unix(o.ts),
            lat=o.lat,
            lon=o.lon,
            alt=o.alt,
            velocity=o.velocity,
            heading=o.heading,
            squawk=o.squawk,
            on_ground=bool(o.on_ground),
            source=o.source or "",
            source_url=o.source_url or "",
            # attrs_json(§17 신설) 우선 — 구 객체(속성 None)는 {}로 폴백.
            attrs=json.loads(o.attrs_json or "{}"),
        )

    @staticmethod
    def _obj_to_operator(o) -> Operator:
        return Operator(
            id=o.operator_id,
            name=o.name,
            kind=o.kind,
            country=o.country,
        )

    @staticmethod
    def _obj_to_satellite(o) -> Satellite:
        te = o.tle_epoch
        return Satellite(
            norad_id=o.norad_id,
            name=o.name,
            operator_ref=o.operator_ref,
            # ⚠️ OSDK는 objectType 속성을 object_type_(뒤 밑줄)로 노출 — object_type은 ObjectSet
            #   클래스 attr과 충돌해 리네임됨.
            object_type=o.object_type_,
            # tleEpoch=timestamp → OSDK는 datetime 반환. 구 저수준 dict(ISO 문자열) 동작에 맞춰
            #   datetime은 isoformat('T'), 그 외는 str.
            tle_epoch=(
                te.isoformat()
                if isinstance(te, datetime)
                else (str(te) if te is not None else None)
            ),
            source="celestrak",
            source_url="",
        )

    @staticmethod
    def _obj_to_orbitpass(o) -> OrbitPass:
        return OrbitPass(
            id=o.pass_id,
            satellite_ref=o.satellite_norad_id or "",
            region_ref=o.region_id or "",
            start_ts=_iso_to_unix(o.start_ts),
            end_ts=_iso_to_unix(o.end_ts),
            max_elevation=o.max_elevation or 0.0,
            # ground_track_json(§17 신설) 우선 — 구 객체(속성 None)는 []로 폴백(지도 궤적 레이어).
            ground_track=json.loads(o.ground_track_json or "[]"),
            source="celestrak",
            source_url="",
        )

    @staticmethod
    def _obj_to_track(o) -> Track:
        return Track(
            id=o.track_id,
            aircraft_ref=o.aircraft_icao24 or "",
            start_ts=_iso_to_unix(o.start_ts),
            end_ts=_iso_to_unix(o.end_ts),
            path=json.loads(o.path_json or "[]"),
            has_gap=bool(o.has_gap),
        )

    @staticmethod
    def _obj_to_weather(o) -> WeatherState:
        wid = o.weather_id
        wind_dir, wind_speed = _parse_wind(o.wind)
        cft = o.ceiling_ft
        return WeatherState(
            id=wid,
            region_ref=o.region_id or "",
            ts=_iso_to_unix(o.ts),
            # station(§17 신설) 우선 — 구 객체(속성 None)는 weatherId PK에서 복원(폴백).
            station=o.station or _station_from_weather_id(wid),
            wind_dir=wind_dir,
            wind_speed_kt=wind_speed,
            visibility_sm=o.visibility_sm,
            # ceilingFt sentinel(99999=무제한)은 다시 None으로 복원.
            ceiling_ft=None if cft is None or cft >= 99999 else int(cft),
            flight_category=o.conditions,
            conditions=o.raw_text or "",
            source=o.source or "",
            source_url=o.source_url or "",
            attrs={},
        )

    @staticmethod
    def _obj_to_news(o) -> NewsEvent:
        return NewsEvent(
            id=o.news_id,
            source=o.source or "",
            source_url=o.url or "",
            ts=_iso_to_unix(o.ts),
            title=o.title or "",
            summary=o.summary or "",
            lat=o.lat,
            lon=o.lon,
            confidence=o.confidence or 0.0,
            entities=json.loads(o.entities_json or "[]"),
            attrs={},
        )

    def query_aircraft(self) -> list[Aircraft]:
        return [self._obj_to_aircraft(o) for o in self._osdk_iter("Aircraft")]

    def aircraft_map(self) -> dict[str, Aircraft]:
        return {a.icao24: a for a in self.query_aircraft()}

    def query_all_observations(self, limit: Optional[int] = None) -> list[Observation]:
        obs = [self._obj_to_obs(o) for o in self._osdk_iter("Observation")]
        obs.sort(key=lambda o: o.ts, reverse=True)
        return obs[:limit] if limit else obs

    def query_observations_for(self, icao24: str) -> list[Observation]:
        # aircraftIcao24 FK가 write_observation에서 설정되므로 FK 필터가 정상 동작함.
        return sorted(
            (o for o in self.query_all_observations() if o.aircraft_ref == icao24),
            key=lambda o: o.ts,
        )

    def query_latest_observations(self) -> list[Observation]:
        latest: dict[str, Observation] = {}
        for o in self.query_all_observations():
            cur = latest.get(o.aircraft_ref)
            if cur is None or o.ts > cur.ts:
                latest[o.aircraft_ref] = o
        return list(latest.values())

    def get_observation(self, obs_id: str) -> Optional[Observation]:
        o = self._osdk_get("Observation", obs_id)
        return self._obj_to_obs(o) if o else None

    def query_operators(self) -> list[Operator]:
        return [self._obj_to_operator(o) for o in self._osdk_iter("Operator")]

    def query_satellites(self) -> list[Satellite]:
        return [self._obj_to_satellite(o) for o in self._osdk_iter("Satellite")]

    def satellite_map(self) -> dict[str, Satellite]:
        return {s.norad_id: s for s in self.query_satellites()}

    def query_orbitpasses(self) -> list[OrbitPass]:
        passes = [self._obj_to_orbitpass(o) for o in self._osdk_iter("OrbitPass")]
        passes.sort(key=lambda p: p.start_ts)
        return passes

    def query_tracks(self) -> list[Track]:
        return [self._obj_to_track(o) for o in self._osdk_iter("Track")]

    def query_weather_latest(self) -> list[WeatherState]:
        """공항(station)별 최신 기상 1건. station은 weatherId PK에서 복원해 그룹핑."""
        latest: dict[str, WeatherState] = {}
        for w in (self._obj_to_weather(o) for o in self._osdk_iter("WeatherState")):
            key = w.station or w.id
            cur = latest.get(key)
            if cur is None or w.ts > cur.ts:
                latest[key] = w
        return list(latest.values())

    def query_news(self) -> list[NewsEvent]:
        news = [self._obj_to_news(o) for o in self._osdk_iter("NewsEvent")]
        news.sort(key=lambda n: n.ts, reverse=True)
        return news

    def counts(self) -> dict[str, int]:
        # OSDK 타입드 집계(.count().compute()) — 서버측 count(전량 materialize 불요, float→int).
        def _n(object_type: str) -> int:
            return int(
                getattr(self._osdk.ontology.objects, object_type).count().compute()
            )

        return {
            "aircraft": _n("Aircraft"),
            "observation": _n("Observation"),
            "operator": _n("Operator"),
            "satellite": _n("Satellite"),
            "orbitpass": _n("OrbitPass"),
            "track": _n("Track"),
            "weatherstate": _n("WeatherState"),
            "newsevent": _n("NewsEvent"),
        }

    # ── 미배선 (Foundry 스키마 결함 — HybridStore가 로컬로 라우팅) ──
    def _unsupported(self, name: str):
        raise FoundryUnsupportedError(
            f"FoundryOntologyStore.{name}: Foundry 미배선 "
            "(HybridStore를 쓰면 로컬로 위임됨). 잔여 갭은 store_foundry 상단 참조."
        )

    def write_anomaly(
        self,
        anomaly: Anomaly,
        evidence: Sequence,
        involves: Sequence = (),
    ) -> None:
        """Anomaly를 Foundry에 write (create-anomaly 액션) + §12 에러 흡수.

        ## §15 E부: 클린 실행 (에러 흡수는 방어용으로 유지)
        사용자가 create-anomaly 규칙의 가짜 에러 원인(링크가 신규 객체가 아닌 `anomalies` 입력
        파라미터에 연결되던 것)을 고쳐 **create-anomaly EXECUTE가 이제 ApplyActionFailed 없이
        깔끔히 성공**한다(§15 실측: err=None). 아래 _create_anomaly_absorbing의 흡수 경로는 방어용
        으로 남기되(회귀 대비) 정상 경로에선 발동하지 않는다. 흡수가 발동하면 경고 로그가 뜨므로
        인제스트/데모에서 재발 여부를 감시할 수 있다.

        ## 단일 링크 파라미터 (나머지는 로컬 권위본)
        create-anomaly의 근거는 단일 `observations`(object=Observation, required)뿐이다(§12-1).
        evidence가 여러 건이면 **첫 Observation**으로만 Foundry evidenced_by 엣지를 만들고,
        나머지 근거·OrbitPass/NewsEvent 근거·involves 여러 건·correlated_with는 **로컬 권위본**이
        보관한다(§11 dual 패턴). involves도 첫 Aircraft만 optional `aircraft`로 밀어 involves 엣지.

        ## evidence 강제 (백엔드 무관)
        validate_evidence로 빈 evidence를 거부(EvidenceError, ontology.md §3) — 어떤 백엔드로도
        근거 없는 Anomaly는 저장 불가. Foundry create-anomaly의 required observations 검증과 이중.
        """
        # evidence 강제(백엔드 무관 불변식) — 빈 evidence면 EvidenceError.
        validate_evidence(anomaly, evidence)
        seen = self._written_other.setdefault("Anomaly", set())
        if anomaly.id in seen:
            return  # 프로세스 내 dedup

        # 근거: 첫 Observation을 단일 observations(required)로. Observation 근거가 하나도 없고
        # 타입드 근거(OrbitPass 등)만 있으면 Foundry의 required observations를 못 채운다 → Foundry
        # 스킵(Anomaly는 로컬 권위본에만). 로컬 write_anomaly는 이미 HybridStore에서 선행됨.
        first_obs = _first_ref_of_type(evidence, "Observation", "Observation")
        if first_obs is None:
            _warn(
                f"write_anomaly: {anomaly.id} — Observation 근거 없음(타입드 근거만) → "
                "Foundry 스킵(로컬 권위본 유지)."
            )
            return

        params: dict = {
            "type": anomaly.type,
            "ts": _unix_to_iso(anomaly.ts),
            "lat": float(anomaly.lat) if anomaly.lat is not None else 0.0,
            "lon": float(anomaly.lon) if anomaly.lon is not None else 0.0,
            "observations": first_obs,  # required → evidenced_by 엣지(첫 근거)
            "anomalyId": anomaly.id,  # PK (E-4 리네임: 구 newParameter, §15)
            # correlatedWith: §17/0.8.0에서 **Optional 강등**(구 required → placeholder 강제) 확인 →
            #   생성 시점엔 상관관계 미상이고(write_anomaly 시그니처에 correlated 인자 없음) 다건
            #   correlated_with는 로컬 권위본이므로, 이제 파라미터 자체를 생략한다(placeholder 불필요).
        }
        # optional 스칼라: 값이 있을 때만.
        if anomaly.confidence is not None:
            params["confidence"] = float(anomaly.confidence)
        if anomaly.status:
            params["status"] = anomaly.status
        if anomaly.explanation:
            params["explanation"] = anomaly.explanation
        # E-3(§15) 신규 속성 배선: createdAt·explainerBackend (create-anomaly에 파라미터 존재·실측).
        if anomaly.created_at:
            params["createdAt"] = _unix_to_iso(anomaly.created_at)
        if anomaly.explainer_backend:
            params["explainerBackend"] = anomaly.explainer_backend
        # involves: 첫 Aircraft만 optional aircraft로(involves 엣지). 나머지는 로컬 권위본.
        first_ac = _first_ref_of_type(involves, "Aircraft", "Aircraft")
        if first_ac:
            params["aircraft"] = first_ac
        # 해당 근거가 있을 때만 newsEvents/orbitPasses(opt object) 채움.
        first_news = _first_ref_of_type(evidence, "Observation", "NewsEvent")
        if first_news:
            params["newsEvents"] = first_news
        first_pass = _first_ref_of_type(evidence, "Observation", "OrbitPass")
        if first_pass:
            params["orbitPasses"] = first_pass

        self._create_anomaly_absorbing(anomaly.id, first_obs, params)

    def _create_anomaly_absorbing(
        self, anomaly_id: str, evidence_obs: str, params: dict
    ) -> None:
        """create-anomaly apply + §12 무해 ApplyActionFailed 흡수(read-back 판정).

        성공 경로: (1) 에러 없이 통과 (2) ObjectAlreadyExists=dedup skip (3) 그 밖 예외 →
        read-back(객체 존재 + evidenced_by 엣지)이 성공이면 무해 에러로 간주·흡수. read-back도
        실패하면 진짜 실패 → 예외 전파(HybridStore가 경고 후 로컬 권위본으로 폴백).
        """
        seen = self._written_other.setdefault("Anomaly", set())
        try:
            self._apply(ACTION_CREATE_ANOMALY, params)
            seen.add(anomaly_id)
            return
        except Exception as e:
            if self._is_already_exists(e):
                _warn(f"create-anomaly: {anomaly_id} 이미 존재 (skip)")
                seen.add(anomaly_id)
                return
            # §12: 데이터는 커밋됐을 수 있음 → read-back으로 실제 성공 여부 판정.
            if self._anomaly_written_ok(anomaly_id, evidence_obs):
                _warn(
                    f"create-anomaly: {anomaly_id} — 무해 ApplyActionFailed 흡수 "
                    "(read-back: 객체+evidenced_by 엣지 확인)."
                )
                seen.add(anomaly_id)
                return
            raise

    def _anomaly_written_ok(self, anomaly_id: str, evidence_obs: str) -> bool:
        """read-back 판정: Anomaly 객체 존재 AND evidenced_by→evidence_obs 엣지 형성(§12-2)."""
        if not self._get_object("Anomaly", anomaly_id):
            return False
        return evidence_obs in self._traverse("Anomaly", anomaly_id, "observations")

    def _traverse(self, otype: str, pk: str, link: str) -> list[str]:
        """OSDK 타입드 링크 accessor로 링크 대상 PK 목록(엣지 read-back용). 실패 시 빈 리스트.

        유일 호출: _anomaly_written_ok의 Anomaly→observations(evidenced_by) read-back(§12 방어).
        OSDK Anomaly.observations() 타입드 accessor로 전환(구 저수준 LinkedObject.list_linked_objects
        대체). link 인자는 OSDK 링크 accessor 메서드명과 일치해야 한다(현재 "observations"만 사용).
        """
        try:
            obj = self._osdk_get(otype, pk)
            if obj is None:
                return []
            linked = getattr(
                obj, link
            )()  # 예: Anomaly.observations() → ObservationObjectSet
            return [o.get_primary_key() for o in linked.iterate()]
        except Exception:
            return []

    def set_anomaly_status(self, anomaly_id: str, status: str) -> None:
        """Anomaly status 전이를 Foundry에 반영(confirm/dismiss-anomaly, P7 §9-4·§10 정상 작동).

        confirm-anomaly: →confirmed, dismiss-anomaly: →dismissed (둘 다 `anomaly` object 파라미터).
        candidate 등 그 외 status는 Foundry 전이 액션이 없어 no-op. 반환 없음 — HybridStore가
        로컬 결과(Anomaly)를 권위본으로 돌려주고, 이 호출은 스파인 동기(best-effort)만 담당.
        """
        if status == "confirmed":
            self._apply(ACTION_CONFIRM_ANOMALY, {"anomaly": anomaly_id})
        elif status == "dismissed":
            self._apply(ACTION_DISMISS_ANOMALY, {"anomaly": anomaly_id})

    # ── B2 staged human review (방법 B) — propose/approve/reject-explanation ──────
    def propose_explanation(
        self, anomaly_id: str, explanation: str, review_status: str = "pending"
    ) -> None:
        """AIP 산출 explanation을 **proposedExplanation**으로 제안(reviewStatus=pending).

        본 explanation 속성은 건드리지 않는다(스테이징의 핵심). set_anomaly_status와 동일하게
        스파인 동기(best-effort)만 담당 — HybridStore가 로컬 권위본을 반환한다.
        OSDK 0.11.0 실측 파라미터명(camelCase Foundry api_name)을 그대로 쓴다.
        """
        params: dict = {"anomaly": anomaly_id, "proposedExplanation": explanation}
        if review_status:
            params["reviewStatus"] = review_status
        self._apply(ACTION_PROPOSE_EXPLANATION, params)

    def approve_explanation(self, anomaly_id: str) -> None:
        """승인 — explanation←proposedExplanation 복사 + reviewStatus=approved(Foundry 규칙).

        코드는 액션 apply만 하고, 복사·상태전이는 approve-explanation Modify 규칙이 수행한다.
        proposedExplanation은 OSDK상 optional이라 target(anomaly)만 넘긴다(규칙이 객체 자신의
        proposedExplanation을 읽어 복사 — E2E로 검증).
        """
        self._apply(ACTION_APPROVE_EXPLANATION, {"anomaly": anomaly_id})

    def reject_explanation(self, anomaly_id: str) -> None:
        """기각 — reviewStatus=rejected(Foundry 규칙). 본 explanation·proposed 불변."""
        self._apply(ACTION_REJECT_EXPLANATION, {"anomaly": anomaly_id})

    def write_region(self, region: Region) -> None:
        # Region write는 로컬 유지: FK 타깃(regionId)은 데모 자산(KADIZ)으로 별도 시딩하고,
        # 앱의 Region 관리(지오펜스 폴리곤 등)는 로컬이 권위본. HybridStore가 로컬로 위임.
        self._unsupported("write_region")

    def query_regions(self):
        self._unsupported("query_regions")

    def query_anomalies(self):
        self._unsupported("query_anomalies")


# 어떤 Protocol 메서드를 Foundry로 보내는가 (문서용; 실제 라우팅은 HybridStore의 명시 메서드).
# 나머지(Region·Anomaly·문장 cites·MANY-MANY provenance 링크 read)는 __getattr__로 로컬 위임.
_FOUNDRY_METHODS = frozenset(
    {
        # write (정보 소재를 Foundry로)
        "write_aircraft",
        "write_observation",
        "write_operator",
        "write_satellite",
        "write_orbitpass",
        "write_track",
        "write_weatherstate",
        "write_newsevent",
        # Anomaly = dual-write(Foundry 스칼라+엣지 / 로컬 권위본). set_anomaly_status = dual 전이.
        "write_anomaly",
        "set_anomaly_status",
        # read
        "query_aircraft",
        "aircraft_map",
        "query_all_observations",
        "query_observations_for",
        "query_latest_observations",
        "get_observation",
        "query_operators",
        "query_satellites",
        "satellite_map",
        "query_orbitpasses",
        "query_tracks",
        "query_weather_latest",
        "query_news",
        # 정리·전이
        "delete_future_orbitpasses_for",
        "set_region_alert_level",
    }
)


class HybridStore:
    """정보 소재를 Foundry(스파인)와 Local(보험/문장·링크 권위본)로 라우팅 (DR-0009).

    OntologyStore Protocol을 그대로 만족한다(커넥터·서버·anomaly 무변경). `SKAI_STORE` 미설정
    시엔 make_store()가 순수 LocalOntologyStore를 돌려주므로 이 클래스는 opt-in 경로에서만 쓴다.

    라우팅 요약:
    - **Foundry 소재**(write+read): Aircraft·Observation·Operator·Satellite·OrbitPass·Track·
      WeatherState·NewsEvent(객체) + FK 링크(observed_as·operated_by·of·Track→AC·Weather→Region·
      composed_of).
    - **로컬 소재**: Region + provenance MANY-MANY 링크(correlated_with·mentions·aggregates·cites
      및 다중 evidenced_by/involves) + SituationAssessment 문장 cites. Anomaly read도 로컬.
    - **dual-write**: Anomaly(Foundry 스칼라 + 단일 observations/aircraft 엣지, §12 에러 흡수 +
      로컬 권위본 전체 링크) · SituationAssessment(Foundry 스칼라 스파인 + 로컬 권위본) · NewsEvent
      mentions(Foundry required-param best-effort + 로컬 권위 링크). 상태 전이(confirm/dismiss)도 dual.

    foundry는 주입 가능(테스트에서 실 SDK 없이 fake 주입 → 라우팅·provenance 단위검증).
    """

    def __init__(
        self,
        local: Optional[LocalOntologyStore] = None,
        foundry=None,
        db_path: str = DEFAULT_DB,
    ):
        self.local = local if local is not None else LocalOntologyStore(db_path)
        # foundry 미주입이면 실 어댑터 생성(크리덴셜 필요). 테스트는 fake를 주입한다.
        self.foundry = foundry if foundry is not None else FoundryOntologyStore()

    # ── write: Foundry 소재 ────────────────────────
    def write_aircraft(self, aircraft: Aircraft) -> None:
        self.foundry.write_aircraft(aircraft)

    def write_observation(self, obs: Observation) -> None:
        # provenance는 Foundry 스토어가 다시 강제하지만, 백엔드 무관 불변식이므로 앞단에서도 방어.
        validate_provenance(obs)
        self.foundry.write_observation(obs)

    def write_operator(self, operator: Operator) -> None:
        self.foundry.write_operator(operator)

    def write_satellite(self, satellite: Satellite) -> None:
        self.foundry.write_satellite(satellite)

    def write_orbitpass(self, orbit_pass: OrbitPass) -> None:
        self.foundry.write_orbitpass(orbit_pass)

    def write_track(self, track: Track) -> None:
        self.foundry.write_track(track)

    def write_weatherstate(self, weather: WeatherState) -> None:
        # provenance 백엔드 무관 강제(Foundry 스토어도 재강제).
        validate_provenance(weather)
        self.foundry.write_weatherstate(weather)

    def write_newsevent(self, news: NewsEvent, mentions: Sequence[tuple] = ()) -> None:
        validate_provenance(news)
        # 객체 → Foundry(mention required-param best-effort). Foundry MANY-MANY 링크는 불안정(§9-4)
        # 이라, query_mentions가 읽는 **권위 mention 링크는 로컬에 저장**한다.
        self.foundry.write_newsevent(news, mentions)
        for dst_type, dst_id in mentions:
            self.local.link("NewsEvent", news.id, "mentions", dst_type, dst_id)

    def write_assessment(self, assessment: SituationAssessment) -> None:
        # dual-write: 로컬 = 권위본(문장 cites·aggregates/cites 링크, sentence-cites 검증 강제).
        #             Foundry = 스칼라 스파인(문장 속성 없음, best-effort). 로컬 실패 시 예외 전파,
        #             Foundry 실패는 경고만(스파인은 부가). read는 로컬(문장 보존).
        self.local.write_assessment(assessment)
        try:
            self.foundry.write_assessment(assessment)
        except Exception as e:
            _warn(f"Foundry write_assessment 스칼라 실패(로컬 권위본은 성공): {e!r}")

    # ── write: 로컬 소재 (명시 — Foundry 미배선) ──────
    def write_region(self, region: Region) -> None:
        self.local.write_region(region)

    def write_anomaly(
        self,
        anomaly: Anomaly,
        evidence: Sequence[str],
        involves: Sequence[str] = (),
    ) -> None:
        # dual-write (P7 §12-6): 로컬 = 권위본(Anomaly + evidenced_by/involves + correlated_with
        #   전체 링크, EvidenceError 강제). Foundry = 스칼라 + 단일 observations(첫 근거의
        #   evidenced_by 엣지) + 단일 aircraft(첫 involves 엣지) 스파인(§12 무해 에러 흡수).
        #   read는 로컬(문장·correlated_with·다중 근거 보존).
        # 로컬을 먼저 써서 EvidenceError를 앞단에서 강제(빈 evidence면 Foundry 도달 전 거부).
        self.local.write_anomaly(anomaly, evidence, involves)
        try:
            self.foundry.write_anomaly(anomaly, evidence, involves)
        except Exception as e:
            _warn(f"Foundry write_anomaly 실패(로컬 권위본은 성공): {e!r}")

    def set_anomaly_status(self, anomaly_id: str, status: str) -> Anomaly:
        # 로컬 = 권위본(status 영속·Anomaly 반환). Foundry = confirm/dismiss-anomaly 액션으로
        # 스파인 동기(best-effort). 로컬 먼저 전이해 상태를 확정하고(반환값), Foundry 실패는 경고만.
        result = self.local.set_anomaly_status(anomaly_id, status)
        try:
            self.foundry.set_anomaly_status(anomaly_id, status)
        except Exception as e:
            _warn(
                f"Foundry set_anomaly_status({status}) 실패(로컬 권위본은 성공): {e!r}"
            )
        return result

    # ── B2 staged human review (방법 B) ──────────────
    def propose_explanation(
        self, anomaly_id: str, explanation: str, review_status: str = "pending"
    ) -> Anomaly:
        # 로컬 = 권위본(proposed_explanation·review_status를 attrs에 미러, 본 explanation 불변).
        # Foundry = propose-explanation 액션으로 스파인 동기(best-effort). set_anomaly_status와 동일.
        result = self.local.propose_explanation(anomaly_id, explanation, review_status)
        try:
            self.foundry.propose_explanation(anomaly_id, explanation, review_status)
        except Exception as e:
            _warn(f"Foundry propose_explanation 실패(로컬 권위본은 성공): {e!r}")
        return result

    def approve_explanation(self, anomaly_id: str) -> Anomaly:
        # 로컬 = 권위본(explanation←proposed 복사·review_status=approved). Foundry = 액션 동기.
        result = self.local.approve_explanation(anomaly_id)
        try:
            self.foundry.approve_explanation(anomaly_id)
        except Exception as e:
            _warn(f"Foundry approve_explanation 실패(로컬 권위본은 성공): {e!r}")
        return result

    def reject_explanation(self, anomaly_id: str) -> Anomaly:
        # 로컬 = 권위본(review_status=rejected). Foundry = 액션 동기. 본 explanation·proposed 불변.
        result = self.local.reject_explanation(anomaly_id)
        try:
            self.foundry.reject_explanation(anomaly_id)
        except Exception as e:
            _warn(f"Foundry reject_explanation 실패(로컬 권위본은 성공): {e!r}")
        return result

    # ── 링크 ───────────────────────────────────────
    def link(
        self, src_type: str, src_id: str, link_type: str, dst_type: str, dst_id: str
    ) -> None:
        if link_type == "observed_as":
            # observed_as: write_observation의 aircraftIcao24 FK로 자동 형성(§7-2) → no-op.
            return
        if link_type == "composed_of":
            # composed_of: Foundry edit-observation.trackId로 형성(P7 §10-5).
            self.foundry.link(src_type, src_id, link_type, dst_type, dst_id)
            return
        # 그 밖(evidenced_by·involves·correlated_with·mentions·aggregates·cites) = 로컬 권위본.
        self.local.link(src_type, src_id, link_type, dst_type, dst_id)

    # ── 정리·전이 ──────────────────────────────────
    def delete_future_orbitpasses_for(self, satellite_ref: str, now_ts: int) -> int:
        return self.foundry.delete_future_orbitpasses_for(satellite_ref, now_ts)

    def set_region_alert_level(self, region_id: str, alert_level: str) -> None:
        # Region 객체는 로컬 권위본이나, alertLevel 전이는 Foundry Modify 액션(set-region-alert-level)
        # 으로 스파인에 반영(P7 §10-1 D-2). 로컬 Region엔 alertLevel 컬럼이 없어(스키마) Foundry만.
        return self.foundry.set_region_alert_level(region_id, alert_level)

    # ── read: Foundry 소재 ─────────────────────────
    def query_aircraft(self) -> list[Aircraft]:
        return self.foundry.query_aircraft()

    def aircraft_map(self) -> dict[str, Aircraft]:
        return self.foundry.aircraft_map()

    def query_all_observations(self, limit: Optional[int] = None) -> list[Observation]:
        return self.foundry.query_all_observations(limit)

    def query_observations_for(self, icao24: str) -> list[Observation]:
        return self.foundry.query_observations_for(icao24)

    def query_latest_observations(self) -> list[Observation]:
        return self.foundry.query_latest_observations()

    def get_observation(self, obs_id: str) -> Optional[Observation]:
        return self.foundry.get_observation(obs_id)

    def query_operators(self) -> list[Operator]:
        return self.foundry.query_operators()

    def query_satellites(self) -> list[Satellite]:
        return self.foundry.query_satellites()

    def satellite_map(self) -> dict[str, Satellite]:
        return self.foundry.satellite_map()

    def query_orbitpasses(self) -> list[OrbitPass]:
        return self.foundry.query_orbitpasses()

    def query_tracks(self) -> list[Track]:
        return self.foundry.query_tracks()

    def query_weather_latest(self) -> list[WeatherState]:
        return self.foundry.query_weather_latest()

    def query_news(self) -> list[NewsEvent]:
        return self.foundry.query_news()

    def counts(self) -> dict[str, int]:
        # Foundry 소재 8종은 Foundry 카운트로, 나머지(region·anomaly·assessment·link 등)는 로컬로 병합.
        out = dict(self.local.counts())
        try:
            fc = self.foundry.counts()
            for k in (
                "aircraft",
                "observation",
                "operator",
                "satellite",
                "orbitpass",
                "track",
                "weatherstate",
                "newsevent",
            ):
                if k in fc:
                    out[k] = fc[k]
        except Exception as e:  # Foundry 카운트 실패해도 로컬 카운트는 반환
            _warn(f"Foundry counts 실패 → 로컬값 사용: {e!r}")
        return out

    # ── 나머지 전부 로컬 위임 ─────────────────────
    def __getattr__(self, name: str):
        # __init__에서 set된 self.local/self.foundry는 여기 안 온다(정상 속성).
        # 위에서 명시하지 않은 Protocol 메서드(query_regions·query_anomalies·query_mentions·
        # query_evidence·query_correlations·query_assessments·get_assessment·set_anomaly_status 등)는
        # 전부 LocalOntologyStore로 위임(문장 cites·provenance 링크 권위본).
        local = self.__dict__.get("local")
        if local is None:
            raise AttributeError(name)
        return getattr(local, name)


def current_backend() -> str:
    """현재 SKAI_STORE가 지시하는 read 백엔드 이름을 반환한다('local'|'foundry').

    make_store와 **동일한 게이트**(SKAI_STORE=foundry만 foundry, 그 외·미설정은 local)를 쓰는
    SSOT. 스토어를 만들지 않고 값만 판정하므로 크리덴셜·SDK·.env 불요 → 서버가 /api/stats·
    /api/live에 read 소스("지금 로컬 SQLite냐 Palantir Foundry냐")를 노출할 때 참조한다.
    """
    return (
        "foundry"
        if os.environ.get("SKAI_STORE", "").strip().lower() == "foundry"
        else "local"
    )


def make_store(db_path: str = DEFAULT_DB):
    """SKAI_STORE 환경변수로 스토어 선택. 기본(미설정)은 LocalOntologyStore.

    - SKAI_STORE=foundry → HybridStore(정보 소재 Foundry+Local 라우팅).
    - 그 외/미설정      → LocalOntologyStore(순수 로컬, 데모 재현성 보존).

    커넥터·서버가 LocalOntologyStore(db_path) 대신 이걸 호출하면 SKAI_STORE로 백엔드가 갈린다.
    """
    if current_backend() == "foundry":
        # .env 자동 로드(FOUNDRY_TOKEN·FOUNDRY_HOSTNAME). 없으면 python-dotenv 부재로 무시.
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        return HybridStore(db_path=db_path)
    return LocalOntologyStore(db_path)
