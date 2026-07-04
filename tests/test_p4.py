"""P4 검증 테스트 — 코파일럿(citation Assessment) + P3 이월 #1 수정.

커버:
  1. 파서 — 지역(별칭)·시간창("지금"=30분·"최근 N분/시간"·기본값 플래그).
  2. cites 없는 문장 거부 — write_assessment가 SentenceEvidenceError(DR-0006).
  3. 사실→문장 조립 — 모든 문장이 cites 보유 + 상관 문장이 이상징후·통과 함께 인용.
  4. Assessment 영속 + aggregates/cites 링크 + 서브그래프 다중홉.
  5. OrbitPass stale 수정 — 미래 pass 선삭제·과거 보존·링크 정합.

실행: .venv/bin/python -m pytest tests/test_p4.py -v
"""

from __future__ import annotations

import time

import pytest

from copilot import tools
from copilot.assessment import assess, build_subgraph
from copilot.parser import DEFAULT_WINDOW_SECONDS, parse_query
from copilot.tools import NEWS_MAX_AGE_SECONDS
from ontology.model import (
    KADIZ_REGION,
    Aircraft,
    Anomaly,
    AssessmentSentence,
    NewsEvent,
    Observation,
    OrbitPass,
    Satellite,
    SituationAssessment,
    WeatherState,
)
from ontology.store import SentenceEvidenceError
from ontology.store_local import LocalOntologyStore
from server import app as server_app


def _store(tmp_path) -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / "p4.db"))


def _seed(store: LocalOntologyStore, now: int):
    """KADIZ에 이상징후(+근거관측)·통과·기상·뉴스를 now 기준으로 심는다."""
    store.write_region(KADIZ_REGION)
    # 항공기 + 비상 스쿽 관측 + 이상징후(evidence 링크)
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
    # 통과창(이상징후 시각 근방 +5분). ISR 허용목록 위성(Sentinel-1A/39634)을 쓴다 —
    # correlated_with 승격은 허용목록만 통과하므로(isr_satellites 게이트), 상관 인용을
    # 검증하려면 허용목록 위성이어야 한다(ISS 25544 같은 비-ISR 위성은 이제 상관되지 않음).
    store.write_satellite(
        Satellite(norad_id="39634", name="SENTINEL-1A", source_url="http://tle")
    )
    store.write_orbitpass(
        OrbitPass(
            id=f"pass-39634-{now + 300}",
            satellite_ref="39634",
            region_ref="KADIZ",
            start_ts=now + 300,
            end_ts=now + 400,
            max_elevation=75.0,
            ground_track=[[36.4, 126.9], [36.6, 127.1]],
            source_url="http://tle",
        )
    )
    # 기상
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
    # 뉴스(KADIZ 언급)
    store.write_newsevent(
        NewsEvent(
            id="news-abc",
            source="gdelt",
            source_url="https://ex/1",
            ts=now - 3600,
            title="KADIZ incident",
            confidence=0.35,
            entities=["KADIZ"],
        ),
        mentions=[("Region", "KADIZ")],
    )
    return a, obs


# ──────────────────────────────────────────────
# 1. 파서 (지역·시간창)
# ──────────────────────────────────────────────
def test_parser_region_default_kadiz():
    assert parse_query("지금 이상한 거 있어?", now=1000).region_id == "KADIZ"


def test_parser_region_alias_seohae():
    pq = parse_query("서해 쪽 기상이랑 뉴스", now=1000)
    assert pq.region_id == "KADIZ"
    assert pq.matched_region_alias == "서해"


def test_parser_window_now_is_30min():
    pq = parse_query("지금 KADIZ 근방", now=10000)
    assert pq.window_seconds == DEFAULT_WINDOW_SECONDS == 1800
    assert pq.window_start == 10000 - 1800 and pq.window_end == 10000


def test_parser_window_recent_n():
    assert parse_query("최근 1시간 위성", now=0).window_seconds == 3600
    assert parse_query("최근 45분", now=0).window_seconds == 45 * 60
    assert parse_query("최근 2시간 상황", now=0).window_seconds == 7200


