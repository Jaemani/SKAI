"""DR-0011 검증 — 의도분류 + 의도별 조립(cites 불변) + LLM 폴백 + 연속 폴러/LIVE.

커버:
  1. 규칙 분류 — 의도별 대표 질의 + 데모 백본 질의는 상황요약(결정성 보존).
  2. 의도별 조립 — count/filter/entity/why/weather/news/correlation 각 사실→문장 + cites.
  3. citation 불변 — 어떤 의도든 모든 문장이 cites 보유(무근거 문장 없음).
  4. LLM 분류 폴백 — 기본 off(결정적), claude 성공/실패 경로.
  5. 연속 폴러 — 간격 하한·유한 사이클·stop 인터럽트·LIVE 사이드카(last_poll_ts).
  6. 서버 계약 — /api/assess intent·slots, /api/live, /api/stats last_poll_ts.

실행: .venv/bin/python -m pytest tests/test_copilot_intent.py -v
"""

from __future__ import annotations

import subprocess
import threading

import pytest

import connectors.opensky as opensky
from copilot import intent as intent_mod
from copilot.assessment import assess
from copilot.intent import (
    INTENT_CORRELATION,
    INTENT_COUNT,
    INTENT_ENTITY_EXPLAIN,
    INTENT_FILTER,
    INTENT_NEWS,
    INTENT_SITUATION_SUMMARY,
    INTENT_WEATHER,
    INTENT_WHY,
    Intent,
    classify,
)
from ontology.model import (
    KADIZ_REGION,
    Aircraft,
    Anomaly,
    NewsEvent,
    Observation,
    OrbitPass,
    Satellite,
    WeatherState,
)
from ontology.store_local import LocalOntologyStore
from server import live_status

NOW = 1783000000


def _store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "intent.db"))


def _seed(store: LocalOntologyStore, now: int = NOW):
    """KADIZ에 군용(명시)·민간 항적 + 이상징후 + 통과 + 기상 + 뉴스를 심는다."""
    store.write_region(KADIZ_REGION)
    # 군용 추정(명시 플래그) + 미국 국적 + 비상 스쿽 이상징후
    store.write_aircraft(Aircraft(icao24="synthx", callsign="TEST77", is_military=True))
    obs = Observation(
        id=f"synthx-{now}",
        aircraft_ref="synthx",
        ts=now,
        lat=36.5,
        lon=127.0,
        squawk="7700",
        source="synthetic",
        source_url="synthetic://x",
        attrs={"origin_country": "United States"},
    )
    store.write_observation(obs)
    # 민간 + 한국 국적
    store.write_aircraft(Aircraft(icao24="civ001", callsign="KAL123"))
    obs2 = Observation(
        id=f"civ001-{now}",
        aircraft_ref="civ001",
        ts=now,
        lat=36.7,
        lon=127.2,
        squawk="1200",
        source="synthetic",
        source_url="synthetic://y",
        attrs={"origin_country": "South Korea"},
    )
    store.write_observation(obs2)
    anom = Anomaly(
        id="anomaly-emergency_squawk-synthx-1",
        type="emergency_squawk",
        ts=now,
        confidence=0.93,
        status="candidate",
        lat=36.5,
        lon=127.0,
        explanation="비상 스쿽 7700 송신 — 하드 신호로 신뢰도 높음.",
        explainer_backend="template",
        created_at=now,
        attrs={
            "squawk": "7700",
            "callsign": "TEST77",
            "meaning": "일반 비상",
            "is_synthetic": True,
        },
    )
    store.write_anomaly(anom, evidence=[obs.id], involves=["synthx"])
    store.write_satellite(
        Satellite(norad_id="25544", name="ISS", source_url="http://tle")
    )
    store.write_orbitpass(
        OrbitPass(
            id=f"pass-25544-{now + 300}",
            satellite_ref="25544",
            region_ref="KADIZ",
            start_ts=now + 300,
            end_ts=now + 400,
            max_elevation=75.0,
            ground_track=[[36.4, 126.9], [36.6, 127.1]],
            source_url="http://tle",
        )
    )
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
            source_url="http://wx",
        )
    )
    store.write_newsevent(
        NewsEvent(
            id="news-abc",
            source="gdelt",
            source_url="https://ex/1",
            ts=now - 3600,
            title="KADIZ incident report",
            confidence=0.35,
            entities=["KADIZ"],
        ),
        mentions=[("Region", "KADIZ")],
    )


