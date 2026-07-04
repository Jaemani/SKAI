"""scripts/inject_synthetic.py — 합성 비상 스쿽 주입기 (P2 데모 재현성).

라이브 KADIZ에는 비상 스쿽(7700 등)이 상시 뜨지 않는다(P1 발견 #3). 이 스크립트는
커넥터를 우회해 store에 직접 합성 Observation을 write한 뒤 탐지 파이프라인을 돌린다.

**명시 실행 시에만** 동작한다(자동 스케줄 없음). provenance는 그대로 유지한다:
source="synthetic", source_url="synthetic://..." → validate_provenance 통과 = 합성도
출처를 남긴다. 설명문에는 "[합성 시나리오]"가 표기된다(explainer가 source로 판별).

사용법:
    .venv/bin/python -m scripts.inject_synthetic                 # 기본 7700 1건 주입+탐지
    .venv/bin/python -m scripts.inject_synthetic --squawk 7500   # 하이재킹 코드
    .venv/bin/python -m scripts.inject_synthetic --icao24 SYN76 --callsign RADIO01 \
        --squawk 7600 --lat 37.2 --lon 129.5
    SKAI_EXPLAINER=claude .venv/bin/python -m scripts.inject_synthetic  # claude 설명

주입 후 지도(http://localhost:8000)의 타임라인에 candidate로 노출된다.
"""

from __future__ import annotations

import argparse
import time

import time as _time

from anomaly.actions import scan_and_create, scan_and_create_all
from anomaly.explainer import get_explainer
from anomaly.rules import EMERGENCY_SQUAWKS
from ontology.custody import rebuild_tracks
from ontology.model import KADIZ_REGION, Aircraft, Observation
from ontology.store_local import DEFAULT_DB, LocalOntologyStore
from scripts.scenarios import SCENARIOS, apply_scenario, scenario_by_id


def inject(
    store: LocalOntologyStore,
    icao24: str,
    callsign: str,
    squawk: str,
    lat: float,
    lon: float,
    alt: float,
    velocity: float,
    heading: float,
    explainer=None,
):
    """합성 Observation write + observed_as 링크 + Track 재구성 + 탐지.

    반환: (Observation, 신규 생성된 Anomaly 리스트).
    """
    ts = int(time.time())
    source_url = f"synthetic://skai/inject/{icao24}/{ts}"  # 합성도 출처를 남긴다

    aircraft = Aircraft(icao24=icao24, callsign=callsign)
    obs = Observation(
        id=f"{icao24}-{ts}",  # (icao24, ts) 자연키
        aircraft_ref=icao24,
        ts=ts,
        lat=lat,
        lon=lon,
        alt=alt,
        velocity=velocity,
        heading=heading,
        squawk=squawk,  # str — 비상 코드 문자열 비교
        on_ground=False,
        source="synthetic",  # ← 합성 표식 (explainer가 "[합성 시나리오]" 표기)
        source_url=source_url,  # ← provenance 유지 (validate_provenance 통과)
        attrs={"synthetic": True, "note": "P2 데모 주입 비상 스쿽"},
    )

    store.write_region(KADIZ_REGION)  # 지도 폴리곤 보장(빈 DB에서도 렌더)
    store.write_aircraft(aircraft)
    store.write_observation(obs)  # provenance 강제 통과해야 저장됨
    store.link("Aircraft", icao24, "observed_as", "Observation", obs.id)
    rebuild_tracks(store)

    # 탐지: 방금 주입한 관측을 룰에 통과 → CreateAnomaly(evidence=[obs.id]).
    created = scan_and_create(store, observations=[obs], explainer=explainer)
    return obs, created