def test_parser_defaults_flagged():
    # 지역·시간 표현이 없으면 기본값 사용을 응답에 노출(투명성)
    pq = parse_query("이상한 거 있어?", now=0)
    assert "region" in pq.fields_defaulted and "window" in pq.fields_defaulted
    assert pq.window_seconds == 1800


# ──────────────────────────────────────────────
# 2. cites 없는 문장 거부 (DR-0006)
# ──────────────────────────────────────────────
def test_citeless_sentence_rejected(tmp_path):
    store = _store(tmp_path)
    bad = SituationAssessment(
        id="assess-x",
        region_ref="KADIZ",
        window_start=0,
        window_end=1,
        query="q",
        summary="s",
        sentences=[
            AssessmentSentence(
                text="근거 없는 주장", cites=[], confidence=0.9, kind="summary"
            )
        ],
        confidence=0.9,
        produced_by="template",
        created_at=1,
    )
    with pytest.raises(SentenceEvidenceError):
        store.write_assessment(bad)
    assert store.counts()["assessment"] == 0  # 거부 → 저장 0건


def test_empty_assessment_rejected(tmp_path):
    store = _store(tmp_path)
    bad = SituationAssessment(
        id="assess-y",
        region_ref="KADIZ",
        window_start=0,
        window_end=1,
        query="q",
        summary="s",
        sentences=[],
        confidence=0,
        produced_by="template",
        created_at=1,
    )
    with pytest.raises(SentenceEvidenceError):
        store.write_assessment(bad)


# ──────────────────────────────────────────────
# 3. 사실→문장 조립 (cites 보존)
# ──────────────────────────────────────────────
def test_every_sentence_has_cites(tmp_path):
    store = _store(tmp_path)
    now = 1783000000
    _seed(store, now)
    r = assess(store, "지금 KADIZ 근방 이상한 거 있어?", now=now)
    assert not r["no_evidence"]
    assert len(r["sentences"]) >= 3
    for s in r["sentences"]:
        assert s["cites"], f"cites 없는 문장 진입: {s}"


def test_correlation_cites_anomaly_and_pass(tmp_path):
    store = _store(tmp_path)
    now = 1783000000
    _seed(store, now)
    r = assess(store, "최근 1시간 위성 통과랑 겹치는 이상징후는?", now=now)
    corr = [s for s in r["sentences"] if s["kind"] == "correlation"]
    assert corr, "상관 문장 없음"
    cites = corr[0]["cites"]
    assert any(c.startswith("anomaly-") for c in cites)  # 이상징후 인용
    assert any(c.startswith("pass-") for c in cites)  # 통과 인용(교차소스)


def test_cited_objects_all_resolved(tmp_path):
    store = _store(tmp_path)
    now = 1783000000
    _seed(store, now)
    r = assess(store, "지금 KADIZ 상황", now=now)
    for s in r["sentences"]:
        for c in s["cites"]:
            assert c in r["cited_objects"], f"미해상 cite: {c}"


def test_no_evidence_when_empty(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)  # 데이터 없음(지역만)
    r = assess(store, "지금 이상한 거 있어?", now=1783000000)
    assert r["no_evidence"] is True
    assert r["assessment_id"] is None
    assert store.counts()["assessment"] == 0  # 무근거 → Assessment 미생성


def test_window_exposed_in_response(tmp_path):
    store = _store(tmp_path)
    now = 1783000000
    _seed(store, now)
    r = assess(store, "최근 2시간 KADIZ", now=now)
    assert r["window"]["label"] == "최근 2시간"
    assert r["window"]["seconds"] == 7200
    assert r["region"]["id"] == "KADIZ"


