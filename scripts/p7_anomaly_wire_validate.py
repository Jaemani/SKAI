#!/usr/bin/env python
"""P7 §13 write_anomaly Foundry 배선 라이브 검증 (2026-07-04).

HybridStore(SKAI_STORE=foundry)로 실 store 코드 경로를 태워:
  1) 기존 잔존 Observation 1건을 근거로 write_anomaly (§12 무해 ApplyActionFailed 흡수 확인)
  2) Foundry에서 Anomaly 존재 + evidenced_by(→Observation)·involves(→Aircraft) traverse
  3) set_anomaly_status("confirmed")로 confirm-anomaly 전이 → status read-back
  4) delete-anomaly 정리 → Foundry 순증 0
로컬 소재는 임시 db에 써서 실 skai.db 미오염. write 최소(Anomaly 1건). 시크릿 미출력.
"""

import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), os.pardir))

from ontology.model import Anomaly  # noqa: E402
from ontology.store_foundry import make_store  # noqa: E402

T = int(time.time())
TMP_DB = f"/private/tmp/claude-501/p7wire-{T}.db"

os.environ["SKAI_STORE"] = "foundry"
store = make_store(TMP_DB)  # HybridStore
fs = store.foundry
pf = fs._pf
ONT = fs.ont
A = pf.ontologies.Action
OO = pf.ontologies.OntologyObject
LO = pf.ontologies.LinkedObject


def _get(o, k):
    return o.get(k) if isinstance(o, dict) else getattr(o, k, None)


def _pk(o):
    return (
        _get(o, "__primaryKey")
        or _get(o, "obsId")
        or _get(o, "icao24")
        or _get(o, "anomalyId")
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


def main():
    sec("0. baseline counts")
    c0 = {t: count(t) for t in ("Aircraft", "Observation", "Anomaly")}
    print(c0)

    # 근거 Observation 1건 확보(기존 잔존 재사용 — write 최소화).
    obs_list = list(OO.list(ONT, "Observation"))
    if not obs_list:
        print("근거 Observation이 Foundry에 없음 → 검증 불가(먼저 인제스트 필요).")
        return 1
    evid = obs_list[0]
    evid_obs = _get(evid, "obsId")
    evid_ac = _get(evid, "aircraftIcao24")
    print(f"  근거 Observation obsId={evid_obs!r} aircraftIcao24={evid_ac!r}")
    involves = [evid_ac] if evid_ac and sget("Aircraft", evid_ac) else []
    print(f"  involves(Aircraft) = {involves} (Aircraft 존재 시만)")

    an_pk = f"p7wire-an-{T}"
    results: dict[str, str] = {}

    # ── 1. write_anomaly (§12 에러 흡수 경로) ──
    sec("1. write_anomaly (Foundry 배선, §12 ApplyActionFailed 흡수)")
    anomaly = Anomaly(
        id=an_pk,
        type="emergency_squawk",
        ts=T,
        confidence=0.9,
        status="candidate",
        lat=36.5,
        lon=124.5,
        explanation="P7 §13 obs-grounded anomaly (wire validate)",
        created_at=T,
    )
    crashed = None
    try:
        store.write_anomaly(anomaly, evidence=[evid_obs], involves=involves)
    except Exception as e:  # 에러 흡수가 정상이면 여기 안 옴
        crashed = f"{type(e).__name__}: {str(e)[:120]}"
    print(f"  write_anomaly 예외 전파? {crashed or '아니오 (에러 흡수 정상)'}")
    results["error_absorption"] = ok(crashed is None)

    # read-back: Anomaly 스칼라
    d = sget("Anomaly", an_pk)
    scalar_ok = (
        d is not None
        and _get(d, "anomalyId") == an_pk
        and _get(d, "status") == "candidate"
    )
    results["anomaly_scalar"] = ok(scalar_ok)
    print(
        f"  read-back anomalyId={_get(d, 'anomalyId')!r} status={_get(d, 'status')!r} "
        f"conf={_get(d, 'confidence')!r} → {results['anomaly_scalar']}"
    )

    # ── 2. evidenced_by / involves traverse ──
    sec("2. evidenced_by(→Observation) · involves(→Aircraft) traverse")
    ev_tr = traverse("Anomaly", an_pk, "observations")
    ev_ok = isinstance(ev_tr, list) and evid_obs in ev_tr
    results["evidenced_by"] = ok(ev_ok)
    print(f"  Anomaly.observations → {ev_tr} (근거≡{evid_obs}? {ev_ok})")
    # 역방향
    rev = traverse("Observation", evid_obs, "anomalies")
    print(
        f"  역방향 Observation.anomalies → {rev} (anomaly 포함? {isinstance(rev, list) and an_pk in rev})"
    )
    if involves:
        inv_tr = traverse("Anomaly", an_pk, "aircraft")
        inv_ok = isinstance(inv_tr, list) and evid_ac in inv_tr
        results["involves"] = ok(inv_ok)
        print(f"  Anomaly.aircraft → {inv_tr} (involves≡{evid_ac}? {inv_ok})")

    # ── 3. confirm 전이 ──
    sec("3. set_anomaly_status('confirmed') → confirm-anomaly 전이")
    store.set_anomaly_status(an_pk, "confirmed")
    d2 = sget("Anomaly", an_pk)
    confirm_ok = d2 is not None and _get(d2, "status") == "confirmed"
    results["confirm_transition"] = ok(confirm_ok)
    print(
        f"  Foundry status candidate→{_get(d2, 'status')!r} → {results['confirm_transition']}"
    )
    # 로컬 권위본도 전이됐는지
    la = store.local.get_anomaly(an_pk)
    print(f"  로컬 권위본 status={la.status if la else None!r} (반환값과 동기)")

    # ── 정리 ──
    sec("CLEANUP")
    try:
        A.apply(
            ONT,
            "delete-anomaly",
            parameters={"Anomaly": an_pk},
            options={"mode": "VALIDATE_AND_EXECUTE"},
        )
        print(f"  delete-anomaly({an_pk!r}) 삭제")
    except Exception as e:
        print(f"  delete-anomaly FAIL: {type(e).__name__}:{str(e)[:80]}")
    # stray(p7wire-) 잔존 청소
    for o in OO.list(ONT, "Anomaly"):
        aid = _get(o, "anomalyId")
        if aid and str(aid).startswith("p7wire-") and aid != an_pk:
            try:
                A.apply(
                    ONT,
                    "delete-anomaly",
                    parameters={"Anomaly": aid},
                    options={"mode": "VALIDATE_AND_EXECUTE"},
                )
                print(f"  [stray] delete-anomaly({aid!r}) 삭제")
            except Exception:
                pass

    sec("after counts / delta")
    c1 = {t: count(t) for t in ("Aircraft", "Observation", "Anomaly")}
    delta = {t: c1[t] - c0[t] for t in c0}
    print("before:", c0)
    print("after :", c1)
    print("delta :", delta)
    net_zero = all(v == 0 for v in delta.values())

    sec("판정")
    for k, v in results.items():
        print(f"  {k}: {v}")
    print(f"  net_zero(순증0): {ok(net_zero)}")
    all_ok = all(v == "OK" for v in results.values()) and net_zero
    print(
        f"\n[ANOMALY-{'OK' if all_ok else '부분/FAIL'}]  Foundry 정리: {'완료(순증0)' if net_zero else '확인필요'}"
    )
    print(f"임시 로컬 db: {TMP_DB} (skai.db 미오염)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
