"""tests/test_live_eval.py — 라이브 평가 하네스(eval/live_eval.py)의 네트워크-무관 단위 검증.

라이브 db 수집(폴러·외부 API)은 여기서 다루지 않는다. 대신 합성 시나리오로 채운 인메모리
스토어에 하네스의 순수 로직을 걸어 다음을 검증한다:
  - citation 정합 집계(전 문장 cites 보유율·해상율)가 실제 100%를 산출하는가.
  - 탐지 결정성(_detect_signatures 2회 동일)·assess 재현성(2회 동일)이 성립하는가.
  - snapshot_db가 스토어 상태를 온전히 복제하는가.

실행: .venv/bin/python -m pytest tests/test_live_eval.py -v
"""

from __future__ import annotations

from anomaly.actions import scan_and_create_all
from eval.live_eval import (
    _detect_signatures,
    run_assessment_determinism,
    run_citation_eval,
    run_detection_eval,
    snapshot_db,
)
from eval.run_eval import EVAL_NOW
from ontology.store_local import LocalOntologyStore
from scripts.scenarios import apply_scenario, scenario_by_id

QUERIES = ["지금 KADIZ 근방 상황 요약해줘", "지금 이상징후 있어?"]


def _populated_store(tmp_path) -> tuple[LocalOntologyStore, int]:
    """합성 시나리오(narrative_hidden = dropout↔위성↔뉴스)로 채운 스토어 + 앵커.

    라이브 db를 흉내내는 게 아니라 하네스 로직을 걸 '이상징후가 실재하는' 스토어를 만든다.
    """
    store = LocalOntologyStore(str(tmp_path / "live.db"))
    sc = scenario_by_id("narrative_hidden")
    mirror = apply_scenario(store, sc, EVAL_NOW)
    scan_and_create_all(store, now=EVAL_NOW, crosscheck=mirror)
    return store, EVAL_NOW


def test_citation_ratios_are_full(tmp_path):
    """산출된 전 문장이 cites를 갖고(100%), 전 cite id가 실 객체로 해상된다(100%)."""
    store, now = _populated_store(tmp_path)
    r = run_citation_eval(store, QUERIES, now)
    assert r["total_sentences"] > 0
    assert (
        r["sentence_cite_ratio"] == 1.0
    )  # cites 없는 문장은 write에서 거부 = 구조 강제
    assert r["cite_resolution_ratio"] == 1.0  # 인용 id 전부 실 온톨로지 객체
    # 근거 있는 응답이 최소 1건(상황요약은 이상징후·항적을 인용).
    assert r["n_queries_with_evidence"] >= 1


def test_detection_is_deterministic(tmp_path):
    """같은 스냅샷에 룰을 2회 걸면 후보 집합이 동일하고, 시나리오상 비어있지 않다."""
    store, now = _populated_store(tmp_path)
    run1 = _detect_signatures(store, now)
    run2 = _detect_signatures(store, now)
    assert run1 == run2
    assert (
        len(run1) > 0
    )  # narrative_hidden은 dropout 등 이상징후를 발화 = 비자명 결정성


def test_run_detection_eval_reports_distribution(tmp_path):
    """run_detection_eval이 결정성 True + 라이브 분포(유형별 건수)를 낸다."""
    store, now = _populated_store(tmp_path)
    det = run_detection_eval(store, now)
    assert det["determinism"]["identical"] is True
    assert det["live_distribution"]["total_anomalies"] > 0
    # 유형 카운트 합 = 총 이상징후 수(분포의 내부 정합).
    assert (
        sum(det["live_distribution"]["by_type"].values())
        == det["live_distribution"]["total_anomalies"]
    )


def test_assessment_determinism(tmp_path):
    """같은 스냅샷·질의·template로 assess 2회 → 문장·cites·신뢰도 완전 동일."""
    store, now = _populated_store(tmp_path)
    r = run_assessment_determinism(store, QUERIES[0], now)
    assert r["identical"] is True
    assert r["no_evidence"] is False
    assert r["n_sentences"] > 0


def test_snapshot_preserves_state(tmp_path):
    """snapshot_db가 스토어 카운트를 온전히 복제한다(스냅샷 평가의 격리 전제)."""
    store, _ = _populated_store(tmp_path)
    snap_path = snapshot_db(store.db_path)
    snap = LocalOntologyStore(snap_path)
    assert snap.counts() == store.counts()
    assert snap.counts()["anomaly"] > 0