# ──────────────────────────────────────────────
# 4. 영속 + 링크 + 서브그래프
# ──────────────────────────────────────────────
def test_assessment_persisted_with_links(tmp_path):
    store = _store(tmp_path)
    now = 1783000000
    _seed(store, now)
    r = assess(store, "지금 KADIZ 위성 겹치는 이상징후", now=now)
    assert store.counts()["assessment"] == 1
    links = store.query_assessment_links(r["assessment_id"])
    lt = {(x["link_type"], x["dst_type"]) for x in links}
    assert ("aggregates", "Anomaly") in lt  # 이상징후 집계
    assert ("cites", "OrbitPass") in lt  # 통과 인용
    assert ("cites", "WeatherState") in lt  # 기상 인용


def test_assessment_link_upsert_no_stale(tmp_path):
    # 같은 id로 재작성 시 이전 링크가 stale로 쌓이지 않는다(cites 축소 재생성 방어).
    store = _store(tmp_path)
    now = 1783000000
    _seed(store, now)
    r1 = assess(store, "지금 KADIZ 상황", now=now)
    n1 = len(store.query_assessment_links(r1["assessment_id"]))
    r2 = assess(store, "지금 KADIZ 상황", now=now)  # 동일 id(now 고정)
    n2 = len(store.query_assessment_links(r2["assessment_id"]))
    assert r1["assessment_id"] == r2["assessment_id"]
    assert n1 == n2  # 재실행해도 링크 수 불변(stale 없음)


def test_subgraph_multihop(tmp_path):
    store = _store(tmp_path)
    now = 1783000000
    _seed(store, now)
    r = assess(store, "지금 KADIZ 위성 겹치는 이상징후", now=now)
    sg = build_subgraph(store, r["assessment_id"])
    assert sg["center"] == r["assessment_id"]
    types = {n["type"] for n in sg["nodes"]}
    assert "SituationAssessment" in types and "Anomaly" in types
    # 다중홉: Anomaly —evidenced_by→ Observation 엣지가 존재(provenance 깊이)
    assert any(e["link_type"] == "evidenced_by" for e in sg["edges"])
    assert any(e["link_type"] == "aggregates" for e in sg["edges"])


# ──────────────────────────────────────────────
# 5. OrbitPass stale 수정 (P3 이월 #1)
# ──────────────────────────────────────────────
def test_delete_future_orbitpasses_preserves_past(tmp_path):
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    store.write_satellite(
        Satellite(norad_id="25544", name="ISS", source_url="http://tle")
    )
    now = 1000000
    for st in (now - 500, now + 100, now + 200):  # 과거 1 + 미래 2
        store.write_orbitpass(
            OrbitPass(
                id=f"pass-25544-{st}",
                satellite_ref="25544",
                region_ref="KADIZ",
                start_ts=st,
                end_ts=st + 60,
                max_elevation=70.0,
                ground_track=[[36, 127]],
                source_url="http://tle",
            )
        )
    n = store.delete_future_orbitpasses_for("25544", now)
    assert n == 2  # 미래 2건 삭제
    remaining = [p.id for p in store.query_orbitpasses()]
    assert remaining == ["pass-25544-999500"]  # 과거 1건 보존
    # 미래 pass의 of/over 링크도 제거 → 과거 pass의 of+over(2개)만 남음
    assert store.counts()["link"] == 2


# ──────────────────────────────────────────────
# 6. 시간 정직성 — 뉴스 나이 필터 · 위성 서술 창 제한 (2026-07-05)
# ──────────────────────────────────────────────
def test_tools_news_excludes_stale_articles(tmp_path):
    """tools.news()는 질의창 종료 기준 48h보다 오래된 기사를 제외한다."""
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    now = 1783000000
    store.write_newsevent(
        NewsEvent(
            id="news-fresh",
            source="synthetic",
            source_url="synthetic://x/fresh",
            ts=now - 1000,  # 신선(48h 이내)
            title="fresh",
            confidence=0.35,
            entities=["KADIZ"],
        )
    )
    store.write_newsevent(
        NewsEvent(
            id="news-stale",
            source="synthetic",
            source_url="synthetic://x/stale",
            ts=now - 50 * 3600,  # 50시간 전(48h 초과) → 제외 대상
            title="stale",
            confidence=0.35,
            entities=["KADIZ"],
        )
    )
    facts = tools.news(store, "KADIZ", (now - 1800, now))
    ids = [f.data["id"] for f in facts]
    assert "news-fresh" in ids
    assert "news-stale" not in ids


