#!/usr/bin/env python
"""발표용 라이브 Foundry 세그먼트 드라이버 (실연).

`scripts/demo_foundry.sh`가 `.venv312` + `SKAI_STORE=foundry`로 이 모듈을 호출한다.
발표 중 한 번 실행해 로컬 스택이 아니라 **실 Palantir Foundry**에서 파이프라인이 도는 것을
라이브로 보여준다:

  (a) OpenSky 1사이클 인제스트 → Palantir Aircraft/Observation (observed_as FK 자동 형성)
  (b) 합성 비상 스쿽(7500=하이재킹) 1건 주입 → 룰 엔진 탐지 → write_anomaly
      (근거 강제 + §12 무해 ApplyActionFailed 에러 흡수) → Foundry Anomaly + evidenced_by
      (→Observation)·involves(→Aircraft) 엣지
  (c) confirm 전이 → Foundry confirm-anomaly 동기 (status candidate→confirmed)

## 데모 자산 (P7 §13 '순증 0'과 구분)
(b)(c)가 만든 Anomaly·합성 Observation·Aircraft는 발표 직후 Palantir Object Explorer에서
근거 그래프·상태를 클릭해 보여주기 위해 **의도적으로 남긴다**(= '데모 자산'). P7 검증
스크립트가 지키던 "순증 0"과 다르다. 과도 누적 방지를 위해 매 실행 시작에 직전 데모 자산
(합성 Anomaly·Observation·Aircraft)을 먼저 삭제한다. 발표 종료 후 전량 삭제도 `... cleanup`.

## 실패 안전 (데모 붕괴 방지)
- **Foundry 연결 실패 = 하드 중단**(exit 3) + 폴백 안내: 이 세그먼트만 스킵하고
  `scripts/demo.sh replay`(네트워크 0 로컬 데모)로 계속.
- **OpenSky/네트워크 실패 = (a)만 스킵**하고 (b)(c) 진행(Foundry만 필요). 세그먼트의 핵심인
  이상징후 생성·confirm 서사는 그대로 시연된다.
- OpenSky 호출은 **실행당 정확히 1회**.

시크릿(토큰·호스트네임) 값은 출력하지 않는다.
"""

from __future__ import annotations

import os
import sys
import time

# ── 데모 자산 식별자 (정리·dedup용) ──────────────────────────────────────────
# 합성 식별자는 **실행마다 유니크**하게 만든다(icao24 = f"{DEMO_STEM}{run_ts}"). 이유:
# 비상 스쿽 anomaly_id = f"anomaly-emergency_squawk-{aircraft_ref}-{window}"(rules.py)라
# aircraft_ref가 고정이면 같은 시간창(600s) 내 재실행이 **동일 anomaly PK를 삭제→재생성**하고,
# Foundry가 evidenced_by 링크 tombstone을 남겨 `LinkAlreadyExists`로 실패한다(리허설에서 실측).
# run_ts를 aircraft_ref에 넣으면 PK가 매 실행 유니크 → churn 없음. 정리는 접두 매칭으로.
DEMO_STEM = (
    "skaidemo"  # 합성 식별자 접두(비-hex = 합성임이 화면에 드러남). 정리 prefix.
)
# 비상 스쿽 anomaly_id 접두(정리 prefix). aircraft_ref가 DEMO_STEM으로 시작하므로 매칭됨.
DEMO_ANOMALY_PREFIX = f"anomaly-emergency_squawk-{DEMO_STEM}"

SAMPLE_N = int(os.environ.get("DEMO_FOUNDRY_SAMPLE_N", "3"))  # (a) 실 항적 write 상한

# 로컬 소재는 throwaway(발표 표시 대상은 Foundry). 매 실행 새로 만들어 anomaly dedup 회피.
LOCAL_DB = os.path.join(
    os.path.dirname(__file__), os.pardir, "data", "demo", "skai_foundry_local.db"
)


def banner(t: str) -> None:
    print("\n" + "═" * 70 + f"\n {t}\n" + "═" * 70)


def ok(cond: bool) -> str:
    return "OK ✓" if cond else "FAIL ✗"


def _fallback_notice() -> None:
    """Foundry 다운 시 발표자용 폴백 안내(데모 붕괴 방지)."""
    print("\n" + "!" * 70)
    print(" 폴백: 이 Foundry 세그먼트만 스킵하고 로컬 데모를 계속하세요.")
    print("   → scripts/demo.sh replay  (네트워크 0 · 결정적, 발표 백본은 그대로)")
    print(
        "   대본상 이 25~30초는 스텝 ⑦ 클로징을 여유있게 늘려 흡수합니다(demo.md §3)."
    )
    print("!" * 70)


