#!/usr/bin/env python
"""P7 — 하이브리드 이관 왕복 검증 (Aircraft·Observation·observed_as FK + 실데이터 1사이클).

두 단계:
  A. 왕복: Aircraft(type·operatorRef·is_military) 1건 + Observation(provenance+FK) 1건 write
     → readback 필드 일치 + observed_as FK 링크(query_observations_for) 1건 traverse 확인.
     같은 사이클 재실행(dedup): ObjectAlreadyExists → skip, 크래시 없이 통과 확인.
  B. 실데이터: OpenSky 1회 호출(KADIZ bbox) → 상위 N건 서브샘플 → HybridStore(foundry)로
     Aircraft·Observation write(실 icao24 hex PK) → Foundry 카운트 확인. 액션 호출 수 로깅.

쓰기 최소화: A=Aircraft·Observation 각 1건(dedup로 2번째 skip), B=N(기본 2)건.
시크릿 값 출력 금지.
"""

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))

# 실데이터 서브샘플 상한(레이트·오염 최소화).
SAMPLE_N = int(os.environ.get("P7_SAMPLE_N", "2"))

from ontology import mapping  # noqa: E402
from ontology.model import KADIZ_BBOX, Aircraft, Observation  # noqa: E402
from ontology.store_foundry import HybridStore  # noqa: E402