# ──────────────────────────────────────────────
# 1. 규칙 분류(결정적)
# ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "query,expected",
    [
        ("KADIZ에 항적 몇 대야?", INTENT_COUNT),
        ("이상징후 몇 건이야?", INTENT_COUNT),
        ("군용기 몇 대?", INTENT_COUNT),
        ("군용기만 보여줘", INTENT_FILTER),
        ("미국 국적 항공기", INTENT_FILTER),
        ("이 이상징후 뭐야?", INTENT_ENTITY_EXPLAIN),
        ("이 이상징후 왜 위험해?", INTENT_WHY),
        ("은닉 정황 있어?", INTENT_CORRELATION),
        ("기상 어때?", INTENT_WEATHER),
        ("관련 뉴스 있어?", INTENT_NEWS),
    ],
)
def test_rule_classification(query, expected):
    assert classify(query).intent == expected


@pytest.mark.parametrize(
    "query",
    [
        # 발표 백본(demo.sh replay) + 기존 test_p4 assess 질의 — 반드시 상황요약으로 귀결.
        "지금 KADIZ 근방 이상한 거 있어?",
        "최근 1시간 위성 통과랑 겹치는 이상징후는?",
        "서해 쪽 기상이랑 뉴스 맥락 요약해줘",
        "지금 KADIZ 상황",
        "지금 KADIZ 위성 겹치는 이상징후",
        "최근 2시간 KADIZ",
    ],
)
def test_demo_backbone_stays_summary(query):
    """데모 백본 질의는 상황요약 — 결정성·기존 테스트 보존."""
    assert classify(query).intent == INTENT_SITUATION_SUMMARY


def test_classification_is_deterministic():
    q = "군용기 몇 대야?"
    a, b = classify(q), classify(q)
    assert (a.intent, a.slots) == (b.intent, b.slots)


def test_count_target_and_filter_slots():
    assert classify("위성 통과 몇 건?").slots["target"] == "passes"
    assert classify("뉴스 몇 건?").slots["target"] == "news"
    assert classify("미국 국적 항공기").slots["origin_country"] == "United States"
    assert classify("민간기만").slots["military"] is False


def test_natural_language_origin_filter():
    """'국적' 없이 국가명+항적 문맥 키워드로 origin_country 필터 발동(KADIZ-safe).

    사용자 핵심 예시: "중국 쪽에서 온 기체 있어?"가 filter로 가야 한다.
    "한국"/"대한민국"은 KADIZ 충돌 위험이 있어 기존처럼 "국적" 명시 필요.
    """
    # 중국 + 기체 문맥 → filter(China)
    r = classify("중국 쪽에서 온 기체 있어?")
    assert r.intent == INTENT_FILTER, f"예상 filter, 실제 {r.intent}"
    assert r.slots.get("origin_country") == "China"

    # 미국 + 항공기 문맥 → filter(United States)
    r = classify("미국 항공기 있어?")
    assert r.intent == INTENT_FILTER, f"예상 filter, 실제 {r.intent}"
    assert r.slots.get("origin_country") == "United States"

    # 일본 + 비행기 문맥 → filter(Japan)
    r = classify("일본에서 온 비행기")
    assert r.intent == INTENT_FILTER, f"예상 filter, 실제 {r.intent}"
    assert r.slots.get("origin_country") == "Japan"

    # 러시아 + 기체 + 몇 대 → count가 filter보다 먼저 발동(집계 마커 우선, 합리적)
    r = classify("러시아 기체 몇 대?")
    assert r.intent in (INTENT_COUNT, INTENT_FILTER), (
        f"count 또는 filter 중 하나여야 함, 실제 {r.intent}"
    )

    # 한국 + 공역 → "국적" 없음, KADIZ 충돌 방지 → situation_summary
    r = classify("한국 공역 이상한거")
    assert r.intent == INTENT_SITUATION_SUMMARY, (
        f"KADIZ 충돌: situation_summary 예상, 실제 {r.intent}"
    )


