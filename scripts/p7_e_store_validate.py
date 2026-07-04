#!/usr/bin/env python
"""P7 §15 E부 store 동기화 라이브 검증 (2026-07-04).

실 store 코드 경로(HybridStore, SKAI_STORE=foundry)로 E-4 리네임·E-2/E-3 배선을 검증:
  1) 타입 왕복 — write_aircraft·write_observation·write_satellite·write_orbitpass(리네임된
     PK 파라미터로) → Foundry read-back + observed_as(FK)·of(FK)·over(FK, E-2.1) traverse.
  2) anomaly 클린 생성 — write_anomaly가 §12 에러 흡수 경고 없이(=클린 실행) 성공하는지 stderr 감시 +
     evidenced_by·involves·correlatedWith(placeholder=엣지없음) traverse.
  3) confirm 전이 — set_anomaly_status('confirmed') → status read-back.
  4) 정리 — 생성분 전량 delete → Foundry 순증 0, KADIZ Region 유지.
로컬 소재는 임시 db(skai.db 미오염). write 최소. 시크릿 미출력.
"""

import io
import os
import sys
import time
from contextlib import redirect_stderr

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from ontology.model import (  # noqa: E402
    Aircraft,
    Anomaly,
    Observation,
    OrbitPass,
    Satellite,
)
from ontology.store_foundry import make_store  # noqa: E402

T = int(time.time())
TMP_DB = f"/private/tmp/claude-501/p7e-store-{T}.db"

os.environ["SKAI_STORE"] = "foundry"
store = make_store(TMP_DB)  # HybridStore
fs = store.foundry
pf = fs._pf
ONT = fs.ont
A = pf.ontologies.Action
OO = pf.ontologies.OntologyObject
LO = pf.ontologies.LinkedObject

AC_PK = f"p7estore{T}"  # 합성 icao24 (유니크)
OBS_PK = f"{AC_PK}-{T}"
SAT_PK = f"p7esat{T}"
PASS_PK = f"p7epass-{T}"
AN_PK = f"p7e-store-an-{T}"
REGION = "KADIZ"  # 기존 데모 자산(유지)


def _get(o, k):
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def _pk(o):
    return (
        _get(o, "__primaryKey")
        or _get(o, "obsId")
        or _get(o, "icao24")
        or _get(o, "anomalyId")
        or _get(o, "passId")
        or _get(o, "noradId")
        or _get(o, "id")
    )


def count(t):
    return len(list(OO.list(ONT, t)))


def sget(t, pk):
    try:
        return OO.get(ONT, t, pk)
    except Exception:
        return None


def traverse(otype, pk, link):
    try:
        return [_pk(o) for o in LO.list_linked_objects(ONT, otype, pk, link)]
    except Exception as e:
        return f"ERR:{type(e).__name__}:{str(e)[:60]}"


def sec(t):
    print("\n" + "=" * 68 + f"\n{t}\n" + "=" * 68)


def ok(cond):
    return "OK" if cond else "FAIL"


def _delete(action_obj, action, pk):
    try:
        A.apply(
            ONT,
            action,
            parameters={action_obj: pk},
            options={"mode": "VALIDATE_AND_EXECUTE"},
        )
        return True
    except Exception:
        return False


