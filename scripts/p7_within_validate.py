#!/usr/bin/env python
"""§16 within 배선 라이브 검증 — Observation.region(within) traverse 확인.

1. KADIZ bbox 내부(36.0, 124.0) 관측 1건 write → regionId='KADIZ' 포함 여부 직접 확인.
2. Observation 객체 read-back → region traverse=[KADIZ].
3. 테스트 객체 delete(순증 0, KADIZ Region 유지).
시크릿 미출력. required=False 확인(§15 introspection 결과).
"""

import os
import sys
import time

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

import foundry_sdk

ONT = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
c = foundry_sdk.FoundryClient(
    auth=foundry_sdk.UserTokenAuth(os.environ["FOUNDRY_TOKEN"]),
    hostname=os.environ["FOUNDRY_HOSTNAME"],
)
A = c.ontologies.Action
OO = c.ontologies.OntologyObject
LO = c.ontologies.LinkedObject

T = int(time.time())
ac_pk = f"p7w-ac-{T}"
obs_pk = f"{ac_pk}-{T}"


def sget(t, pk):
    try:
        return OO.get(ONT, t, pk)
    except Exception:
        return None


def count(t):
    return len(list(OO.list(ONT, t)))


def apply(action, params):
    return A.apply(
        ONT,
        action,
        parameters=params,
        options={"mode": "VALIDATE_AND_EXECUTE"},
    )


def sec(title):
    print(f"\n── {title}")


results = {}


def ok(b):
    return "OK" if b else "FAIL"


# ── 카운트 before ──
c0 = {t: count(t) for t in ("Aircraft", "Observation", "Region")}
print(f"[before] {c0}")

# ── Setup: Aircraft (FK 타깃) ──
sec("SETUP Aircraft")
apply("create-aircraft", {"icao24": ac_pk, "callsign": "P7W", "isMilitary": False})
d = sget("Aircraft", ac_pk)
print(f"  Aircraft: icao24={d.get('icao24') if d else None!r}")

# ── write_observation: KADIZ 내부 (36.0, 124.0) ──
sec("write_observation (KADIZ 내부 36.0, 124.0)")
from datetime import datetime, timezone

ts_iso = datetime.fromtimestamp(T, tz=timezone.utc).isoformat()
obs_params = {
    "obsId": obs_pk,
    "aircraftIcao24": ac_pk,
    "ts": ts_iso,
    "lat": 36.0,
    "lon": 124.0,
    "onGround": False,
    "source": "p7w-validate",
    "sourceUrl": "https://opensky-network.org/api/states/all",
    "regionId": "KADIZ",  # 배선 핵심: bbox 내부 → KADIZ
}
apply("create-observation", obs_params)
print(f"  create-observation obs_pk={obs_pk}")

# ── read-back + traverse ──
sec("read-back + Observation.region traverse")
time.sleep(1)  # Foundry 색인 지연 여유
d = sget("Observation", obs_pk)
if d is None:
    print("  FAIL: read-back 없음")
    results["readback"] = "FAIL"
else:
    actual_rid = d.get("regionId")
    print(f"  regionId(속성)={actual_rid!r}")
    results["readback"] = ok(actual_rid == "KADIZ")

# region traverse
try:
    region_links = list(LO.list_linked_objects(ONT, "Observation", obs_pk, "region"))
    region_ids = [r.get("id") if isinstance(r, dict) else str(r) for r in region_links]
    print(f"  Observation.region traverse={region_ids}")
    results["traverse"] = ok("KADIZ" in region_ids)
except Exception as e:
    print(f"  traverse 오류: {type(e).__name__}: {e}")
    results["traverse"] = "FAIL"

# ── cleanup ──
sec("cleanup (순증 0)")
for action, param in [
    ("delete-observation", {"Observation": obs_pk}),
    ("delete-aircraft", {"Aircraft": ac_pk}),
]:
    try:
        apply(action, param)
        print(f"  {action} OK")
    except Exception as e:
        print(f"  {action} 오류: {type(e).__name__}")

c1 = {t: count(t) for t in ("Aircraft", "Observation", "Region")}
print(f"\n[after] {c1}")
delta = {t: c1[t] - c0[t] for t in c0}
print(f"[delta] {delta}")
results["순증"] = ok(all(v == 0 for v in delta.values()))

print("\n── 판정 ──")
for k, v in results.items():
    print(f"  {k}: {v}")

failed = [k for k, v in results.items() if v != "OK"]
print(f"\n{'WITHIN-OK' if not failed else 'WITHIN-FAIL: ' + ', '.join(failed)}")
sys.exit(0 if not failed else 1)