def test_focus_id_forces_entity():
    """선택 객체(focus_id)가 있으면 모호 질의도 entity_explain."""
    it = classify("이거 뭐야", focus_id="civ001")
    assert it.intent == INTENT_ENTITY_EXPLAIN
    assert it.slots["entity_id"] == "civ001"


def test_id_in_text_extracted_full():
    it = classify("anomaly-emergency_squawk-synthx-1 설명해줘")
    assert it.intent == INTENT_ENTITY_EXPLAIN
    assert it.slots["entity_id"] == "anomaly-emergency_squawk-synthx-1"


# ──────────────────────────────────────────────
# 2~3. 의도별 조립 + citation 불변
# ──────────────────────────────────────────────
@pytest.mark.parametrize(
    "query",
    [
        "지금 KADIZ 상황",  # summary
        "KADIZ에 항적 몇 대야?",  # count flights
        "이상징후 몇 건이야?",  # count anomalies
        "군용기 몇 대?",  # count military
        "군용기만 보여줘",  # filter military
        "미국 국적 항공기",  # filter origin
        "이 이상징후 뭐야?",  # entity
        "이 이상징후 왜 위험해?",  # why
        "은닉 정황 있어?",  # correlation
        "기상 어때?",  # weather
        "관련 뉴스 있어?",  # news
    ],
)
def test_every_sentence_has_cites_all_intents(tmp_path, query):
    """어떤 의도든 모든 문장이 cites 보유(무근거 문장 없음 — DR-0006 불변)."""
    store = _seed_store(tmp_path)
    r = assess(store, query, now=NOW)
    assert not r["no_evidence"], f"근거 없음: {query}"
    assert r["sentences"], f"문장 없음: {query}"
    for s in r["sentences"]:
        assert s["cites"], f"cites 없는 문장({query}): {s['text']!r}"
    # 모든 cite가 cited_objects로 해상되는지(배지 렌더 가능)
    for s in r["sentences"]:
        for c in s["cites"]:
            assert c in r["cited_objects"], f"미해상 cite {c} ({query})"


def _seed_store(tmp_path) -> LocalOntologyStore:
    store = _store(tmp_path)
    _seed(store)
    return store


def test_count_cites_underlying_objects(tmp_path):
    store = _seed_store(tmp_path)
    r = assess(store, "KADIZ에 항적 몇 대야?", now=NOW)
    assert r["intent"] == INTENT_COUNT and r["slots"]["target"] == "flights"
    assert "2대" in r["sentences"][0]["text"]
    assert len(r["sentences"][0]["cites"]) == 2  # 관측 2건 인용


def test_filter_military_only_matches_military(tmp_path):
    store = _seed_store(tmp_path)
    r = assess(store, "군용기만 보여줘", now=NOW)
    assert r["intent"] == INTENT_FILTER and r["slots"]["military"] is True
    # 군용 추정 1대(synthx)만 — 헤드라인 + 상세 1
    assert "1대" in r["sentences"][0]["text"]
    detail = [s for s in r["sentences"] if s["kind"] == "flight"]
    assert len(detail) == 1
    assert "TEST77" in detail[0]["text"]


def test_filter_origin_country(tmp_path):
    store = _seed_store(tmp_path)
    r = assess(store, "미국 국적 항공기", now=NOW)
    assert r["slots"]["origin_country"] == "United States"
    detail = [s for s in r["sentences"] if s["kind"] == "flight"]
    assert len(detail) == 1 and "United States" in detail[0]["text"]


