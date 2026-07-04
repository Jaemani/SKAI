"""P2 검증 테스트 — 비상 스쿽 이상탐지 끝단.

커버:
  1. evidence 강제 — 근거 없는 Anomaly는 어떤 경로로도 생성 불가(EvidenceError).
  2. 룰 탐지 — 7500/7600/7700만 잡고 정상 스쿽·None은 안 잡음.
  3. dedup — 같은 (기체, 유형, 시간창)은 1개만.
  4. confirm/dismiss — status 전이 영속.
  5. explainer — template 신뢰도 0.9대, 합성 표기, claude-cli 폴백.

실행: .venv/bin/python -m pytest tests/test_p2.py -v
"""

from __future__ import annotations

import pytest

from anomaly.actions import (
    confirm_anomaly,
    create_anomaly,
    dismiss_anomaly,
    scan_and_create,
)
from anomaly.explainer import ClaudeCliExplainer, TemplateExplainer
from anomaly.rules import (
    ANOMALY_TYPE_EMERGENCY_SQUAWK,
    AnomalyCandidate,
    detect_emergency_squawk,
)
from ontology.model import Anomaly, Observation
from ontology.store import EvidenceError, validate_evidence
from ontology.store_local import LocalOntologyStore


# ──────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────
def _store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "p2.db"))


def _obs(icao24="abc123", ts=1783091489, squawk="7700", source="synthetic", **kw):
    base = dict(
        id=f"{icao24}-{ts}",
        aircraft_ref=icao24,
        ts=ts,
        lat=36.5,
        lon=127.0,
        squawk=squawk,
        source=source,
        source_url=f"synthetic://skai/inject/{icao24}/{ts}",
    )
    base.update(kw)
    return Observation(**base)


def _anom(icao24="abc123", ts=1783091489, squawk="7700"):
    return Anomaly(
        id=f"anomaly-{ANOMALY_TYPE_EMERGENCY_SQUAWK}-{icao24}-{ts // 600}",
        type=ANOMALY_TYPE_EMERGENCY_SQUAWK,
        ts=ts,
        confidence=0.93,
        lat=36.5,
        lon=127.0,
        attrs={"squawk": squawk},
    )


def _write_obs(store, o):
    """관측 + Aircraft를 저장(근거로 쓸 수 있게)."""
    from ontology.model import Aircraft

    store.write_aircraft(Aircraft(icao24=o.aircraft_ref, callsign="TEST01"))
    store.write_observation(o)
    store.link("Aircraft", o.aircraft_ref, "observed_as", "Observation", o.id)


# ──────────────────────────────────────────────
# 1. evidence 강제 (핵심) — 근거 없는 Anomaly 거부
# ──────────────────────────────────────────────
def test_validate_evidence_rejects_empty():
    with pytest.raises(EvidenceError):
        validate_evidence(_anom(), [])


def test_validate_evidence_ok_with_nonempty():
    validate_evidence(_anom(), ["abc123-1783091489"])  # 예외 없어야 함


def test_write_anomaly_rejects_empty_evidence(tmp_path):
    """store 레벨(어떤 경로로도)에서 근거 없는 Anomaly write 거부 + 저장 0건."""
    store = _store(tmp_path)
    with pytest.raises(EvidenceError):
        store.write_anomaly(_anom(), evidence=[])
    assert store.counts()["anomaly"] == 0


def test_write_anomaly_persists_evidence_and_involves(tmp_path):
    store = _store(tmp_path)
    o = _obs()
    _write_obs(store, o)
    a = _anom()
    store.write_anomaly(a, evidence=[o.id], involves=[o.aircraft_ref])
    assert store.counts()["anomaly"] == 1
    assert store.query_evidence_ids(a.id) == [o.id]
    assert store.query_involves_ids(a.id) == [o.aircraft_ref]


# ──────────────────────────────────────────────
# 2. 룰 탐지 — 비상 스쿽만
# ──────────────────────────────────────────────
def test_rule_detects_all_emergency_codes():
    obs = [
        _obs(icao24="e75", squawk="7500"),
        _obs(icao24="e76", squawk="7600"),
        _obs(icao24="e77", squawk="7700"),
    ]
    cands = detect_emergency_squawk(obs)
    assert len(cands) == 3
    assert {c.aircraft_ref for c in cands} == {"e75", "e76", "e77"}
    assert all(c.type == ANOMALY_TYPE_EMERGENCY_SQUAWK for c in cands)


def test_rule_ignores_normal_and_missing_squawk():
    obs = [
        _obs(icao24="n1", squawk="1200"),  # 정상 VFR
        _obs(icao24="n2", squawk="3647"),  # 정상
        _obs(icao24="n3", squawk=None),  # 스쿽 없음
    ]
    assert detect_emergency_squawk(obs) == []