def banner(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def main():
    store = HybridStore(
        db_path=os.path.join(
            os.path.dirname(__file__), os.pardir, "data", "p7_hybrid.db"
        )
    )
    fs = store.foundry  # FoundryOntologyStore (계측·직접 read용)

    write_failures: list[str] = []

    def safe_write(fn, label):
        """write 실패(ApplyActionFailed 등)를 잡아 BLOCKED 계측 — 스크립트가 안 죽게."""
        try:
            fn()
            return True
        except Exception as e:
            name = getattr(e, "error_name", None) or type(e).__name__
            first = str(e).strip().splitlines()[0][:80]
            write_failures.append(f"{label}: {name}")
            print(f"  [WRITE FAIL] {label} → {name} ({first})")
            return False

    banner(
        "A. 왕복 검증 — Aircraft(신규 속성) + Observation(FK) + observed_as 링크 traverse"
    )
    c0 = fs.counts()
    print(f"쓰기 전 Foundry 카운트: {c0}")

    # A-1. Aircraft — 실 icao24 hex를 PK로(§7-0: newParameter=icao24 바인딩)
    test_icao = "p7t2test"
    test_ac = Aircraft(
        icao24=test_icao,
        callsign="P7RT",
        registration="P7RT01",
        type="C-130",
        operator_ref="TESTAF",
        is_military=True,
    )
    safe_write(lambda: store.write_aircraft(test_ac), "A-1 create-aircraft")

    # A-2. Observation — aircraftIcao24 FK로 observed_as 자동 형성(§7-2)
    now = int(time.time())
    test_obs = Observation(
        id="p7t2test-{}".format(now),
        aircraft_ref=test_icao,
        ts=now,
        lat=36.5,
        lon=124.5,
        alt=9500.0,
        velocity=210.0,
        heading=270.0,
        squawk="7700",
        source="p7test",
        source_url="https://opensky-network.org/api/states/all?p7=roundtrip",
    )
    safe_write(lambda: store.write_observation(test_obs), "A-2 create-observation")

    # A-3. observed_as link() 호출 — no-op(FK 자동 형성), 크래시 없어야 함
    store.link("Aircraft", test_icao, "observed_as", "Observation", test_obs.id)
    print("  observed_as link() no-op 확인 OK (FK 자동 형성 — 별도 액션 불필요)")

    # readback
    time.sleep(1.0)
    ac_found = [a for a in fs.query_aircraft() if a.icao24 == test_icao]
    obs_found = [o for o in fs.query_all_observations() if o.source == "p7test"]
    print("\n[readback] Aircraft(icao24={}):".format(test_icao))
    for a in ac_found:
        print(
            f"  icao24={a.icao24}  is_military={a.is_military}  "
            f"type={a.type}  operator_ref={a.operator_ref}"
        )
    print("[readback] Observation(source=p7test):")
    for o in obs_found:
        print(
            f"  obsId={o.id}  aircraft_ref={o.aircraft_ref!r}  "
            f"ts={o.ts}  source={o.source}  source_url={'있음' if o.source_url else '없음'}"
        )

    # observed_as FK 링크 traverse: query_observations_for(icao24) 1건 이상 확인
    linked_obs = fs.query_observations_for(test_icao)
    link_ok = len(linked_obs) > 0
    print(
        f"\n[observed_as FK traverse] query_observations_for({test_icao!r}): {len(linked_obs)}건 → {'OK' if link_ok else 'FAIL(FK 링크 미형성)'}"
    )

    ac_ok = any(
        a.icao24 == test_icao and a.is_military and a.type == "C-130" for a in ac_found
    )
    obs_ok = any(
        o.source == "p7test"
        and o.source_url
        and o.ts == now
        and o.aircraft_ref == test_icao
        for o in obs_found
    )
    print(
        f"\n왕복 판정: Aircraft PK+속성 {'OK' if ac_ok else 'FAIL'} / "
        f"Observation provenance+FK {'OK' if obs_ok else 'FAIL'} / "
        f"observed_as FK traverse {'OK' if link_ok else 'FAIL'}"
    )

    # A-4. dedup 검증 — 같은 사이클 재실행: ObjectAlreadyExists → skip, 크래시 금지
    banner(
        "A-4. dedup 검증 — 같은 Aircraft·Observation 재실행(ObjectAlreadyExists → skip)"
    )
    dedup_ac_ok = safe_write(
        lambda: store.write_aircraft(test_ac), "A-4 dedup write_aircraft"
    )
    dedup_obs_ok = safe_write(
        lambda: store.write_observation(test_obs), "A-4 dedup write_observation"
    )
    c_dedup = fs.counts()
    # dedup 후 카운트가 증가하지 않아야 함
    dedup_ok = c_dedup.get("aircraft", 0) == c0.get("aircraft", 0) + (
        1 if ac_ok else 0
    ) and c_dedup.get("observation", 0) == c0.get("observation", 0) + (
        1 if obs_ok else 0
    )
    print(f"dedup 후 카운트: {c_dedup}")
    print(
        f"dedup 판정: {'OK (카운트 불변)' if dedup_ok else 'WARN (카운트 변동 — dedup 확인 필요)'}"
    )

    banner(
        f"B. 실데이터 1사이클 — OpenSky KADIZ bbox → 상위 {SAMPLE_N}건 → Foundry write"
    )
    import httpx

    from connectors.opensky import fetch_states

    fetched_at = int(time.time())
    action_calls = 0
    with httpx.Client() as client:
        states, source_url = fetch_states(client, KADIZ_BBOX)
    print(f"OpenSky states 수신: {len(states)}건 → 서브샘플 {SAMPLE_N}건만 write")

    written_ac, written_obs = 0, 0
    for st in states:
        ev = mapping.opensky_state_to_event(st, source_url, fetched_at)
        if ev is None:
            continue
        ac = mapping.event_to_aircraft(ev)
        obs = mapping.event_to_observation(ev)
        action_calls += 1
        if safe_write(
            lambda _ac=ac: store.write_aircraft(_ac), f"B create-aircraft[{written_ac}]"
        ):
            written_ac += 1
        action_calls += 1
        if safe_write(
            lambda _obs=obs: store.write_observation(_obs),
            f"B create-observation[{written_obs}]",
        ):
            written_obs += 1
        # observed_as link() no-op — write_observation의 FK로 이미 형성됨
        store.link("Aircraft", ac.icao24, "observed_as", "Observation", obs.id)
        if action_calls >= SAMPLE_N * 2:
            break

    print(
        f"write 성공: Aircraft {written_ac}건 + Observation {written_obs}건 "
        f"(create 액션 시도 {action_calls}회)"
    )

    time.sleep(1.0)
    c1 = fs.counts()
    banner("결과 — Foundry 객체 카운트")
    print(f"쓰기 전: {c0}")
    print(f"쓰기 후: {c1}")
    print(
        f"델타: aircraft +{c1['aircraft'] - c0['aircraft']}, "
        f"observation +{c1['observation'] - c0['observation']}"
    )

    banner("판정")
    ingest_ok = not write_failures and ac_ok and obs_ok and link_ok
    if write_failures:
        print("WRITE-BLOCKED — Foundry 액션 실행 실패:")
        for f in write_failures:
            print(f"  - {f}")
    else:
        print("INGEST-OK" if ingest_ok else "INGEST-PARTIAL")
        print(f"  Aircraft PK+속성: {'OK' if ac_ok else 'FAIL'}")
        print(f"  Observation provenance+FK: {'OK' if obs_ok else 'FAIL'}")
        print(f"  observed_as FK traverse(1건): {'OK' if link_ok else 'FAIL'}")
        print(f"  dedup(재실행): {'OK' if dedup_ok else 'WARN'}")
    print("\nDONE")
    return 0 if ingest_ok else 1


if __name__ == "__main__":
    sys.exit(main())