def inject_scenario(store: LocalOntologyStore, scenario_id: str, now: int) -> dict:
    """선언적 시나리오(P5) 주입 → 전 유형 탐지 + correlated_with 상관 영속.

    scenario_id="all"이면 모든 시나리오를 순차 주입(데모 보드에 전 유형을 한 번에 올림).
    반환: {유형: [신규 Anomaly, ...]} 합계.
    """
    from anomaly.crosscheck import SyntheticMirrorSource

    targets = SCENARIOS if scenario_id == "all" else [scenario_by_id(scenario_id)]
    if any(t is None for t in targets):
        raise SystemExit(
            f"알 수 없는 시나리오: {scenario_id} "
            f"(가능: all, {', '.join(s['id'] for s in SCENARIOS)})"
        )
    merged_mirror = SyntheticMirrorSource()
    for sc in targets:
        m = apply_scenario(store, sc, now)
        if m is not None:  # 미러 데이터 병합(all 주입 시 dropout 교차 판정 유지)
            merged_mirror.absent |= m.absent
            merged_mirror.present |= m.present
    return scan_and_create_all(store, now=now, crosscheck=merged_mirror)


def main() -> None:
    p = argparse.ArgumentParser(
        description="합성 이상징후 주입기 (P2 스쿽 + P5 시나리오)"
    )
    p.add_argument("--squawk", default="7700", choices=sorted(EMERGENCY_SQUAWKS))
    p.add_argument("--icao24", default="synth01")
    p.add_argument("--callsign", default="SYNTH01")
    p.add_argument("--lat", type=float, default=36.5)  # KADIZ 내부
    p.add_argument("--lon", type=float, default=127.0)
    p.add_argument("--alt", type=float, default=10000.0)
    p.add_argument("--velocity", type=float, default=230.0)
    p.add_argument("--heading", type=float, default=90.0)
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument(
        "--scenario",
        default=None,
        help="P5 선언적 시나리오 주입(예: narrative_hidden, all). 지정 시 스쿽 인자 무시.",
    )
    p.add_argument(
        "--now",
        type=int,
        default=None,
        help="시나리오 now 앵커(기본 벽시계). '지금' 질의창에 맞추려면 현재 시각.",
    )
    args = p.parse_args()

    store = LocalOntologyStore(args.db)

    # P5 시나리오 경로 — 선언적 시나리오 주입(전 유형 + correlated_with).
    if args.scenario:
        now = args.now if args.now is not None else int(_time.time())
        created = inject_scenario(store, args.scenario, now)
        total = sum(len(v) for v in created.values())
        print(f"[inject] 시나리오 '{args.scenario}' 주입 (now={now})")
        for t, v in created.items():
            for a in v:
                print(
                    f"  + {t}: {a.id} conf={a.confidence:.2f} corr="
                    f"{len(store.query_correlations(a.id))}"
                )
        print(
            f"[inject] 신규 이상징후 {total}건 · 상관 링크 {len(store.query_all_correlations())}"
        )
        print(f"[inject] 누적 카운트: {store.counts()}")
        print("[inject] 지도 확인: http://localhost:8000")
        return

    explainer = get_explainer()  # SKAI_EXPLAINER (기본 template)
    obs, created = inject(
        store,
        icao24=args.icao24,
        callsign=args.callsign,
        squawk=args.squawk,
        lat=args.lat,
        lon=args.lon,
        alt=args.alt,
        velocity=args.velocity,
        heading=args.heading,
        explainer=explainer,
    )

    print(
        f"[inject] 합성 관측 write: id={obs.id} squawk={obs.squawk} "
        f"source={obs.source} url={obs.source_url}"
    )
    if created:
        for a in created:
            print(
                f"[inject] Anomaly 생성: id={a.id} type={a.type} "
                f"status={a.status} confidence={a.confidence:.2f} "
                f"backend={a.explainer_backend}"
            )
            print(f"          설명: {a.explanation}")
            print(f"          근거(evidence): {store.query_evidence_ids(a.id)}")
            print(f"          주체(involves): {store.query_involves_ids(a.id)}")
    else:
        print("[inject] 신규 Anomaly 없음 (dedup — 같은 기체·유형·시간창이 이미 존재)")
    print(f"[inject] 누적 카운트: {store.counts()}")
    print("[inject] 지도 확인: http://localhost:8000 (타임라인에 candidate 노출)")


if __name__ == "__main__":
    main()
