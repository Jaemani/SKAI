"""P6 검증 테스트 — replay 데모 패키징(now 앵커링 · 결정성 · 오프라인 가드).

커버:
  1. now 앵커링 — server._now_anchor 환경변수 파싱(정상/미설정/오류) + assess가 앵커 now로
     벽시계 무관하게 같은 창을 해석.
  2. created_at 앵커링 — scan_and_create_all(now=X)이 탐지 이상징후의 created_at까지 X로 고정
     (replay 완전 결정성의 유일 휘발 필드 제거).
  3. replay 결정성 — 같은 앵커로 데모 DB를 두 번 빌드 → 이상징후(created_at 포함)·assess 응답
     (문장·cites·신뢰도) 동일.
  4. 오프라인 가드 — is_offline 파싱 · _is_loopback 판정 · 외부 connect 차단/루프백 허용.

실행: .venv/bin/python -m pytest tests/test_p6.py -v
"""

from __future__ import annotations

import socket

from anomaly.actions import scan_and_create_all
from copilot.assessment import assess
from ontology.store_local import LocalOntologyStore
from scripts.inject_synthetic import inject_scenario
from server import app as server_app
from server import offline_guard

ANCHOR = 1783000000
DEMO_QUERY = "지금 KADIZ 근방 이상한 거 있어?"


def _store(tmp_path, name="p6.db") -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / name))


# ── 1. now 앵커링 (server._now_anchor) ────────────────────────────────────────
def test_now_anchor_env_parse(monkeypatch):
    monkeypatch.delenv("SKAI_NOW_ANCHOR", raising=False)
    assert server_app._now_anchor() is None  # 미설정 → 벽시계(None)

    monkeypatch.setenv("SKAI_NOW_ANCHOR", str(ANCHOR))
    assert server_app._now_anchor() == ANCHOR  # 정상 파싱

    monkeypatch.setenv("SKAI_NOW_ANCHOR", "notanumber")
    assert server_app._now_anchor() is None  # 오류 → None(벽시계 폴백)

    monkeypatch.setenv("SKAI_NOW_ANCHOR", "  ")
    assert server_app._now_anchor() is None  # 공백 → None


def test_assess_anchored_is_walltime_independent(tmp_path):
    """앵커 now로 assess하면 벽시계와 무관하게 같은 창을 해석 → 같은 근거를 낸다."""
    store = _store(tmp_path)
    inject_scenario(store, "all", now=ANCHOR)
    # 앵커 now로 두 번(개념상 '다른 시각에 실행') → 완전 동일.
    r1 = assess(store, DEMO_QUERY, now=ANCHOR)
    r2 = assess(store, DEMO_QUERY, now=ANCHOR)
    assert r1["no_evidence"] is False
    assert r1["counts"]["anomalies"] == 10  # 전 유형 6종(급기동 포함) = 10건
    assert r1["window"]["start"] == ANCHOR - 30 * 60  # '지금' = 최근 30분
    assert r1["window"]["end"] == ANCHOR
    assert _assess_fingerprint(r1) == _assess_fingerprint(r2)


# ── 2. created_at 앵커링 (탐지 시각 고정) ─────────────────────────────────────
def test_scan_all_created_at_anchored(tmp_path):
    store = _store(tmp_path)
    inject_scenario(store, "all", now=ANCHOR)
    anomalies = store.query_anomalies()
    assert len(anomalies) == 10  # 전 유형 6종(급기동 포함, all 주입 기준)
    # 모든 이상징후의 created_at이 앵커에 고정(벽시계 아님) → replay 완전 결정성.
    assert all(a.created_at == ANCHOR for a in anomalies)


def test_scan_all_now_defaults_to_walltime(tmp_path):
    """now 미지정(라이브)이면 created_at은 벽시계(앵커와 무관) — 기존 동작 보존."""
    import time

    store = _store(tmp_path)
    # 시나리오는 now 앵커로 주입하되, 탐지 now는 미지정 → created_at=벽시계.
    from scripts.scenarios import apply_scenario, scenario_by_id

    sc = scenario_by_id("emergency_hijack")
    apply_scenario(store, sc, ANCHOR)
    before = int(time.time())
    created = scan_and_create_all(store)  # now 미지정
    after = int(time.time())
    flat = [a for v in created.values() for a in v]
    assert flat
    assert all(before <= a.created_at <= after for a in flat)  # 벽시계 범위


