"""FoundryOntologyStore + HybridStore — Foundry 하이브리드 저장 어댑터 (DR-0009).

## 무엇 (DR-0009 결정)
Foundry 온톨로지는 현재 **Aircraft·Observation·Region·Anomaly** Object Type이 구축돼 있다.
전량 이관 대신 **하이브리드**로 간다:

- **FoundryOntologyStore**: Aircraft·Observation을 Foundry에 write(액션)/read(저수준 SDK).
- **HybridStore**: Aircraft·Observation → Foundry, **나머지 전부 → LocalOntologyStore**.
  `SKAI_STORE=foundry`로 활성화(미설정이면 순수 로컬 — 데모 재현성 보존).

## observed_as 링크 (P7 §7-2 확인)
write_observation의 `aircraftIcao24` FK 파라미터로 **자동 형성**된다.
별도 link() 호출 불필요 — HybridStore.link(observed_as)는 no-op.

## read = 저수준 SDK (P0B §8-3 "read=OSDK" 대비 변경)
저수준 `foundry_sdk`(1.97.0)의 `OntologyObject.list/get`으로 Aircraft·Observation 읽기.
OSDK 0.3.0도 발행됐으나(Aircraft·Observation·Region·Anomaly Object + Action 12개)
저수준 SDK는 재발행 없이 라이브 스키마를 dict로 읽어 더 견고.

## 스키마 잔여 이슈 (P7 §7 실측, Ontology Manager UI 대응 필요)
1. `newParameter` 파라미터 오명명 — 기능은 PK 바인딩으로 정상이나 이름이 혼동스러움.
   → icao24/obsId로 UI 리네임 권장(기능 영향 없음).
2. Foundry Observation에 `attrs` 속성 없음 → model.attrs(origin_country 등) 저장 안 됨.
3. Anomaly/Region write: evidence·confidence·status 파라미터 부재(§7-4) → 로컬 유지.
4. Region: PK 바인딩 파라미터 없어 dedup 불가(자동 UUID).

## provenance
write_observation은 백엔드 무관하게 store.validate_provenance로 source·source_url·ts를
강제한다(누락 write 거부). = 환각방지 백본은 Foundry에서도 동일 적용.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from typing import Optional, Sequence

from ontology.model import (
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
from ontology.store import validate_provenance
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

# 사용자 온톨로지 rid (P0B §8-2 실측, OSDK 내장값과 동일).
DEFAULT_ONT_RID = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"

# 액션 API name (2026-07-04 introspection).
ACTION_CREATE_AIRCRAFT = "create-aircraft"
ACTION_CREATE_OBSERVATION = "create-observation"


class FoundryUnsupportedError(NotImplementedError):
    """Foundry에 아직 스키마가 없는 Object Type/메서드 호출.

    HybridStore가 라우팅을 잘못했거나, FoundryOntologyStore를 단독으로 (로컬 위임 없이)
    쓰면서 미구축 객체를 건드릴 때 난다. 갭 목록은 이 파일 상단 참조.
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


def _warn(msg: str) -> None:
    print(f"[store_foundry] {msg}", file=sys.stderr)


