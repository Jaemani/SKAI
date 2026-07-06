"""copilot/assessment.py — P4 오케스트레이터 (DR-0006 핵심).

흐름(architecture.md §4):
  질의 → 파서(지역·시간창) → **툴 병렬 read** → 사실(Fact, 근거 id 보유) 확정
       → **사실→문장 조립(각 문장이 cites를 갖고 태어남)** → (옵션)LLM 서술만 다듬기
       → GenerateSituationAssessment(store 영속 + aggregates/cites 링크).

불변식(어떤 편의로도 우회 금지):
  1. 문장은 사실에서 조립된다 — LLM이 문장을 "생성"하지 않는다(citation 사후장식 방지).
  2. 각 문장은 근거 객체 id(cites)를 갖는다 — cites 없는 문장은 write_assessment가 거부.
  3. LLM(SKAI_EXPLAINER=claude)은 조립된 문장의 **서술만** 다듬고 cites 매핑은 불변,
     실패 시 원문 유지(DR-0004 폴백).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from anomaly import correlation
from anomaly.correlation import CORRELATION_WINDOW_SECONDS
from copilot import tools
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
from copilot.parser import ParsedQuery, parse_query
from copilot.region_summary import AipRegionSummarizer
from copilot.tools import Fact
from ontology.model import AssessmentSentence, SituationAssessment
from ontology.store_foundry import current_backend

# CORRELATION_WINDOW_SECONDS(±60분)의 SSOT는 상관 엔진(anomaly.correlation)이다. P5부터
# 상관 로직은 correlation.py 한 곳이 담당하고, copilot은 correlate()가 영속한
# correlated_with 링크를 **읽기만** 한다(중복 계산 제거 — DR-0007 결정 4). _parallel_read는
# 이 창만큼 위성 통과를 넓혀 읽어 상관 후보 통과를 놓치지 않는다.

# 문장 kind (섹션 분류·UI 배지·신뢰도 산출 분기)
KIND_SUMMARY = "summary"
KIND_ANOMALY = "anomaly"
KIND_SATELLITE = "satellite"
KIND_CORRELATION = "correlation"
KIND_WEATHER = "weather"
KIND_NEWS = "news"
KIND_FLIGHT = "flight"  # 필터/카운트/엔티티 항적 상세(DR-0011 신규 의도용)


def _fmt_hm(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")


def _fmt_full(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _fmt_news_age(seconds: float) -> str:
    """뉴스 기사 나이 → "약 N시간 전" 문구(시간 정직성 — 회고 보도임을 명시).

    음수(미래 ts, 이론상 발생 안 함)는 0으로 clamp. 1시간 미만은 "1시간 미만 전".
    """
    hours = max(seconds, 0) / 3600
    if hours < 1:
        return "1시간 미만 전"
    return f"약 {round(hours)}시간 전"


# resolved = 반증 증거 기반 자동 해소(복귀 관측으로 침묵 종료 — actions.scan_and_resolve).
_STATUS_KO = {
    "candidate": "미검토",
    "confirmed": "확인됨",
    "dismissed": "기각됨",
    "resolved": "해소됨",
}


def _anomaly_sentence_text(d: dict, confidence: float) -> str:
    """이상징후 유형별 서술(P5 5종). 근거·신뢰도·상태를 붙인다(무근거 서술 금지).

    비상 스쿽 외 유형(dropout·로이터링·군용기·위성 근접)은 attrs의 유형별 필드로
    서술한다. dropout은 교차확인 여부, 군용기는 저신뢰 판정임을 문장에 명시한다.
    """
    t = d.get("type")
    attrs = d.get("attrs") or {}
    synth = "[합성] " if d.get("is_synthetic") else ""
    status = _STATUS_KO.get(d.get("status"), d.get("status"))
    conf = f"신뢰도 {confidence:.2f}"
    nev = d.get("n_evidence", 0)
    who = d.get("callsign") or attrs.get("callsign") or d.get("id")

    if t == "emergency_squawk":
        return (
            f"{synth}항공기 {who}가 비상 스쿽 {d.get('squawk')}"
            f"({d.get('meaning') or '비상'})를 송신 — 상태 {status}, {conf}, "
            f"근거 관측 {nev}건."
        )
    if t == "adsb_dropout":
        cross = (
            "교차 확인"
            if attrs.get("cross_confirmed") is True
            else "교차 미확인(단정 금지)"
        )
        return (
            f"{synth}항공기 {who}의 ADS-B 신호가 민감구역 {attrs.get('region')}에서 "
            f"끊겼습니다(dropout, {cross}) — 상태 {status}, {conf}, 근거 관측 {nev}건."
        )
    if t == "loitering":
        return (
            f"{synth}항공기 {who}가 {attrs.get('duration_min')}분간 반복·선회(로이터링) "
            f"패턴(변위/경로 {attrs.get('ratio')}) — 상태 {status}, {conf}."
        )
    if t == "military_approach":
        return (
            f"{synth}군용 추정 항공기 {who}가 작전구역 {attrs.get('region')}에 접근"
            f"(근거: {attrs.get('mil_reason')}, 저신뢰 판정) — 상태 {status}, {conf}."
        )
    if t == "satellite_proximity":
        sat = attrs.get("sat_name") or attrs.get("norad_id")
        elev = float(attrs.get("max_elevation") or 0)
        return (
            f"위성 {sat}(NORAD {attrs.get('norad_id')})이 관심지역 상공을 최대앙각 "
            f"{elev:.0f}°로 근접 통과 — 상태 {status}, {conf}(정황)."
        )
    if t == "rapid_maneuver":
        kind = attrs.get("kind")
        fpm = attrs.get("peak_vertical_fpm") or 0
        acc = attrs.get("peak_accel_mps2") or 0
        if kind == "speed":
            what = f"속도 급변(가속 {acc} m/s²)"
        elif kind == "both":
            what = f"고도·속도 동시 급변({fpm} ft/min · {acc} m/s²)"
        else:
            what = f"고도 급변({fpm} ft/min)"
        return (
            f"{synth}항공기 {who}가 {what}의 급기동(민항 정상 범위 초과) — "
            f"상태 {status}, {conf}, 근거 관측 {nev}건."
        )
    return f"{synth}이상징후({t}) {who} — 상태 {status}, {conf}, 근거 {nev}건."


# ── 병렬 툴 read ──────────────────────────────────────────────────────────────
@dataclass
class ToolReads:
    """병렬 read 결과 묶음. 각 리스트 원소는 근거 id를 든 Fact."""

    flights: list[Fact]
    anomalies: list[Fact]
    passes: list[Fact]
    weather: list[Fact]
    news: list[Fact]


def _parallel_read(store, pq: ParsedQuery) -> ToolReads:
    """5개 툴을 스레드로 병렬 read(store 메서드는 호출마다 연결을 열어 스레드 안전).

    위성은 상관용으로 창을 ±CORRELATION_WINDOW만큼 넓혀 읽는다(미래/최근 통과 모두 후보).
    나머지는 파싱된 질의 창 그대로.
    """
    win = (pq.window_start, pq.window_end)
    sat_win = (
        pq.window_start - CORRELATION_WINDOW_SECONDS,
        pq.window_end + CORRELATION_WINDOW_SECONDS,
    )
    region = pq.region_id
    with ThreadPoolExecutor(max_workers=5) as ex:
        f_flights = ex.submit(tools.query_flights, store, region, win)
        f_anom = ex.submit(tools.query_anomalies, store, region, win)
        f_pass = ex.submit(tools.sat_passes, store, region, sat_win)
        f_weather = ex.submit(tools.weather, store, region, win)
        f_news = ex.submit(tools.news, store, region, win)
        return ToolReads(
            flights=f_flights.result(),
            anomalies=f_anom.result(),
            passes=f_pass.result(),
            weather=f_weather.result(),
            news=f_news.result(),
        )


# ── 사실 → 문장 조립 (cites 강제) ───────────────────────────────────────────
def _region_name(store, region_id: str) -> str:
    for r in store.query_regions():
        if r.id == region_id:
            return r.name
    return region_id


def _assemble_sentences(
    store, pq: ParsedQuery, reads: ToolReads
) -> list[AssessmentSentence]:
    """Fact들 → 문장 리스트. 각 문장은 근거 객체 id(cites)를 갖고 태어난다.

    섹션: 요약 → 이상징후 → 위성 상관/맥락 → 기상 → 뉴스. 사실이 없는 섹션은 건너뛴다
    (근거 없는 문장을 만들지 않는다 = 무근거 주장 원천 차단).
    """
    region_name = _region_name(store, pq.region_id)
    label = pq.window_label
    sents: list[AssessmentSentence] = []

    # ── 요약(헤드라인) — 인용은 요약이 집계하는 객체들 ──
    headline = _headline_sentence(region_name, label, reads)
    if headline is not None:
        sents.append(headline)

    # ── 이상징후 (문장당 이상징후 1건 + 근거) — 유형별 서술 ──
    for f in reads.anomalies:
        sents.append(
            AssessmentSentence(
                text=_anomaly_sentence_text(f.data, f.confidence),
                cites=f.cites,  # [Anomaly.id, *evidence 근거 객체 ids]
                confidence=round(f.confidence, 2),
                kind=KIND_ANOMALY,
            )
        )

    # ── 상관(persisted correlated_with) + 위성 통과 맥락 ──
    sents.extend(_assemble_correlations_and_satellite(store, reads, pq))

    # ── 기상 ──
    sents.extend(_weather_sentences(pq, reads))

    # ── 뉴스(저신뢰 OSINT) ──
    news_sent = _news_sentence(pq, region_name, reads)
    if news_sent is not None:
        sents.append(news_sent)

    return sents


# ── 섹션 조립 헬퍼(요약·기상·뉴스 SSOT — 상황요약과 포커스 의도가 공용) ─────────
def _headline_sentence(
    region_name: str, label: str, reads: ToolReads
) -> Optional[AssessmentSentence]:
    """정량 헤드라인 1문장. cites = 모든 이상징후 + 대표 항적 표본(최대 6).

    "N대" 전수 근거는 counts에 있고, 서브그래프/배지 가독을 위해 표본만 인용(무근거 아님 —
    같은 tool read 산출). 이상징후·항적이 하나도 없으면 None(무근거 헤드라인 금지).
    """
    n_anom = len(reads.anomalies)
    n_flight = len(reads.flights)
    n_synth = sum(1 for f in reads.anomalies if f.data.get("is_synthetic"))
    summary_cites: list[str] = []
    for f in reads.anomalies:
        summary_cites.extend(f.cites)
    flight_cites: list[str] = []
    for f in reads.flights:
        flight_cites.extend(f.cites[:1])
    summary_cites.extend(flight_cites[:6])
    if not summary_cites:
        return None
    summary_conf = (
        max((f.confidence for f in reads.anomalies), default=0.0)
        if reads.anomalies
        else 0.85
    )
    # 합성 라벨 전면 전파(DR-0013 #6) — 헤드라인 카운트에 합성 포함 여부를 사실대로 명시.
    synth_note = f"(합성 {n_synth}건 포함)" if n_synth else ""
    return AssessmentSentence(
        text=(
            f"{label} {region_name}에서 이상징후 {n_anom}건{synth_note}·항적 {n_flight}대를 "
            f"확인했습니다."
        ),
        cites=_dedup(summary_cites),
        confidence=round(summary_conf, 2),
        kind=KIND_SUMMARY,
    )


def _weather_sentences(pq: ParsedQuery, reads: ToolReads) -> list[AssessmentSentence]:
    """기상 문장(공항별 1건). stale는 질의창 밖임을 명시한다."""
    out: list[AssessmentSentence] = []
    for f in reads.weather:
        d = f.data
        cat = d.get("flight_category") or "—"
        ceil = f"{d['ceiling_ft']}ft" if d.get("ceiling_ft") is not None else "무제한"
        vis = f"{d['visibility_sm']}sm" if d.get("visibility_sm") is not None else "—"
        stale = (
            f" (관측 {round(abs(pq.now - f.ts) / 60)}분 {'전' if f.ts <= pq.now else '후'}, "
            f"질의창 밖)"
            if d.get("stale")
            else ""
        )
        out.append(
            AssessmentSentence(
                text=(
                    f"관심지역 기상은 {d.get('station')} {cat} · 실링 {ceil} · 시정 {vis}"
                    f"{stale} — ISR 임무 가용성 참고."
                ),
                cites=f.cites,
                confidence=round(f.confidence, 2),
                kind=KIND_WEATHER,
            )
        )
    return out


def _news_sentence(
    pq: ParsedQuery, region_name: str, reads: ToolReads
) -> Optional[AssessmentSentence]:
    """뉴스(저신뢰 OSINT) 요약 1문장. 뉴스가 없으면 None.

    예시 기사(최신 1건)의 보도 시각을 pq.now 기준 경과시간으로 명시한다(시간 정직성 —
    회고 보도가 "방금 일"처럼 읽히지 않도록). tools.news()가 이미 48h 상한으로 걸러
    들어온 기사만 대상이므로 여기서는 표기만 한다.
    """
    if not reads.news:
        return None
    top_fact = reads.news[0]
    top = top_fact.data
    age_txt = _fmt_news_age(pq.now - top_fact.ts)
    news_cites: list[str] = []
    for f in reads.news:
        news_cites.extend(f.cites)
    return AssessmentSentence(
        text=(
            f"OSINT(저신뢰 ≤0.4) {len(reads.news)}건이 {region_name}를 언급 — "
            f"예: '{_clip(top.get('title'), 48)}'({age_txt} 보도). "
            f"확증 아님, 하드 소스로 교차검증 요망."
        ),
        cites=_dedup(news_cites),
        confidence=round(max(f.confidence for f in reads.news), 2),
        kind=KIND_NEWS,
    )


def _assemble_correlations_and_satellite(
    store, reads: ToolReads, pq: ParsedQuery
) -> list[AssessmentSentence]:
    """상관 문장(persisted correlated_with) + 비상관 위성 통과 맥락 문장.

    correlation.py가 영속한 Anomaly —correlated_with→ OrbitPass/NewsEvent/Anomaly 링크를
    **읽어** 문장을 만든다(이 함수는 ±버킷 계산을 하지 않는다 — 로직 SSOT는 correlation.py).
    이상징후당 위성 상관 → 뉴스 상관("은닉 정황") → 이상징후↔이상징후 상관 순으로 낸다.
    상관에 안 걸린 질의창 통과는 맥락 1문장으로 요약한다.

    ⚠️ reads.passes는 _parallel_read가 상관 계산용으로 ±CORRELATION_WINDOW_SECONDS만큼
    넓혀 읽은 결과다(위 상관 블록은 이 확장 읽기가 필요 — 상관 후보를 놓치지 않기 위해).
    하지만 아래 "비상관 맥락" 서술 문장은 실제 질의창(pq.window_start~window_end)과
    겹치는 통과만 포함한다 — 아니면 "지금"(30분 창) 질의에도 최대 2.5시간치 통과가
    나열되어 시간 정직성을 해친다(팀 진단 2026-07-05).
    """
    out: list[AssessmentSentence] = []

    # 통과·뉴스 상세 조회용 맵(한 번만 읽어 재사용).
    sat_map = store.satellite_map()
    pass_info: dict[str, dict] = {}
    for p in store.query_orbitpasses():
        sat = sat_map.get(p.satellite_ref)
        pass_info[p.id] = {
            "id": p.id,
            "norad_id": p.satellite_ref,
            "name": sat.name if sat else p.satellite_ref,
            "start_ts": p.start_ts,
            "end_ts": p.end_ts,
            "max_elevation": p.max_elevation,
        }
    news_title = {n.id: n.title for n in store.query_news()}

    correlated_pass_ids: set[str] = set()
    for af in reads.anomalies:
        aid = af.data["id"]
        a_who = af.data.get("callsign") or aid
        a_ts = af.ts
        corrs = store.query_correlations(aid)
        pass_ids = [c["dst_id"] for c in corrs if c["dst_type"] == "OrbitPass"]
        news_ids = [c["dst_id"] for c in corrs if c["dst_type"] == "NewsEvent"]
        anom_ids = [c["dst_id"] for c in corrs if c["dst_type"] == "Anomaly"]

        # 1) 위성통과 상관(확증 아님) — 먼저(교차소스 provenance: 이상징후+통과 인용)
        if pass_ids:
            phrases = []
            for pid in pass_ids[:3]:
                d = pass_info.get(pid)
                correlated_pass_ids.add(pid)
                if d:
                    win = f"{_fmt_hm(d['start_ts'])}~{_fmt_hm(d['end_ts'])}"
                    phrases.append(
                        f"{d['name']}(NORAD {d['norad_id']}, {win} UTC, 최대앙각 "
                        f"{d['max_elevation']:.0f}°)"
                    )
            out.append(
                AssessmentSentence(
                    text=(
                        f"이상징후 {a_who}({_fmt_hm(a_ts)} UTC)는 correlated_with로 관심지역 "
                        f"상공 위성 통과 {len(pass_ids)}건과 시공간 상관: "
                        f"{', '.join(phrases)} — 확증 아님, 교차검증 요망."
                    ),
                    cites=[aid, *pass_ids],
                    confidence=0.5,  # 시공간 상관은 정황(중간 신뢰도)
                    kind=KIND_CORRELATION,
                )
            )

        # 2) 뉴스 상관("은닉 정황") — 콜사인 비의존 시공간 버킷(DR-0007 결정 4)
        if news_ids:
            titles = [_clip(news_title.get(nid, ""), 40) for nid in news_ids[:2]]
            out.append(
                AssessmentSentence(
                    text=(
                        f"이상징후 {a_who}는 같은 시공간 창에서 OSINT 뉴스 {len(news_ids)}건과 "
                        f"correlated_with로 연결됩니다(은닉 정황): "
                        f"'{'; '.join(t for t in titles if t)}' — 저신뢰, 확증 아님."
                    ),
                    cites=[aid, *news_ids],
                    confidence=0.4,
                    kind=KIND_CORRELATION,
                )
            )

        # 3) 이상징후 ↔ 이상징후 상관(교차소스 클러스터)
        if anom_ids:
            out.append(
                AssessmentSentence(
                    text=(
                        f"이상징후 {a_who}는 다른 이상징후 {len(anom_ids)}건과 시공간 상관"
                        f"(correlated_with) — 교차소스 정황 클러스터."
                    ),
                    cites=[aid, *anom_ids],
                    confidence=0.5,
                    kind=KIND_CORRELATION,
                )
            )

    # 상관에 안 걸린 질의창 통과 → 맥락 1문장(근거 = 그 통과 id들). 서술은 실제 질의창과
    # 겹치는 통과로 한정(확장 읽기 창 전체를 그대로 나열하지 않는다).
    win_start, win_end = pq.window_start, pq.window_end
    extras = [
        pf
        for pf in reads.passes
        if pf.data["id"] not in correlated_pass_ids
        and not (pf.data["start_ts"] > win_end or pf.data["end_ts"] < win_start)
    ]
    if extras:
        extras.sort(key=lambda pf: pf.data["start_ts"])
        sample = extras[0].data
        win = f"{_fmt_hm(sample['start_ts'])}~{_fmt_hm(sample['end_ts'])}"
        out.append(
            AssessmentSentence(
                text=(
                    f"질의 시각창 근방 관심지역 상공 위성 통과 {len(extras)}건 "
                    f"(예: {sample['name']} {win} UTC, 최대앙각 {sample['max_elevation']:.0f}°)."
                ),
                cites=_dedup([pf.data["id"] for pf in extras]),
                confidence=0.85,
                kind=KIND_SATELLITE,
            )
        )
    return out


def _dedup(ids: list[str]) -> list[str]:
    """순서 보존 중복 제거."""
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def _clip(s: Optional[str], n: int) -> str:
    s = s or ""
    return s if len(s) <= n else s[: n - 1] + "…"


# ── (옵션) LLM 서술 다듬기 — cites 불변, 실패 시 원문 (DR-0004) ──────────────
def _polish_narration(
    sentences: list[AssessmentSentence], claude_bin: str = "claude", timeout: int = 40
) -> tuple[list[AssessmentSentence], str]:
    """조립된 문장의 **서술만** LLM으로 다듬는다. cites·confidence·kind는 불변.

    한 번의 `claude -p` 호출로 번호 매긴 전체 문장을 넘겨 같은 개수의 번호 줄을 돌려받는다.
    개수 불일치·실패·타임아웃 → 전부 원문 유지(폴백, 데모 안전). 반환 (문장들, backend).
    """
    numbered = "\n".join(f"{i + 1}. {s.text}" for i, s in enumerate(sentences))
    prompt = (
        "너는 공중 ISR 상황분석 보조다. 아래 번호 매긴 문장들의 **서술만** 자연스러운 "
        "한국어로 다듬어라. 규칙: (1) 사실·수치·고유명사·시각을 절대 바꾸지 마라. "
        "(2) 문장을 추가/삭제/병합하지 마라 — 입력과 정확히 같은 개수의 줄을 같은 번호로 "
        "출력하라. (3) 근거·신뢰도 언급을 지어내지 마라. 머리말·마크다운 없이 번호 줄만.\n\n"
        f"{numbered}"
    )
    try:
        proc = subprocess.run(
            [claude_bin, "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            raise RuntimeError(f"claude 실패 rc={proc.returncode}")
        lines = [
            ln.strip()
            for ln in proc.stdout.strip().splitlines()
            if ln.strip() and ln.strip()[0].isdigit()
        ]
        if len(lines) != len(sentences):
            raise RuntimeError(
                f"문장 개수 불일치({len(lines)}≠{len(sentences)}) → 원문 유지"
            )
        polished: list[AssessmentSentence] = []
        for orig, ln in zip(sentences, lines):
            # "N. text" → text (번호 접두 제거). cites/confidence/kind는 원본 유지.
            text = ln.split(".", 1)[1].strip() if "." in ln else ln
            polished.append(
                AssessmentSentence(
                    text=text or orig.text,
                    cites=orig.cites,  # ← 매핑 불변(citation은 룰 측에 남는다)
                    confidence=orig.confidence,
                    kind=orig.kind,
                )
            )
        return polished, "claude"
    except Exception as e:  # 실패·타임아웃·불일치 → 원문 유지(폴백)
        print(f"[assessment] claude 서술 다듬기 실패 → 원문 유지: {e!r}")
        return sentences, "template(claude 폴백)"


# ── (옵션) AIP Logic 지역 상황요약 — 헤드라인 서술만 생성, cites 불변 ──────────
def _weather_one_liner(reads: ToolReads) -> Optional[str]:
    """AIP 입력용 기상 한 줄(선택 파라미터). 기상 사실이 없으면 None."""
    if not reads.weather:
        return None
    d = reads.weather[0].data
    cat = d.get("flight_category") or "—"
    ceil = f"{d['ceiling_ft']}ft" if d.get("ceiling_ft") is not None else "무제한"
    return f"{d.get('station')} {cat}·실링 {ceil}"


def _aip_region_summary(
    pq: ParsedQuery,
    reads: ToolReads,
    region_name: str,
    sentences: list[AssessmentSentence],
    summarizer: Optional[AipRegionSummarizer] = None,
) -> tuple[list[AssessmentSentence], str, Optional[str]]:
    """헤드라인(요약) 문장의 **서술만** AIP Logic region-situation-summary로 생성한다.

    반환 (sentences, backend, overall_assessment). 사실 확정·문장별 cites는 룰이 유지하고,
    AIP는 요약 서술만 만든다(citation 불변식 우회 없음):
      - 헤드라인(kind=summary) 문장이 있고 이상징후가 있을 때만 AIP 호출(0건이면 스킵 — 0건
        규칙 이중 안전 + 호출 절약).
      - AIP summary로 헤드라인 **text만** 교체하고 cites·kind는 불변(집계 Anomaly id 등 provenance
        보존). confidence는 AIP 종합값(이 백엔드 한정 — explainer aip와 동일 규율).
      - overallAssessment는 응답 메타로 반환(+요약 문장에 자연 반영). 실패·빈응답 → 원문 유지.

    호출자(assess)가 Foundry 스토어 게이트를 이미 통과시켜 부른다(anomalies2가 Foundry Object
    set이라 로컬 전용 모드에선 호출하지 않는다).
    """
    if not sentences or sentences[0].kind != KIND_SUMMARY:
        return (
            sentences,
            "template",
            None,
        )  # 요약 헤드라인 없음(무근거 헤드라인 방지 경로)
    anomaly_ids = [f.data["id"] for f in reads.anomalies]
    if not anomaly_ids:
        return sentences, "template", None  # 0건 → AIP 호출 없이 template 유지

    summarizer = summarizer or AipRegionSummarizer()
    result = summarizer.summarize(
        region_name,
        anomaly_ids,
        window_label=pq.window_label,
        weather_summary=_weather_one_liner(reads),
    )
    if result is None:  # 크리덴셜·네트워크·빈응답 등 → template 헤드라인 유지(폴백)
        return sentences, "template(aip 폴백)", None

    head = sentences[0]
    new_head = AssessmentSentence(
        text=result.summary,
        cites=head.cites,  # ← cites 불변(citation은 룰 측에 남는다)
        confidence=round(result.confidence, 2),
        kind=head.kind,
    )
    return [new_head, *sentences[1:]], "aip_logic", result.overall_assessment or None


# ── 종합 신뢰도 ──────────────────────────────────────────────────────────────
def _overall_confidence(sentences: list[AssessmentSentence]) -> float:
    """문장 신뢰도의 평균 = 종합 신뢰도. 저신뢰 뉴스가 있으면 자연히 내려간다(투명).

    요약 문장은 다른 문장의 재요약이라 평균에서 제외(이중계상 방지).
    """
    body = [s for s in sentences if s.kind != KIND_SUMMARY]
    if not body:
        return round(sentences[0].confidence, 2) if sentences else 0.0
    return round(sum(s.confidence for s in body) / len(body), 2)


# ── cites 인덱스(프론트 배지·하이라이트용) ───────────────────────────────────
def _cited_objects(store, sentences: list[AssessmentSentence]) -> dict:
    """cites에 쓰인 모든 객체 id → 표시용 상세(type·label·좌표·원 소스 링크).

    프론트가 배지를 그리고 클릭 시 지도 하이라이트/원 소스로 갈 수 있게 한 번에 내려준다.
    """
    all_ids: set[str] = set()
    for s in sentences:
        all_ids.update(s.cites)
    idx: dict[str, dict] = {}
    ac_map = store.aircraft_map()
    sat_map = store.satellite_map()
    for cid in all_ids:
        obj = _resolve_object(store, cid, ac_map, sat_map)
        if obj:
            idx[cid] = obj
    return idx


def _first_evidence_source_url(
    store, anomaly_id: str, ac_map: dict, sat_map: dict
) -> str:
    """Anomaly의 첫 evidence 객체 source_url(원 소스 역추적). 없으면 "".

    Anomaly는 파생 객체라 자체 source_url이 없다 — 화면의 "원 소스" 링크가 늘 공백이었다
    (DR-0013 #9). evidenced_by 근거 중 첫 건(Observation 또는 P5 비-Observation 근거)의
    source_url을 대신 낸다. 근거는 Anomaly가 아니므로 _resolve_object 재귀는 1단만 내려간다.
    """
    evidence = store.query_evidence(anomaly_id)
    if not evidence:
        return ""
    obj = _resolve_object(store, evidence[0]["id"], ac_map, sat_map)
    return (obj or {}).get("source_url") or ""


def _resolve_object(store, cid: str, ac_map: dict, sat_map: dict) -> Optional[dict]:
    """cite id → {type, label, lat, lon, source_url, ...} (없으면 None).

    Anomaly는 추가로 is_synthetic(합성 라벨 전파, DR-0013 #6)을 포함한다.
    """
    if cid.startswith("anomaly-"):
        a = store.get_anomaly(cid)
        if not a:
            return None
        who = (a.attrs or {}).get("callsign") or a.type
        return {
            "type": "Anomaly",
            "label": f"이상징후 {who}"
            + (
                f" sq{a.attrs.get('squawk')}"
                if a.attrs and a.attrs.get("squawk")
                else ""
            ),
            "lat": a.lat,
            "lon": a.lon,
            "source_url": _first_evidence_source_url(store, cid, ac_map, sat_map),
            "status": a.status,
            "is_synthetic": bool((a.attrs or {}).get("is_synthetic")),
        }
    if cid.startswith("pass-"):
        for p in store.query_orbitpasses():
            if p.id == cid:
                sat = sat_map.get(p.satellite_ref)
                mid = (
                    p.ground_track[len(p.ground_track) // 2]
                    if p.ground_track
                    else [None, None]
                )
                return {
                    "type": "OrbitPass",
                    "label": f"🛰 {sat.name if sat else p.satellite_ref} "
                    f"{_fmt_hm(p.start_ts)}~{_fmt_hm(p.end_ts)}",
                    "lat": mid[0],
                    "lon": mid[1],
                    "source_url": p.source_url,
                }
        return None
    if cid.startswith("wx-"):
        for w in store.query_weather_latest():
            if w.id == cid:
                return {
                    "type": "WeatherState",
                    "label": f"기상 {w.station} {w.flight_category or ''}".strip(),
                    "lat": w.lat,
                    "lon": w.lon,
                    "source_url": w.source_url,
                }
        return None
    if cid.startswith("news-"):
        for n in store.query_news():
            if n.id == cid:
                return {
                    "type": "NewsEvent",
                    "label": f"📰 {_clip(n.title, 40)}",
                    "lat": n.lat,
                    "lon": n.lon,
                    "source_url": n.source_url,
                    "confidence": n.confidence,
                }
        return None
    # 접두어 없음 → Observation (f"{icao24}-{ts}")
    o = store.get_observation(cid)
    if not o:
        return None
    ac = ac_map.get(o.aircraft_ref)
    return {
        "type": "Observation",
        "label": f"관측 {(ac.callsign if ac else None) or o.aircraft_ref}"
        + (f" sq{o.squawk}" if o.squawk else ""),
        "lat": o.lat,
        "lon": o.lon,
        "source_url": o.source_url,
    }


# ── 의도별 조립(DR-0011) — 의도가 read·조립을 라우팅, cites 불변 ────────────────
def _assemble_count(
    store, pq: ParsedQuery, reads: ToolReads, slots: dict
) -> list[AssessmentSentence]:
    """집계(count) — 대상 종류를 세고 근거 객체를 인용한다. 0건이면 빈 리스트(→ no_evidence).

    각 대상 Fact가 근거 객체 id(cites)를 들고 있어, "N건"은 그 id들을 인용해 근거가 붙는다
    (무근거 카운트 금지). 군용은 필터 집계(query_flights military=True)로 센다.
    """
    target = (slots or {}).get("target", "flights")
    region_name = _region_name(store, pq.region_id)
    label = pq.window_label
    win = (pq.window_start, pq.window_end)

    if target == "anomalies":
        facts, noun, unit = reads.anomalies, "이상징후", "건"
        conf = round(max((f.confidence for f in facts), default=0.0), 2)
    elif target == "passes":
        facts, noun, unit, conf = reads.passes, "위성 통과", "건", 0.85
    elif target == "news":
        facts, noun, unit = reads.news, "OSINT 뉴스", "건"
        conf = round(max((f.confidence for f in facts), default=0.0), 2)
    elif target == "military":
        facts = tools.query_flights(store, pq.region_id, win, military=True)
        noun, unit = "군용 추정 항적", "대"
        conf = round(
            max((f.data.get("military_confidence", 0.0) for f in facts), default=0.0), 2
        )
    else:  # flights
        facts, noun, unit, conf = reads.flights, "항적", "대", 0.9

    cites = _dedup([c for f in facts for c in f.cites])
    if not facts or not cites:
        return []
    particle = "을" if unit == "건" else "를"  # 받침 유무에 맞춘 목적격 조사
    return [
        AssessmentSentence(
            text=(
                f"{label} {region_name}에서 {noun} {len(facts)}{unit}{particle} 확인했습니다"
                f"(근거 객체 {len(cites)}건 인용)."
            ),
            cites=cites,
            confidence=conf,
            kind=KIND_SUMMARY,
        )
    ]


def _filter_desc(slots: dict) -> str:
    """필터 슬롯 → 사람이 읽는 조건 문구(투명성)."""
    parts: list[str] = []
    mil = slots.get("military")
    if mil is True:
        parts.append("군용 추정")
    elif mil is False:
        parts.append("민간")
    if slots.get("origin_country"):
        parts.append(f"{slots['origin_country']} 국적")
    if slots.get("operator"):
        parts.append(f"operator '{slots['operator']}'")
    if slots.get("aircraft_type"):
        parts.append(f"기종 '{slots['aircraft_type']}'")
    return " · ".join(parts) if parts else "지정 조건"


def _flight_detail_sentence(f: Fact) -> AssessmentSentence:
    """항적 1건 상세 문장(필터·엔티티용). cites = [Observation.id]."""
    d = f.data
    who = d.get("callsign") or d.get("icao24")
    origin = d.get("origin_country") or "국적 미상"
    mil = "군용 추정" if d.get("is_military") else "민간"
    loc = (
        f"{d['lat']:.2f}, {d['lon']:.2f}"
        if d.get("lat") is not None and d.get("lon") is not None
        else "위치 미상"
    )
    conf = d.get("military_confidence") if d.get("is_military") else f.confidence
    return AssessmentSentence(
        text=f"{who} — {origin}, {mil}, 위치 {loc}.",
        cites=f.cites,
        confidence=round(conf or f.confidence, 2),
        kind=KIND_FLIGHT,
    )


def _assemble_filter(store, pq: ParsedQuery, slots: dict) -> list[AssessmentSentence]:
    """필터(filter) — 조건에 맞는 항적을 헤드라인 + 최대 8건 상세로 조립. 0건이면 no_evidence.

    조건 read는 query_flights의 슬롯 필터(military/origin/operator/type)로 좁힌다. 각 항적은
    근거 관측(Observation) 1건을 인용한다(무근거 나열 금지).
    """
    region_name = _region_name(store, pq.region_id)
    label = pq.window_label
    win = (pq.window_start, pq.window_end)
    facts = tools.query_flights(
        store,
        pq.region_id,
        win,
        military=slots.get("military"),
        origin_country=slots.get("origin_country"),
        operator=slots.get("operator"),
        aircraft_type=slots.get("aircraft_type"),
    )
    if not facts:
        return []
    desc = _filter_desc(slots)
    lead_cites = _dedup([c for f in facts for c in f.cites])[:12]
    out = [
        AssessmentSentence(
            text=f"{label} {region_name}에서 {desc} 조건에 맞는 항적 {len(facts)}대.",
            cites=lead_cites,
            confidence=0.85,
            kind=KIND_SUMMARY,
        )
    ]
    for f in facts[:8]:
        out.append(_flight_detail_sentence(f))
    return out


def _salient_by_kind(reads: ToolReads, kind: Optional[str]) -> Optional[Fact]:
    """지시어(id 없음) 시 종류별로 가장 두드러진 Fact를 고른다(결정적)."""
    if kind == "satellite":
        return reads.passes[0] if reads.passes else None
    if kind == "weather":
        return reads.weather[0] if reads.weather else None
    if kind == "news":
        return reads.news[0] if reads.news else None
    if kind == "flight":
        return reads.flights[0] if reads.flights else None
    # anomaly 또는 미상 → 최고신뢰 이상징후, 없으면(미상) 첫 항적.
    if reads.anomalies:
        return max(reads.anomalies, key=lambda f: f.confidence)
    if kind is None and reads.flights:
        return reads.flights[0]
    return None


def _entity_sentences(store, pq: ParsedQuery, fact: Fact) -> list[AssessmentSentence]:
    """단건 엔티티 Fact → 설명 문장(kind별). cites = fact.cites(엔티티 + provenance)."""
    d = fact.data
    if fact.kind == "anomaly":
        return [
            AssessmentSentence(
                text="요청하신 이상징후 — "
                + _anomaly_sentence_text(d, fact.confidence),
                cites=fact.cites,
                confidence=round(fact.confidence, 2),
                kind=KIND_ANOMALY,
            )
        ]
    if fact.kind == "satellite":
        win = f"{_fmt_hm(d['start_ts'])}~{_fmt_hm(d['end_ts'])}"
        return [
            AssessmentSentence(
                text=(
                    f"위성 {d.get('name')}(NORAD {d.get('norad_id')}) — 관심지역 상공 통과 "
                    f"{win} UTC, 최대앙각 {float(d.get('max_elevation') or 0):.0f}°(정황)."
                ),
                cites=fact.cites,
                confidence=round(fact.confidence, 2),
                kind=KIND_SATELLITE,
            )
        ]
    if fact.kind == "weather":
        return _weather_sentences(pq, ToolReads([], [], [], [fact], []))
    if fact.kind == "news":
        age_txt = _fmt_news_age(pq.now - fact.ts)
        return [
            AssessmentSentence(
                text=(
                    f"OSINT 뉴스(저신뢰 {d.get('confidence'):.2f}) — "
                    f"'{_clip(d.get('title'), 60)}'({age_txt} 보도). 확증 아님, 교차검증 요망."
                ),
                cites=fact.cites,
                confidence=round(fact.confidence, 2),
                kind=KIND_NEWS,
            )
        ]
    # flight
    return [_flight_detail_sentence(fact)]


def _assemble_entity(
    store,
    pq: ParsedQuery,
    reads: ToolReads,
    slots: dict,
    focus_id: Optional[str],
) -> list[AssessmentSentence]:
    """엔티티 설명(entity_explain) — id/지시어로 단건을 골라 설명. 못 찾으면 no_evidence.

    id(질의문 또는 focus_id)가 있으면 그 객체를, 없으면 지시어의 종류에서 가장 두드러진
    것을 고른다. 문장은 그 객체와 provenance를 인용한다.
    """
    entity_id = focus_id or (slots or {}).get("entity_id")
    kind = (slots or {}).get("entity_kind")
    if entity_id:
        # 명시 id → 그 객체만. 해소 안 되면 정직하게 no_evidence(엉뚱한 객체 대체 금지).
        fact = tools.get_entity_fact(store, entity_id)
        if fact is None:
            return []
    else:
        # 지시어(id 없음) → 종류에서 가장 두드러진 것.
        fact = _salient_by_kind(reads, kind)
    if fact is None:
        return []
    return _entity_sentences(store, pq, fact)


def _assemble_why(
    store,
    pq: ParsedQuery,
    reads: ToolReads,
    slots: dict,
    focus_id: Optional[str],
) -> list[AssessmentSentence]:
    """근거 설명(why) — 이상징후가 왜 이상한지 유형 서술 + 저장된 판단 근거를 인용해 낸다.

    id가 이상징후면 그것을, 아니면 가장 두드러진 이상징후를 설명한다. 이상징후가 없으면
    no_evidence(무근거로 "왜"를 지어내지 않는다).
    """
    entity_id = focus_id or (slots or {}).get("entity_id")
    if entity_id and entity_id.startswith("anomaly-"):
        # 명시 이상징후 id → 그것만. 해소 안 되면 정직하게 no_evidence.
        fact = tools.get_entity_fact(store, entity_id)
        if fact is None:
            return []
    elif reads.anomalies:
        # id 없음("왜") → 가장 두드러진 이상징후의 근거를 설명.
        fact = max(reads.anomalies, key=lambda f: f.confidence)
    else:
        fact = None
    if fact is None or fact.kind != "anomaly":
        return []
    d = fact.data
    sents = [
        AssessmentSentence(
            text="왜 이상징후인가 — " + _anomaly_sentence_text(d, fact.confidence),
            cites=fact.cites,
            confidence=round(fact.confidence, 2),
            kind=KIND_ANOMALY,
        )
    ]
    expl = (d.get("explanation") or "").strip()
    if expl:
        sents.append(
            AssessmentSentence(
                text=f"판단 근거: {expl}",
                cites=fact.cites,
                confidence=round(fact.confidence, 2),
                kind=KIND_ANOMALY,
            )
        )
    return sents


def _assemble_for_intent(
    store,
    pq: ParsedQuery,
    reads: ToolReads,
    intent_obj: Intent,
    focus_id: Optional[str],
) -> list[AssessmentSentence]:
    """의도 → 조립 라우팅. situation_summary·미지 의도는 현행 전체 조립으로 폴백."""
    it = intent_obj.intent
    slots = intent_obj.slots or {}
    if it == INTENT_COUNT:
        return _assemble_count(store, pq, reads, slots)
    if it == INTENT_FILTER:
        return _assemble_filter(store, pq, slots)
    if it == INTENT_ENTITY_EXPLAIN:
        return _assemble_entity(store, pq, reads, slots, focus_id)
    if it == INTENT_WHY:
        return _assemble_why(store, pq, reads, slots, focus_id)
    if it == INTENT_CORRELATION:
        return _assemble_correlations_and_satellite(store, reads, pq)
    if it == INTENT_WEATHER:
        return _weather_sentences(pq, reads)
    if it == INTENT_NEWS:
        s = _news_sentence(pq, _region_name(store, pq.region_id), reads)
        return [s] if s is not None else []
    return _assemble_sentences(store, pq, reads)  # situation_summary


# ── 공개 진입점 ──────────────────────────────────────────────────────────────
def assess(
    store,
    query: str,
    now: Optional[int] = None,
    explainer: Optional[str] = None,
    focus_id: Optional[str] = None,
    intent: Optional[Intent] = None,
) -> dict:
    """자연어 질의 → 의도 분류 → 의도별 조립 → SituationAssessment 생성·영속 → 응답 dict.

    이것이 GenerateSituationAssessment 액션(ontology.md §3)의 코드 구현이다:
    파싱 → 의도분류(DR-0011) → 병렬 read → 의도별 사실→문장 조립(cites 강제) →
    (옵션)서술 다듬기 → store 영속. 근거 사실이 하나도 없으면 no_evidence 응답(무근거 주장
    대신 "못 찾음" 투명 보고 — cites 없는 문장을 만들지 않으므로 Assessment는 생성되지 않는다).

    focus_id: 프론트가 선택한 객체 id(엔티티/why 의도의 지시 대상). 질의문에 박힌 id보다 우선.
    intent: 미리 분류된 Intent를 주입하면 재분류를 건너뛴다(테스트·재현). 기본은 자동 분류.
    """
    pq = parse_query(query, now=now)
    # 의도 분류(규칙 1차 + SKAI_COPILOT_LLM=claude 시 모호 질의만 claude 폴백). 결정적 기본.
    intent_obj = (
        intent
        if isinstance(intent, Intent)
        else classify(
            query,
            now=pq.now,
            llm=os.environ.get("SKAI_COPILOT_LLM"),
            focus_id=focus_id,
        )
    )
    reads = _parallel_read(store, pq)

    # 상관 영속 — 질의 범위 이상징후를 위성통과·뉴스·다른 이상징후와 시공간 버킷으로 잇고
    # correlated_with 링크를 저장한다. 로직 SSOT는 correlation.py, 아래 조립은 그 링크를
    # 읽기만 한다(교차소스 내러티브를 온톨로지 그래프에 남김 — DR-0007 결정 4).
    scope_anomalies = [
        a
        for a in (store.get_anomaly(f.data["id"]) for f in reads.anomalies)
        if a is not None
    ]
    correlation.correlate(store, scope_anomalies, now=pq.now)

    sentences = _assemble_for_intent(store, pq, reads, intent_obj, focus_id)

    region_name = _region_name(store, pq.region_id)
    counts = {
        "flights": len(reads.flights),
        "anomalies": len(reads.anomalies),
        "passes": len(reads.passes),
        "weather": len(reads.weather),
        "news": len(reads.news),
    }
    intent_meta = {
        "confidence": intent_obj.confidence,
        "matched": intent_obj.matched,
        "backend": intent_obj.backend,
    }

    # 근거 사실이 하나도 없음 → 무근거 주장 대신 투명한 "못 찾음"(Assessment 미생성).
    if not sentences:
        return {
            "assessment_id": None,
            "no_evidence": True,
            "query": query,
            "intent": intent_obj.intent,
            "slots": intent_obj.slots,
            "intent_meta": intent_meta,
            "region": {"id": pq.region_id, "name": region_name},
            "window": _window_dict(pq),
            "summary": (
                f"{pq.window_label} {region_name}에서 요청하신 근거 객체를 찾지 못했습니다"
                f"(의도={intent_obj.intent}) — 무근거 주장 대신 '해당 없음'을 보고합니다."
            ),
            "sentences": [],
            "confidence": 0.0,
            "produced_by": "template",
            "created_at": pq.now,
            "counts": counts,
            "cited_objects": {},
        }

    # (옵션) 서술 백엔드 — cites 불변, 실패 시 원문(DR-0004 폴백). 기본 template=결정적.
    # SKAI_COPILOT_LLM(신규) 우선, 없으면 SKAI_EXPLAINER(하위호환).
    #   claude → 모든 문장 서술 다듬기(_polish_narration).
    #   aip    → situation_summary 헤드라인 서술을 AIP Logic region-situation-summary로 생성.
    #            (Foundry Anomaly Object set을 종합하므로 Foundry 스토어일 때만 — 로컬 전용 모드는
    #             template 유지. 그 외 의도는 template — 이번 배선은 상황요약 헤드라인 한정.)
    backend = "template"
    overall_assessment: Optional[str] = None
    name = (
        explainer
        or os.environ.get("SKAI_COPILOT_LLM")
        or os.environ.get("SKAI_EXPLAINER", "template")
    ).lower()
    if name == "claude":
        sentences, backend = _polish_narration(sentences)
    elif name == "aip":
        if (
            intent_obj.intent == INTENT_SITUATION_SUMMARY
            and current_backend() == "foundry"
        ):
            sentences, backend, overall_assessment = _aip_region_summary(
                pq, reads, region_name, sentences
            )
        else:
            # aip 요청이나 비적용(비요약 의도·로컬 스토어) → template 헤드라인 유지(정직 라벨).
            backend = "template(aip 미적용)"

    overall = _overall_confidence(sentences)
    created_at = pq.now
    # id에 질의 해시를 넣어 같은 초의 서로 다른 질의가 충돌하지 않게 한다(각 질의 = 별 인텔
    # 객체). 같은 질의·같은 now는 같은 id → 재실행이 덮어씀(멱등, upsert 정합).
    qhash = hashlib.md5(query.encode("utf-8")).hexdigest()[:6]
    assessment = SituationAssessment(
        id=f"assess-{pq.region_id}-{created_at}-{qhash}",
        region_ref=pq.region_id,
        window_start=pq.window_start,
        window_end=pq.window_end,
        query=query,
        summary=sentences[0].text,
        sentences=sentences,
        confidence=overall,
        produced_by=backend,
        created_at=created_at,
        window_label=pq.window_label,
        attrs={
            "counts": counts,
            "matched_region_alias": pq.matched_region_alias,
            "intent": intent_obj.intent,
            "slots": intent_obj.slots,
            # AIP 상황요약 한줄판정(있을 때만) — 프론트 후속 표시용 메타.
            **(
                {"overall_assessment": overall_assessment} if overall_assessment else {}
            ),
        },
    )
    # GenerateSituationAssessment 액션 — 문장별 cites 강제 + aggregates/cites 링크 영속.
    store.write_assessment(assessment)

    return {
        "assessment_id": assessment.id,
        "no_evidence": False,
        "query": query,
        "intent": intent_obj.intent,
        "slots": intent_obj.slots,
        "intent_meta": intent_meta,
        "region": {"id": pq.region_id, "name": region_name},
        "window": _window_dict(pq),
        "summary": assessment.summary,
        "sentences": [
            {
                "text": s.text,
                "cites": s.cites,
                "confidence": s.confidence,
                "kind": s.kind,
            }
            for s in sentences
        ],
        "confidence": overall,
        "produced_by": backend,
        "overall_assessment": overall_assessment,  # AIP 한줄판정(없으면 None)
        "created_at": created_at,
        "counts": counts,
        "cited_objects": _cited_objects(store, sentences),
    }


def build_subgraph(store, assessment_id: str) -> Optional[dict]:
    """Assessment 중심 서브그래프(노드·엣지) — 프론트 자체 SVG 렌더용(DR-0006).

    깊이(온톨로지 다중홉 시연): SituationAssessment
      —aggregates→ Anomaly —evidenced_by→ Observation / —involves→ Aircraft
      —cites→ OrbitPass / WeatherState / NewsEvent.
    반환 {center, nodes:[{id,type,label,lat,lon,...}], edges:[{src,dst,link_type}]} 또는 None.
    """
    a = store.get_assessment(assessment_id)
    if a is None:
        return None
    ac_map = store.aircraft_map()
    sat_map = store.satellite_map()
    # Aircraft 노드에 "지도에서 보기"를 붙이려면 좌표가 필요하다(엔티티 자체엔 좌표가 없음) —
    # 항공기별 최신 관측 좌표로 보강(DR-0013 #9). 최신 관측이 없으면 None(지도 버튼 생략).
    latest_obs_by_ac = {o.aircraft_ref: o for o in store.query_latest_observations()}
    nodes: dict[str, dict] = {
        a.id: {
            "id": a.id,
            "type": "SituationAssessment",
            "label": f"상황평가 · {a.window_label}",
            "lat": None,
            "lon": None,
            "confidence": a.confidence,
        }
    }
    edges: list[dict] = []
    links = store.query_assessment_links(assessment_id)
    # 1홉: Assessment → (aggregates/cites) → 근거 객체
    for lk in links:
        dst_id = lk["dst_id"]
        obj = _resolve_object(store, dst_id, ac_map, sat_map)
        if not obj:
            continue
        nodes.setdefault(dst_id, {"id": dst_id, **obj})
        edges.append({"src": a.id, "dst": dst_id, "link_type": lk["link_type"]})
    # 2홉: 각 Anomaly → evidenced_by Observation / involves Aircraft (provenance 깊이)
    for lk in links:
        if lk["dst_type"] != "Anomaly":
            continue
        aid = lk["dst_id"]
        for obs_id in store.query_evidence_ids(aid):
            obj = _resolve_object(store, obs_id, ac_map, sat_map)
            if obj:
                nodes.setdefault(obs_id, {"id": obs_id, **obj})
                edges.append({"src": aid, "dst": obs_id, "link_type": "evidenced_by"})
        for icao in store.query_involves_ids(aid):
            nid = f"ac-{icao}"
            ac = ac_map.get(icao)
            latest = latest_obs_by_ac.get(icao)
            nodes.setdefault(
                nid,
                {
                    "id": nid,
                    "type": "Aircraft",
                    "label": f"✈ {(ac.callsign if ac else None) or icao}",
                    "lat": latest.lat if latest else None,
                    "lon": latest.lon if latest else None,
                },
            )
            edges.append({"src": aid, "dst": nid, "link_type": "involves"})
    # 3홉: 이상징후 ↔ (다른 이상징후 / OrbitPass / NewsEvent) correlated_with 링크.
    # "은닉 정황" 내러티브 = dropout Anomaly ─correlated_with→ OrbitPass·NewsEvent 서브그래프.
    anomaly_node_ids = [nid for nid, n in nodes.items() if n.get("type") == "Anomaly"]
    for aid in anomaly_node_ids:
        for c in store.query_correlations(aid):
            did = c["dst_id"]
            obj = _resolve_object(store, did, ac_map, sat_map)
            if not obj:
                continue
            nodes.setdefault(did, {"id": did, **obj})
            edges.append({"src": aid, "dst": did, "link_type": "correlated_with"})
    return {"center": a.id, "nodes": list(nodes.values()), "edges": edges}


def assessment_to_summary_dict(a: SituationAssessment) -> dict:
    """저장된 SituationAssessment → 목록/재조회용 요약 dict(문장별 cites 포함)."""
    return {
        "assessment_id": a.id,
        "query": a.query,
        "region": a.region_ref,
        "window": {
            "start": a.window_start,
            "end": a.window_end,
            "label": a.window_label,
        },
        "summary": a.summary,
        "sentences": [
            {
                "text": s.text,
                "cites": s.cites,
                "confidence": s.confidence,
                "kind": s.kind,
            }
            for s in a.sentences
        ],
        "confidence": a.confidence,
        "produced_by": a.produced_by,
        "created_at": a.created_at,
        "attrs": a.attrs,
    }


def _window_dict(pq: ParsedQuery) -> dict:
    """파싱된 시간창을 응답에 노출(투명성 — 어떻게 해석됐나)."""
    return {
        "start": pq.window_start,
        "end": pq.window_end,
        "seconds": pq.window_seconds,
        "label": pq.window_label,
        "start_iso": _fmt_full(pq.window_start),
        "end_iso": _fmt_full(pq.window_end),
        "matched_region_alias": pq.matched_region_alias,
        "matched_window_phrase": pq.matched_window_phrase,
        "defaulted": pq.fields_defaulted,
    }