# ── 3. replay 결정성 (같은 앵커 → 완전 동일) ──────────────────────────────────
def _assess_fingerprint(r: dict):
    """assess 응답의 결정적 지문 — 문장(text·cites·신뢰도·kind) + assessment_id + counts."""
    return (
        r["assessment_id"],
        r["confidence"],
        tuple(sorted(r["counts"].items())),
        tuple(
            (s["text"], tuple(s["cites"]), s["confidence"], s["kind"])
            for s in r["sentences"]
        ),
    )


def _anomaly_fingerprint(store):
    return sorted(
        (a.id, a.type, round(a.confidence, 4), a.status, a.created_at, a.lat, a.lon)
        for a in store.query_anomalies()
    )


def test_replay_build_is_deterministic(tmp_path):
    """같은 앵커로 데모 DB를 두 번 빌드 → 이상징후·assess 산출이 완전 동일."""
    s1 = _store(tmp_path, "demoA.db")
    inject_scenario(s1, "all", now=ANCHOR)
    r1 = assess(s1, DEMO_QUERY, now=ANCHOR)

    s2 = _store(tmp_path, "demoB.db")
    inject_scenario(s2, "all", now=ANCHOR)
    r2 = assess(s2, DEMO_QUERY, now=ANCHOR)

    # 이상징후 전량(created_at 포함) 동일.
    assert _anomaly_fingerprint(s1) == _anomaly_fingerprint(s2)
    # assess 응답(문장·cites·신뢰도·id) 동일.
    assert _assess_fingerprint(r1) == _assess_fingerprint(r2)
    # 상관 링크 수 동일(교차소스 그래프 결정성).
    assert len(s1.query_all_correlations()) == len(s2.query_all_correlations())


# ── 4. 오프라인 가드 (네트워크 0 증명) ────────────────────────────────────────
def test_is_offline_parsing(monkeypatch):
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("SKAI_OFFLINE", truthy)
        assert offline_guard.is_offline() is True
    for falsy in ("0", "false", "", "no"):
        monkeypatch.setenv("SKAI_OFFLINE", falsy)
        assert offline_guard.is_offline() is False
    monkeypatch.delenv("SKAI_OFFLINE", raising=False)
    assert offline_guard.is_offline() is False


def test_is_loopback_classification():
    assert offline_guard._is_loopback(("127.0.0.1", 8000)) is True
    assert offline_guard._is_loopback(("::1", 80)) is True
    assert offline_guard._is_loopback(("localhost", 80)) is True
    assert offline_guard._is_loopback("/tmp/x.sock") is True  # UNIX 소켓 = 로컬
    assert offline_guard._is_loopback(("8.8.8.8", 443)) is False
    assert (
        offline_guard._is_loopback(("opensky-network.org", 443)) is False
    )  # 외부 도메인


def test_offline_guard_blocks_external_allows_loopback():
    """가드 설치 시 외부 connect는 OfflineViolation, 루프백은 통과(원함수 위임).

    프로세스 전역 패치라 테스트 후 원상복구한다(다른 테스트 격리).
    """
    orig_connect = socket.socket.connect
    orig_connect_ex = socket.socket.connect_ex
    orig_installed = offline_guard._installed
    orig_blocked = offline_guard.blocked_attempts
    offline_guard._installed = False  # 강제 재설치 허용
    try:
        assert offline_guard.install_offline_guard(force=True) is True
        # 외부 → 차단.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            raised = False
            try:
                s.connect(("93.184.216.34", 80))
            except offline_guard.OfflineViolation:
                raised = True
            assert raised is True
        finally:
            s.close()
        assert offline_guard.blocked_attempts > orig_blocked
        # 루프백 → 가드 통과(연결 자체는 리스너 없으면 ConnectionRefused, 그건 OfflineViolation 아님).
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2.settimeout(0.2)
        try:
            s2.connect(("127.0.0.1", 59999))  # 아무도 안 듣는 포트
        except offline_guard.OfflineViolation:
            raise AssertionError("루프백이 차단됨(가드 오작동)")
        except OSError:
            pass  # ConnectionRefused/timeout = 정상(가드는 통과시킴)
        finally:
            s2.close()
    finally:
        socket.socket.connect = orig_connect  # type: ignore[method-assign]
        socket.socket.connect_ex = orig_connect_ex  # type: ignore[method-assign]
        offline_guard._installed = orig_installed
        offline_guard.blocked_attempts = orig_blocked
