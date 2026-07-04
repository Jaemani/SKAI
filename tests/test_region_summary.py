"""tests/test_region_summary.py — AIP region-situation-summary 배선 단위 + 라이브(gated).

단위 테스트는 fake 주입으로 네트워크·OSDK 없이 매핑/폴백/게이트를 검증한다(메인 .venv 통과).
라이브 통합은 OSDK 설치 + Foundry 크리덴셜이 있을 때만 실행(그 외 skip).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

from copilot import assessment
from copilot.assessment import (
    KIND_ANOMALY,
    KIND_SUMMARY,
    ToolReads,
    _aip_region_summary,
    assess,
)
from copilot.parser import parse_query
from copilot.region_summary import AipRegionSummarizer, RegionSummaryResult
from copilot.tools import Fact
from ontology.model import (
    KADIZ_REGION,
    Aircraft,
    Anomaly,
    AssessmentSentence,
    Observation,
    WeatherState,
)
from ontology.store_local import LocalOntologyStore

NOW = 1783170000


# ── 공용 fake (네트워크 없이 매핑/폴백 검증) ─────────────────────────────
class _FakeRegionResp:
    def __init__(self, summary, overall_assessment="주의 — 한줄판정", confidence=0.9):
        self.summary = summary
        self.overall_assessment = overall_assessment
        self.confidence = confidence


class _FakeQueries:
    def __init__(self, resp, capture, raises=None):
        self._resp = resp
        self._capture = capture
        self._raises = raises

    def region_situation_summary(self, **kwargs):
        self._capture.clear()
        self._capture.update(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._resp


class _FakeOntology:
    def __init__(self, queries):
        self.objects = object()  # _anomaly_object_set 오버라이드로 우회 → 미접근
        self.queries = queries


class FakeAipClient:
    def __init__(self, resp=None, raises=None):
        self.capture: dict = {}
        self.ontology = _FakeOntology(_FakeQueries(resp, self.capture, raises))


class _StubSummarizer(AipRegionSummarizer):
    """OSDK 없이 매핑을 검증하려고 객체집합 빌드만 sentinel로 대체(나머지 경로는 실코드)."""

    def __init__(self, sentinel, **kw):
        super().__init__(**kw)
        self._sentinel = sentinel
        self.seen_ids = None

    def _anomaly_object_set(self, client, anomaly_ids):
        self.seen_ids = list(anomaly_ids)
        return self._sentinel


# ── AipRegionSummarizer 매핑/파싱 ─────────────────────────────────────
def test_region_summarizer_maps_and_parses():
    sentinel = object()  # AnomalyObjectSet 대역
    client = FakeAipClient(
        resp=_FakeRegionResp("AIP 상황요약", "경계 — 활성 1건", 0.95)
    )
    s = _StubSummarizer(sentinel, client=client)
    r = s.summarize(
        "한국 방공식별구역 (KADIZ)",
        ["anomaly-a", "anomaly-b"],
        window_label="최근 30분",
        weather_summary="RKSI MVFR·실링 1000ft",
    )
    assert isinstance(r, RegionSummaryResult)
    assert r.backend == "aip_logic"
    assert r.summary == "AIP 상황요약"
    assert r.overall_assessment == "경계 — 활성 1건"
    assert r.confidence == 0.95
    # 매핑 검증: anomalies2 = Foundry 객체집합 참조, region/window/weather 전달.
    assert client.capture["anomalies2"] is sentinel
    assert client.capture["region_name"] == "한국 방공식별구역 (KADIZ)"
    assert client.capture["window_label"] == "최근 30분"
    assert client.capture["weather_summary"] == "RKSI MVFR·실링 1000ft"
    assert s.seen_ids == ["anomaly-a", "anomaly-b"]


def test_region_summarizer_confidence_clamped():
    client = FakeAipClient(resp=_FakeRegionResp("x", confidence=1.5))
    r = _StubSummarizer(object(), client=client).summarize("R", ["anomaly-a"])
    assert r.confidence == 1.0


def test_region_summarizer_empty_summary_returns_none():
    client = FakeAipClient(resp=_FakeRegionResp("   ", confidence=0.9))
    r = _StubSummarizer(object(), client=client).summarize("R", ["anomaly-a"])
    assert r is None  # 빈 summary → 폴백(None)


def test_region_summarizer_zero_anomalies_skips_call():
    client = FakeAipClient(resp=_FakeRegionResp("절대 안 불림"))
    r = _StubSummarizer(object(), client=client).summarize("R", [])
    assert r is None
    assert client.capture == {}  # 함수 미호출(0건 스킵)


def test_region_summarizer_query_error_returns_none():
    client = FakeAipClient(raises=RuntimeError("함수 실패"))
    r = _StubSummarizer(object(), client=client).summarize("R", ["anomaly-a"])
    assert r is None  # 예외 → 폴백(None)


def test_region_summarizer_optional_params_default_empty_string():
    client = FakeAipClient(resp=_FakeRegionResp("요약"))
    _StubSummarizer(object(), client=client).summarize("R", ["anomaly-a"])
    # ⚠️ 배포 함수가 두 파라미터를 참조 → None이면 Empty 부재로 실패(ReferenceHasNoValue).
    # 그래서 미지정이어도 생략하지 않고 ""로 넘긴다(값 있음으로 통과).
    assert client.capture["window_label"] == ""
    assert client.capture["weather_summary"] == ""


# ── _aip_region_summary 통합(헤드라인 교체·cites 불변·폴백) ────────────
def _reads_with_anomaly(n_anomaly=1):
    anomalies = [
        Fact(
            kind="anomaly",
            cites=[f"anomaly-{i}", f"obs-{i}"],
            data={"id": f"anomaly-{i}"},
            confidence=0.93,
            ts=NOW,
        )
        for i in range(n_anomaly)
    ]
    return ToolReads(flights=[], anomalies=anomalies, passes=[], weather=[], news=[])


def _headline(cites):
    return AssessmentSentence(
        text="원본 template 헤드라인", cites=cites, confidence=0.85, kind=KIND_SUMMARY
    )


class _FixedSummarizer:
    def __init__(self, result):
        self._result = result
        self.calls = []

    def summarize(
        self, region_name, anomaly_ids, window_label=None, weather_summary=None
    ):
        self.calls.append(
            (region_name, list(anomaly_ids), window_label, weather_summary)
        )
        return self._result


def test_aip_region_summary_replaces_headline_keeps_cites():
    pq = parse_query("지금 KADIZ 상황", now=NOW)
    reads = _reads_with_anomaly(2)
    head = _headline(["anomaly-0", "obs-0", "anomaly-1", "obs-1"])
    body = AssessmentSentence(
        text="이상징후 문장", cites=["anomaly-0"], confidence=0.93, kind=KIND_ANOMALY
    )
    result = RegionSummaryResult("AIP가 생성한 상황요약", "경계 — 활성 2건", 0.94)
    summ = _FixedSummarizer(result)

    sents, backend, overall = _aip_region_summary(
        pq, reads, "KADIZ", [head, body], summarizer=summ
    )
    assert backend == "aip_logic"
    assert overall == "경계 — 활성 2건"
    assert sents[0].text == "AIP가 생성한 상황요약"  # 헤드라인 서술만 교체
    assert sents[0].cites == head.cites  # cites 불변(집계 Anomaly id 보존)
    assert sents[0].kind == KIND_SUMMARY
    assert sents[0].confidence == 0.94  # AIP 종합 신뢰도
    assert sents[1] is body  # 나머지 문장 불변
    # 집계 대상 = reads.anomalies의 id들.
    assert summ.calls[0][1] == ["anomaly-0", "anomaly-1"]


def test_aip_region_summary_fallback_keeps_template():
    pq = parse_query("지금 KADIZ 상황", now=NOW)
    reads = _reads_with_anomaly(1)
    head = _headline(["anomaly-0", "obs-0"])
    summ = _FixedSummarizer(None)  # AIP 실패/빈응답 → None
    sents, backend, overall = _aip_region_summary(
        pq, reads, "KADIZ", [head], summarizer=summ
    )
    assert backend == "template(aip 폴백)"
    assert overall is None
    assert sents[0].text == "원본 template 헤드라인"  # 헤드라인 유지


def test_aip_region_summary_zero_anomalies_skips():
    pq = parse_query("지금 KADIZ 상황", now=NOW)
    reads = ToolReads(flights=[], anomalies=[], passes=[], weather=[], news=[])
    head = _headline(["obs-0"])  # 항적만으로 생긴 헤드라인
    summ = _FixedSummarizer(RegionSummaryResult("안 불림", "x", 0.9))
    sents, backend, overall = _aip_region_summary(
        pq, reads, "KADIZ", [head], summarizer=summ
    )
    assert backend == "template"
    assert sents[0].text == "원본 template 헤드라인"
    assert summ.calls == []  # 0건 → 호출 스킵


def test_aip_region_summary_no_headline_template():
    pq = parse_query("지금 KADIZ 상황", now=NOW)
    reads = _reads_with_anomaly(1)
    body = AssessmentSentence(
        text="비요약 문장", cites=["anomaly-0"], confidence=0.9, kind=KIND_ANOMALY
    )
    summ = _FixedSummarizer(RegionSummaryResult("안 불림", "x", 0.9))
    sents, backend, overall = _aip_region_summary(
        pq, reads, "KADIZ", [body], summarizer=summ
    )
    assert backend == "template"
    assert summ.calls == []


# ── assess() 게이트(로컬 스토어 vs foundry) ────────────────────────────
def _seed(store, now):
    store.write_region(KADIZ_REGION)
    store.write_aircraft(Aircraft(icao24="synthx", callsign="TEST77"))
    obs = Observation(
        id=f"synthx-{now}",
        aircraft_ref="synthx",
        ts=now,
        lat=36.5,
        lon=127.0,
        squawk="7700",
        source="synthetic",
        source_url="synthetic://x",
    )
    store.write_observation(obs)
    a = Anomaly(
        id="anomaly-emergency_squawk-synthx-1",
        type="emergency_squawk",
        ts=now,
        confidence=0.93,
        status="candidate",
        lat=36.5,
        lon=127.0,
        explanation="테스트",
        explainer_backend="template",
        created_at=now,
        attrs={
            "squawk": "7700",
            "callsign": "TEST77",
            "meaning": "일반 비상",
            "is_synthetic": True,
        },
    )
    store.write_anomaly(a, evidence=[obs.id], involves=["synthx"])
    store.write_weatherstate(
        WeatherState(
            id=f"wx-RKSI-{now}",
            region_ref="KADIZ",
            ts=now,
            station="RKSI",
            flight_category="MVFR",
            ceiling_ft=1000,
            visibility_sm=3.7,
            lat=37.4,
            lon=126.4,
            source="metar",
            source_url="http://metar",
        )
    )


def test_assess_aip_gate_local_store_keeps_template(tmp_path, monkeypatch):
    """로컬 스토어(현재 백엔드=local)에서 aip 요청 → template 헤드라인 유지(미적용 라벨)."""
    monkeypatch.delenv("SKAI_STORE", raising=False)
    store = LocalOntologyStore(str(tmp_path / "r.db"))
    _seed(store, NOW)
    r = assess(store, "지금 KADIZ 상황", now=NOW, explainer="aip")
    assert r["produced_by"] == "template(aip 미적용)"
    assert r["overall_assessment"] is None
    assert not r["no_evidence"]


def test_assess_aip_situation_summary_foundry(tmp_path, monkeypatch):
    """foundry 백엔드 게이트 통과 시 헤드라인이 AIP 생성으로 교체되고 메타가 노출된다(fake 주입)."""
    monkeypatch.setattr(assessment, "current_backend", lambda: "foundry")

    fake_result = RegionSummaryResult(
        "AIP 지역 상황요약", "경계 — 비상 스쿽 1건 활성", 0.95
    )

    class _FakeSummarizerCls:
        def __init__(self, *a, **k):
            pass

        def summarize(
            self, region_name, anomaly_ids, window_label=None, weather_summary=None
        ):
            assert anomaly_ids == ["anomaly-emergency_squawk-synthx-1"]
            return fake_result

    monkeypatch.setattr(assessment, "AipRegionSummarizer", _FakeSummarizerCls)

    store = LocalOntologyStore(str(tmp_path / "r2.db"))
    _seed(store, NOW)
    r = assess(store, "지금 KADIZ 상황", now=NOW, explainer="aip")
    assert r["produced_by"] == "aip_logic"
    assert r["summary"] == "AIP 지역 상황요약"
    assert r["overall_assessment"] == "경계 — 비상 스쿽 1건 활성"
    # 헤드라인 cites 보존 → write_assessment가 거부하지 않음(assessment 생성됨).
    assert r["assessment_id"] is not None
    assert r["sentences"][0]["cites"]  # 헤드라인 cites 비어있지 않음(불변식)
    # attrs에도 한줄판정 영속.
    a = store.get_assessment(r["assessment_id"])
    assert a.attrs.get("overall_assessment") == "경계 — 비상 스쿽 1건 활성"


# ── 라이브 통합 (OSDK 설치 + 크리덴셜 있을 때만) ───────────────────────
_HAS_OSDK = importlib.util.find_spec("skai_osdk_sdk") is not None
_HAS_CREDS = bool(
    os.environ.get("FOUNDRY_TOKEN") and os.environ.get("FOUNDRY_HOSTNAME")
)


@pytest.mark.skipif(
    not (_HAS_OSDK and _HAS_CREDS),
    reason="OSDK 미설치 또는 Foundry 크리덴셜 없음 — 라이브 AIP 통합 스킵",
)
def test_region_summary_live():
    """실 Foundry Anomaly 객체집합으로 region-situation-summary 실호출 → 요약·판정 read-back."""
    from foundry_sdk import Config, UserTokenAuth
    from skai_osdk_sdk import FoundryClient

    client = FoundryClient(
        auth=UserTokenAuth(token=os.environ["FOUNDRY_TOKEN"]),
        hostname=os.environ["FOUNDRY_HOSTNAME"],
        config=Config(timeout=30),
    )
    ids = [
        getattr(a, "anomaly_id")
        for a in client.ontology.objects.Anomaly.page(page_size=10).data
    ]
    ids = [i for i in ids if i]
    if not ids:
        pytest.skip("Foundry에 Anomaly 없음")

    r = AipRegionSummarizer(client=client).summarize(
        "한국 방공식별구역 (KADIZ)", ids[:3], window_label="최근 30분"
    )
    assert r is not None
    assert r.summary and len(r.summary) > 10
    assert 0.0 <= r.confidence <= 1.0
