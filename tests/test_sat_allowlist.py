"""ISR 위성 허용목록 게이트 + 상관 사유 저장 검증.

배경: 과거 celestrak stations/visual 그룹(ISS·밝은 위성)의 KADIZ 통과가 전부 저신뢰
Anomaly로 승격되고 correlation 시공간 버킷과 곱해져 상관이 폭주했다. 이 테스트는
  1. detect_satellite_proximity·correlation이 허용목록 밖 실 위성을 게이트하고,
  2. 합성(synthetic) 통과는 우회(데모 replay 발화 유지)하며,
  3. correlated_with 링크에 "왜 상관인가"(시간차·공간관계) 사유가 영속되고,
  4. 마이그레이션 전(구스키마) 링크 읽기가 깨지지 않음(하위호환)
을 고정한다.
"""

from __future__ import annotations

from anomaly.correlation import correlate
from anomaly.isr_satellites import (
    ISR_ALLOWLIST,
    is_isr_satellite,
    is_signal_promotable_pass,
)
from anomaly.rules import ANOMALY_TYPE_SATELLITE_PROXIMITY, detect_satellite_proximity
from ontology.model import Anomaly, KADIZ_REGION, NewsEvent, OrbitPass
from ontology.store_local import LocalOntologyStore

# 대표 허용목록 위성(Sentinel-1A). 실 celestrak resource 그룹에서 확인된 NORAD.
ISR_NORAD = "39634"
# 비-ISR 실 위성(ISS). 허용목록 밖 → 게이트로 차단돼야 함.
NON_ISR_NORAD = "25544"


def _store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "allowlist.db"))


def _pass(norad: str, source: str, start=900, end=1100, elev=84.0, region="KADIZ"):
    return OrbitPass(
        id=f"pass-{norad}-{start}",
        satellite_ref=norad,
        region_ref=region,
        start_ts=start,
        end_ts=end,
        max_elevation=elev,
        ground_track=[[35.9, 126.9], [36.0, 127.0], [36.1, 127.1]],
        source=source,
    )


# ── 허용목록 자체 ──────────────────────────────────────────────────────────────
def test_allowlist_membership():
    assert is_isr_satellite(ISR_NORAD) is True  # Sentinel-1A
    assert is_isr_satellite(NON_ISR_NORAD) is False  # ISS
    assert is_isr_satellite(None) is False
    assert 40 <= len(ISR_ALLOWLIST) <= 60  # 문서화된 규모(20~60기)


def test_promotable_predicate():
    # 합성은 항상 우회(비-ISR NORAD여도).
    assert is_signal_promotable_pass("synthetic", NON_ISR_NORAD) is True
    assert is_signal_promotable_pass("synthetic", "90001") is True
    # 실 위성은 허용목록만.
    assert is_signal_promotable_pass("celestrak", ISR_NORAD) is True
    assert is_signal_promotable_pass("celestrak", NON_ISR_NORAD) is False


def test_gate_off_env_restores_ungated(monkeypatch):
    monkeypatch.setenv("SKAI_SAT_ALLOWLIST", "off")
    # 게이트 off면 비-ISR 실 위성도 승격 가능(구 무게이트 동작 복원 탈출구).
    assert is_signal_promotable_pass("celestrak", NON_ISR_NORAD) is True
    assert is_isr_satellite(NON_ISR_NORAD) is True


# ── detect_satellite_proximity 게이트 ───────────────────────────────────────────
def test_detect_gates_non_isr_real_pass():
    p = _pass(NON_ISR_NORAD, "celestrak")  # ISS 실 통과
    assert (
        detect_satellite_proximity([p], {"KADIZ": KADIZ_REGION}, now=1000) == []
    )  # 승격 금지


def test_detect_allows_isr_real_pass():
    p = _pass(ISR_NORAD, "celestrak")  # Sentinel-1A 실 통과
    drafts = detect_satellite_proximity([p], {"KADIZ": KADIZ_REGION}, now=1000)
    assert len(drafts) == 1 and drafts[0].type == ANOMALY_TYPE_SATELLITE_PROXIMITY


def test_detect_allows_synthetic_bypass():
    p = _pass("90001", "synthetic")  # 합성 시나리오 위성(replay 데모)
    drafts = detect_satellite_proximity([p], {"KADIZ": KADIZ_REGION}, now=1000)
    assert len(drafts) == 1  # 허용목록 밖 NORAD여도 synthetic이라 우회


# ── correlation 게이트 + 사유 저장 ──────────────────────────────────────────────
def _seed_dropout(store, aid="anomaly-adsb_dropout-x-1", ts=1000, lat=36.0, lon=127.0):
    a = Anomaly(id=aid, type="adsb_dropout", ts=ts, confidence=0.5, lat=lat, lon=lon)
    store.write_anomaly(a, evidence=[("Observation", f"obs-{aid}")])
    return a