class FoundryOntologyStore:
    """Aircraft·Observation 전용 Foundry 어댑터 (write=액션, read=저수준 SDK).

    이 스토어가 직접 지원하는 것은 Aircraft·Observation·observed_as 뿐이다. 나머지
    Protocol 메서드는 FoundryUnsupportedError를 던진다 — HybridStore가 그것들을
    LocalOntologyStore로 라우팅하므로 정상 흐름에선 호출되지 않는다.
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
        self._pf = foundry_sdk.FoundryClient(
            auth=foundry_sdk.UserTokenAuth(token), hostname=hostname
        )
        # 프로세스 내 client-side dedup: 같은 세션 내 이중 write 방지.
        # 세션 간(크로스런) ObjectAlreadyExists는 write 메서드에서 catch·skip.
        self._written_aircraft: dict[str, str] = {}  # icao24 → foundry pk
        self._written_obs: set[str] = set()  # obs.id(자연키)

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
        """ObjectAlreadyExists 계열 예외 판별 (dedup: 크래시 없이 skip용)."""
        name = type(e).__name__
        msg = str(e).lower()
        return (
            "ObjectAlreadyExists" in name
            or "already_exists" in msg
            or "already exists" in msg
        )

    # ── write (Foundry) ───────────────────────────
    def write_aircraft(self, aircraft: Aircraft) -> None:
        if aircraft.icao24 in self._written_aircraft:
            return  # 프로세스 내 dedup
        params: dict = {
            "callsign": aircraft.callsign or aircraft.icao24,
            "registration": aircraft.registration or aircraft.icao24,
            "isMilitary": bool(aircraft.is_military),
            # newParameter = icao24 PK 바인딩 (§7-0: UI 자동오명명, 기능은 PK 설정)
            "newParameter": aircraft.icao24,
        }
        if aircraft.type:
            params["type"] = aircraft.type
        if aircraft.operator_ref:
            params["operatorRef"] = aircraft.operator_ref
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
            # newParameter = obsId PK 바인딩 (§7-0)
            "newParameter": obs.id,
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
        # 잔여 갭: obs.attrs(origin_country 등) Foundry Observation에 attrs 속성 없어 저장 안 됨.

    def link(
        self, src_type: str, src_id: str, link_type: str, dst_type: str, dst_id: str
    ) -> None:
        # observed_as: write_observation의 aircraftIcao24 FK로 자동 형성(§7-2) → no-op.
        if link_type == "observed_as":
            return
        raise FoundryUnsupportedError(
            f"FoundryOntologyStore.link: {link_type}는 Foundry 미지원 "
            "(observed_as만 처리, 나머지는 HybridStore가 로컬로 라우팅)."
        )

    # ── read (Foundry, 저수준 SDK dict→dataclass) ──
    def _list_objects(self, object_type: str) -> list[dict]:
        return list(self._pf.ontologies.OntologyObject.list(self.ont, object_type))

    @staticmethod
    def _dict_to_aircraft(d: dict) -> Aircraft:
        return Aircraft(
            icao24=d.get("icao24"),
            callsign=d.get("callsign"),
            registration=d.get("registration"),
            operator_ref=d.get("operatorRef"),
            type=d.get("type"),
            is_military=bool(d.get("isMilitary")),
        )

    @staticmethod
    def _dict_to_obs(d: dict) -> Observation:
        return Observation(
            id=d.get("obsId"),
            aircraft_ref=d.get("aircraftIcao24") or "",
            ts=_iso_to_unix(d.get("ts")),
            lat=d.get("lat"),
            lon=d.get("lon"),
            alt=d.get("alt"),
            velocity=d.get("velocity"),
            heading=d.get("heading"),
            squawk=d.get("squawk"),
            on_ground=bool(d.get("onGround")),
            source=d.get("source") or "",
            source_url=d.get("sourceUrl") or "",
            attrs={},
        )

    def query_aircraft(self) -> list[Aircraft]:
        return [self._dict_to_aircraft(d) for d in self._list_objects("Aircraft")]

    def aircraft_map(self) -> dict[str, Aircraft]:
        return {a.icao24: a for a in self.query_aircraft()}

    def query_all_observations(self, limit: Optional[int] = None) -> list[Observation]:
        obs = [self._dict_to_obs(d) for d in self._list_objects("Observation")]
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
        try:
            d = self._pf.ontologies.OntologyObject.get(self.ont, "Observation", obs_id)
        except Exception:
            return None
        return self._dict_to_obs(d) if d else None

    def counts(self) -> dict[str, int]:
        return {
            "aircraft": len(self._list_objects("Aircraft")),
            "observation": len(self._list_objects("Observation")),
        }

    # ── 미지원 (Foundry 미구축 객체 — HybridStore가 로컬로 라우팅) ──
    def _unsupported(self, name: str):
        raise FoundryUnsupportedError(
            f"FoundryOntologyStore.{name}: Foundry에 해당 Object Type 미구축 "
            "(HybridStore를 쓰면 로컬로 위임됨). 갭 목록은 store_foundry 상단 참조."
        )

    def write_region(self, region: Region) -> None:
        # Foundry write 미구현: Region에 PK 바인딩 파라미터 없어 dedup 불가(자동 UUID).
        # UI 선행조건: id PK 바인딩 파라미터 추가 필요.
        # → 로컬 유지(HybridStore.__getattr__이 LocalOntologyStore로 위임).
        self._unsupported("write_region")

    def write_track(self, track: Track) -> None:
        self._unsupported("write_track")

    def write_anomaly(self, anomaly, evidence, involves=()) -> None:
        # Foundry write 미구현: create-anomaly에 confidence·status·explanation 파라미터가 없어
        # provenance 정보(근거 링크·신뢰도·상태·설명)를 손실 없이 쓸 수 없음(프로젝트 원칙 위반).
        # UI 선행조건: evidenced_by 링크 파라미터 + confidence/status/explanation 파라미터 추가.
        # → 로컬 유지.
        self._unsupported("write_anomaly")

    def write_satellite(self, satellite: Satellite) -> None:
        self._unsupported("write_satellite")

    def write_orbitpass(self, orbit_pass: OrbitPass) -> None:
        self._unsupported("write_orbitpass")

    def write_weatherstate(self, weather: WeatherState) -> None:
        self._unsupported("write_weatherstate")

    def write_newsevent(self, news: NewsEvent, mentions=()) -> None:
        self._unsupported("write_newsevent")

    def write_operator(self, operator: Operator) -> None:
        self._unsupported("write_operator")

    def write_assessment(self, assessment: SituationAssessment) -> None:
        self._unsupported("write_assessment")

    def query_regions(self):
        self._unsupported("query_regions")

    def query_tracks(self):
        self._unsupported("query_tracks")

    def query_anomalies(self):
        self._unsupported("query_anomalies")


# 어떤 Protocol 메서드를 Foundry로 보내는가 (나머지는 전부 LocalOntologyStore).
_FOUNDRY_METHODS = frozenset(
    {
        "write_aircraft",
        "write_observation",
        "query_aircraft",
        "aircraft_map",
        "query_all_observations",
        "query_observations_for",
        "query_latest_observations",
        "get_observation",
    }
)


class HybridStore:
    """Aircraft·Observation·observed_as → Foundry, 나머지 → LocalOntologyStore (DR-0009).

    OntologyStore Protocol을 그대로 만족한다(커넥터·서버·anomaly 무변경). `SKAI_STORE` 미설정
    시엔 make_store()가 순수 LocalOntologyStore를 돌려주므로 이 클래스는 opt-in 경로에서만 쓴다.

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

    # ── Foundry 라우팅 (핵심 엔티티) ──────────────
    def write_aircraft(self, aircraft: Aircraft) -> None:
        self.foundry.write_aircraft(aircraft)

    def write_observation(self, obs: Observation) -> None:
        # provenance는 Foundry 스토어가 다시 강제하지만, 백엔드 무관 불변식이므로 앞단에서도 방어.
        validate_provenance(obs)
        self.foundry.write_observation(obs)

    def link(
        self, src_type: str, src_id: str, link_type: str, dst_type: str, dst_id: str
    ) -> None:
        if link_type == "observed_as":
            # observed_as: write_observation의 aircraftIcao24 FK로 자동 형성(§7-2) → no-op.
            return
        self.local.link(src_type, src_id, link_type, dst_type, dst_id)

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

    def counts(self) -> dict[str, int]:
        # Aircraft·Observation은 Foundry 카운트, 나머지는 로컬 카운트로 병합(관측 소재 반영).
        out = dict(self.local.counts())
        try:
            fc = self.foundry.counts()
            out["aircraft"] = fc.get("aircraft", out.get("aircraft", 0))
            out["observation"] = fc.get("observation", out.get("observation", 0))
        except Exception as e:  # Foundry 카운트 실패해도 로컬 카운트는 반환
            _warn(f"Foundry counts 실패 → 로컬값 사용: {e!r}")
        return out

    # ── 나머지 전부 로컬 위임 ─────────────────────
    def __getattr__(self, name: str):
        # __init__에서 set된 self.local/self.foundry는 여기 안 온다(정상 속성).
        # 위에서 명시하지 않은 Protocol 메서드는 전부 LocalOntologyStore로 위임.
        local = self.__dict__.get("local")
        if local is None:
            raise AttributeError(name)
        return getattr(local, name)


def make_store(db_path: str = DEFAULT_DB):
    """SKAI_STORE 환경변수로 스토어 선택. 기본(미설정)은 LocalOntologyStore.

    - SKAI_STORE=foundry → HybridStore(Aircraft·Observation·observed_as=Foundry, 나머지=로컬).
    - 그 외/미설정      → LocalOntologyStore(순수 로컬, 데모 재현성 보존).

    커넥터·서버가 LocalOntologyStore(db_path) 대신 이걸 호출하면 SKAI_STORE로 백엔드가 갈린다.
    """
    backend = os.environ.get("SKAI_STORE", "").strip().lower()
    if backend == "foundry":
        # .env 자동 로드(FOUNDRY_TOKEN·FOUNDRY_HOSTNAME). 없으면 python-dotenv 부재로 무시.
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        return HybridStore(db_path=db_path)
    return LocalOntologyStore(db_path)
