"""anomaly/actions.py — CreateAnomaly / ConfirmAnomaly / DismissAnomaly (ontology.md §3).

- create_anomaly: 후보 → 설명·신뢰도 → Anomaly 생성. **evidence(근거 Observation) 링크 필수**
  (없으면 store.write_anomaly가 EvidenceError로 거부 = 어떤 경로로도 근거 없는 Anomaly 불가).
  같은 (기체, 유형, 시간창) Anomaly가 있으면 재생성 없이 기존 반환(dedup).
- confirm/dismiss_anomaly: status 전이(candidate→confirmed/dismissed). 사람 승인 = human-on-the-loop.
- scan_and_create: 폴러/주입기 공용 파이프라인(관측 → 룰 → dedup → explainer → CreateAnomaly).
"""

from __future__ import annotations

import os
import time
from typing import Optional

from anomaly import correlation
from anomaly.crosscheck import CrossCheckSource, NullCrossCheckSource
from anomaly.explainer import ExplainerBackend, TemplateExplainer, explain_draft
from anomaly.mil_enrich import MilEnrichmentSource, NullMilEnrichment
from anomaly.rules import (
    AnomalyCandidate,
    AnomalyDraft,
    detect_adsb_dropout,
    detect_emergency_squawk,
    detect_loitering,
    detect_military_approach,
    detect_rapid_maneuver,
    detect_satellite_proximity,
)
from ontology.model import SENSITIVE_CLASSIFICATIONS, Anomaly, Observation


def create_anomaly(
    store,
    candidate: AnomalyCandidate,
    explainer: Optional[ExplainerBackend] = None,
    created_at: Optional[int] = None,
) -> Anomaly:
    """후보 → Anomaly 생성(evidence 링크 필수). dedup 시 기존 Anomaly 반환.

    created_at 미지정 시 벽시계(라이브). replay/평가는 now 앵커를 넘겨 탐지 시각까지
    스냅샷에 고정한다(재현성 — 연속 실행에도 동일 산출).
    """
    explainer = explainer or TemplateExplainer()

    # dedup — 같은 (기체, 유형, 시간창)이 이미 있으면 재생성하지 않음.
    existing = store.get_anomaly(candidate.anomaly_id)
    if existing is not None:
        return existing

    # callsign 보강(설명 품질용). Observation엔 없고 Aircraft에만 있음.
    ac = store.aircraft_map().get(candidate.aircraft_ref)
    if ac and ac.callsign:
        candidate.signal.setdefault("callsign", ac.callsign)

    result = explainer.explain(candidate)
    o: Observation = candidate.observation
    staged = _review_staged()

    if staged:
        # 스테이징(SKAI_REVIEW=staged, B2 방법 B): 구성된 explainer(예: AIP) 산출을 본
        # explanation에 **즉시 쓰지 않는다**. 본 속성엔 결정적 template 베이스라인을 두어
        # (항상 사용가능·재현성) 시스템을 기능 상태로 유지하고, explainer 산출은 아래
        # propose_explanation으로 proposedExplanation에 제안(reviewStatus=pending) → 사람 승인 대기.
        baseline = TemplateExplainer().explain(candidate)
        main_explanation = baseline.explanation
        main_backend = baseline.backend
        main_confidence = baseline.confidence
    else:
        main_explanation = result.explanation
        main_backend = result.backend
        main_confidence = result.confidence

    anomaly = Anomaly(
        id=candidate.anomaly_id,
        type=candidate.type,
        ts=candidate.ts,
        confidence=main_confidence,
        status="candidate",
        lat=o.lat,
        lon=o.lon,
        explanation=main_explanation,
        explainer_backend=main_backend,
        created_at=int(created_at if created_at is not None else time.time()),
        attrs={
            "squawk": candidate.signal.get("squawk"),
            "meaning": candidate.signal.get("meaning"),
            "callsign": candidate.signal.get("callsign"),
            "is_synthetic": o.source == "synthetic",
        },
    )
    # evidence 강제는 store.write_anomaly가 집행 — evidence=[]면 EvidenceError.
    store.write_anomaly(
        anomaly,
        evidence=[o.id],  # Anomaly —evidenced_by→ Observation
        involves=[candidate.aircraft_ref],  # Anomaly —involves→ Aircraft
    )
    if staged:
        # 구성된 explainer 산출을 제안 상태로 스테이징(본 explanation 불변). 반환 Anomaly에도
        # 반영(review_status=pending·proposed_explanation)되게 갱신본을 받는다.
        updated = store.propose_explanation(anomaly.id, result.explanation)
        if updated is not None:
            anomaly = updated
    return anomaly


