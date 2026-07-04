"""P7 — HybridStore 라우팅·provenance 단위 테스트 + Foundry 라이브 통합(옵션).

단위(네트워크·크리덴셜 불요): fake Foundry 어댑터를 주입해 HybridStore의
  - 라우팅(Aircraft·Observation→Foundry, 나머지→Local)
  - observed_as no-op(write_observation의 aircraftIcao24 FK 자동 형성 — §7-2)
  - provenance 강제(백엔드 무관)
  - counts 병합
을 검증한다. foundry_sdk 없이(메인 .venv=3.14) 통과해야 한다.

파라미터 매핑 단위: FoundryOntologyStore를 mock _pf로 생성해
  - write_aircraft: icao24=PK (E-4 리네임: 구 newParameter, §15)
  - write_observation: obsId=PK, aircraftIcao24=aircraft_ref(FK), optional None 생략
를 직접 검증한다. (edit-observation은 여전히 newParameter required — composed_of 경로에서 유지.)

라이브(skip 마커): FOUNDRY_TOKEN·FOUNDRY_HOSTNAME + foundry_sdk 있을 때만.
  실 Foundry read-only(query_aircraft·counts)만 — 테스트 스위트가 Foundry에 쓰지 않게.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from ontology.model import (
    KADIZ_REGION,
    Aircraft,
    Anomaly,
    NewsEvent,
    Observation,
    Operator,
    OrbitPass,
    Satellite,
    Track,
    WeatherState,
)
from ontology.store import ProvenanceError
from ontology.store_foundry import HybridStore, make_store
from ontology.store_local import LocalOntologyStore


# ── fake Foundry 어댑터 (네트워크 없이 라우팅 검증) ──────────────────────────
class FakeFoundry:
    def __init__(self):
        self.calls: list[tuple] = []
        self.aircraft: list[Aircraft] = []
        self.obs: list[Observation] = []
        self.links: list[tuple] = []
        # P7 §11 확장 소재
        self.operators: list[Operator] = []
        self.satellites: list[Satellite] = []
        self.orbitpasses: list[OrbitPass] = []
        self.tracks: list[Track] = []
        self.weather: list[WeatherState] = []
        self.news: list[tuple] = []  # (NewsEvent, mentions)
        self.assessments: list = []
        self.alerts: list[tuple] = []  # (region_id, level)
        self.deleted_passes: list[tuple] = []  # (satellite_ref, now_ts)
        self.anomalies: list[tuple] = []  # (anomaly, evidence, involves)
        self.anomaly_status: list[tuple] = []  # (anomaly_id, status)

    def write_aircraft(self, a: Aircraft) -> None:
        self.calls.append(("write_aircraft", a.icao24))
        self.aircraft.append(a)

    def write_observation(self, o: Observation) -> None:
        self.calls.append(("write_observation", o.id))
        self.obs.append(o)

    def write_operator(self, op: Operator) -> None:
        self.calls.append(("write_operator", op.id))
        self.operators.append(op)

    def write_satellite(self, s: Satellite) -> None:
        self.calls.append(("write_satellite", s.norad_id))
        self.satellites.append(s)

    def write_orbitpass(self, p: OrbitPass) -> None:
        self.calls.append(("write_orbitpass", p.id))
        self.orbitpasses.append(p)

    def write_track(self, t: Track) -> None:
        self.calls.append(("write_track", t.id))
        self.tracks.append(t)

    def write_weatherstate(self, w: WeatherState) -> None:
        self.calls.append(("write_weatherstate", w.id))
        self.weather.append(w)

    def write_newsevent(self, n: NewsEvent, mentions=()) -> None:
        self.calls.append(("write_newsevent", n.id))
        self.news.append((n, tuple(mentions)))

    def write_assessment(self, a) -> None:
        self.calls.append(("write_assessment", a.id))
        self.assessments.append(a)

    def write_anomaly(self, anomaly, evidence, involves=()) -> None:
        self.calls.append(("write_anomaly", anomaly.id))
        self.anomalies.append((anomaly, tuple(evidence), tuple(involves)))

    def set_anomaly_status(self, anomaly_id, status) -> None:
        self.calls.append(("set_anomaly_status", anomaly_id))
        self.anomaly_status.append((anomaly_id, status))

    def set_region_alert_level(self, region_id, level) -> None:
        self.calls.append(("set_region_alert_level", region_id))
        self.alerts.append((region_id, level))

    def delete_future_orbitpasses_for(self, satellite_ref, now_ts) -> int:
        self.calls.append(("delete_future_orbitpasses_for", satellite_ref))
        self.deleted_passes.append((satellite_ref, now_ts))
        return 0

    def link(self, src_type, src_id, link_type, dst_type, dst_id) -> None:
        self.calls.append(("link", link_type))
        self.links.append((src_type, src_id, link_type, dst_type, dst_id))

    def query_aircraft(self):
        return list(self.aircraft)

    def aircraft_map(self):
        return {a.icao24: a for a in self.aircraft}

    def query_all_observations(self, limit=None):
        return list(self.obs)[:limit] if limit else list(self.obs)

    def query_observations_for(self, icao24):
        return [o for o in self.obs if o.aircraft_ref == icao24]

    def query_latest_observations(self):
        return list(self.obs)

    def get_observation(self, obs_id):
        return next((o for o in self.obs if o.id == obs_id), None)

    def query_operators(self):
        return list(self.operators)

    def query_satellites(self):
        return list(self.satellites)

    def satellite_map(self):
        return {s.norad_id: s for s in self.satellites}

    def query_orbitpasses(self):
        return list(self.orbitpasses)

    def query_tracks(self):
        return list(self.tracks)

    def query_weather_latest(self):
        return list(self.weather)

    def query_news(self):
        return [n for n, _ in self.news]

    def counts(self):
        return {
            "aircraft": len(self.aircraft),
            "observation": len(self.obs),
            "operator": len(self.operators),
            "satellite": len(self.satellites),
            "orbitpass": len(self.orbitpasses),
            "track": len(self.tracks),
            "weatherstate": len(self.weather),
            "newsevent": len(self.news),
        }


def _valid_obs(icao24="abc123", ts=1_700_000_000) -> Observation:
    return Observation(
        id=f"{icao24}-{ts}",
        aircraft_ref=icao24,
        ts=ts,
        lat=36.0,
        lon=124.0,
        source="opensky",
        source_url="https://opensky-network.org/api/states/all",
    )


@pytest.fixture
def hybrid(tmp_path):
    fake = FakeFoundry()
    store = HybridStore(
        local=LocalOntologyStore(str(tmp_path / "hybrid.db")), foundry=fake
    )
    return store, fake


# ── 라우팅: 핵심 엔티티 → Foundry ────────────────────────────────────────────
def test_write_aircraft_routes_to_foundry(hybrid):
    store, fake = hybrid
    store.write_aircraft(Aircraft(icao24="abc123", callsign="TEST"))
    assert [c for c in fake.calls if c[0] == "write_aircraft"]
    # 로컬 aircraft 테이블엔 안 들어감(Foundry 소재)
    assert store.local.query_aircraft() == []


def test_write_observation_routes_to_foundry(hybrid):
    store, fake = hybrid
    obs = _valid_obs()
    store.write_observation(obs)
    assert fake.obs and fake.obs[0].id == obs.id
    assert store.local.query_all_observations() == []


def test_observed_as_link_is_noop(hybrid):
    """observed_as link() 호출은 no-op — write_observation의 aircraftIcao24 FK로 자동 형성(§7-2)."""
    store, fake = hybrid
    store.link("Aircraft", "abc123", "observed_as", "Observation", "abc123-1")
    # Foundry link() 미호출, 로컬 link 테이블도 미기록
    assert not fake.links
    assert store.local.query_mentions("x") == []


def test_non_observed_link_routes_to_local(hybrid):
    store, fake = hybrid
    # evidenced_by 등 나머지 링크는 로컬로
    store.link("Anomaly", "anomaly-1", "evidenced_by", "Observation", "abc123-1")
    assert not fake.links  # Foundry로 안 감
    rows = store.local.query_evidence("anomaly-1")
    assert {"type": "Observation", "id": "abc123-1"} in rows


# ── 라우팅: 나머지 객체 → Local (via __getattr__) ────────────────────────────
def test_write_region_routes_to_local(hybrid):
    store, fake = hybrid
    store.write_region(KADIZ_REGION)
    assert [r.id for r in store.local.query_regions()] == ["KADIZ"]
    assert not fake.calls  # Foundry 미접촉


def test_write_track_routes_to_foundry(hybrid):
    """P7 §11: write_track은 Foundry 소재로 라우팅(구 로컬 → Foundry)."""
    store, fake = hybrid
    track = Track(
        id="track-abc", aircraft_ref="abc123", start_ts=1, end_ts=2, path=[[36, 124]]
    )
    store.write_track(track)
    assert [c for c in fake.calls if c[0] == "write_track"]
    assert [t.id for t in fake.tracks] == ["track-abc"]
    # 로컬 track 테이블엔 안 들어감(Foundry 소재)
    assert store.local.query_tracks() == []


def test_write_anomaly_dual_write(hybrid):
    """write_anomaly는 dual-write(P7 §12): 로컬 권위본 + Foundry 스칼라/엣지 스파인."""
    store, fake = hybrid
    anomaly = Anomaly(
        id="anomaly-1", type="emergency_squawk", ts=1_700_000_000, confidence=0.9
    )
    store.write_anomaly(anomaly, evidence=["abc123-1700000000"])
    # 로컬 권위본
    assert [a.id for a in store.local.query_anomalies()] == ["anomaly-1"]
    assert store.local.query_evidence("anomaly-1") == [
        {"type": "Observation", "id": "abc123-1700000000"}
    ]
    # Foundry 스파인 dual-write
    assert [a.id for a, _, _ in fake.anomalies] == ["anomaly-1"]


def test_write_anomaly_empty_evidence_rejected_before_foundry(hybrid):
    """빈 evidence는 EvidenceError로 거부(백엔드 무관) — Foundry 도달 전."""
    from ontology.store import EvidenceError

    store, fake = hybrid
    anomaly = Anomaly(id="anomaly-x", type="emergency_squawk", ts=1, confidence=0.5)
    with pytest.raises(EvidenceError):
        store.write_anomaly(anomaly, evidence=[])
    assert not fake.anomalies  # Foundry 미접촉
    assert store.local.query_anomalies() == []  # 로컬도 미저장


def test_write_anomaly_foundry_failure_keeps_local(hybrid):
    """Foundry write_anomaly 예외는 흡수(경고) — 로컬 권위본은 남는다."""
    store, fake = hybrid

    def boom(anomaly, evidence, involves=()):
        raise RuntimeError("read-back 실패 = 진짜 실패")

    fake.write_anomaly = boom
    anomaly = Anomaly(id="anomaly-2", type="loss_of_signal", ts=1, confidence=0.7)
    store.write_anomaly(anomaly, evidence=["abc123-1"])  # 크래시 없어야
    assert [a.id for a in store.local.query_anomalies()] == ["anomaly-2"]


def test_set_anomaly_status_dual_transition(hybrid):
    """confirm/dismiss 전이는 dual: 로컬 권위본(반환) + Foundry confirm/dismiss-anomaly 액션."""
    store, fake = hybrid
    anomaly = Anomaly(id="anomaly-3", type="emergency_squawk", ts=1, confidence=0.9)
    store.write_anomaly(anomaly, evidence=["abc123-1"])
    got = store.set_anomaly_status("anomaly-3", "confirmed")
    # 로컬 권위본 전이·반환
    assert got.status == "confirmed"
    assert store.local.get_anomaly("anomaly-3").status == "confirmed"
    # Foundry 동기 호출
    assert ("anomaly-3", "confirmed") in fake.anomaly_status


def test_set_anomaly_status_foundry_failure_keeps_local(hybrid):
    """Foundry 전이 실패는 흡수 — 로컬 status는 확정·반환된다."""
    store, fake = hybrid
    anomaly = Anomaly(id="anomaly-4", type="emergency_squawk", ts=1, confidence=0.9)
    store.write_anomaly(anomaly, evidence=["abc123-1"])

    def boom(anomaly_id, status):
        raise RuntimeError("confirm-anomaly 실패")

    fake.set_anomaly_status = boom
    got = store.set_anomaly_status("anomaly-4", "dismissed")
    assert got.status == "dismissed"
    assert store.local.get_anomaly("anomaly-4").status == "dismissed"


# ── read 라우팅 ──────────────────────────────────────────────────────────────
def test_query_aircraft_reads_from_foundry(hybrid):
    store, fake = hybrid
    store.write_aircraft(Aircraft(icao24="abc123", callsign="TEST"))
    got = store.query_aircraft()
    assert [a.icao24 for a in got] == ["abc123"]
    assert set(store.aircraft_map().keys()) == {"abc123"}


def test_get_observation_reads_from_foundry(hybrid):
    store, fake = hybrid
    obs = _valid_obs()
    store.write_observation(obs)
    assert store.get_observation(obs.id).id == obs.id
    assert store.query_latest_observations()[0].id == obs.id


# ── provenance 강제 (백엔드 무관) ────────────────────────────────────────────
def test_provenance_missing_rejected_before_foundry(hybrid):
    store, fake = hybrid
    bad = _valid_obs()
    bad.source = ""  # provenance 누락
    with pytest.raises(ProvenanceError):
        store.write_observation(bad)
    assert not fake.obs  # Foundry에 도달 전 거부


def test_provenance_missing_ts_rejected(hybrid):
    store, fake = hybrid
    bad = _valid_obs()
    bad.ts = 0
    with pytest.raises(ProvenanceError):
        store.write_observation(bad)
    assert not fake.obs


# ── counts 병합 ──────────────────────────────────────────────────────────────
def test_counts_merges_foundry_and_local(hybrid):
    store, fake = hybrid
    store.write_region(KADIZ_REGION)  # local
    store.write_aircraft(Aircraft(icao24="abc123"))  # foundry
    store.write_observation(_valid_obs())  # foundry
    counts = store.counts()
    assert counts["aircraft"] == 1  # Foundry 카운트
    assert counts["observation"] == 1  # Foundry 카운트
    assert counts["region"] == 1  # 로컬 카운트


# ── make_store 팩토리 ────────────────────────────────────────────────────────
def test_make_store_default_is_local(tmp_path, monkeypatch):
    monkeypatch.delenv("SKAI_STORE", raising=False)
    store = make_store(str(tmp_path / "x.db"))
    assert isinstance(store, LocalOntologyStore)


def test_make_store_non_foundry_is_local(tmp_path, monkeypatch):
    monkeypatch.setenv("SKAI_STORE", "local")
    store = make_store(str(tmp_path / "x.db"))
    assert isinstance(store, LocalOntologyStore)


# ── FoundryOntologyStore 파라미터 매핑 단위 (mock _pf, 크리덴셜 불요) ────────────────────


def _make_foundry_store_with_mock():
    """foundry_sdk를 mock해 FoundryOntologyStore를 생성, (store, captured_calls) 반환."""
    from ontology.store_foundry import FoundryOntologyStore

    mock_sdk = MagicMock()
    mock_client = MagicMock()
    mock_sdk.FoundryClient.return_value = mock_client
    mock_sdk.UserTokenAuth.return_value = MagicMock()

    captured: list[dict] = []

    def fake_apply(ont, action, parameters, options=None):
        captured.append({"action": action, "parameters": dict(parameters)})
        return MagicMock(edits=None)

    mock_client.ontologies.Action.apply.side_effect = fake_apply

    with patch.dict(sys.modules, {"foundry_sdk": mock_sdk}):
        store = FoundryOntologyStore(token="t", hostname="h")

    return store, captured


def test_write_aircraft_uses_real_pk():
    """write_aircraft: icao24=aircraft.icao24(E-4 리네임 PK), newParameter1 없음."""
    store, captured = _make_foundry_store_with_mock()
    ac = Aircraft(icao24="abc123", callsign="TEST", registration="REG1")

    with patch.object(store, "_apply", wraps=store._apply):
        # _apply를 직접 모킹해 파라미터 캡처
        calls: list[dict] = []
        original_apply = store._apply

        def capturing_apply(action, parameters):
            calls.append({"action": action, "parameters": dict(parameters)})
            return None

        store._apply = capturing_apply
        store.write_aircraft(ac)

    assert len(calls) == 1
    params = calls[0]["parameters"]
    assert params["icao24"] == "abc123"  # 실 PK (E-4 리네임)
    assert "newParameter" not in params  # 리네임됨(§15)
    assert "newParameter1" not in params  # 제거됨(§7-6 수정 1)
    assert params["callsign"] == "TEST"
    assert params["isMilitary"] is False


def test_write_aircraft_dedup_no_double_call():
    """같은 icao24 두 번 호출 시 두 번째는 _apply 호출 없음(프로세스 내 dedup)."""
    store, _ = _make_foundry_store_with_mock()
    calls: list = []
    store._apply = lambda action, parameters: calls.append(action) or None

    ac = Aircraft(icao24="abc123", callsign="TEST")
    store.write_aircraft(ac)
    store.write_aircraft(ac)  # 두 번째 — dedup으로 skip
    assert len(calls) == 1


def test_write_observation_params():
    """write_observation: obsId=obs.id(E-4 리네임 PK), aircraftIcao24=aircraft_ref(FK)."""
    store, _ = _make_foundry_store_with_mock()
    calls: list[dict] = []
    store._apply = lambda action, params: (
        calls.append({"action": action, "params": dict(params)}) or None
    )

    obs = _valid_obs(icao24="abc123", ts=1_700_000_000)
    store.write_observation(obs)

    assert len(calls) == 1
    params = calls[0]["params"]
    assert params["obsId"] == obs.id  # obsId PK (E-4 리네임, §15)
    assert (
        params["aircraftIcao24"] == "abc123"
    )  # FK → observed_as 자동 형성(§7-6 수정 2)
    assert params["source"] == "opensky"
    assert params["sourceUrl"]


def test_write_observation_none_telemetry_omitted():
    """optional 텔레메트리 None이면 파라미터에서 생략(0.0 placeholder 없음)."""
    store, _ = _make_foundry_store_with_mock()
    calls: list[dict] = []
    store._apply = lambda action, params: calls.append(dict(params)) or None

    obs = _valid_obs()
    # alt/velocity/heading/squawk 전부 None/빈값
    obs.alt = None
    obs.velocity = None
    obs.heading = None
    obs.squawk = None

    store.write_observation(obs)
    params = calls[0]
    assert "alt" not in params
    assert "velocity" not in params
    assert "heading" not in params
    assert "squawk" not in params


def test_write_observation_telemetry_included_when_set():
    """optional 텔레메트리 값이 있으면 파라미터에 포함됨."""
    store, _ = _make_foundry_store_with_mock()
    calls: list[dict] = []
    store._apply = lambda action, params: calls.append(dict(params)) or None

    obs = _valid_obs()
    obs.alt = 9500.0
    obs.velocity = 210.0
    obs.heading = 270.0
    obs.squawk = "7700"

    store.write_observation(obs)
    params = calls[0]
    assert params["alt"] == 9500.0
    assert params["velocity"] == 210.0
    assert params["heading"] == 270.0
    assert params["squawk"] == "7700"


def test_write_observation_already_exists_no_crash():
    """ObjectAlreadyExists 예외 → 크래시 없이 skip."""
    store, _ = _make_foundry_store_with_mock()

    def raise_already_exists(action, params):
        raise type("ObjectAlreadyExistsError", (Exception,), {})("already exists")

    store._apply = raise_already_exists
    obs = _valid_obs()
    store.write_observation(obs)  # 크래시 없이 통과해야 함
    assert obs.id in store._written_obs  # dedup set에 추가됨


def test_foundry_link_observed_as_noop():
    """FoundryOntologyStore.link(observed_as) → no-op (예외 없음, _apply 미호출)."""
    store, _ = _make_foundry_store_with_mock()
    apply_calls: list = []
    store._apply = lambda *a, **k: apply_calls.append(a)

    store.link("Aircraft", "abc123", "observed_as", "Observation", "obs-1")
    assert not apply_calls  # _apply 미호출


# ── P7 §11: 신규 7타입 write 파라미터 매핑 (mock _pf) ────────────────────────
def _capture_store():
    """FoundryOntologyStore + _apply 캡처 리스트 반환."""
    store, _ = _make_foundry_store_with_mock()
    calls: list[dict] = []
    store._apply = lambda action, params: (
        calls.append({"action": action, "params": dict(params)}) or None
    )
    return store, calls


def test_write_operator_params():
    """create-operator: operatorId(E-4 리네임 PK), name/kind/country 매핑."""
    store, calls = _capture_store()
    store.write_operator(Operator(id="op-1", name="KAF", kind="airforce", country="KR"))
    p = calls[0]["params"]
    assert calls[0]["action"] == "create-operator"
    assert p["operatorId"] == "op-1"
    assert (p["name"], p["kind"], p["country"]) == ("KAF", "airforce", "KR")


def test_write_operator_none_country_placeholder():
    """country는 required(Foundry) — model None이면 placeholder로 채움."""
    store, calls = _capture_store()
    store.write_operator(Operator(id="op-2", name="X", kind="airline", country=None))
    assert calls[0]["params"]["country"] == "unknown"


def test_write_satellite_params():
    """create-satellite: noradId(E-4 리네임 PK), objectType/operatorRef/tleEpoch 매핑."""
    store, calls = _capture_store()
    store.write_satellite(
        Satellite(
            norad_id="25544",
            name="ISS",
            operator_ref="NASA",
            object_type="PAYLOAD",
            tle_epoch="2026-07-04T00:00:00+00:00",
        )
    )
    p = calls[0]["params"]
    assert calls[0]["action"] == "create-satellite"
    assert p["noradId"] == "25544"
    assert p["objectType"] == "PAYLOAD"
    assert p["operatorRef"] == "NASA"
    assert p["tleEpoch"] == "2026-07-04T00:00:00+00:00"


def test_write_satellite_none_tle_epoch_fallback():
    """tleEpoch required — None이면 현재시각 ISO로 대체(빈값 금지)."""
    store, calls = _capture_store()
    store.write_satellite(Satellite(norad_id="1", name="s", tle_epoch=None))
    assert calls[0]["params"]["tleEpoch"]  # 비어있지 않음


def test_write_orbitpass_fk_params():
    """create-orbit-pass: satelliteNoradId(FK→of)·regionId·ts 매핑, PK 바인딩."""
    store, calls = _capture_store()
    store.write_orbitpass(
        OrbitPass(
            id="pass-25544-100",
            satellite_ref="25544",
            region_ref="KADIZ",
            start_ts=100,
            end_ts=200,
            max_elevation=45.0,
        )
    )
    p = calls[0]["params"]
    assert calls[0]["action"] == "create-orbit-pass"
    assert p["passId"] == "pass-25544-100"
    assert p["satelliteNoradId"] == "25544"  # FK → OrbitPass.satellite(of)
    assert p["regionId"] == "KADIZ"
    assert p["maxElevation"] == 45.0
    assert p["startTs"].startswith("1970")  # unix 100 → ISO


def test_write_track_params():
    """create-track: aircraftIcao24(FK→Track.aircraft)·pathJson·hasGap 매핑, PK 바인딩."""
    store, calls = _capture_store()
    store.write_track(
        Track(
            id="track-abc",
            aircraft_ref="abc123",
            start_ts=1,
            end_ts=2,
            path=[[36.0, 124.0]],
            has_gap=True,
        )
    )
    p = calls[0]["params"]
    assert calls[0]["action"] == "create-track"
    assert p["trackId"] == "track-abc"
    assert p["aircraftIcao24"] == "abc123"  # FK → Track.aircraft
    assert p["hasGap"] is True
    assert p["pathJson"] == "[[36.0, 124.0]]"


def test_write_weatherstate_params():
    """create-weather-state: regionId(FK)·wind 합성·conditions←flight_category·rawText←conditions."""
    store, calls = _capture_store()
    ws = WeatherState(
        id="wx-RKSI-100",
        region_ref="KADIZ",
        ts=100,
        station="RKSI",
        wind_dir=200,
        wind_speed_kt=8,
        visibility_sm=6.0,
        ceiling_ft=3000,
        flight_category="MVFR",
        conditions="METAR RKSI 200208KT",
        source="aviationweather",
        source_url="https://aviationweather.gov/metar",
    )
    store.write_weatherstate(ws)
    p = calls[0]["params"]
    assert calls[0]["action"] == "create-weather-state"
    assert p["weatherId"] == "wx-RKSI-100"
    assert p["regionId"] == "KADIZ"  # FK → WeatherState.region
    assert p["wind"] == "200/8"  # dir/speed 합성
    assert p["visibilitySm"] == 6.0
    assert p["ceilingFt"] == 3000.0
    assert p["conditions"] == "MVFR"  # ← flight_category
    assert p["rawText"] == "METAR RKSI 200208KT"  # ← model.conditions(원문)


def test_write_weatherstate_none_ceiling_sentinel():
    """ceiling_ft None(무제한) → required double라 sentinel(99999)로 표기(왜곡 최소화)."""
    store, calls = _capture_store()
    ws = WeatherState(
        id="wx-RKSI-1",
        region_ref="KADIZ",
        ts=100,
        station="RKSI",
        ceiling_ft=None,
        source="aw",
        source_url="https://x",
    )
    store.write_weatherstate(ws)
    assert calls[0]["params"]["ceilingFt"] == 99999.0


def test_write_weatherstate_provenance_missing_rejected():
    """provenance(source/source_url/ts) 누락 weather는 write 거부(백엔드 무관)."""
    store, calls = _capture_store()
    ws = WeatherState(id="wx-x-1", region_ref="KADIZ", ts=0, station="X")  # ts=0
    with pytest.raises(ProvenanceError):
        store.write_weatherstate(ws)
    assert not calls  # _apply 미도달


def test_write_newsevent_params_and_clamp():
    """create-news-event: newsId(E-4 PK), url←source_url, confidence clamp, mentions Optional(E-2.4)."""
    store, calls = _capture_store()
    news = NewsEvent(
        id="news-x",
        source="gdelt",
        source_url="https://a.example/story",
        ts=100,
        title="t",
        summary="s",
        confidence=0.9,  # → 0.4로 clamp
        entities=["KADIZ"],
    )
    store.write_newsevent(news, mentions=[("Region", "KADIZ")])
    p = calls[0]["params"]
    assert calls[0]["action"] == "create-news-event"
    assert p["newsId"] == "news-x"  # E-4 리네임 PK
    assert p["url"] == "https://a.example/story"  # Foundry url = source_url
    assert p["confidence"] == 0.4  # NEWS_MAX_CONFIDENCE clamp
    assert p["regions"] == "KADIZ"  # mention → regions 파라미터
    # E-2.4(§15): mention 파라미터 Optional화 → 실 ref 없으면 생략(구 "none" placeholder 제거).
    assert "aircraft" not in p and "operators" not in p
    assert p["entitiesJson"] == '["KADIZ"]'


def test_write_satellite_dedup_no_double_call():
    """같은 noradId 두 번 write → 두 번째는 _apply 호출 없음(프로세스 내 dedup)."""
    store, _ = _make_foundry_store_with_mock()
    calls: list = []
    store._apply = lambda action, params: calls.append(action) or None
    sat = Satellite(norad_id="25544", name="ISS")
    store.write_satellite(sat)
    store.write_satellite(sat)
    assert calls == ["create-satellite"]


def test_write_satellite_already_exists_no_crash():
    """ObjectAlreadyExists(크로스런) → 크래시 없이 skip + dedup 마킹."""
    store, _ = _make_foundry_store_with_mock()

    def raise_exists(action, params):
        raise type("ObjectAlreadyExistsError", (Exception,), {})("already exists")

    store._apply = raise_exists
    store.write_satellite(Satellite(norad_id="25544", name="ISS"))  # 크래시 없어야
    assert "25544" in store._written_other.get("Satellite", set())


def test_foundry_link_composed_of_edits_observation():
    """link(composed_of) → edit-observation으로 기존 Observation.trackId 세팅."""
    store, _ = _make_foundry_store_with_mock()
    # _get_object가 기존 Observation dict를 반환하도록 mock
    store._get_object = lambda ot, pk: {
        "obsId": pk,
        "lat": 36.0,
        "lon": 124.0,
        "onGround": False,
        "source": "opensky",
        "sourceUrl": "https://x",
        "ts": "2023-11-14T22:13:20+00:00",
    }
    calls: list[dict] = []
    store._apply = lambda action, params: (
        calls.append({"action": action, "params": dict(params)}) or None
    )
    store.link("Track", "track-1", "composed_of", "Observation", "abc123-100")
    assert calls[0]["action"] == "edit-observation"
    p = calls[0]["params"]
    assert p["Observation"] == "abc123-100"
    assert p["trackId"] == "track-1"  # composed_of FK
    assert p["newParameter"] == "abc123-100"  # obsId PK


# ── P7 §12: write_anomaly 에러 흡수 + read-back 판정 (mock _pf) ───────────────
def _anomaly(**kw):
    base = dict(
        id="anomaly-1",
        type="emergency_squawk",
        ts=1_700_000_000,
        confidence=0.9,
        status="candidate",
        lat=36.5,
        lon=124.5,
        explanation="obs-grounded",
    )
    base.update(kw)
    return Anomaly(**base)


def _raise(exc_name="ApplyActionFailedError", msg="INVALID_ARGUMENT ApplyActionFailed"):
    def _fn(action, params):
        raise type(exc_name, (Exception,), {})(msg)

    return _fn


def test_write_anomaly_params_mapping():
    """create-anomaly: observations=첫 근거, anomalyId(E-4 PK), correlatedWith placeholder, 스칼라·aircraft."""
    store, calls = _capture_store()
    store.write_anomaly(_anomaly(), evidence=["obs-a", "obs-b"], involves=["847114"])
    assert len(calls) == 1
    p = calls[0]["params"]
    assert calls[0]["action"] == "create-anomaly"
    assert p["observations"] == "obs-a"  # 첫 근거만 Foundry(단일 파라미터)
    assert p["anomalyId"] == "anomaly-1"  # E-4 리네임 PK (구 newParameter)
    assert "newParameter" not in p  # 리네임됨(§15)
    assert p["correlatedWith"] == "none"  # required(E-2.3) → present-only placeholder
    assert p["confidence"] == 0.9
    assert p["status"] == "candidate"
    assert p["explanation"] == "obs-grounded"
    assert p["aircraft"] == "847114"  # involves 첫 Aircraft
    assert p["type"] == "emergency_squawk"


def test_write_anomaly_e3_attrs_wired():
    """E-3(§15): createdAt·explainerBackend 값이 있으면 create-anomaly 파라미터에 배선."""
    store, calls = _capture_store()
    store.write_anomaly(
        _anomaly(created_at=1_700_000_500, explainer_backend="claude_cli"),
        evidence=["obs-a"],
    )
    p = calls[0]["params"]
    assert p["explainerBackend"] == "claude_cli"
    assert p["createdAt"].startswith("2023")  # unix → ISO
    # 값이 없으면(기본 _anomaly) 생략됨 — 회귀 방지
    store2, calls2 = _capture_store()
    store2.write_anomaly(_anomaly(), evidence=["obs-a"])
    assert "explainerBackend" not in calls2[0]["params"]
    assert "createdAt" not in calls2[0]["params"]


def test_write_anomaly_empty_evidence_rejected_foundry():
    """FoundryOntologyStore.write_anomaly도 빈 evidence는 EvidenceError(백엔드 무관)."""
    from ontology.store import EvidenceError

    store, calls = _capture_store()
    with pytest.raises(EvidenceError):
        store.write_anomaly(_anomaly(), evidence=[])
    assert not calls  # _apply 미도달


def test_write_anomaly_typed_evidence_only_skips_foundry():
    """Observation 근거가 없고 타입드 근거(OrbitPass)만이면 Foundry 스킵(로컬 권위본)."""
    store, calls = _capture_store()
    store.write_anomaly(_anomaly(), evidence=[("OrbitPass", "pass-1")])
    assert not calls  # create-anomaly 미호출(observations required 못 채움)


def test_write_anomaly_absorbs_apply_action_failed():
    """§12: create-anomaly가 ApplyActionFailed를 던져도 read-back 성공이면 흡수(예외 없음)."""
    store, _ = _make_foundry_store_with_mock()
    store._apply = _raise()
    # read-back: 객체 존재 + evidenced_by 엣지 형성 모사(§12 무해 에러 동작)
    store._get_object = lambda ot, pk: {"anomalyId": pk, "status": "candidate"}
    store._traverse = lambda ot, pk, link: ["obs-a"]

    store.write_anomaly(_anomaly(), evidence=["obs-a"])  # 크래시 없어야
    assert "anomaly-1" in store._written_other.get("Anomaly", set())


def test_write_anomaly_readback_object_missing_raises():
    """read-back에서 객체가 미존재(진짜 실패)면 예외 전파."""
    store, _ = _make_foundry_store_with_mock()
    store._apply = _raise(msg="ApplyActionFailed")
    store._get_object = lambda ot, pk: None  # 객체 미생성 = 진짜 실패
    store._traverse = lambda ot, pk, link: []

    with pytest.raises(Exception):
        store.write_anomaly(_anomaly(), evidence=["obs-a"])
    assert "anomaly-1" not in store._written_other.get("Anomaly", set())


def test_write_anomaly_readback_edge_missing_raises():
    """객체는 있으나 evidenced_by 엣지가 비면 실패 판정(half-Anomaly = 진짜 실패)."""
    store, _ = _make_foundry_store_with_mock()
    store._apply = _raise()
    store._get_object = lambda ot, pk: {"anomalyId": pk}
    store._traverse = lambda ot, pk, link: []  # 엣지 빈 채 → 실패

    with pytest.raises(Exception):
        store.write_anomaly(_anomaly(), evidence=["obs-a"])


def test_write_anomaly_dedup_no_double_call():
    """같은 anomalyId 두 번 write → 두 번째는 _apply 미호출(프로세스 내 dedup)."""
    store, _ = _make_foundry_store_with_mock()
    calls: list = []
    store._apply = lambda action, params: calls.append(action) or None
    an = _anomaly()
    store.write_anomaly(an, evidence=["obs-a"])
    store.write_anomaly(an, evidence=["obs-a"])
    assert calls == ["create-anomaly"]


def test_write_anomaly_already_exists_no_crash():
    """ObjectAlreadyExists(크로스런) → 크래시 없이 skip + dedup 마킹."""
    store, _ = _make_foundry_store_with_mock()
    store._apply = _raise("ObjectAlreadyExistsError", "already exists")
    store.write_anomaly(_anomaly(), evidence=["obs-a"])  # 크래시 없어야
    assert "anomaly-1" in store._written_other.get("Anomaly", set())


def test_set_anomaly_status_confirm_dismiss_actions():
    """set_anomaly_status: confirmed→confirm-anomaly, dismissed→dismiss-anomaly, 그 외 no-op."""
    store, calls = _capture_store()
    store.set_anomaly_status("anomaly-1", "confirmed")
    store.set_anomaly_status("anomaly-1", "dismissed")
    store.set_anomaly_status("anomaly-1", "candidate")  # 전이 액션 없음 → no-op
    assert [c["action"] for c in calls] == ["confirm-anomaly", "dismiss-anomaly"]
    assert calls[0]["params"] == {"anomaly": "anomaly-1"}
    assert calls[1]["params"] == {"anomaly": "anomaly-1"}


# ── P7 §11: HybridStore 라우팅 (신규 7타입 + 링크 + 정리·전이) ────────────────
def _valid_ws() -> WeatherState:
    return WeatherState(
        id="wx-RKSI-100",
        region_ref="KADIZ",
        ts=1_700_000_000,
        station="RKSI",
        source="aviationweather",
        source_url="https://aviationweather.gov/metar",
    )


def _valid_news() -> NewsEvent:
    return NewsEvent(
        id="news-1",
        source="gdelt",
        source_url="https://a.example/x",
        ts=1_700_000_000,
        title="t",
    )


def test_write_operator_satellite_orbitpass_route_to_foundry(hybrid):
    store, fake = hybrid
    store.write_operator(Operator(id="op-1", name="KAF", kind="airforce"))
    store.write_satellite(Satellite(norad_id="25544", name="ISS"))
    store.write_orbitpass(
        OrbitPass(
            id="p-1",
            satellite_ref="25544",
            region_ref="KADIZ",
            start_ts=1,
            end_ts=2,
            max_elevation=10.0,
        )
    )
    assert [o.id for o in fake.operators] == ["op-1"]
    assert [s.norad_id for s in fake.satellites] == ["25544"]
    assert [p.id for p in fake.orbitpasses] == ["p-1"]
    # 로컬 미접촉(Foundry 소재)
    assert store.local.query_operators() == []
    assert store.local.query_satellites() == []


def test_write_weatherstate_routes_to_foundry(hybrid):
    store, fake = hybrid
    store.write_weatherstate(_valid_ws())
    assert [w.id for w in fake.weather] == ["wx-RKSI-100"]
    assert store.local.query_weather_latest() == []


def test_write_weatherstate_provenance_rejected_before_foundry(hybrid):
    store, fake = hybrid
    bad = _valid_ws()
    bad.source = ""
    with pytest.raises(ProvenanceError):
        store.write_weatherstate(bad)
    assert not fake.weather


def test_write_newsevent_object_foundry_mentions_local(hybrid):
    """news 객체 → Foundry, mention 링크 → 로컬 권위본(query_mentions는 로컬)."""
    store, fake = hybrid
    store.write_newsevent(_valid_news(), mentions=[("Region", "KADIZ")])
    assert [n.id for n, _ in fake.news] == ["news-1"]
    # 권위 mention 링크는 로컬에서 읽힌다
    assert {"type": "Region", "id": "KADIZ"} in store.local.query_mentions("news-1")


def test_write_newsevent_provenance_rejected(hybrid):
    store, fake = hybrid
    bad = _valid_news()
    bad.ts = 0
    with pytest.raises(ProvenanceError):
        store.write_newsevent(bad)
    assert not fake.news


def test_write_assessment_dual_write(hybrid):
    """assessment: 로컬 권위본(문장 cites) + Foundry 스칼라 스파인 dual-write."""
    from ontology.model import AssessmentSentence, SituationAssessment

    store, fake = hybrid
    assessment = SituationAssessment(
        id="assess-KADIZ-1",
        region_ref="KADIZ",
        window_start=1,
        window_end=2,
        query="q",
        summary="headline",
        sentences=[
            AssessmentSentence(text="근거 문장", cites=["abc123-100"], confidence=0.9)
        ],
        confidence=0.8,
        produced_by="template",
        created_at=100,
    )
    store.write_assessment(assessment)
    # 로컬 권위본(문장 보존)
    assert [a.id for a in store.local.query_assessments()] == ["assess-KADIZ-1"]
    assert store.local.get_assessment("assess-KADIZ-1").sentences[0].cites == [
        "abc123-100"
    ]
    # Foundry 스칼라 스파인
    assert [a.id for a in fake.assessments] == ["assess-KADIZ-1"]


def test_composed_of_link_routes_to_foundry(hybrid):
    store, fake = hybrid
    store.link("Track", "track-1", "composed_of", "Observation", "abc123-100")
    assert (
        "Track",
        "track-1",
        "composed_of",
        "Observation",
        "abc123-100",
    ) in fake.links


def test_set_region_alert_level_routes_to_foundry(hybrid):
    store, fake = hybrid
    store.set_region_alert_level("KADIZ", "RED")
    assert ("KADIZ", "RED") in fake.alerts


def test_delete_future_orbitpasses_routes_to_foundry(hybrid):
    store, fake = hybrid
    store.delete_future_orbitpasses_for("25544", 1_700_000_000)
    assert ("25544", 1_700_000_000) in fake.deleted_passes


def test_read_p3_objects_route_to_foundry(hybrid):
    """query_satellites/orbitpasses/weather/news/operators/tracks → Foundry 소재."""
    store, fake = hybrid
    store.write_satellite(Satellite(norad_id="25544", name="ISS"))
    store.write_weatherstate(_valid_ws())
    store.write_newsevent(_valid_news())
    assert [s.norad_id for s in store.query_satellites()] == ["25544"]
    assert set(store.satellite_map().keys()) == {"25544"}
    assert [w.id for w in store.query_weather_latest()] == ["wx-RKSI-100"]
    assert [n.id for n in store.query_news()] == ["news-1"]


def test_counts_merges_p3_foundry_counts(hybrid):
    store, fake = hybrid
    store.write_region(KADIZ_REGION)  # 로컬
    store.write_satellite(Satellite(norad_id="25544", name="ISS"))  # Foundry
    counts = store.counts()
    assert counts["satellite"] == 1  # Foundry 카운트
    assert counts["region"] == 1  # 로컬 카운트


# ── 라이브 통합 (토큰 + foundry_sdk 있을 때만) ──────────────────────────────
def _foundry_live_available() -> bool:
    if importlib.util.find_spec("foundry_sdk") is None:
        return False
    # .env 로드 시도
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    return bool(os.environ.get("FOUNDRY_TOKEN") and os.environ.get("FOUNDRY_HOSTNAME"))


live = pytest.mark.skipif(
    not _foundry_live_available(),
    reason="FOUNDRY_TOKEN·FOUNDRY_HOSTNAME + foundry_sdk 필요 (메인 .venv엔 SDK 없음)",
)


@live
def test_live_query_aircraft_readonly():
    from ontology.store_foundry import FoundryOntologyStore

    fs = FoundryOntologyStore()
    aircraft = fs.query_aircraft()
    assert isinstance(aircraft, list)
    # PK가 UUID로 자동부여됨(갭 1) — icao24 필드가 채워져 있는지만 확인
    for a in aircraft[:3]:
        assert a.icao24


@live
def test_live_counts_readonly():
    from ontology.store_foundry import FoundryOntologyStore

    fs = FoundryOntologyStore()
    counts = fs.counts()
    assert "aircraft" in counts and "observation" in counts
    assert counts["aircraft"] >= 0