def test_tools_news_age_boundary_inclusive(tmp_path):
    """나이가 정확히 상한(48h)이면 포함, 1초라도 넘으면 제외(경계 명확화)."""
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    now = 1783000000
    store.write_newsevent(
        NewsEvent(
            id="news-exact",
            source="synthetic",
            source_url="synthetic://x/exact",
            ts=now - NEWS_MAX_AGE_SECONDS,  # 정확히 상한
            title="exact",
            confidence=0.35,
            entities=["KADIZ"],
        )
    )
    store.write_newsevent(
        NewsEvent(
            id="news-overby1",
            source="synthetic",
            source_url="synthetic://x/overby1",
            ts=now - NEWS_MAX_AGE_SECONDS - 1,  # 상한 + 1초
            title="overby1",
            confidence=0.35,
            entities=["KADIZ"],
        )
    )
    facts = tools.news(store, "KADIZ", (now - 1800, now))
    ids = [f.data["id"] for f in facts]
    assert "news-exact" in ids
    assert "news-overby1" not in ids


def test_assess_news_sentence_excludes_stale_and_states_age(tmp_path):
    """assess() 응답의 뉴스 문장 — 오래된 기사는 빠지고, 남은 기사엔 경과시간이 명시된다."""
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    now = 1783000000
    store.write_newsevent(
        NewsEvent(
            id="news-fresh2",
            source="synthetic",
            source_url="synthetic://x/fresh2",
            ts=now - 3600,  # 1시간 전
            title="최근 KADIZ 동향 보도",
            confidence=0.35,
            entities=["KADIZ"],
        )
    )
    store.write_newsevent(
        NewsEvent(
            id="news-old2",
            source="synthetic",
            source_url="synthetic://x/old2",
            ts=now - 200 * 3600,  # 200시간 전(48h 훨씬 초과)
            title="아주 오래된 KADIZ 보도",
            confidence=0.35,
            entities=["KADIZ"],
        )
    )
    r = assess(store, "지금 KADIZ 뉴스 있어?", now=now)
    assert not r["no_evidence"]
    news_sents = [s for s in r["sentences"] if s["kind"] == "news"]
    assert news_sents, "뉴스 문장 없음"
    text = news_sents[0]["text"]
    assert "최근 KADIZ 동향 보도" in text
    assert "아주 오래된 KADIZ 보도" not in text
    assert (
        "전 보도" in text
    )  # 경과시간 명시("약 N시간 전 보도" 또는 "1시간 미만 전 보도")
    assert "news-old2" not in news_sents[0]["cites"]


def test_api_news_excludes_stale_articles(tmp_path, monkeypatch):
    """/api/news(server.app.api_news)도 copilot과 동일한 48h 상한을 적용한다(DR-0013 #10).

    이전에는 store.query_news() 전체를 무필터 반환해, 웹 뉴스 패널에 며칠 전 기사가
    무제한 노출됐다(코파일럿 경로만 48h 컷이 있던 불일치).
    """
    store = _store(tmp_path)
    monkeypatch.setattr(server_app, "DB_PATH", str(tmp_path / "p4.db"))
    monkeypatch.delenv("SKAI_NOW_ANCHOR", raising=False)
    now = 1783000000
    store.write_newsevent(
        NewsEvent(
            id="news-web-fresh",
            source="synthetic",
            source_url="synthetic://x/web-fresh",
            ts=now - 1000,  # 신선(48h 이내)
            title="fresh",
            confidence=0.35,
        )
    )
    store.write_newsevent(
        NewsEvent(
            id="news-web-stale",
            source="synthetic",
            source_url="synthetic://x/web-stale",
            ts=now - 50 * 3600,  # 50시간 전(48h 초과) → 제외 대상
            title="stale",
            confidence=0.35,
        )
    )
    monkeypatch.setattr(time, "time", lambda: float(now))  # 벽시계 now 고정
    ids = [d["id"] for d in server_app.api_news()]
    assert "news-web-fresh" in ids
    assert "news-web-stale" not in ids


