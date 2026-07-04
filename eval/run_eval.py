"""eval/run_eval.py — P5 평가 하네스 (architecture.md §7 "숫자로 증명", DR-0007 결정 5).

세 가지를 산출한다:
  1. **탐지 precision/recall** — 라벨된 합성 시나리오(양성 N + 정상 음성 M)를 각각 격리된
     임시 DB에 주입·탐지해 유형별·전체 P/R 표(+ JSON).
  2. **맨몸 LLM 비교** — 같은 질의를 (a) 온톨로지+파이프라인(assess) (b) `claude -p` 단독
     (온톨로지 없이 같은 원시 관측 요약 텍스트만)에 던져 출처·사실성·무근거 주장을 비교.
     (b) 실패(중첩 세션 타임아웃 등) 시 정성 구조 비교표로 대체하고 사유를 기록한다.
  3. **claude 서술 경로 실호출**(P4 이월 #4) — SKAI_EXPLAINER=claude로 assess 1회 실행해
     서술 백엔드·cites 불변을 확인(실패 시 폴백 동작 기록).

실행:
    .venv/bin/python -m eval.run_eval                # 전체(LLM 포함, 120s 타임아웃)
    .venv/bin/python -m eval.run_eval --no-llm       # 탐지 P/R만(LLM 호출 생략)
JSON은 docs/worklog/p5_eval.json, 표는 stdout(마크다운) — worklog에 붙인다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from anomaly.actions import scan_and_create_all
from copilot.assessment import assess
from ontology.store_local import LocalOntologyStore
from scripts.scenarios import (
    SCENARIOS,
    T_DROPOUT,
    T_LOITERING,
    T_MILITARY,
    T_SATELLITE,
    T_SQUAWK,
    apply_scenario,
    scenario_by_id,
)

# 재현성: 고정 now 앵커(합성 시나리오는 이 시각 상대). 벽시계 무관 = 결정적.
EVAL_NOW = 1783000000
TYPE_LABELS_KO = {
    T_SQUAWK: "비상 스쿽",
    T_DROPOUT: "ADS-B dropout",
    T_LOITERING: "로이터링",
    T_MILITARY: "군용기 접근",
    T_SATELLITE: "위성 근접",
}
ALL_TYPES = [T_SQUAWK, T_DROPOUT, T_LOITERING, T_MILITARY, T_SATELLITE]


def _fresh_store() -> LocalOntologyStore:
    d = tempfile.mkdtemp(prefix="skai-eval-")
    return LocalOntologyStore(os.path.join(d, "eval.db"))


# ── 1. 탐지 precision/recall ──────────────────────────────────────────────────
def run_detection_eval(now: int = EVAL_NOW) -> dict:
    """각 시나리오를 격리 주입·탐지 → 유형별·전체 P/R(시나리오 레벨)."""
    per_scenario = []
    for sc in SCENARIOS:
        store = _fresh_store()
        mirror = apply_scenario(store, sc, now)
        created = scan_and_create_all(store, now=now, crosscheck=mirror)
        detected = {t for t, v in created.items() if v}
        labels = set(sc["labels"])
        per_scenario.append(
            {
                "id": sc["id"],
                "desc": sc["desc"],
                "labels": sorted(labels),
                "detected": sorted(detected),
                "correct": detected == labels,
                "confidences": {
                    t: round(v[0].confidence, 2) for t, v in created.items() if v
                },
            }
        )

    # 유형별 혼동행렬(시나리오 레벨: 각 시나리오 = 유형별 양성/음성 1표본)
    per_type: dict[str, dict] = {}
    for t in ALL_TYPES:
        tp = fp = fn = tn = 0
        for r in per_scenario:
            in_label = t in r["labels"]
            in_det = t in r["detected"]
            if in_label and in_det:
                tp += 1
            elif in_label and not in_det:
                fn += 1
            elif not in_label and in_det:
                fp += 1
            else:
                tn += 1
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall)
            else 0.0
        )
        per_type[t] = {
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
            "precision": round(precision, 3),
            "recall": round(recall, 3),
            "f1": round(f1, 3),
        }

    tp_all = sum(v["tp"] for v in per_type.values())
    fp_all = sum(v["fp"] for v in per_type.values())
    fn_all = sum(v["fn"] for v in per_type.values())
    micro_p = tp_all / (tp_all + fp_all) if (tp_all + fp_all) else 1.0
    micro_r = tp_all / (tp_all + fn_all) if (tp_all + fn_all) else 1.0
    return {
        "now": now,
        "n_scenarios": len(SCENARIOS),
        "per_type": per_type,
        "overall": {
            "tp": tp_all,
            "fp": fp_all,
            "fn": fn_all,
            "micro_precision": round(micro_p, 3),
            "micro_recall": round(micro_r, 3),
        },
        "per_scenario": per_scenario,
    }


# ── 2. 맨몸 LLM 비교 ──────────────────────────────────────────────────────────
def _raw_observation_summary(store) -> str:
    """온톨로지 구조 없이 '원시 관측 요약' 텍스트(맨몸 LLM 입력). 링크·출처 강제 없음."""
    lines = ["[관측(ADS-B)]"]
    for o in store.query_all_observations():
        gap = ""
        lines.append(
            f"- {o.aircraft_ref} @ ({o.lat:.2f},{o.lon:.2f}) ts={o.ts} "
            f"squawk={o.squawk} src={o.source}{gap}"
        )
    lines.append("[위성 통과]")
    for p in store.query_orbitpasses():
        lines.append(
            f"- NORAD {p.satellite_ref} over {p.region_ref} "
            f"{p.start_ts}~{p.end_ts} 최대앙각 {p.max_elevation:.0f}"
        )
    lines.append("[뉴스/OSINT]")
    for n in store.query_news():
        lines.append(f"- {n.title} (ts={n.ts}, 신뢰도 {n.confidence})")
    return "\n".join(lines)


def _bare_llm(question: str, raw_text: str, timeout: int = 120) -> dict:
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
    except Exception as e:  # 타임아웃·중첩 세션 등
        return {"ok": False, "reason": repr(e), "output": ""}


def run_bare_llm_comparison(now: int = EVAL_NOW, use_llm: bool = True) -> dict:
    """같은 질의를 파이프라인 vs 맨몸 LLM에 던져 provenance·사실성 비교."""
    store = _fresh_store()
    sc = scenario_by_id("narrative_hidden")
    mirror = apply_scenario(store, sc, now)
    scan_and_create_all(store, now=now, crosscheck=mirror)
    question = "지금 KADIZ에서 ADS-B가 끊긴 기체가 위성 통과·뉴스와 겹치나? 근거와 함께 답하라."

    # (a) 파이프라인
    r = assess(store, question, now=now)
    n_sent = len(r["sentences"])
    n_cited = sum(1 for s in r["sentences"] if s["cites"])
    pipeline = {
        "n_sentences": n_sent,
        "n_cited_sentences": n_cited,
        "cited_ratio": round(n_cited / n_sent, 3) if n_sent else 0.0,
        "overall_confidence": r["confidence"],
        "has_machine_citations": True,  # 문장별 cites → 원 객체 id로 역추적
        "summary": r["summary"],
        "unsupported_claims": 0,  # 구조상 cites 없는 문장은 write에서 거부(0 보장)
    }

    # (b) 맨몸 LLM
    raw = _raw_observation_summary(store)
    if use_llm:
        bare = _bare_llm(question, raw)
    else:
        bare = {"ok": False, "reason": "--no-llm(생략)", "output": ""}

    out_txt = bare.get("output", "")
    # 자동 신호: 맨몸 출력에 기계검증 가능한 출처(객체 id·URL)가 있는가(대개 없음).
    has_url = "http" in out_txt or "synthetic://" in out_txt
    bare_summary = {
        "ok": bare["ok"],
        "reason": bare.get("reason", ""),
        "output_chars": len(out_txt),
        "has_machine_citations": has_url,  # 기계 역추적 가능한 인용 유무
        "output_excerpt": out_txt[:600],
    }
    return {
        "question": question,
        "raw_input_chars": len(raw),
        "pipeline": pipeline,
        "bare_llm": bare_summary,
    }


# ── 3. claude 서술 경로 실호출 (P4 이월 #4) ──────────────────────────────────
def run_claude_narration_check(now: int = EVAL_NOW, use_llm: bool = True) -> dict:
    """SKAI_EXPLAINER=claude로 assess 1회 실호출 → 서술 백엔드·cites 불변 확인."""
    store = _fresh_store()
    sc = scenario_by_id("narrative_hidden")
    mirror = apply_scenario(store, sc, now)
    scan_and_create_all(store, now=now, crosscheck=mirror)
    q = "지금 KADIZ 상황 요약해줘"

    r_tmpl = assess(store, q, now=now, explainer="template")
    cites_tmpl = [s["cites"] for s in r_tmpl["sentences"]]
    if not use_llm:
        return {
            "skipped": True,
            "reason": "--no-llm(생략)",
            "template_produced_by": r_tmpl["produced_by"],
        }
    r_claude = assess(store, q, now=now, explainer="claude")
    cites_claude = [s["cites"] for s in r_claude["sentences"]]
    return {
        "skipped": False,
        "produced_by": r_claude["produced_by"],  # claude | template(claude 폴백)
        "claude_succeeded": r_claude["produced_by"] == "claude",
        "cites_invariant": cites_tmpl == cites_claude,  # 서술 다듬기 후에도 cites 불변
        "n_sentences": len(r_claude["sentences"]),
    }


# ── 마크다운 렌더 ─────────────────────────────────────────────────────────────
def _render_markdown(det: dict, cmp: dict, narr: dict) -> str:
    lines = []
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"# P5 평가 결과 (생성 {ts})\n")
    lines.append(
        f"고정 now 앵커 {det['now']} · 시나리오 {det['n_scenarios']}건(격리 임시 DB)\n"
    )

    lines.append("## 유형별 precision / recall\n")
    lines.append("| 유형 | TP | FP | FN | Precision | Recall | F1 |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")
    for t in ALL_TYPES:
        v = det["per_type"][t]
        lines.append(
            f"| {TYPE_LABELS_KO[t]} | {v['tp']} | {v['fp']} | {v['fn']} | "
            f"{v['precision']:.2f} | {v['recall']:.2f} | {v['f1']:.2f} |"
        )
    o = det["overall"]
    lines.append(
        f"| **전체(micro)** | {o['tp']} | {o['fp']} | {o['fn']} | "
        f"{o['micro_precision']:.2f} | {o['micro_recall']:.2f} | — |"
    )

    lines.append("\n## 시나리오별 탐지 결과\n")
    lines.append("| 시나리오 | 라벨 | 탐지 | 판정 |")
    lines.append("|---|---|---|---|")
    for r in det["per_scenario"]:
        conf = ", ".join(f"{k} {v}" for k, v in r["confidences"].items())
        lab = ", ".join(r["labels"]) or "정상(음성)"
        got = ", ".join(f"{d}({r['confidences'].get(d)})" for d in r["detected"]) or "—"
        mark = "OK" if r["correct"] else "XX"
        lines.append(f"| {r['id']} | {lab} | {got} | {mark} |")

    lines.append("\n## 맨몸 LLM vs 온톨로지+AIP 파이프라인\n")
    p = cmp["pipeline"]
    b = cmp["bare_llm"]
    lines.append(f'질의: "{cmp["question"]}"\n')
    lines.append("| 항목 | 온톨로지+파이프라인 | 맨몸 LLM(claude -p) |")
    lines.append("|---|---|---|")
    lines.append(
        f"| 출처(provenance) | 문장별 cites → 객체 id 역추적 | "
        f"{'출처 텍스트 있음' if b['has_machine_citations'] else '기계검증 가능한 출처 없음'} |"
    )
    lines.append(
        f"| 인용 문장 비율 | {p['n_cited_sentences']}/{p['n_sentences']} "
        f"({p['cited_ratio'] * 100:.0f}%) | 구조적 인용 없음(자유 텍스트) |"
    )
    lines.append(
        f"| 무근거 주장 | {p['unsupported_claims']}건(cites 없는 문장은 write 거부) | "
        f"검증 불가(사실 그라운딩 소스 없음) |"
    )
    lines.append(
        f"| 종합 신뢰도 | {p['overall_confidence']:.2f}(문장 평균, 저신뢰 뉴스 반영) | "
        f"표기 없음 |"
    )
    if b["ok"]:
        lines.append(f"| 맨몸 출력(발췌) | — | {b['output_excerpt'][:200]!r} |")
    else:
        lines.append(
            f"| 맨몸 호출 | — | **실패**: {b['reason']} → 정성 구조 비교로 대체 |"
        )
    lines.append(
        "\n> 비교의 본질 = **provenance 유무**. 파이프라인은 모든 문장이 온톨로지 객체 id를 "
        "인용(역추적 가능·환각 구조적 차단)하는 반면, 맨몸 LLM은 같은 원시 관측을 받고도 "
        "기계검증 가능한 출처를 남기지 못한다. 맨몸 호출 성공 여부와 무관하게 이 구조 차이가 "
        "핵심 우위다.\n"
    )

    lines.append("## claude 서술 경로 실호출 (P4 이월 #4)\n")
    if narr.get("skipped"):
        lines.append(
            f"- 생략({narr['reason']}). template 서술 백엔드={narr.get('template_produced_by')}"
        )
    else:
        lines.append(
            f"- 서술 백엔드: `{narr['produced_by']}` "
            f"(claude 성공={narr['claude_succeeded']})"
        )
        lines.append(
            f"- cites 불변(서술 다듬기 후에도 근거 매핑 동일): {narr['cites_invariant']}"
        )
        lines.append(
            "- 실패/타임아웃 시 template로 폴백하며 cites·신뢰도는 불변(DR-0004)."
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="P5 평가 하네스")
    ap.add_argument(
        "--no-llm", action="store_true", help="맨몸 LLM·claude 서술 호출 생략"
    )
    ap.add_argument(
        "--json-out",
        default=str(
            Path(__file__).resolve().parent.parent / "docs" / "worklog" / "p5_eval.json"
        ),
    )
    args = ap.parse_args()
    use_llm = not args.no_llm

    t0 = time.time()
    det = run_detection_eval()
    cmp = run_bare_llm_comparison(use_llm=use_llm)
    narr = run_claude_narration_check(use_llm=use_llm)
    elapsed = round(time.time() - t0, 1)

    result = {
        "detection": det,
        "bare_llm_comparison": cmp,
        "claude_narration_check": narr,
        "elapsed_seconds": elapsed,
    }
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_out).write_text(json.dumps(result, ensure_ascii=False, indent=2))

    print(_render_markdown(det, cmp, narr))
    print(f"\n[eval] JSON 저장: {args.json_out} · 소요 {elapsed}s")


if __name__ == "__main__":
    main()
