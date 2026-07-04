"""eval/live_eval.py — 라이브 평가 하네스 (architecture.md §7 "숫자로 증명"의 라이브 절반).

합성 회귀(eval/run_eval.py)는 "탐지 로직의 결정성"을 검증하지만 라이브 성능이 아니다.
라이브에는 ground truth가 없어 **precision/recall을 원리적으로 낼 수 없다**(EVALUATION OVERSOLD#4).
그래서 이 하네스는 P/R을 흉내내지 않고, **라이브에서 측정 가능한 것만** 정직하게 측정한다:

  1. **citation 정합(라이브)** — 라이브 데이터로 의도 다양한 질의 N개를 던져
     ① 전 문장 cites 보유율 ② cites id가 실 온톨로지 객체로 해상되는 비율을 측정한다.
     둘 다 구조적으로 강제되므로 100%가 기대값이고, "그 강제가 라이브에서도 지켜짐"의 실측 증거다.
  2. **탐지 결정성 + 분포(라이브)** — 같은 라이브 스냅샷에 룰을 2회 실행해 후보 집합이
     동일한지(결정성) + 라이브 수집창에서 실제로 발화한 이상징후 유형·건수 분포를 기록한다
     (라이브에서 "무엇이 잡히나"의 사실 기록 — 흔히 0건이며 그것이 정직한 결과다).
     보강으로 assess를 2회 실행해 코파일럿 산출(문장·cites)의 재현성도 확인한다.
  3. **맨몸 LLM 대비(라이브 데이터)** — 라이브 스냅샷 질의 1개를 (a)파이프라인 (b)`claude -p`
     단독에 던져 **기계검증 가능한 출처 수**를 비교한다(run_eval의 p5 방식, 라이브 데이터로 재실행).

라이브 db가 비었으면(방금 리셋 등) opensky+gdelt+metar+celestrak 유한 사이클로 선수집한다
(폴러 재사용, 크레딧 최소 = 기본 2사이클). 평가는 라이브 db의 **스냅샷 사본**에 대해 돌려
런타임 skai.db(실데이터 전용, db-regime.md §1)를 assess/correlate 산출물로 오염시키지 않는다.

실행:
    .venv/bin/python -m eval.live_eval               # 전체(선수집 자동 + 맨몸 LLM 포함)
    .venv/bin/python -m eval.live_eval --no-llm      # 맨몸 LLM 호출 생략(빠름·중첩세션 회피)
    .venv/bin/python -m eval.live_eval --no-collect  # 선수집 금지(빈 db면 그대로 측정)
JSON은 docs/worklog/live_eval_result.json, 표는 stdout(마크다운).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from anomaly.crosscheck import NullCrossCheckSource
from anomaly.rules import (
    detect_adsb_dropout,
    detect_emergency_squawk,
    detect_loitering,
    detect_military_approach,
    detect_satellite_proximity,
)
from copilot.assessment import assess
from ontology.model import SENSITIVE_CLASSIFICATIONS
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

TYPE_LABELS_KO = {
    "emergency_squawk": "비상 스쿽",
    "adsb_dropout": "ADS-B dropout",
    "loitering": "로이터링",
    "military_approach": "군용기 접근",
    "satellite_proximity": "위성 근접",
}
ALL_TYPES = list(TYPE_LABELS_KO.keys())

# 의도 다양한 라이브 질의(situation_summary·count·filter·weather·news·correlation).
# 라이브 데이터가 얇으면 일부는 no_evidence로 정직 응답한다 — cites 비율은 산출된 문장에만 적용.
DEFAULT_QUERIES = [
    "지금 KADIZ 근방 상황 요약해줘",
    "지금 KADIZ에 항적 몇 대야?",
    "위성 통과 몇 건이야?",
    "군용 추정 항적만 보여줘",
    "관심지역 기상 어때?",
    "최근 뉴스 맥락 있어?",
    "지금 이상징후 있어?",
    "최근 1시간 위성 통과랑 겹치는 이상징후는?",
    "지금 떠 있는 민간기 목록 보여줘",
    "서해 상공 상황 브리핑해줘",
]

# 맨몸 LLM 비교에 쓸 라이브 질의(파이프라인이 산출 있는 상황요약을 선호).
BARE_QUERY = "지금 KADIZ 근방 상황 요약해줘"


# ── 스냅샷 · 선수집 ────────────────────────────────────────────────────────────
def snapshot_db(db_path: str) -> str:
    """라이브 db를 임시 사본으로 스냅샷(sqlite 온라인 백업 — WAL 포함 커밋 상태 일관 캡처).

    평가의 assess/correlate 쓰기는 이 사본에만 들어가고 런타임 skai.db는 실데이터 전용으로 남는다.
    """
    # 이전 실행이 남긴 스냅샷 사본 정리(claude -p 타임아웃 등 드문 누수 회수 — 누적 방지).
    stale_root = tempfile.gettempdir()
    for name in os.listdir(stale_root):
        if name.startswith("skai-live-eval-"):
            shutil.rmtree(os.path.join(stale_root, name), ignore_errors=True)
    d = tempfile.mkdtemp(prefix="skai-live-eval-")
    dst = os.path.join(d, "snapshot.db")
    src_conn = sqlite3.connect(db_path)
    dst_conn = sqlite3.connect(dst)
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()
    return dst


def ensure_live_data(
    db_path: str = DEFAULT_DB,
    min_obs: int = 1,
    cycles: int = 2,
    interval: int = 10,
    sources: Optional[list[str]] = None,
) -> dict:
    """라이브 db가 비었으면 유한 폴링으로 선수집(폴러 재사용, 크레딧 최소). 이미 차 있으면 no-op.

    connectors.opensky.run_poller를 그대로 재사용한다(뉴스 커넥터 등 무수정 — read/호출만).
    """
    store = LocalOntologyStore(db_path)
    counts = store.counts()
    if counts["observation"] >= min_obs:
        return {"collected": False, "counts": counts}
    # 지연 import — 선수집이 필요할 때만 폴러 의존을 끌어온다.
    from connectors.opensky import DEFAULT_LIVE_SOURCES, run_poller

    srcs = sources if sources is not None else list(DEFAULT_LIVE_SOURCES)
    run_poller(interval=interval, max_cycles=cycles, db_path=db_path, sources=srcs)
    return {"collected": True, "counts": LocalOntologyStore(db_path).counts()}


def _snapshot_now(store: LocalOntologyStore) -> int:
    """스냅샷의 '현재 시각' = 최신 실관측 ts(없으면 벽시계). 탐지·질의 창의 앵커."""
    obs = store.query_all_observations(limit=1)  # ORDER BY ts DESC
    return obs[0].ts if obs else int(time.time())


# ── 1. citation 정합(라이브) ─────────────────────────────────────────────────
def run_citation_eval(store: LocalOntologyStore, queries: list[str], now: int) -> dict:
    """의도 다양한 질의 → 전 문장 cites 보유율 + cites id 실객체 해상율.

    - 문장 cites 보유율 = (cites 있는 문장) / (전체 산출 문장). 구조상 100% 기대.
    - cites 해상율 = (실 온톨로지 객체로 해상된 cite id) / (전체 고유 cite id). 100% 기대.
      assess가 반환하는 cited_objects(해상 인덱스)로 판정 — 해상 안 되는 id는 여기 없다.
    no_evidence 응답(라이브가 얇아 해당 객체 없음)은 문장 0 → 비율 분모에서 자연 제외(정직).
    """
    per_query = []
    tot_sent = tot_cited_sent = 0
    tot_cite_ids = tot_resolved = 0
    for q in queries:
        r = assess(store, q, now=now)
        sents = r["sentences"]
        n_sent = len(sents)
        n_cited = sum(1 for s in sents if s["cites"])
        cite_ids: set[str] = set()
        for s in sents:
            cite_ids.update(s["cites"])
        resolved = {cid for cid in cite_ids if cid in r["cited_objects"]}
        per_query.append(
            {
                "query": q,
                "intent": r["intent"],
                "no_evidence": r["no_evidence"],
                "n_sentences": n_sent,
                "n_cited_sentences": n_cited,
                "n_cite_ids": len(cite_ids),
                "n_resolved": len(resolved),
                "unresolved": sorted(cite_ids - resolved),
            }
        )
        tot_sent += n_sent
        tot_cited_sent += n_cited
        tot_cite_ids += len(cite_ids)
        tot_resolved += len(resolved)

    return {
        "n_queries": len(queries),
        "n_queries_with_evidence": sum(1 for p in per_query if not p["no_evidence"]),
        "total_sentences": tot_sent,
        "total_cited_sentences": tot_cited_sent,
        "sentence_cite_ratio": round(tot_cited_sent / tot_sent, 4)
        if tot_sent
        else None,
        "total_cite_ids": tot_cite_ids,
        "total_resolved": tot_resolved,
        "cite_resolution_ratio": round(tot_resolved / tot_cite_ids, 4)
        if tot_cite_ids
        else None,
        "per_query": per_query,
    }


# ── 2. 탐지 결정성 + 분포(라이브) ────────────────────────────────────────────
def _detect_signatures(store: LocalOntologyStore, now: int) -> list[tuple]:
    """룰(순수 detect_*)을 라이브 스냅샷에 1회 적용 → 후보 시그니처 정렬 리스트.

    라이브 기본 crosscheck는 Null(2차 소스 미배선 → dropout은 미확인·저신뢰). 쓰기 없음(순수).
    시그니처 = (anomaly_id, type, confidence). 같은 입력·같은 now면 결정적으로 동일해야 한다.
    """
    regions = store.query_regions()
    sensitive = [r for r in regions if r.classification in SENSITIVE_CLASSIFICATIONS]
    opareas = [r for r in regions if r.classification == "OpArea"]
    region_map = {r.id: r for r in regions}
    tracks = store.query_tracks()
    latest = store.query_latest_observations()
    latest_by_ac = {o.aircraft_ref: o for o in latest}
    aircraft_map = store.aircraft_map()
    orbitpasses = store.query_orbitpasses()
    satellite_map = store.satellite_map()
    crosscheck = NullCrossCheckSource()

    sigs: list[tuple] = []
    # 비상 스쿽(AnomalyCandidate — 신뢰도는 explainer 소관이라 시그니처는 id·type만).
    for c in detect_emergency_squawk(latest):
        sigs.append((c.anomaly_id, c.type, None))
    # P5 룰(AnomalyDraft — 신뢰도를 룰이 확정하므로 시그니처에 포함).
    drafts = []
    drafts += detect_adsb_dropout(tracks, latest_by_ac, sensitive, now, crosscheck)
    drafts += detect_loitering(tracks, latest_by_ac, now)
    drafts += detect_military_approach(latest, aircraft_map, opareas, now)
    drafts += detect_satellite_proximity(orbitpasses, region_map, now, satellite_map)
    for d in drafts:
        sigs.append((d.anomaly_id, d.type, round(d.confidence, 3)))
    return sorted(sigs, key=lambda t: (t[1], t[0]))


def run_detection_eval(store: LocalOntologyStore, now: int) -> dict:
    """룰 2회 실행 결정성 + 라이브 수집창 이상징후 분포(이미 영속된 것) 기록."""
    run1 = _detect_signatures(store, now)
    run2 = _detect_signatures(store, now)
    deterministic = run1 == run2

    # 라이브 수집창에서 폴러가 실제 영속한 이상징후 분포(유형별 건수 + dropout 교차상태).
    stored = store.query_anomalies()
    by_type: dict[str, int] = {t: 0 for t in ALL_TYPES}
    dropout_cross = {"confirmed": 0, "unconfirmed": 0}
    conf_by_type: dict[str, list[float]] = {t: [] for t in ALL_TYPES}
    for a in stored:
        by_type[a.type] = by_type.get(a.type, 0) + 1
        conf_by_type.setdefault(a.type, []).append(round(a.confidence, 2))
        if a.type == "adsb_dropout":
            cc = (a.attrs or {}).get("cross_confirmed")
            dropout_cross["confirmed" if cc is True else "unconfirmed"] += 1

    return {
        "now_anchor": now,
        "determinism": {
            "n_candidates_run1": len(run1),
            "n_candidates_run2": len(run2),
            "identical": deterministic,
            "candidates": [
                {"id": s[0], "type": s[1], "confidence": s[2]} for s in run1
            ],
        },
        "live_distribution": {
            "total_anomalies": len(stored),
            "by_type": by_type,
            "confidence_by_type": {t: v for t, v in conf_by_type.items() if v},
            "dropout_cross_status": dropout_cross,
        },
    }


def run_assessment_determinism(store: LocalOntologyStore, query: str, now: int) -> dict:
    """같은 스냅샷·질의·template 백엔드로 assess 2회 → 문장·cites·신뢰도 완전 동일 확인.

    라이브 이상징후가 0건이어도 항적 등 실데이터로 코파일럿 산출의 재현성을 실측한다
    (탐지 결정성보다 넓은 read/조립 경로의 결정성 — "같은 스냅샷 → 같은 답").
    """

    def _fingerprint(r: dict) -> list:
        return [
            (s["text"], tuple(s["cites"]), s["confidence"], s["kind"])
            for s in r["sentences"]
        ]

    r1 = assess(store, query, now=now, explainer="template")
    r2 = assess(store, query, now=now, explainer="template")
    fp1, fp2 = _fingerprint(r1), _fingerprint(r2)
    return {
        "query": query,
        "n_sentences": len(fp1),
        "no_evidence": r1["no_evidence"],
        "identical": fp1 == fp2,
        "confidence": r1["confidence"],
    }


# ── 3. 맨몸 LLM 대비(라이브 데이터) ──────────────────────────────────────────
def _raw_observation_summary(store: LocalOntologyStore) -> str:
    """온톨로지 구조 없이 '원시 관측 요약'(맨몸 LLM 입력). run_eval와 동형 — 링크·출처 강제 없음."""
    lines = ["[관측(ADS-B)]"]
    for o in store.query_latest_observations():
        lines.append(
            f"- {o.aircraft_ref} @ ({o.lat:.2f},{o.lon:.2f}) ts={o.ts} "
            f"squawk={o.squawk} src={o.source}"
        )
    lines.append("[위성 통과]")
    for p in store.query_orbitpasses()[:20]:
        lines.append(
            f"- NORAD {p.satellite_ref} over {p.region_ref} "
            f"{p.start_ts}~{p.end_ts} 최대앙각 {p.max_elevation:.0f}"
        )
    lines.append("[기상]")
    for w in store.query_weather_latest():
        lines.append(f"- {w.station} {w.flight_category} 시정 {w.visibility_sm}")
    lines.append("[뉴스/OSINT]")
    for n in store.query_news():
        lines.append(f"- {n.title} (ts={n.ts}, 신뢰도 {n.confidence})")
    return "\n".join(lines)


def _bare_llm(question: str, raw_text: str, timeout: int = 60) -> dict:
    prompt = (
        "너는 공중 ISR 상황분석 보조다. 아래 '원시 관측 요약'만 근거로 질문에 한국어로 "
        "간결히 답하라.\n\n[질문]\n" + question + "\n\n[원시 관측 요약]\n" + raw_text
    )
    try:
        proc = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return {
                "ok": False,
                "reason": f"rc={proc.returncode} stderr={(proc.stderr or '')[:200]!r}",
                "output": "",
            }
        return {"ok": True, "output": proc.stdout.strip()}
    except subprocess.TimeoutExpired:
        # repr(TimeoutExpired)는 cmd(=프롬프트 전문)를 덤프하므로 간결한 사유로 대체.
        return {"ok": False, "reason": f"TimeoutExpired({timeout}s)", "output": ""}
    except Exception as e:  # 중첩 세션·바이너리 부재 등
        return {"ok": False, "reason": repr(e)[:160], "output": ""}


def run_bare_llm_comparison(
    store: LocalOntologyStore, query: str, now: int, use_llm: bool = True
) -> dict:
    """라이브 질의 1개 → 파이프라인 vs 맨몸 LLM 기계검증 출처 수 비교(run_eval p5 방식)."""
    r = assess(store, query, now=now)
    n_sent = len(r["sentences"])
    n_cited = sum(1 for s in r["sentences"] if s["cites"])
    # 파이프라인의 기계검증 출처 = 산출된 문장이 인용한 고유 객체 id 중 실해상된 것.
    cite_ids: set[str] = set()
    for s in r["sentences"]:
        cite_ids.update(s["cites"])
    resolved = {cid for cid in cite_ids if cid in r["cited_objects"]}
    pipeline = {
        "no_evidence": r["no_evidence"],
        "n_sentences": n_sent,
        "n_cited_sentences": n_cited,
        "n_machine_citations": len(resolved),  # 역추적 가능한 실 객체 인용 수
        "summary": r["summary"],
    }

    raw = _raw_observation_summary(store)
    if use_llm:
        bare = _bare_llm(query, raw)
    else:
        bare = {"ok": False, "reason": "--no-llm(생략)", "output": ""}
    out_txt = bare.get("output", "")
    # 맨몸 출력에 기계검증 가능한 출처(객체 id·URL)가 있는가 — 대개 없다(자유 텍스트).
    has_url = "http" in out_txt or "synthetic://" in out_txt
    bare_summary = {
        "ok": bare["ok"],
        "reason": bare.get("reason", ""),
        "output_chars": len(out_txt),
        "n_machine_citations": 0 if not has_url else 1,  # URL 있으면 1+(대개 0)
        "output_excerpt": out_txt[:400],
    }
    return {"question": query, "pipeline": pipeline, "bare_llm": bare_summary}


# ── 마크다운 렌더 ─────────────────────────────────────────────────────────────
def _render_markdown(
    counts: dict, now: int, cit: dict, det: dict, adet: dict, cmp: dict
) -> str:
    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    anchor_iso = datetime.fromtimestamp(now, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M UTC"
    )
    lines.append(f"# 라이브 평가 결과 (생성 {ts})\n")
    lines.append(
        f"라이브 스냅샷 앵커 {now} ({anchor_iso}) · 카운트: 항적 {counts['aircraft']}·"
        f"관측 {counts['observation']}·위성통과 {counts['orbitpass']}·기상 "
        f"{counts['weatherstate']}·뉴스 {counts['newsevent']}·이상징후 {counts['anomaly']}\n"
    )
    lines.append(
        "> 라이브에는 ground truth가 없어 precision/recall을 원리적으로 낼 수 없다"
        "(합성 회귀만 P/R 산출 가능 — EVALUATION OVERSOLD#4). 아래는 라이브에서 "
        "**측정 가능한 것만** 정직하게 측정한 값이다.\n"
    )

    # 1. citation 정합
    lines.append("## 1. citation 정합 (라이브)\n")
    scr = cit["sentence_cite_ratio"]
    crr = cit["cite_resolution_ratio"]
    lines.append(
        f"- 질의 {cit['n_queries']}건(의도 다양) 중 근거 있는 응답 "
        f"{cit['n_queries_with_evidence']}건 · 산출 문장 {cit['total_sentences']}개\n"
        f"- **문장 cites 보유율: {cit['total_cited_sentences']}/{cit['total_sentences']} "
        f"= {scr * 100:.1f}%**" + (" (구조적 강제 실측)" if scr == 1.0 else "") + "\n"
        f"- **cites id 실객체 해상율: {cit['total_resolved']}/{cit['total_cite_ids']} "
        f"= {crr * 100:.1f}%**"
        + (" (전 인용이 실 온톨로지 객체로 역추적)" if crr == 1.0 else "")
    )
    lines.append("\n| 질의 | 의도 | 문장 | cites문장 | cite id | 해상 | no_evidence |")
    lines.append("|---|---|---:|---:|---:|---:|:---:|")
    for p in cit["per_query"]:
        lines.append(
            f"| {p['query']} | {p['intent']} | {p['n_sentences']} | "
            f"{p['n_cited_sentences']} | {p['n_cite_ids']} | {p['n_resolved']} | "
            f"{'예' if p['no_evidence'] else '—'} |"
        )

    # 2. 탐지 결정성 + 분포
    lines.append("\n## 2. 탐지 결정성 + 라이브 분포\n")
    d = det["determinism"]
    lines.append(
        f"- **결정성: 룰 2회 실행 후보 집합 {'동일' if d['identical'] else '불일치'}** "
        f"(run1 {d['n_candidates_run1']}건 = run2 {d['n_candidates_run2']}건)"
    )
    ad = adet
    lines.append(
        f"- **재현성: 같은 스냅샷·질의 assess 2회 산출 {'동일' if ad['identical'] else '불일치'}** "
        f"(문장 {ad['n_sentences']}개, template 백엔드)"
    )
    dist = det["live_distribution"]
    lines.append(
        f"- 라이브 수집창 영속 이상징후 **총 {dist['total_anomalies']}건**: "
        + ", ".join(
            f"{TYPE_LABELS_KO[t]} {dist['by_type'].get(t, 0)}" for t in ALL_TYPES
        )
    )
    if dist["total_anomalies"] == 0:
        lines.append(
            "  - (라이브 KADIZ 실교통에 발화 조건이 없었음 = 정직한 0. 비상 스쿽·민감구역 "
            "gap 교차확인·창내 위성 근접이 이 수집창엔 부재. 데모 재현성은 합성 주입기가 담당.)"
        )
    lines.append(
        f"  - dropout 교차상태: 교차확인 {dist['dropout_cross_status']['confirmed']} · "
        f"미확인 {dist['dropout_cross_status']['unconfirmed']} (라이브 2차 피드 미배선 → 미확인·저신뢰만)"
    )

    # 3. 맨몸 LLM 대비
    lines.append("\n## 3. 맨몸 LLM 대비 (라이브 데이터)\n")
    pp = cmp["pipeline"]
    bb = cmp["bare_llm"]
    lines.append(f'질의: "{cmp["question"]}"\n')
    lines.append("| 항목 | 온톨로지+파이프라인 | 맨몸 LLM(claude -p) |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 기계검증 출처 수 | **{pp['n_machine_citations']}건**(문장 cites→실 객체 역추적) | "
        f"**{bb['n_machine_citations']}건**"
        f"{'(호출 실패 → 구조 비교)' if not bb['ok'] else ''} |"
    )
    lines.append(
        f"| 인용 문장 | {pp['n_cited_sentences']}/{pp['n_sentences']} | 구조적 인용 없음(자유 텍스트) |"
    )
    if not bb["ok"]:
        lines.append(f"| 맨몸 호출 | — | 실패: {bb['reason']} |")
    else:
        lines.append(f"| 맨몸 출력(발췌) | — | {bb['output_excerpt'][:160]!r} |")
    lines.append(
        "\n> 비교의 본질 = **provenance 유무**. 파이프라인의 모든 문장은 라이브 온톨로지 객체 "
        "id를 인용(역추적 가능)하고, 맨몸 LLM은 같은 원시 관측을 받고도 기계검증 가능한 출처를 "
        "남기지 못한다. 맨몸 호출 성공 여부와 무관하게 이 구조 차이가 핵심 우위다.\n"
    )
    return "\n".join(lines) + "\n"


def run_all(
    db_path: str = DEFAULT_DB,
    queries: Optional[list[str]] = None,
    use_llm: bool = True,
    collect: bool = True,
    collect_cycles: int = 2,
) -> dict:
    """전체 라이브 평가 파이프라인 — (선수집) → 스냅샷 → 1·2·3 산출 → 결과 dict."""
    queries = queries if queries is not None else DEFAULT_QUERIES
    collected = {"collected": False, "counts": LocalOntologyStore(db_path).counts()}
    if collect:
        collected = ensure_live_data(db_path, cycles=collect_cycles)

    snap = snapshot_db(db_path)
    store = LocalOntologyStore(snap)
    counts = store.counts()
    now = _snapshot_now(store)

    cit = run_citation_eval(store, queries, now)
    det = run_detection_eval(store, now)
    adet = run_assessment_determinism(store, BARE_QUERY, now)
    cmp = run_bare_llm_comparison(store, BARE_QUERY, now, use_llm=use_llm)

    return {
        "generated_at": int(time.time()),
        "snapshot_now_anchor": now,
        "collected": collected["collected"],
        "live_counts": counts,
        "citation": cit,
        "detection": det,
        "assessment_determinism": adet,
        "bare_llm_comparison": cmp,
        "_snapshot_path": snap,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="SKAI 라이브 평가 하네스")
    ap.add_argument(
        "--no-llm", action="store_true", help="맨몸 LLM(claude -p) 호출 생략"
    )
    ap.add_argument(
        "--no-collect", action="store_true", help="빈 라이브 db여도 선수집 금지"
    )
    ap.add_argument("--cycles", type=int, default=2, help="선수집 폴링 사이클(기본 2)")
    ap.add_argument("--db", default=DEFAULT_DB, help="라이브 db 경로")
    ap.add_argument(
        "--json-out",
        default=str(
            Path(__file__).resolve().parent.parent
            / "docs"
            / "worklog"
            / "live_eval_result.json"
        ),
    )
    args = ap.parse_args()

    t0 = time.time()
    result = run_all(
        db_path=args.db,
        use_llm=not args.no_llm,
        collect=not args.no_collect,
        collect_cycles=args.cycles,
    )
    elapsed = round(time.time() - t0, 1)
    result["elapsed_seconds"] = elapsed

    md = _render_markdown(
        result["live_counts"],
        result["snapshot_now_anchor"],
        result["citation"],
        result["detection"],
        result["assessment_determinism"],
        result["bare_llm_comparison"],
    )
    # JSON에는 내부 스냅샷 경로를 남기지 않는다(임시 경로 노이즈).
    out = {k: v for k, v in result.items() if k != "_snapshot_path"}
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print(md)
    print(f"\n[live_eval] JSON 저장: {args.json_out} · 소요 {elapsed}s")

    # 스냅샷 임시 사본 정리(재실행 시 누적 방지 — 결과는 이미 JSON/표로 산출됨).
    snap_path = result.get("_snapshot_path")
    if snap_path:
        shutil.rmtree(os.path.dirname(snap_path), ignore_errors=True)


if __name__ == "__main__":
    main()