def test_api_news_uses_now_anchor_for_age(tmp_path, monkeypatch):
    """replay 모드(SKAI_NOW_ANCHOR 설정)는 벽시계가 아니라 앵커 시각 기준으로 나이를 잰다.

    앵커 없이 벽시계로 계산하면 replay 재현성이 깨진다(항상 "지금" 실행 시각 기준이 되어
    같은 데모 DB라도 실행 시점마다 어느 기사가 살아남는지 달라짐).
    """
    store = _store(tmp_path)
    monkeypatch.setattr(server_app, "DB_PATH", str(tmp_path / "p4.db"))
    anchor = 1783000000
    monkeypatch.setenv("SKAI_NOW_ANCHOR", str(anchor))
    store.write_newsevent(
        NewsEvent(
            id="news-anchor-fresh",
            source="synthetic",
            source_url="synthetic://x/anchor-fresh",
            ts=anchor - 1000,  # 앵커 기준 신선(48h 이내)
            title="anchor-fresh",
            confidence=0.35,
        )
    )
    # 벽시계를 앵커보다 훨씬 미래로 돌려도(가상의 "실제 지금"), 앵커 기준 신선한 기사는
    # 여전히 포함되어야 한다(벽시계 기준이었다면 이미 상한을 초과해 제외됐을 것).
    monkeypatch.setattr(time, "time", lambda: float(anchor + 365 * 86400))
    ids = [d["id"] for d in server_app.api_news()]
    assert "news-anchor-fresh" in ids


def test_satellite_context_sentence_limited_to_query_window(tmp_path):
    """비상관 위성 통과 맥락 문장(KIND_SATELLITE)은 실제 질의창과 겹치는 통과만 인용한다.

    _parallel_read는 상관 계산용으로 ±CORRELATION_WINDOW_SECONDS(1h)만큼 넓혀 위성을
    읽으므로 counts.passes에는 창 밖 통과도 잡히지만("지금"=30분 창인데 최대 2.5시간치가
    잡힘), 서술 문장에는 실제 질의창과 겹치는 통과만 나와야 한다(시간 정직성).
    """
    store = _store(tmp_path)
    store.write_region(KADIZ_REGION)
    store.write_satellite(
        Satellite(norad_id="25544", name="ISS", source_url="http://tle")
    )
    now = 1783000000
    # 질의창(지금=[now-1800, now])과 실제로 겹치는 통과.
    store.write_orbitpass(
        OrbitPass(
            id="pass-25544-inwin",
            satellite_ref="25544",
            region_ref="KADIZ",
            start_ts=now - 200,
            end_ts=now - 100,
            max_elevation=60.0,
            ground_track=[[36, 127]],
            source_url="http://tle",
        )
    )
    # 확장 읽기 창(±1h)에는 걸리지만 실제 질의창 밖인 통과(이상징후가 없어 상관도 안 걸림).
    store.write_orbitpass(
        OrbitPass(
            id="pass-25544-outwin",
            satellite_ref="25544",
            region_ref="KADIZ",
            start_ts=now + 2000,
            end_ts=now + 2100,
            max_elevation=70.0,
            ground_track=[[36, 127]],
            source_url="http://tle",
        )
    )
    r = assess(store, "지금 KADIZ 근방 이상한 거 있어?", now=now)
    assert not r["no_evidence"]
    assert r["counts"]["passes"] == 2  # 확장 읽기는 둘 다 잡는다(상관 계산용, 불변)
    sat_sents = [s for s in r["sentences"] if s["kind"] == "satellite"]
    assert sat_sents, "위성 맥락 문장 없음"
    cites = sat_sents[0]["cites"]
    assert "pass-25544-inwin" in cites
    assert "pass-25544-outwin" not in cites  # 질의창 밖 통과는 서술에서 제외
