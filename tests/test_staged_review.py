"""B2 staged human review (방법 B) 단위 테스트.

커버:
  1. 게이트 off(기본) — SKAI_REVIEW 미설정이면 explainer 산출이 본 explanation에 즉시 적용(현행 불변).
  2. 게이트 on(staged) — 구성 explainer(AIP 모사) 산출은 본 explanation에 **안 쓰이고**
     proposedExplanation으로 제안(reviewStatus=pending). ★ pending 중 본 explanation 불변.
  3. 승인 — approve_explanation이 explanation←proposedExplanation 복사 + reviewStatus=approved.
  4. 기각 — reject_explanation이 reviewStatus=rejected, 본 explanation·proposed 불변.
  5. 스토어 레벨(LocalOntologyStore) propose/approve/reject 복사 규율 + 없는 id KeyError.
  6. Foundry 라우팅 — FoundryOntologyStore가 정확한 액션명·파라미터(실측 camelCase)로 _apply.

실행: .venv/bin/python -m pytest tests/test_staged_review.py -v
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from anomaly.actions import (
    approve_explanation,
    create_anomaly,
    reject_explanation,
    scan_and_create,
)
from anomaly.explainer import ExplainerResult
from anomaly.rules import ANOMALY_TYPE_EMERGENCY_SQUAWK, detect_emergency_squawk
from ontology.model import Aircraft, Observation
from ontology.store_local import LocalOntologyStore


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────
def _store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "staged.db"))


def _obs(icao24="abc123", ts=1783091489, squawk="7700"):
    return Observation(
        id=f"{icao24}-{ts}",
        aircraft_ref=icao24,
        ts=ts,
        lat=36.5,
        lon=127.0,
        squawk=squawk,
        source="synthetic",
        source_url=f"synthetic://skai/inject/{icao24}/{ts}",
    )


def _write_obs(store, o):
    store.write_aircraft(Aircraft(icao24=o.aircraft_ref, callsign="TEST01"))
    store.write_observation(o)
    store.link("Aircraft", o.aircraft_ref, "observed_as", "Observation", o.id)


# AIP 모사 explainer — template 베이스라인과 **다른** 고유 설명문을 낸다(제안 대상).
_AIP_TEXT = "AIP가 생성한 서술: 이 항적은 비상 상황으로 판단됩니다(모사)."


class FakeAipExplainer:
    backend_name = "aip_logic"

    def explain(self, candidate) -> ExplainerResult:
        return ExplainerResult(
            explanation=_AIP_TEXT, confidence=0.88, backend=self.backend_name
        )


def _candidate(o):
    return detect_emergency_squawk([o])[0]


# ── 1. 게이트 off(기본) — 현행 즉시 적용 불변 ──────────────────────────────────
def test_gate_off_applies_immediately(tmp_path, monkeypatch):
    monkeypatch.delenv("SKAI_REVIEW", raising=False)
    store = _store(tmp_path)
    o = _obs()
    _write_obs(store, o)
    a = create_anomaly(store, _candidate(o), explainer=FakeAipExplainer())
    # 본 explanation = AIP 산출(즉시 적용). 검토 상태 필드 없음.
    assert a.explanation == _AIP_TEXT
    assert a.explainer_backend == "aip_logic"
    assert a.attrs.get("review_status") is None
    assert a.attrs.get("proposed_explanation") is None
    # 영속 확인
    assert store.get_anomaly(a.id).explanation == _AIP_TEXT


def test_gate_off_scan_and_create_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("SKAI_REVIEW", raising=False)
    store = _store(tmp_path)
    o = _obs()
    _write_obs(store, o)
    created = scan_and_create(store, observations=[o], explainer=FakeAipExplainer())
    assert len(created) == 1
    assert created[0].explanation == _AIP_TEXT  # 즉시 적용
    assert created[0].attrs.get("review_status") is None


# ── 2. 게이트 on(staged) — 제안 라우팅 + ★pending 중 본 explanation 불변 ────────
def test_staged_routes_to_proposed_not_main(tmp_path, monkeypatch):
    monkeypatch.setenv("SKAI_REVIEW", "staged")
    store = _store(tmp_path)
    o = _obs()
    _write_obs(store, o)
    a = create_anomaly(store, _candidate(o), explainer=FakeAipExplainer())

    # ★ 핵심: 본 explanation은 AIP 산출이 **아니다**(template 베이스라인). 제안만 pending.
    assert a.explanation != _AIP_TEXT
    assert a.explainer_backend == "template"  # 본 속성 = 결정적 베이스라인
    assert a.attrs["review_status"] == "pending"
    assert a.attrs["proposed_explanation"] == _AIP_TEXT

    # 스토어 영속본도 동일 — pending 중 본 explanation은 AIP 산출을 담지 않는다.
    persisted = store.get_anomaly(a.id)
    assert persisted.explanation != _AIP_TEXT
    assert persisted.attrs["review_status"] == "pending"
    assert persisted.attrs["proposed_explanation"] == _AIP_TEXT


# ── 3. 승인 — explanation←proposed 복사 + approved ────────────────────────────
def test_staged_approve_copies_proposed_to_main(tmp_path, monkeypatch):
    monkeypatch.setenv("SKAI_REVIEW", "staged")
    store = _store(tmp_path)
    o = _obs()
    _write_obs(store, o)
    a = create_anomaly(store, _candidate(o), explainer=FakeAipExplainer())
    assert store.get_anomaly(a.id).explanation != _AIP_TEXT  # 승인 전

    approve_explanation(store, a.id)

    after = store.get_anomaly(a.id)
    assert after.explanation == _AIP_TEXT  # 승인 후 본 속성에 반영
    assert after.attrs["review_status"] == "approved"


# ── 4. 기각 — rejected, 본 explanation 불변 ───────────────────────────────────
def test_staged_reject_keeps_main(tmp_path, monkeypatch):
    monkeypatch.setenv("SKAI_REVIEW", "staged")
    store = _store(tmp_path)
    o = _obs()
    _write_obs(store, o)
    a = create_anomaly(store, _candidate(o), explainer=FakeAipExplainer())
    baseline = store.get_anomaly(a.id).explanation

    reject_explanation(store, a.id)

    after = store.get_anomaly(a.id)
    assert after.explanation == baseline  # 본 explanation 불변(AIP 미반영)
    assert after.explanation != _AIP_TEXT
    assert after.attrs["review_status"] == "rejected"


# ── 5. 스토어 레벨(LocalOntologyStore) 직접 ────────────────────────────────────
def test_local_store_propose_keeps_explanation(tmp_path):
    store = _store(tmp_path)
    o = _obs()
    _write_obs(store, o)
    a = scan_and_create(store, observations=[o])[
        0
    ]  # template 설명으로 생성(게이트 무관)
    original = a.explanation

    store.propose_explanation(a.id, "제안 설명 A")

    got = store.get_anomaly(a.id)
    assert got.explanation == original  # 제안은 본 속성 불변
    assert got.attrs["proposed_explanation"] == "제안 설명 A"
    assert got.attrs["review_status"] == "pending"

    store.approve_explanation(a.id)
    assert store.get_anomaly(a.id).explanation == "제안 설명 A"
    assert store.get_anomaly(a.id).attrs["review_status"] == "approved"


def test_local_store_missing_id_raises(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        store.propose_explanation("anomaly-nope", "x")
    with pytest.raises(KeyError):
        store.approve_explanation("anomaly-nope")
    with pytest.raises(KeyError):
        store.reject_explanation("anomaly-nope")


# ── 6. Foundry 라우팅 — 액션명·파라미터 실측 매핑 ──────────────────────────────
def _foundry_store_with_capture():
    """foundry_sdk mock으로 FoundryOntologyStore 생성 후 _apply를 캡처로 교체."""
    from ontology.store_foundry import FoundryOntologyStore

    mock_sdk = MagicMock()
    mock_sdk.FoundryClient.return_value = MagicMock()
    mock_sdk.UserTokenAuth.return_value = MagicMock()
    mock_osdk = MagicMock()
    mock_osdk.FoundryClient.return_value = MagicMock()
    with patch.dict(sys.modules, {"foundry_sdk": mock_sdk, "skai_osdk_sdk": mock_osdk}):
        store = FoundryOntologyStore(token="t", hostname="h")
    calls: list[dict] = []
    store._apply = lambda action, parameters: calls.append(
        {"action": action, "parameters": dict(parameters)}
    )
    return store, calls


def test_foundry_propose_action_params():
    store, calls = _foundry_store_with_capture()
    store.propose_explanation("anomaly-x", "제안문")
    assert len(calls) == 1
    assert calls[0]["action"] == "propose-explanation"
    p = calls[0]["parameters"]
    assert p["anomaly"] == "anomaly-x"
    assert p["proposedExplanation"] == "제안문"
    assert p["reviewStatus"] == "pending"


def test_foundry_approve_action_params():
    store, calls = _foundry_store_with_capture()
    store.approve_explanation("anomaly-x")
    assert calls == [
        {"action": "approve-explanation", "parameters": {"anomaly": "anomaly-x"}}
    ]


def test_foundry_reject_action_params():
    store, calls = _foundry_store_with_capture()
    store.reject_explanation("anomaly-x")
    assert calls == [
        {"action": "reject-explanation", "parameters": {"anomaly": "anomaly-x"}}
    ]