def test_entity_explain_by_id_cites_anomaly_and_evidence(tmp_path):
    store = _seed_store(tmp_path)
    r = assess(
        store,
        "anomaly-emergency_squawk-synthx-1 설명해줘",
        now=NOW,
    )
    assert r["intent"] == INTENT_ENTITY_EXPLAIN
    cites = r["sentences"][0]["cites"]
    assert "anomaly-emergency_squawk-synthx-1" in cites  # 이상징후 인용
    assert any(not c.startswith("anomaly-") for c in cites)  # 근거 관측도 인용


def test_why_uses_anomaly_explanation(tmp_path):
    store = _seed_store(tmp_path)
    r = assess(store, "이 이상징후 왜 위험해?", now=NOW)
    assert r["intent"] == INTENT_WHY
    texts = " ".join(s["text"] for s in r["sentences"])
    assert "판단 근거" in texts  # 저장된 explanation을 근거로 노출
    for s in r["sentences"]:
        assert s["cites"]


def test_entity_no_match_is_no_evidence(tmp_path):
    store = _seed_store(tmp_path)
    r = assess(store, "설명해줘", now=NOW, focus_id="pass-does-not-exist")
    assert r["no_evidence"] is True
    assert r["intent"] == INTENT_ENTITY_EXPLAIN


def test_response_exposes_intent_slots(tmp_path):
    store = _seed_store(tmp_path)
    r = assess(store, "군용기 몇 대?", now=NOW)
    assert r["intent"] == INTENT_COUNT
    assert "slots" in r and "intent_meta" in r
    assert r["intent_meta"]["backend"] == "rule"


# ──────────────────────────────────────────────
# 4. LLM 분류 폴백(기본 off·성공·실패)
# ──────────────────────────────────────────────
def test_llm_off_by_default_is_rule_only(monkeypatch):
    """llm 미지정이면 모호 질의도 규칙 기본(상황요약) — subprocess 호출 안 함."""
    called = {"n": 0}

    def _boom(*a, **k):
        called["n"] += 1
        raise AssertionError("LLM은 호출되면 안 됨")

    monkeypatch.setattr(intent_mod.subprocess, "run", _boom)
    it = classify("저 근처 좀 봐줘")  # 마커 없음 → 모호
    assert it.intent == INTENT_SITUATION_SUMMARY
    assert it.backend == "rule(default)"
    assert called["n"] == 0


def test_llm_fallback_success(monkeypatch):
    """claude가 유효 의도를 돌려주면 그 의도 채택(backend=claude)."""

    class _P:
        returncode = 0
        stdout = "count\n"
        stderr = ""

    monkeypatch.setattr(intent_mod.subprocess, "run", lambda *a, **k: _P())
    it = classify("저 근처 좀 봐줘", llm="claude")  # 모호 → LLM 경로
    assert it.intent == INTENT_COUNT and it.backend == "claude"


def test_llm_fallback_failure_keeps_rule(monkeypatch):
    """claude 실패(타임아웃 등) → 규칙 기본(상황요약) 유지."""

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=30)

    monkeypatch.setattr(intent_mod.subprocess, "run", _timeout)
    it = classify("저 근처 좀 봐줘", llm="claude")
    assert it.intent == INTENT_SITUATION_SUMMARY


def test_llm_only_for_ambiguous(monkeypatch):
    """규칙이 확정한 질의(군용기 몇 대)는 llm 켜져 있어도 LLM 호출 안 함."""

    def _boom(*a, **k):
        raise AssertionError("확정 질의엔 LLM 호출 금지")

    monkeypatch.setattr(intent_mod.subprocess, "run", _boom)
    it = classify("군용기 몇 대야?", llm="claude")
    assert it.intent == INTENT_COUNT and it.backend == "rule"


# ──────────────────────────────────────────────
# 5. 연속 폴러 + LIVE 사이드카
# ──────────────────────────────────────────────
_SAMPLE_STATE = [
    "abc123",
    "KAL77  ",
    "South Korea",
    None,
    NOW,
    127.0,
    36.5,
    10000,
    False,
    200.0,
    90.0,
    0,
    None,
    10500,
    None,
    None,
    0,
]


