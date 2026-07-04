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
from copilot.parser import ParsedQuery, parse_query
from copilot.tools import Fact
from ontology.model import AssessmentSentence, SituationAssessment

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


def _fmt_hm(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H:%M")


def _fmt_full(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


_STATUS_KO = {"candidate": "미검토", "confirmed": "확인됨", "dismissed": "기각됨"}


def _anomaly_sentence_text(d: dict, confidence: float) -> str:
    """이상징후 유형별 서술(P5 5종). 근거·신뢰도·상태를 붙인다(무근거 서술 금지).

    비상 스쿽 외 유형(dropout·로이터링·군용기·위성 근접)은 attrs의 유형별 필드로
    서술한다. dropout은 교차확인 여부, 군용기는 저신뢰 휴리스틱임을 문장에 명시한다.
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
            f"(근거: {attrs.get('mil_reason')}, 저신뢰 휴리스틱) — 상태 {status}, {conf}."
        )
    if t == "satellite_proximity":
        sat = attrs.get("sat_name") or attrs.get("norad_id")
        elev = float(attrs.get("max_elevation") or 0)
        return (
            f"위성 {sat}(NORAD {attrs.get('norad_id')})이 관심지역 상공을 최대앙각 "
            f"{elev:.0f}°로 근접 통과 — 상태 {status}, {conf}(정황)."
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
    n_anom = len(reads.anomalies)
    n_flight = len(reads.flights)
    # 요약 cites = 모든 이상징후 + 대표 항적 표본(최대 6). "N대" 전수 근거는 counts에 있고,
    # 서브그래프/배지 가독을 위해 표본만 인용(무근거 아님 — 같은 tool read 산출).
    summary_cites: list[str] = []
    for f in reads.anomalies:
        summary_cites.extend(f.cites)
    flight_cites: list[str] = []
    for f in reads.flights:
        flight_cites.extend(f.cites[:1])
    summary_cites.extend(flight_cites[:6])
    if summary_cites:  # 이상징후/항적이 있을 때만 정량 요약(근거 있음)
        summary_conf = (
            max((f.confidence for f in reads.anomalies), default=0.0)
            if reads.anomalies
            else 0.85
        )
        sents.append(
            AssessmentSentence(
                text=(
                    f"{label} {region_name}에서 이상징후 {n_anom}건·항적 {n_flight}대를 "
                    f"확인했습니다."
                ),
                cites=_dedup(summary_cites),
                confidence=round(summary_conf, 2),
                kind=KIND_SUMMARY,
            )
        )

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
    sents.extend(_assemble_correlations_and_satellite(store, reads))

    # ── 기상 ──
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
        sents.append(
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

    # ── 뉴스(저신뢰 OSINT) ──
    if reads.news:
        top = reads.news[0].data
        news_cites: list[str] = []
        for f in reads.news:
            news_cites.extend(f.cites)
        sents.append(
            AssessmentSentence(
                text=(
                    f"OSINT(저신뢰 ≤0.4) {len(reads.news)}건이 {region_name}를 언급 — "
                    f"예: '{_clip(top.get('title'), 48)}'. 확증 아님, 하드 소스로 교차검증 요망."
                ),
                cites=_dedup(news_cites),
                confidence=round(max(f.confidence for f in reads.news), 2),
                kind=KIND_NEWS,
            )
        )

    return sents


def _assemble_correlations_and_satellite(
    store, reads: ToolReads
) -> list[AssessmentSentence]:
    """상관 문장(persisted correlated_with) + 비상관 위성 통과 맥락 문장.

    correlation.py가 영속한 Anomaly —correlated_with→ OrbitPass/NewsEvent/Anomaly 링크를
    **읽어** 문장을 만든다(이 함수는 ±버킷 계산을 하지 않는다 — 로직 SSOT는 correlation.py).
    이상징후당 위성 상관 → 뉴스 상관("은닉 정황") → 이상징후↔이상징후 상관 순으로 낸다.
    상관에 안 걸린 질의창 통과는 맥락 1문장으로 요약한다.
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

    # 상관에 안 걸린 질의창 통과 → 맥락 1문장(근거 = 그 통과 id들)
    extras = [pf for pf in reads.passes if pf.data["id"] not in correlated_pass_ids]
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


def _resolve_object(store, cid: str, ac_map: dict, sat_map: dict) -> Optional[dict]:
    """cite id → {type, label, lat, lon, source_url} (없으면 None)."""
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
            "source_url": "",
            "status": a.status,
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


# ── 공개 진입점 ──────────────────────────────────────────────────────────────
def assess(
    store, query: str, now: Optional[int] = None, explainer: Optional[str] = None
) -> dict:
    """자연어 질의 → SituationAssessment 생성·영속 → 응답 dict(문장별 cites 포함).

    이것이 GenerateSituationAssessment 액션(ontology.md §3)의 코드 구현이다:
    파싱 → 병렬 read → 사실→문장 조립(cites 강제) → (옵션)서술 다듬기 → store 영속.
    근거 사실이 하나도 없으면 no_evidence 응답(무근거 주장 대신 "못 찾음" 투명 보고 —
    cites 없는 문장을 만들지 않으므로 Assessment 객체는 생성되지 않는다).
    """
    pq = parse_query(query, now=now)
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

    sentences = _assemble_sentences(store, pq, reads)

    region_name = _region_name(store, pq.region_id)
    counts = {
        "flights": len(reads.flights),
        "anomalies": len(reads.anomalies),
        "passes": len(reads.passes),
        "weather": len(reads.weather),
        "news": len(reads.news),
    }

    # 근거 사실이 하나도 없음 → 무근거 주장 대신 투명한 "못 찾음"(Assessment 미생성).
    if not sentences:
        return {
            "assessment_id": None,
            "no_evidence": True,
            "query": query,
            "region": {"id": pq.region_id, "name": region_name},
            "window": _window_dict(pq),
            "summary": (
                f"{pq.window_label} {region_name}에서 근거 객체(관측·이상징후·통과·기상·뉴스)를 "
                f"찾지 못했습니다 — 무근거 주장 대신 '해당 없음'을 보고합니다."
            ),
            "sentences": [],
            "confidence": 0.0,
            "produced_by": "template",
            "created_at": pq.now,
            "counts": counts,
            "cited_objects": {},
        }

    # (옵션) LLM 서술 다듬기 — cites 불변, 실패 시 원문(DR-0004 폴백).
    backend = "template"
    name = (explainer or os.environ.get("SKAI_EXPLAINER", "template")).lower()
    if name == "claude":
        sentences, backend = _polish_narration(sentences)

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
        attrs={"counts": counts, "matched_region_alias": pq.matched_region_alias},
    )
    # GenerateSituationAssessment 액션 — 문장별 cites 강제 + aggregates/cites 링크 영속.
    store.write_assessment(assessment)

    return {
        "assessment_id": assessment.id,
        "no_evidence": False,
        "query": query,
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
            nodes.setdefault(
                nid,
                {
                    "id": nid,
                    "type": "Aircraft",
                    "label": f"✈ {(ac.callsign if ac else None) or icao}",
                    "lat": None,
                    "lon": None,
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