def confirm_anomaly(store, anomaly_id: str) -> Anomaly:
    """분석가 승인 — status candidate→confirmed (영속)."""
    return store.set_anomaly_status(anomaly_id, "confirmed")


def dismiss_anomaly(store, anomaly_id: str) -> Anomaly:
    """분석가 기각 — status candidate→dismissed (영속)."""
    return store.set_anomaly_status(anomaly_id, "dismissed")


# ── B2 staged human review (방법 B) ──────────────────────────────────────────────
def _review_staged() -> bool:
    """SKAI_REVIEW=staged면 explainer 산출 explanation을 즉시 적용하지 않고 제안→승인 2단계로
    스테이징한다. 기본(미설정)은 즉시 적용 = 현행 동작 불변(데모 재현성 보존)."""
    return os.environ.get("SKAI_REVIEW", "").strip().lower() == "staged"


def propose_explanation(store, anomaly_id: str, explanation: str) -> Anomaly:
    """explainer 산출 explanation을 proposedExplanation으로 제안(reviewStatus=pending).

    본 explanation 속성은 건드리지 않는다(스테이징의 핵심 — 사람 승인 전엔 미적용).
    """
    return store.propose_explanation(anomaly_id, explanation)


def approve_explanation(store, anomaly_id: str) -> Anomaly:
    """분석가 승인 — explanation←proposedExplanation 복사 + reviewStatus=approved (human-on-the-loop)."""
    return store.approve_explanation(anomaly_id)


def reject_explanation(store, anomaly_id: str) -> Anomaly:
    """분석가 기각 — reviewStatus=rejected. 본 explanation·proposedExplanation 불변."""
    return store.reject_explanation(anomaly_id)


def scan_and_create(
    store,
    observations: Optional[list[Observation]] = None,
    explainer: Optional[ExplainerBackend] = None,
    created_at: Optional[int] = None,
) -> list[Anomaly]:
    """관측 → 룰 → 후보 → CreateAnomaly. 반환: 이번에 **신규** 생성된 Anomaly.

    observations 미지정 시 store의 최신 관측(항공기별 1건)을 스캔한다.
    dedup으로 이미 있는 이상징후는 새로 만들지 않는다(신규만 반환).
    P2 배선 보존용: 비상 스쿽 1종만 스캔한다(P5 전 유형은 scan_and_create_all).
    created_at은 now 앵커 전파용(replay 결정성) — 미지정 시 벽시계.
    """
    explainer = explainer or TemplateExplainer()
    if observations is None:
        observations = store.query_latest_observations()

    created: list[Anomaly] = []
    for cand in detect_emergency_squawk(observations):
        before = store.get_anomaly(cand.anomaly_id)
        anomaly = create_anomaly(store, cand, explainer, created_at=created_at)
        if before is None:  # 이번 스캔에서 새로 만든 것만 신규로 집계
            created.append(anomaly)
    return created