# ── 정리 (누적 방지 / 전량 삭제 공용) ─────────────────────────────────────────
def cleanup_demo_assets(fs, verbose: bool = True) -> tuple[int, int, int]:
    """합성 데모 자산(Anomaly·Observation·Aircraft)을 접두 매칭으로 Foundry에서 삭제.

    합성 식별자가 실행마다 유니크(DEMO_STEM+ts)라 접두로 이전 실행분을 전부 잡는다. 실
    OpenSky 인제스트분(실 hex)은 접두가 달라 건드리지 않는다. best-effort(개별 실패는 경고).
    반환: (삭제 Anomaly, 삭제 Observation, 삭제 Aircraft).
    삭제 순서 = Anomaly → Observation → Aircraft(참조 방향 역순).
    """
    del_anom = del_obs = del_ac = 0
    for d in fs._list_objects("Anomaly"):
        aid = d.get("anomalyId")
        if aid and str(aid).startswith(DEMO_ANOMALY_PREFIX):
            try:
                fs._apply("delete-anomaly", {"Anomaly": aid})
                del_anom += 1
            except Exception as e:  # noqa: BLE001
                print(f"  [정리] delete-anomaly {aid} 실패: {type(e).__name__}")
    for d in fs._list_objects("Observation"):
        oid = d.get("obsId")
        if oid and str(oid).startswith(DEMO_STEM):
            try:
                fs._apply("delete-observation", {"Observation": oid})
                del_obs += 1
            except Exception as e:  # noqa: BLE001
                print(f"  [정리] delete-observation {oid} 실패: {type(e).__name__}")
    for d in fs._list_objects("Aircraft"):
        icao = d.get("icao24")
        if icao and str(icao).startswith(DEMO_STEM):
            try:
                fs._apply("delete-aircraft", {"Aircraft": icao})
                del_ac += 1
            except Exception as e:  # noqa: BLE001
                print(f"  [정리] delete-aircraft {icao} 실패: {type(e).__name__}")
    if verbose:
        print(
            f"  직전 데모 자산 정리: Anomaly {del_anom} · Observation {del_obs} · "
            f"Aircraft {del_ac} 삭제"
        )
    return del_anom, del_obs, del_ac


def full_cleanup(fs) -> None:
    """발표 종료 후 전량 삭제 — 합성 Anomaly·Observation·Aircraft."""
    banner("CLEANUP — 데모 자산 전량 삭제")
    a, o, ac = cleanup_demo_assets(fs, verbose=False)
    print(f"  삭제: Anomaly {a} · Observation {o} · Aircraft {ac}")
    print("\n정리 완료. 실 OpenSky 인제스트분(실 hex 항적)은 실데이터라 보존합니다.")


# ── (a) OpenSky 1사이클 인제스트 ──────────────────────────────────────────────
def phase_a_ingest(store, fs) -> bool:
    banner("[A] OpenSky 1사이클 → Palantir Aircraft/Observation (observed_as FK)")
    import httpx

    from connectors.opensky import fetch_states
    from ontology import mapping
    from ontology.model import KADIZ_BBOX

    c0 = fs.counts()
    try:
        fetched_at = int(time.time())
        with httpx.Client() as client:
            states, source_url = fetch_states(client, KADIZ_BBOX)  # OpenSky 1회 호출
    except Exception as e:  # noqa: BLE001  네트워크/OpenSky 실패 = (a)만 스킵
        print(f"  OpenSky 호출 실패({type(e).__name__}) → (a) 인제스트 스킵.")
        print(
            "  네트워크가 불안정합니다. (b)(c)는 Foundry만 필요하므로 계속 진행합니다."
        )
        return False

    print(f"  실 항적 {len(states)}건 수신 → 상위 {SAMPLE_N}건만 Palantir에 write")
    written_icaos: list[str] = []
    for st in states:
        if len(written_icaos) >= SAMPLE_N:
            break
        ev = mapping.opensky_state_to_event(st, source_url, fetched_at)
        if ev is None:
            continue
        ac = mapping.event_to_aircraft(ev)
        obs = mapping.event_to_observation(ev)
        try:
            # 이미 있는 실 항적은 store가 ObjectAlreadyExists를 dedup(크래시 없이 skip).
            store.write_aircraft(ac)
            store.write_observation(obs)  # aircraftIcao24 FK → observed_as 자동
            store.link("Aircraft", ac.icao24, "observed_as", "Observation", obs.id)
            written_icaos.append(ac.icao24)
        except Exception as e:  # noqa: BLE001
            print(f"  write 실패({ac.icao24}): {type(e).__name__}")

    time.sleep(1.0)  # Foundry 인덱싱 반영 대기(read-back 안정)
    c1 = fs.counts()
    print(
        f"  Foundry 카운트: Aircraft {c0['aircraft']}→{c1['aircraft']} "
        f"(+{c1['aircraft'] - c0['aircraft']}) · "
        f"Observation {c0['observation']}→{c1['observation']} "
        f"(+{c1['observation'] - c0['observation']})"
    )
    # observed_as FK traverse 예시(실 항적 1건).
    if written_icaos:
        sample = written_icaos[0]
        linked = fs.query_observations_for(sample)
        print(
            f"  observed_as FK traverse: Aircraft {sample} → 관측 {len(linked)}건  "
            f"{ok(len(linked) > 0)}"
        )
        print(
            f"  ▶ Palantir Object Explorer에서 Aircraft {sample} 를 열고 "
            "observations 링크를 클릭하세요."
        )
    return bool(written_icaos)