def test_live_status_roundtrip(tmp_path):
    db = str(tmp_path / "s.db")
    live_status.write_status(db, mode="live", last_poll_ts=123, cycle=2)
    st = live_status.read_status(db)
    assert st["mode"] == "live" and st["last_poll_ts"] == 123
    assert live_status.read_status(str(tmp_path / "none.db")) is None


def test_poller_interval_floor_and_finite(tmp_path, monkeypatch, capsys):
    db = str(tmp_path / "p.db")
    monkeypatch.setattr(
        opensky, "fetch_states", lambda c, b: ([_SAMPLE_STATE], "synthetic://o")
    )
    ev = threading.Event()  # 유한 사이클이라 신호 불요, 격리용
    opensky.run_poller(interval=3, max_cycles=1, db_path=db, stop_event=ev)
    st = live_status.read_status(db)
    assert st["interval"] == opensky.MIN_POLL_INTERVAL  # 3→10 하한 상향
    assert st["mode"] == "stopped" and st["cycle"] == 1
    assert st["last_poll_ts"] is not None
    assert st["counts"]["observation"] == 1  # 실 write 발생


def test_poller_stop_event_interrupts(tmp_path, monkeypatch):
    db = str(tmp_path / "q.db")
    monkeypatch.setattr(
        opensky, "fetch_states", lambda c, b: ([_SAMPLE_STATE], "synthetic://o")
    )
    ev = threading.Event()
    ev.set()  # 시작 전에 세팅 → 첫 while 조건에서 즉시 종료(대기 없음)
    opensky.run_poller(interval=10, max_cycles=0, db_path=db, stop_event=ev)
    st = live_status.read_status(db)
    assert st["mode"] == "stopped" and st["cycle"] == 0  # 사이클 0회로 정리


# ──────────────────────────────────────────────
# 5b. 다중소스 due 스케줄링 (DR-0012 갭#3)
# ──────────────────────────────────────────────
def test_resolve_sources_default_and_normalize():
    # 기본(None) = OpenSky-only(하위호환 — 기존 프로그래밍 호출·테스트 불변).
    assert opensky.resolve_sources(None) == ["opensky"]
    # 대소문자·공백 정규화 + 중복 제거 + 알 수 없는 이름 무시.
    got = opensky.resolve_sources(["OpenSky", " gdelt ", "gdelt", "bogus", "metar"])
    assert got == ["opensky", "gdelt", "metar"]
    # opensky 미명시여도 항적은 base 사이클이라 선두로 보장된다.
    assert opensky.resolve_sources(["gdelt"])[0] == "opensky"


def test_due_sources_scheduling():
    intervals = {"gdelt": 300, "metar": 1800, "celestrak": 43200}
    sources = ["opensky", "gdelt", "metar", "celestrak"]
    # 첫 사이클(모두 미폴=0) → 모든 보조 소스 due. opensky는 base 경로라 여기서 제외.
    now = 1_000_000
    assert opensky.due_sources({}, now, intervals, sources) == [
        "gdelt",
        "metar",
        "celestrak",
    ]
    # 방금 폴한 뒤: gdelt만 5분 경과 → gdelt만 due(metar 30분·celestrak 12h는 아직).
    last = {"gdelt": now, "metar": now, "celestrak": now}
    assert opensky.due_sources(last, now + 301, intervals, sources) == ["gdelt"]
    # 아무 주기도 도래 안 함 → 빈 리스트(OpenSky만 매 사이클 도는 상태).
    assert opensky.due_sources(last, now + 10, intervals, sources) == []