# ── P5: 유형-무관 초안 → Anomaly + 전 유형 스캔 파이프라인 ──────────────────────
def create_from_draft(
    store, draft: AnomalyDraft, created_at: Optional[int] = None
) -> Anomaly:
    """AnomalyDraft → Anomaly 생성(evidence 링크 필수). dedup 시 기존 Anomaly 반환.

    create_anomaly(비상 스쿽 전용)의 유형-무관판. evidence/involves가 (dst_type, dst_id)
    튜플이라 Observation·OrbitPass·Satellite 등 어떤 근거/주체 타입도 담는다.
    설명은 explain_draft(템플릿), 신뢰도는 draft가 확정(룰 산출).
    created_at은 now 앵커 전파용(replay 결정성) — 미지정 시 벽시계.
    """
    existing = store.get_anomaly(draft.anomaly_id)
    if existing is not None:
        return existing

    # 항공기 기반 유형은 callsign 보강(설명 품질용). involves의 Aircraft에서 가져온다.
    if "callsign" not in draft.signal:
        ac_map = store.aircraft_map()
        for dst_type, dst_id in draft.involves:
            if dst_type == "Aircraft":
                ac = ac_map.get(dst_id)
                if ac and ac.callsign:
                    draft.signal["callsign"] = ac.callsign
                break

    anomaly = Anomaly(
        id=draft.anomaly_id,
        type=draft.type,
        ts=draft.ts,
        confidence=draft.confidence,
        status="candidate",
        lat=draft.lat,
        lon=draft.lon,
        explanation=explain_draft(draft),
        explainer_backend="template",
        created_at=int(created_at if created_at is not None else time.time()),
        attrs={**draft.signal, "is_synthetic": bool(draft.signal.get("is_synthetic"))},
    )
    # evidence 강제는 store.write_anomaly가 집행(evidence=[]면 EvidenceError).
    store.write_anomaly(anomaly, evidence=draft.evidence, involves=draft.involves)
    return anomaly


def scan_and_create_all(
    store,
    now: Optional[int] = None,
    crosscheck: Optional[CrossCheckSource] = None,
    explainer: Optional[ExplainerBackend] = None,
    do_correlate: bool = True,
    mil_enrich: Optional[MilEnrichmentSource] = None,
) -> dict[str, list[Anomaly]]:
    """전 유형 스캔(비상 스쿽 + dropout + 로이터링 + 군용기 + 위성 근접) → CreateAnomaly.

    탐지 직후 correlation.correlate_all로 correlated_with 링크까지 영속한다(is_correlate).
    군용기 접근은 mil_enrich(공개 DB 플래그, 기본 Null)로 라이브 식별을 저신뢰 보강한다.
    반환: {유형: [이번에 신규 생성된 Anomaly, ...]} (dedup으로 기존은 제외).
    """
    now = int(now if now is not None else time.time())
    crosscheck = crosscheck or NullCrossCheckSource()
    explainer = explainer or TemplateExplainer()
    mil_enrich = mil_enrich or NullMilEnrichment()

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
    # 급기동 룰은 최신 1건이 아니라 기체별 **연속 관측 시퀀스**를 본다(고도·속도 변화율).
    # 한 번의 질의로 전 관측을 읽어 기체별로 묶는다(데모 bbox 규모에선 비용 무시가능).
    observations_by_ac: dict[str, list[Observation]] = {}
    for o in store.query_all_observations():
        observations_by_ac.setdefault(o.aircraft_ref, []).append(o)

    created: dict[str, list[Anomaly]] = {}

    # 비상 스쿽(기존 경로 — AnomalyCandidate). created_at=now로 탐지 시각도 앵커에 고정.
    for a in scan_and_create(
        store, observations=latest, explainer=explainer, created_at=now
    ):
        created.setdefault(a.type, []).append(a)

    # P5 룰(AnomalyDraft).
    drafts: list[AnomalyDraft] = []
    drafts += detect_adsb_dropout(tracks, latest_by_ac, sensitive, now, crosscheck)
    drafts += detect_loitering(tracks, latest_by_ac, now)
    drafts += detect_military_approach(latest, aircraft_map, opareas, now, mil_enrich)
    drafts += detect_satellite_proximity(orbitpasses, region_map, now, satellite_map)
    drafts += detect_rapid_maneuver(tracks, observations_by_ac, now)
    for draft in drafts:
        before = store.get_anomaly(draft.anomaly_id)
        a = create_from_draft(store, draft, created_at=now)
        if before is None:
            created.setdefault(a.type, []).append(a)

    # 교차소스 상관 영속(dropout↔위성통과↔뉴스 = 은닉 정황 그래프).
    if do_correlate:
        correlation.correlate_all(store, now=now)

    return created