def test_rule_squawk_is_string_comparison():
    # 정수 7700이 아니라 문자열 "7700"만 매칭 (P0A gotcha 2)
    assert detect_emergency_squawk([_obs(squawk="7700")])
    assert detect_emergency_squawk([_obs(squawk="7699")]) == []


# ──────────────────────────────────────────────
# 3. scan_and_create + dedup
# ──────────────────────────────────────────────
def test_scan_creates_anomaly_with_evidence(tmp_path):
    store = _store(tmp_path)
    o = _obs(squawk="7700")
    _write_obs(store, o)
    created = scan_and_create(store, observations=[o])
    assert len(created) == 1
    a = created[0]
    assert a.type == ANOMALY_TYPE_EMERGENCY_SQUAWK
    assert a.status == "candidate"
    assert 0.9 <= a.confidence <= 1.0  # 하드 신호 → 0.9대
    # evidence 링크가 실제로 걸렸는지 (provenance 백본)
    assert store.query_evidence_ids(a.id) == [o.id]
    assert store.query_involves_ids(a.id) == [o.aircraft_ref]


def test_scan_dedup_same_window(tmp_path):
    store = _store(tmp_path)
    o = _obs(squawk="7700")
    _write_obs(store, o)
    first = scan_and_create(store, observations=[o])
    second = scan_and_create(store, observations=[o])  # 같은 관측 재스캔
    assert len(first) == 1
    assert len(second) == 0  # dedup — 신규 없음
    assert store.counts()["anomaly"] == 1


def test_create_anomaly_returns_existing_on_dedup(tmp_path):
    store = _store(tmp_path)
    o = _obs(squawk="7700")
    _write_obs(store, o)
    cand = detect_emergency_squawk([o])[0]
    a1 = create_anomaly(store, cand)
    a2 = create_anomaly(store, cand)  # 재호출 → 기존 반환
    assert a1.id == a2.id
    assert store.counts()["anomaly"] == 1


# ──────────────────────────────────────────────
# 4. confirm / dismiss — 상태 전이 영속
# ──────────────────────────────────────────────
def test_confirm_transition_persists(tmp_path):
    store = _store(tmp_path)
    o = _obs(squawk="7700")
    _write_obs(store, o)
    a = scan_and_create(store, observations=[o])[0]
    assert a.status == "candidate"
    confirm_anomaly(store, a.id)
    assert store.get_anomaly(a.id).status == "confirmed"  # 영속 확인


def test_dismiss_transition_persists(tmp_path):
    store = _store(tmp_path)
    o = _obs(squawk="7500")
    _write_obs(store, o)
    a = scan_and_create(store, observations=[o])[0]
    dismiss_anomaly(store, a.id)
    assert store.get_anomaly(a.id).status == "dismissed"


def test_set_status_invalid_value(tmp_path):
    store = _store(tmp_path)
    o = _obs()
    _write_obs(store, o)
    a = scan_and_create(store, observations=[o])[0]
    with pytest.raises(ValueError):
        store.set_anomaly_status(a.id, "bogus")


def test_set_status_missing_anomaly(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(KeyError):
        store.set_anomaly_status("anomaly-does-not-exist", "confirmed")


# ──────────────────────────────────────────────
# 5. explainer
# ──────────────────────────────────────────────
def _candidate(o):
    from anomaly.rules import EMERGENCY_SQUAWKS

    return AnomalyCandidate(
        type=ANOMALY_TYPE_EMERGENCY_SQUAWK,
        aircraft_ref=o.aircraft_ref,
        observation=o,
        signal={"squawk": o.squawk, "meaning": EMERGENCY_SQUAWKS[o.squawk]},
    )


def test_template_confidence_is_high():
    r = TemplateExplainer().explain(_candidate(_obs(squawk="7700")))
    assert 0.9 <= r.confidence <= 1.0
    assert r.backend == "template"


def test_template_marks_synthetic():
    # source="synthetic" → 설명문에 "[합성 시나리오]" 표기 (요구사항 #3)
    r = TemplateExplainer().explain(_candidate(_obs(squawk="7700", source="synthetic")))
    assert "합성 시나리오" in r.explanation


def test_template_live_no_synthetic_mark():
    r = TemplateExplainer().explain(_candidate(_obs(squawk="7700", source="opensky")))
    assert "합성 시나리오" not in r.explanation


def test_claude_cli_falls_back_on_bad_binary():
    # 존재하지 않는 바이너리 → 예외 → template 폴백 (데모 안전)
    ex = ClaudeCliExplainer(claude_bin="claude-does-not-exist-xyz", timeout=5)
    r = ex.explain(_candidate(_obs(squawk="7700")))
    assert "폴백" in r.backend  # template(claude_cli 폴백)
    assert 0.9 <= r.confidence <= 1.0
    assert r.explanation  # 폴백 설명문 존재
