"""tests/test_aip_explainer.py — AipLogicExplainer 매핑·폴백 단위 + 라이브 통합(gated).

단위 테스트는 FakeAipClient 주입으로 네트워크 없이 매핑/폴백을 검증한다(메인 .venv에서
OSDK 없이도 통과). 라이브 통합은 OSDK 설치 + Foundry 크리덴셜이 있을 때만 실행(그 외 skip).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

from anomaly.explainer import AipLogicExplainer, ExplainerResult
from anomaly.rules import (
    EMERGENCY_SQUAWKS,
    ANOMALY_TYPE_EMERGENCY_SQUAWK,
    AnomalyCandidate,
)
from ontology.model import Observation


# ── 헬퍼 ──────────────────────────────────────────────
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


def _candidate(o, **signal):
    sig = {"squawk": o.squawk, "meaning": EMERGENCY_SQUAWKS[o.squawk]}
    sig.update(signal)
    return AnomalyCandidate(
        type=ANOMALY_TYPE_EMERGENCY_SQUAWK,
        aircraft_ref=o.aircraft_ref,
        observation=o,
        signal=sig,
    )


# ── Fake OSDK client (네트워크 없이 매핑/폴백 검증) ──────────
class _FakeResp:
    def __init__(self, explanation, confidence, recommendation=""):
        self.explanation = explanation
        self.confidence = confidence
        self.recommendation = recommendation


class _FakeObsSet:
    def __init__(self, objs):
        self._objs = objs  # {obsId: sentinel obj}

    def get(self, pk):
        return self._objs.get(pk)


class _FakeQueries:
    def __init__(self, resp, capture, raises=None):
        self._resp = resp
        self._capture = capture
        self._raises = raises

    def explain_anomaly(self, **kwargs):
        self._capture.clear()
        self._capture.update(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._resp


class _FakeOntology:
    def __init__(self, objects, queries):
        self.objects = objects
        self.queries = queries


class FakeAipClient:
    """AipLogicExplainer가 부르는 표면만 흉내: ontology.objects.Observation.get / ontology.queries.explain_anomaly."""

    def __init__(self, obs_objs=None, resp=None, raises=None):
        self.capture: dict = {}
        objects = type("_Objs", (), {})()
        objects.Observation = _FakeObsSet(obs_objs or {})
        self.ontology = _FakeOntology(objects, _FakeQueries(resp, self.capture, raises))


# ── 단위: 객체 근거 매핑 ───────────────────────────────
def test_aip_object_evidence_mapping():
    o = _obs(squawk="7500", icao24="hjk987")
    sentinel = object()  # Foundry Observation 객체 대역
    client = FakeAipClient(
        obs_objs={o.id: sentinel},
        resp=_FakeResp("AIP가 생성한 설명", 0.91, "교차검증 요망"),
    )
    ex = AipLogicExplainer(client=client)
    r = ex.explain(_candidate(o, callsign="ARMY01", region="KADIZ"))

    assert isinstance(r, ExplainerResult)
    assert r.backend == "aip_logic"  # 객체 근거 → 온톨로지 참조 경로
    assert r.confidence == 0.91  # AIP 산출 신뢰도 사용(이 백엔드 한정)
    assert "AIP가 생성한 설명" in r.explanation
    assert "권고: 교차검증 요망" in r.explanation  # recommendation 보존
    # 매핑 검증: evidence=객체 참조, anomaly_type/callsign/region 전달.
    assert client.capture["evidence"] is sentinel
    assert client.capture["anomaly_type"] == ANOMALY_TYPE_EMERGENCY_SQUAWK
    assert client.capture["callsign"] == "ARMY01"
    assert client.capture["region_name"] == "KADIZ"


def test_aip_string_evidence_fallback_when_obs_missing():
    o = _obs(squawk="7700", icao24="nomatch1")
    # obs가 Foundry에 없음(get→None) → String 요약 근거 폴백.
    client = FakeAipClient(obs_objs={}, resp=_FakeResp("설명문", 0.93))
    ex = AipLogicExplainer(client=client)
    r = ex.explain(_candidate(o))

    assert r.backend == "aip_logic(string-evidence)"  # 한계 명시
    ev = client.capture["evidence"]
    assert isinstance(ev, str)
    assert o.id in ev and "7700" in ev  # 요약 String에 obsId·squawk 포함


def test_aip_confidence_is_clamped():
    o = _obs(squawk="7700")
    client = FakeAipClient(obs_objs={o.id: object()}, resp=_FakeResp("x", 1.5))
    r = AipLogicExplainer(client=client).explain(_candidate(o))
    assert r.confidence == 1.0  # [0,1] 클램프


def test_aip_falls_back_to_template_on_query_error():
    o = _obs(squawk="7700")
    client = FakeAipClient(obs_objs={o.id: object()}, raises=RuntimeError("함수 실패"))
    r = AipLogicExplainer(client=client).explain(_candidate(o))
    assert r.backend == "template(aip_logic 폴백)"
    assert 0.9 <= r.confidence <= 1.0  # 폴백은 룰 신뢰도
    assert r.explanation  # 폴백 설명문 존재


def test_aip_empty_explanation_falls_back():
    o = _obs(squawk="7700")
    client = FakeAipClient(obs_objs={o.id: object()}, resp=_FakeResp("   ", 0.9))
    r = AipLogicExplainer(client=client).explain(_candidate(o))
    assert r.backend == "template(aip_logic 폴백)"  # 빈 explanation → 폴백


def test_aip_falls_back_when_no_credentials(monkeypatch):
    monkeypatch.delenv("FOUNDRY_TOKEN", raising=False)
    monkeypatch.delenv("FOUNDRY_HOSTNAME", raising=False)
    o = _obs(squawk="7700")
    # client 미주입 → lazy 생성 시 크리덴셜 없음 → template 폴백(데모 안전).
    r = AipLogicExplainer().explain(_candidate(o))
    assert r.backend == "template(aip_logic 폴백)"
    assert r.explanation


# ── 라이브 통합 (OSDK 설치 + 크리덴셜 있을 때만) ───────────
_HAS_OSDK = importlib.util.find_spec("skai_osdk_sdk") is not None
_HAS_CREDS = bool(
    os.environ.get("FOUNDRY_TOKEN") and os.environ.get("FOUNDRY_HOSTNAME")
)


@pytest.mark.skipif(
    not (_HAS_OSDK and _HAS_CREDS),
    reason="OSDK 미설치 또는 Foundry 크리덴셜 없음 — 라이브 AIP 통합 스킵",
)
def test_aip_live_explain_anomaly():
    """실 Foundry Observation을 근거로 explain-anomaly 실호출 → 설명·신뢰도 read-back."""
    from foundry_sdk import Config, UserTokenAuth
    from skai_osdk_sdk import FoundryClient

    client = FoundryClient(
        auth=UserTokenAuth(token=os.environ["FOUNDRY_TOKEN"]),
        hostname=os.environ["FOUNDRY_HOSTNAME"],
        config=Config(timeout=30),
    )
    # 실 Observation 하나 선택(가능하면 비상 스쿽).
    picked = None
    for ob in client.ontology.objects.Observation.page(page_size=50).data:
        if getattr(ob, "squawk", None) in ("7500", "7600", "7700"):
            picked = ob
            break
        picked = picked or ob
    if picked is None:
        pytest.skip("Foundry에 Observation 없음")

    obs_id = getattr(picked, "obs_id")
    sq = getattr(picked, "squawk", None) or "7700"
    icao = getattr(picked, "aircraft_icao24", None) or "UNKNOWN"
    o = _obs(
        icao24=icao,
        ts=1783091489,
        squawk=sq if sq in EMERGENCY_SQUAWKS else "7700",
        source=getattr(picked, "source", "opensky") or "opensky",
    )
    o.id = obs_id  # 실 Foundry obsId로 교체(객체 fetch 근거)

    r = AipLogicExplainer(client=client).explain(_candidate(o, callsign=icao))
    assert r.backend.startswith("aip_logic")  # 객체/string 어느 쪽이든 AIP 경로
    assert r.explanation and len(r.explanation) > 10
    assert 0.0 <= r.confidence <= 1.0
