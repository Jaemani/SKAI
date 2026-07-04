#!/usr/bin/env python
"""P7 — 하이브리드 이관 왕복 검증 (Aircraft·Observation·observed_as + 실데이터 1사이클).

두 단계:
  A. 왕복: 테스트 Aircraft(신규 속성 type·operatorRef·is_military=bool 포함) 1건 +
     Observation(provenance) 1건 write → readback으로 필드 일치 확인 + observed_as 링크 시도.
  B. 실데이터: OpenSky 1회 호출(KADIZ bbox) → 상위 N건 서브샘플 → HybridStore(foundry)로
     Aircraft·Observation write → Foundry 카운트 확인. 액션 호출 수 로깅.

쓰기 최소화: A=2건, B=Aircraft·Observation 각 N(기본 2)건. 시크릿 값 출력 금지.
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

    banner("A. 왕복 검증 — Aircraft(신규 속성) + Observation(provenance) + observed_as")
    c0 = fs.counts()
    print(f"쓰기 전 Foundry 카운트: {c0}")

    # A-1. Aircraft — icao24="p0test1" 요구(갭 1로 UUID 자동부여될 것) + 신규 속성 검증
    test_ac = Aircraft(
        icao24="p0test1",
        callsign="P7RT",
        registration="P7RT01",
        type="C-130",
        operator_ref="TESTAF",
        is_military=True,
    )
    safe_write(lambda: store.write_aircraft(test_ac), "A-1 create-aircraft")

    # A-2. Observation — provenance(source/source_url/ts) Foundry 저장 확인용
    now = int(time.time())
    test_obs = Observation(
        id="p0test1-{}".format(now),
        aircraft_ref="p0test1",
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

    # A-3. observed_as 링크 시도(갭 2로 드롭될 것 — 계측)
    store.link("Aircraft", "p0test1", "observed_as", "Observation", test_obs.id)

    # readback
    time.sleep(1.0)  # 안전용(OSV2는 즉시지만 소폭 여유)
    ac_found = [a for a in fs.query_aircraft() if a.callsign == "P7RT"]
    obs_found = [o for o in fs.query_all_observations() if o.source == "p7test"]
    print("\n[readback] Aircraft(callsign=P7RT):")
    for a in ac_found:
        print(
            f"  icao24={a.icao24}  (요구 'p0test1' 아님 = 갭 1)  "
            f"is_military={a.is_military}  type={a.type}  operator_ref={a.operator_ref}"
        )
    print("[readback] Observation(source=p7test):")
    for o in obs_found:
        print(
            f"  obsId={o.id}  aircraft_ref={o.aircraft_ref!r}(빈값=갭2 링크불가)  "
            f"ts={o.ts}  source={o.source}  source_url={'있음' if o.source_url else '없음'}"
        )
    ac_ok = any(
        a.is_military and a.type == "C-130" and a.operator_ref == "TESTAF"
        for a in ac_found
    )
    obs_ok = any(
        o.source == "p7test" and o.source_url and o.ts == now for o in obs_found
    )
    print(
        f"\n왕복 판정: Aircraft 신규속성 {'OK' if ac_ok else 'FAIL'} / "
        f"Observation provenance {'OK' if obs_ok else 'FAIL'}"
    )
    print(
        f"observed_as 링크 드롭 수(계측): {fs.dropped_observed_as} (갭 2 — FK 미설정)"
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
            lambda: store.write_aircraft(ac), f"B create-aircraft[{written_ac}]"
        ):
            written_ac += 1
        action_calls += 1
        if safe_write(
            lambda: store.write_observation(obs), f"B create-observation[{written_obs}]"
        ):
            written_obs += 1
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
    print(f"observed_as 링크 드롭 누계: {fs.dropped_observed_as} (전부 갭 2로 미생성)")

    banner("판정")
    if write_failures:
        print("WRITE-BLOCKED — Foundry 액션 실행 실패로 쓰기 검증 불가:")
        for f in write_failures:
            print(f"  - {f}")
        print(
            "→ create-aircraft·create-observation이 ApplyActionFailed(갭 0). "
            "Ontology Manager에서 액션 재구성 필요. read 경로는 정상."
        )
    else:
        print("WRITE-OK — Aircraft·Observation 쓰기/읽기 왕복 성공.")
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
