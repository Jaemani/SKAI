"""P7 — HybridStore 라우팅·provenance 단위 테스트 + Foundry 라이브 통합(옵션).

단위(네트워크·크리덴셜 불요): fake Foundry 어댑터를 주입해 HybridStore의
  - 라우팅(Aircraft·Observation→Foundry, 나머지→Local)
  - observed_as no-op(write_observation의 aircraftIcao24 FK 자동 형성 — §7-2)
  - provenance 강제(백엔드 무관)
  - counts 병합
을 검증한다. foundry_sdk 없이(메인 .venv=3.14) 통과해야 한다.

파라미터 매핑 단위: FoundryOntologyStore를 mock _pf로 생성해
  - write_aircraft: newParameter=icao24(PK), newParameter1 없음
  - write_observation: newParameter=obs.id(PK), aircraftIcao24=aircraft_ref(FK), optional None 생략
를 직접 검증한다.

라이브(skip 마커): FOUNDRY_TOKEN·FOUNDRY_HOSTNAME + foundry_sdk 있을 때만.
  실 Foundry read-only(query_aircraft·counts)만 — 테스트 스위트가 Foundry에 쓰지 않게.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from ontology.model import KADIZ_REGION, Aircraft, Anomaly, Observation, Track
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

    def write_aircraft(self, a: Aircraft) -> None:
        self.calls.append(("write_aircraft", a.icao24))
        self.aircraft.append(a)

    def write_observation(self, o: Observation) -> None:
        self.calls.append(("write_observation", o.id))
        self.obs.append(o)

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

    def counts(self):
        return {"aircraft": len(self.aircraft), "observation": len(self.obs)}


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


def test_write_track_and_anomaly_route_to_local(hybrid):
    store, fake = hybrid
    store.write_track(
        Track(
            id="track-abc",
            aircraft_ref="abc123",
            start_ts=1,
            end_ts=2,
            path=[[36, 124]],
        )
    )
    anomaly = Anomaly(
        id="anomaly-1", type="emergency_squawk", ts=1_700_000_000, confidence=0.9
    )
    store.write_anomaly(anomaly, evidence=["abc123-1700000000"])
    assert [t.id for t in store.local.query_tracks()] == ["track-abc"]
    assert [a.id for a in store.local.query_anomalies()] == ["anomaly-1"]
    assert not fake.calls


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
    """write_aircraft: newParameter=aircraft.icao24(실 PK 바인딩), newParameter1 없음."""
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
    assert params["newParameter"] == "abc123"  # 실 PK
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
    """write_observation: newParameter=obs.id(PK), aircraftIcao24=aircraft_ref(FK)."""
    store, _ = _make_foundry_store_with_mock()
    calls: list[dict] = []
    store._apply = lambda action, params: (
        calls.append({"action": action, "params": dict(params)}) or None
    )

    obs = _valid_obs(icao24="abc123", ts=1_700_000_000)
    store.write_observation(obs)

    assert len(calls) == 1
    params = calls[0]["params"]
    assert params["newParameter"] == obs.id  # obsId PK 바인딩(§7-6 수정 2)
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