def test_correlation_gates_non_isr_pass(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    T = 1000
    a = _seed_dropout(store, ts=T)
    store.write_orbitpass(_pass(ISR_NORAD, "celestrak", start=T + 100, end=T + 200))
    store.write_orbitpass(_pass(NON_ISR_NORAD, "celestrak", start=T + 100, end=T + 200))
    correlate(store, [a], now=T)
    pass_ids = [
        c["dst_id"]
        for c in store.query_correlations(a.id)
        if c["dst_type"] == "OrbitPass"
    ]
    assert any(ISR_NORAD in pid for pid in pass_ids)  # 허용목록 위성은 상관
    assert not any(NON_ISR_NORAD in pid for pid in pass_ids)  # 비-ISR은 게이트로 배제


def test_correlation_synthetic_bypass(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    T = 1000
    a = _seed_dropout(store, ts=T)
    # 허용목록 밖 NORAD지만 synthetic → 상관 유지(replay 은닉정황 서사).
    store.write_orbitpass(_pass("90007", "synthetic", start=T + 100, end=T + 200))
    correlate(store, [a], now=T)
    pass_ids = [
        c["dst_id"]
        for c in store.query_correlations(a.id)
        if c["dst_type"] == "OrbitPass"
    ]
    assert any("90007" in pid for pid in pass_ids)


def test_correlation_reason_persisted(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    T = 1000
    a = _seed_dropout(store, ts=T)
    store.write_orbitpass(_pass(ISR_NORAD, "celestrak", start=T + 100, end=T + 200))
    store.write_newsevent(
        NewsEvent(
            id="news-1",
            source="synthetic",
            source_url="synthetic://n",
            ts=T - 500,
            title="KADIZ",
            confidence=0.3,
            entities=["KADIZ"],
        ),
        mentions=[("Region", "KADIZ")],
    )
    correlate(store, [a], now=T)
    corr = store.query_correlations(a.id)
    by_type = {c["dst_type"]: c for c in corr}
    # OrbitPass 사유: 시간차·공간관계(구역·앙각·NORAD).
    pr = by_type["OrbitPass"]["reason"]
    assert pr["kind"] == "anomaly_orbitpass"
    assert pr["dt_s"] == -100  # 이상징후(T)가 통과 시작(T+100)보다 100초 이름
    assert pr["region"] == "KADIZ" and pr["norad_id"] == ISR_NORAD
    # NewsEvent 사유: 시간차 + 공유 구역.
    nr = by_type["NewsEvent"]["reason"]
    assert nr["kind"] == "anomaly_news"
    assert nr["dt_s"] == 500 and nr["shared_regions"] == ["KADIZ"]


def test_correlation_reason_anomaly_pair(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    T = 1000
    a = _seed_dropout(store, aid="anomaly-adsb_dropout-a-1", ts=T, lat=36.0, lon=127.0)
    _seed_dropout(store, aid="anomaly-loitering-b-1", ts=T + 60, lat=36.05, lon=127.05)
    correlate(store, [a], now=T)
    anom = [c for c in store.query_correlations(a.id) if c["dst_type"] == "Anomaly"]
    assert anom, "이상징후↔이상징후 상관 없음"
    r = anom[0]["reason"]
    assert r["kind"] == "anomaly_anomaly"
    assert r["gap_s"] == 60 and r["distance_km"] >= 0


def test_reason_upsert_refreshes_and_is_idempotent(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    T = 1000
    a = _seed_dropout(store, ts=T)
    store.write_orbitpass(_pass(ISR_NORAD, "celestrak", start=T + 100, end=T + 200))
    correlate(store, [a], now=T)
    correlate(store, [a], now=T)  # 재실행: 링크 upsert라 중복 안 쌓임
    passes = [c for c in store.query_correlations(a.id) if c["dst_type"] == "OrbitPass"]
    assert len(passes) == 1  # 멱등
    assert passes[0]["reason"]["dt_s"] == -100  # 사유 유지


# ── 하위호환: 마이그레이션 전 구스키마 링크 읽기 ────────────────────────────────
def test_backward_compat_old_link_without_attrs(tmp_path):
    import sqlite3

    db = str(tmp_path / "old.db")
    # 구 스키마(attrs_json 없음) + 기존 correlated_with 링크 1건을 직접 심는다.
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE link (src_type TEXT, src_id TEXT, link_type TEXT, "
        "dst_type TEXT, dst_id TEXT, "
        "UNIQUE(src_type,src_id,link_type,dst_type,dst_id));"
        "INSERT INTO link VALUES "
        "('Anomaly','a1','correlated_with','OrbitPass','pass-old-1');"
    )
    conn.commit()
    conn.close()
    # 신 store로 열면 마이그레이션이 attrs_json을 추가하고 구링크는 그대로 읽혀야 한다.
    store = LocalOntologyStore(db)
    corr = store.query_correlations("a1")
    assert len(corr) == 1
    assert corr[0]["dst_id"] == "pass-old-1"
    assert corr[0]["reason"] is None  # 구링크는 사유 없음(None)