# ── (b) 합성 비상 스쿽 → write_anomaly (근거 강제 · 에러 흡수) ─────────────────
def phase_b_anomaly(store, fs):
    banner("[B] 합성 비상 스쿽(7500=하이재킹) → 이상징후 생성 (근거 강제)")
    from anomaly.actions import scan_and_create
    from ontology.model import Aircraft, Observation

    now = int(time.time())
    demo_icao = f"{DEMO_STEM}{now}"  # 실행마다 유니크 PK(PK 재사용 churn 회피)
    ac = Aircraft(
        icao24=demo_icao,
        callsign="DEMO7500",
        registration="SKAI-DEMO",
        type="UNKNOWN",
        is_military=False,
    )
    obs = Observation(
        id=f"{demo_icao}-{now}",
        aircraft_ref=demo_icao,
        ts=now,
        lat=36.8,
        lon=124.2,  # KADIZ 내부
        alt=9000.0,
        velocity=220.0,
        heading=270.0,
        squawk="7500",  # 비상(하이재킹) — str 비교(P0A gotcha)
        source="synthetic",  # 합성임 명시(정직성 — attrs.is_synthetic=True)
        source_url="synthetic://skai-demo/emergency-squawk-7500",
    )
    store.write_aircraft(ac)
    store.write_observation(obs)  # evidenced_by 대상(Foundry)
    print(
        f"  합성 주입: Aircraft {demo_icao} · Observation {obs.id} (source=synthetic)"
    )

    # 룰 엔진 탐지 → CreateAnomaly(evidence 강제) → dual-write(로컬 권위본 + Foundry 스파인).
    created = scan_and_create(store, observations=[obs], created_at=now)
    if not created:
        print("  [경고] 룰 엔진이 이상징후를 만들지 못함(예상 밖) → (b) 실패.")
        return None
    anomaly = created[0]
    aid = anomaly.id
    print(
        f"  룰 엔진 탐지 → Anomaly 생성: {aid}\n"
        f"    type={anomaly.type} · status={anomaly.status} · "
        f"confidence={anomaly.confidence}"
    )

    time.sleep(1.0)
    # Foundry read-back: 객체 + evidenced_by(→Observation) + involves(→Aircraft) 엣지.
    fd = fs._get_object("Anomaly", aid)
    scalar_ok = fd is not None and fd.get("anomalyId") == aid
    ev_tr = fs._traverse("Anomaly", aid, "observations")
    inv_tr = fs._traverse("Anomaly", aid, "aircraft")
    ev_ok = obs.id in ev_tr
    inv_ok = demo_icao in inv_tr
    print(
        f"  Foundry write_anomaly: 무해 ApplyActionFailed는 read-back으로 흡수(§12)\n"
        f"    Anomaly 스칼라 read-back        {ok(scalar_ok)}\n"
        f"    evidenced_by → Observation      {ok(ev_ok)}\n"
        f"    involves     → Aircraft         {ok(inv_ok)}"
    )
    print(
        f"  ▶ Palantir Object Explorer에서 Anomaly {aid} 를 열어 "
        "evidenced_by(근거 관측)·involves(관련 기체) 그래프를 보여주세요."
    )
    return aid if (scalar_ok and ev_ok) else None