def test_poller_multisource_records_per_source_freshness(tmp_path, monkeypatch):
    """다중소스 폴러 1사이클: 각 보조 소스 1회 ingest + source_last_poll 기록."""
    db = str(tmp_path / "multi.db")
    monkeypatch.setattr(
        opensky, "fetch_states", lambda c, b: ([_SAMPLE_STATE], "synthetic://o")
    )
    called: list[str] = []

    def fake_ingest(src, store):
        called.append(src)
        return f"{src}-ok"

    monkeypatch.setattr(opensky, "_ingest_source", fake_ingest)
    ev = threading.Event()
    opensky.run_poller(
        interval=10,
        max_cycles=1,
        db_path=db,
        stop_event=ev,
        sources=["opensky", "gdelt", "metar", "celestrak"],
    )
    # 첫 사이클에 세 보조 소스가 각각 1회 fetch(due) — 실 URL 뉴스 등 원소스 연결 경로.
    assert set(called) == {"gdelt", "metar", "celestrak"}
    st = live_status.read_status(db)
    assert st["mode"] == "stopped"
    slp = st["source_last_poll"]
    # 소스별 last_poll_ts가 노출된다(프론트 소스별 신선도 표시용).
    for s in ("opensky", "gdelt", "metar", "celestrak"):
        assert slp[s] > 0
    assert all(v == "ok" for v in st["source_last_status"].values())


def test_poller_source_failure_isolated(tmp_path, monkeypatch):
    """한 보조 소스가 죽어도 루프·타 소스는 지속(실패 개별 격리)."""
    db = str(tmp_path / "iso.db")
    monkeypatch.setattr(
        opensky, "fetch_states", lambda c, b: ([_SAMPLE_STATE], "synthetic://o")
    )

    def flaky_ingest(src, store):
        if src == "metar":
            raise RuntimeError("metar 소스 장애(합성)")
        return f"{src}-ok"

    monkeypatch.setattr(opensky, "_ingest_source", flaky_ingest)
    ev = threading.Event()
    opensky.run_poller(
        interval=10,
        max_cycles=1,
        db_path=db,
        stop_event=ev,
        sources=["opensky", "gdelt", "metar"],
    )
    st = live_status.read_status(db)
    # 루프는 정상 종료(러너웨이 아님)하고 카운트도 기록된다.
    assert st["mode"] == "stopped" and st["cycle"] == 1
    # 실패 소스만 error·미갱신(last_poll 0 유지), 나머지는 ok·갱신.
    assert st["source_last_status"]["metar"] == "error"
    assert st["source_last_poll"]["metar"] == 0
    assert st["source_last_status"]["opensky"] == "ok"
    assert st["source_last_poll"]["gdelt"] > 0


# ──────────────────────────────────────────────
# 6. 서버 계약(intent·slots·LIVE)
# ──────────────────────────────────────────────
def test_server_assess_and_live(tmp_path, monkeypatch):
    db = str(tmp_path / "srv.db")
    monkeypatch.setenv("SKAI_DB", db)
    store = LocalOntologyStore(db)
    _seed(store)
    # SKAI_DB를 반영해 서버 모듈을 재로딩
    import importlib

    import server.app as app_mod

    importlib.reload(app_mod)
    from fastapi.testclient import TestClient

    c = TestClient(app_mod.app)
    r = c.post("/api/assess", json={"query": "군용기 몇 대?"})
    assert r.status_code == 200
    j = r.json()
    assert j["intent"] == INTENT_COUNT and "slots" in j and "intent_meta" in j
    # 폴러 없음 → live False
    lv = c.get("/api/live").json()
    assert lv["live"] is False and lv["last_poll_ts"] is None
    stats = c.get("/api/stats").json()
    assert "last_poll_ts" in stats and stats["live"] is False
    assert stats["observation"] == 2  # 카운트 키 보존(하위호환)
    # read 소스 노출(SKAI_STORE 미설정 → 로컬 SQLite read).
    assert stats["store_backend"] == "local"
    assert lv["store_backend"] == "local"


def test_injected_intent_bypasses_classify(tmp_path):
    """미리 분류된 Intent 주입 시 재분류 없이 그 의도로 조립(테스트·재현)."""
    store = _seed_store(tmp_path)
    forced = Intent(INTENT_WEATHER, {}, 1.0, backend="injected")
    r = assess(store, "아무 질의나", now=NOW, intent=forced)
    assert r["intent"] == INTENT_WEATHER
    assert all(s["kind"] == "weather" for s in r["sentences"])