def main():
    types = ("Aircraft", "Observation", "Satellite", "OrbitPass", "Anomaly", "Region")
    sec("0. baseline counts")
    c0 = {t: count(t) for t in types}
    print(c0)
    results: dict[str, str] = {}

    # ── 1. 타입 왕복 (리네임된 PK 파라미터) ──
    sec("1. 타입 왕복 — write_aircraft/observation/satellite/orbitpass (E-4 리네임)")
    warn_buf = io.StringIO()
    with redirect_stderr(warn_buf):
        store.write_aircraft(
            Aircraft(
                icao24=AC_PK, callsign="P7E", registration="P7E-REG", is_military=True
            )
        )
        store.write_observation(
            Observation(
                id=OBS_PK,
                aircraft_ref=AC_PK,
                ts=T,
                lat=36.5,
                lon=124.5,
                squawk="7700",
                on_ground=False,
                source="synthetic",
                source_url="https://skai.local/p7e",
            )
        )
        store.write_satellite(
            Satellite(norad_id=SAT_PK, name="P7E-SAT", object_type="PAYLOAD")
        )
        store.write_orbitpass(
            OrbitPass(
                id=PASS_PK,
                satellite_ref=SAT_PK,
                region_ref=REGION,
                start_ts=T + 3600,
                end_ts=T + 4200,
                max_elevation=42.0,
            )
        )
    ac = sget("Aircraft", AC_PK)
    obv = sget("Observation", OBS_PK)
    results["aircraft_pk"] = ok(ac and _get(ac, "icao24") == AC_PK)
    results["observation_pk"] = ok(obv and _get(obv, "obsId") == OBS_PK)
    print(
        f"  Aircraft read-back icao24={_get(ac, 'icao24')!r} → {results['aircraft_pk']}"
    )
    print(
        f"  Observation read-back obsId={_get(obv, 'obsId')!r} ac={_get(obv, 'aircraftIcao24')!r} → {results['observation_pk']}"
    )
    # observed_as (FK)
    oa = traverse("Observation", OBS_PK, "aircraft")
    results["observed_as"] = ok(isinstance(oa, list) and AC_PK in oa)
    print(f"  observed_as Observation.aircraft → {oa} → {results['observed_as']}")
    # of (FK) / over (FK, E-2.1)
    of_tr = traverse("OrbitPass", PASS_PK, "satellite")
    results["of_fk"] = ok(isinstance(of_tr, list) and SAT_PK in of_tr)
    print(f"  of OrbitPass.satellite → {of_tr} → {results['of_fk']}")
    over_tr = traverse("OrbitPass", PASS_PK, "region")
    results["over_fk_E21"] = ok(isinstance(over_tr, list) and REGION in over_tr)
    print(f"  over(E-2.1) OrbitPass.region → {over_tr} → {results['over_fk_E21']}")

    # ── 2. anomaly 클린 생성 (§12 흡수 경고 없어야) ──
    sec("2. write_anomaly 클린 생성 (§12 흡수 경고 미발동 확인)")
    an_warn = io.StringIO()
    crashed = None
    with redirect_stderr(an_warn):
        try:
            store.write_anomaly(
                Anomaly(
                    id=AN_PK,
                    type="emergency_squawk",
                    ts=T,
                    confidence=0.9,
                    status="candidate",
                    lat=36.5,
                    lon=124.5,
                    explanation="P7E store-path clean anomaly",
                    explainer_backend="template",
                    created_at=T,
                ),
                evidence=[OBS_PK],
                involves=[AC_PK],
            )
        except Exception as e:
            crashed = f"{type(e).__name__}: {str(e)[:120]}"
    warn_txt = an_warn.getvalue()
    absorb_fired = "무해 ApplyActionFailed 흡수" in warn_txt
    print(f"  write_anomaly 예외 전파? {crashed or '아니오'}")
    print(
        f"  §12 흡수 경고 발동? {'예 (에러 재발!)' if absorb_fired else '아니오 (클린 실행 ✓)'}"
    )
    results["anomaly_no_crash"] = ok(crashed is None)
    results["clean_execution"] = ok(not absorb_fired)
    d = sget("Anomaly", AN_PK)
    results["anomaly_scalar"] = ok(
        d and _get(d, "anomalyId") == AN_PK and _get(d, "status") == "candidate"
    )
    print(
        f"  read-back anomalyId={_get(d, 'anomalyId')!r} status={_get(d, 'status')!r} "
        f"createdAt={_get(d, 'createdAt')!r} explainerBackend={_get(d, 'explainerBackend')!r} → {results['anomaly_scalar']}"
    )
    # traverse
    ev = traverse("Anomaly", AN_PK, "observations")
    results["evidenced_by"] = ok(isinstance(ev, list) and OBS_PK in ev)
    print(f"  evidenced_by Anomaly.observations → {ev} → {results['evidenced_by']}")
    inv = traverse("Anomaly", AN_PK, "aircraft")
    results["involves"] = ok(isinstance(inv, list) and AC_PK in inv)
    print(f"  involves Anomaly.aircraft → {inv} → {results['involves']}")
    cw = traverse("Anomaly", AN_PK, "correlatedWithAnomalies")
    results["correlatedWith_placeholder_empty"] = ok(isinstance(cw, list) and not cw)
    print(
        f"  correlatedWith(placeholder) → {cw} (빈=정상) → {results['correlatedWith_placeholder_empty']}"
    )

    # ── 3. confirm 전이 ──
    sec("3. set_anomaly_status('confirmed') 전이")
    store.set_anomaly_status(AN_PK, "confirmed")
    d2 = sget("Anomaly", AN_PK)
    results["confirm_transition"] = ok(d2 and _get(d2, "status") == "confirmed")
    print(
        f"  Foundry status → {_get(d2, 'status')!r} → {results['confirm_transition']}"
    )

    # ── 정리 ──
    sec("CLEANUP")
    print("  delete-anomaly:", ok(_delete("Anomaly", "delete-anomaly", AN_PK)))
    print(
        "  delete-orbit-pass:", ok(_delete("OrbitPass", "delete-orbit-pass", PASS_PK))
    )
    print("  delete-satellite:", ok(_delete("Satellite", "delete-satellite", SAT_PK)))
    print(
        "  delete-observation:",
        ok(_delete("Observation", "delete-observation", OBS_PK)),
    )
    print("  delete-aircraft:", ok(_delete("Aircraft", "delete-aircraft", AC_PK)))

    sec("after counts / delta")
    c1 = {t: count(t) for t in types}
    delta = {t: c1[t] - c0[t] for t in c0}
    print("before:", c0)
    print("after :", c1)
    print("delta :", delta)
    net_zero = all(v == 0 for v in delta.values())
    kadiz_kept = sget("Region", REGION) is not None

    sec("판정")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print(f"  net_zero(순증0): {ok(net_zero)}")
    print(f"  KADIZ 유지: {ok(kadiz_kept)}")
    all_ok = all(v == "OK" for v in results.values()) and net_zero and kadiz_kept
    print(
        f"\n[E-STORE-{'OK' if all_ok else '부분/FAIL'}]  임시 db: {TMP_DB} (skai.db 미오염)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