# ── (c) confirm 전이 ──────────────────────────────────────────────────────────
def phase_c_confirm(store, fs, aid: str) -> bool:
    banner("[C] confirm 전이 (분석가 승인 · human-on-the-loop)")
    from anomaly.actions import confirm_anomaly

    confirm_anomaly(store, aid)  # dual: 로컬 권위본 + Foundry confirm-anomaly
    time.sleep(1.0)
    fd = fs._get_object("Anomaly", aid)
    status = fd.get("status") if fd else None
    good = status == "confirmed"
    print(f"  Foundry status: candidate → {status!r}  {ok(good)}")
    print(
        f"  ▶ Palantir Object Explorer에서 같은 Anomaly {aid} 의 "
        "status = confirmed 를 보여주세요."
    )
    return good


# ── 오케스트레이션 ────────────────────────────────────────────────────────────
def run() -> int:
    banner("SKAI · 라이브 Foundry 세그먼트 (Palantir 실연)")

    # 로컬 소재 db는 매 실행 새로(anomaly dedup 회피 — Foundry가 표시 권위본).
    os.makedirs(os.path.dirname(LOCAL_DB), exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(LOCAL_DB + suffix)
        except FileNotFoundError:
            pass

    os.environ["SKAI_STORE"] = "foundry"
    from ontology.store_foundry import make_store

    # [0] Foundry 연결 — 실패 시 하드 중단 + 폴백.
    try:
        store = make_store(LOCAL_DB)  # HybridStore
        fs = store.foundry
        c0 = fs.counts()
    except Exception as e:  # noqa: BLE001
        print(f"\n[0] Foundry 연결 실패: {type(e).__name__} — {str(e)[:120]}")
        _fallback_notice()
        return 3
    print(
        f"\n[0] Foundry 연결 OK ✓  기준 카운트: "
        f"Aircraft {c0['aircraft']} · Observation {c0['observation']} · "
        f"Anomaly {store.counts().get('anomaly', 0)}"
    )
    cleanup_demo_assets(fs)  # 직전 데모 자산 정리(누적 방지)

    # disjoint 해소(db-regime.md): 로컬 미러(skai_foundry_local.db)가 자연 assess 흐름에
    # 자립하도록 KADIZ region을 로컬에 심는다. foundry 모드 assess는 anomaly·region을 로컬에서
    # 읽으므로(HybridStore 설계), region이 없으면 지역명이 id('KADIZ') 폴백된다. 관측·기체는
    # Foundry 소재라 여기 로컬엔 없어도 됨(assess가 Foundry에서 읽음). write_region은 로컬 전용.
    from ontology.model import KADIZ_REGION

    store.write_region(KADIZ_REGION)

    a_ok = phase_a_ingest(store, fs)  # OpenSky 실패해도 계속
    aid = phase_b_anomaly(store, fs)
    c_ok = phase_c_confirm(store, fs, aid) if aid else False

    banner("판정")
    b_ok = aid is not None
    tail = "" if a_ok else "(네트워크 — 스킵/비치명)"
    print(f"  (a) OpenSky 인제스트     {ok(a_ok)}   {tail}")
    print(f"  (b) 이상징후 생성·근거    {ok(b_ok)}")
    print(f"  (c) confirm 전이         {ok(c_ok)}")
    if b_ok and c_ok:
        verdict = "DEMO-FOUNDRY-OK" if a_ok else "DEMO-FOUNDRY-OK (a 제외 — 네트워크)"
        print(f"\n[{verdict}]")
        print(
            "  데모 자산 유지: 위 Anomaly·합성 Observation·Aircraft는 Object Explorer용으로 "
            "남겨둡니다(P7 §13 '순증 0'과 구분되는 '데모 자산').\n"
            "  발표 종료 후 전량 삭제: scripts/demo_foundry.sh cleanup"
        )
        return 0
    # (b)/(c) 실패 = Foundry 쓰기 이상 → 폴백.
    print("\n[DEMO-FOUNDRY-부분/FAIL] Foundry 쓰기 경로 이상.")
    _fallback_notice()
    return 2


def cleanup_only() -> int:
    os.environ["SKAI_STORE"] = "foundry"
    from ontology.store_foundry import make_store

    try:
        store = make_store(LOCAL_DB)
    except Exception as e:  # noqa: BLE001
        print(f"Foundry 연결 실패: {type(e).__name__} — {str(e)[:120]}")
        return 3
    full_cleanup(store.foundry)
    return 0


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "cleanup":
        return cleanup_only()
    if mode in ("run", ""):
        return run()
    print(f"사용법: {sys.argv[0]} [run|cleanup]")
    return 1


if __name__ == "__main__":
    sys.exit(main())
