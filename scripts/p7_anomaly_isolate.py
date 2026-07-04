#!/usr/bin/env python
"""create-anomaly ApplyActionFailed 원인 격리 — 전체 에러메시지 확보. 끝에 정리."""

import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

import foundry_sdk
from foundry_sdk.v2.ontologies.models import ApplyActionRequestOptions

ONT = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
c = foundry_sdk.FoundryClient(
    auth=foundry_sdk.UserTokenAuth(os.environ["FOUNDRY_TOKEN"]),
    hostname=os.environ["FOUNDRY_HOSTNAME"],
)
A = c.ontologies.Action
OO = c.ontologies.OntologyObject
EXEC = ApplyActionRequestOptions(mode="VALIDATE_AND_EXECUTE", return_edits="ALL")
T = int(time.time())
cln = []


def now():
    return datetime.now(timezone.utc).isoformat()


def ex(action, params, capture_new=None):
    err = None
    try:
        A.apply(ONT, action, parameters=params, options=EXEC)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)}"
    return err


def one(action, params, otype, delp):
    before = {
        getattr(o, "get", lambda k: None)("__primaryKey")
        if isinstance(o, dict)
        else None
        for o in OO.list(ONT, otype)
    }
    err = ex(action, params)
    after_objs = list(OO.list(ONT, otype))
    print(f"\n{action}: err={err[:90] if err else None}")
    return after_objs


def pkset(otype):
    s = set()
    for o in OO.list(ONT, otype):
        pk = o.get("__primaryKey") if isinstance(o, dict) else None
        if pk is None and isinstance(o, dict):
            for k in ("icao24", "id", "operatorId", "passId", "anomalyId", "newsId"):
                if k in o:
                    pk = o[k]
                    break
        s.add(pk)
    return s


def cap(action, params, otype, delp):
    b = pkset(otype)
    err = ex(action, params)
    a = pkset(otype)
    new = a - b
    pk = next(iter(new)) if len(new) == 1 else None
    if pk:
        cln.append((f"delete-{delp[0]}", {delp[1]: pk}))
    print(f"{action}: err={(err[:120] + '...') if err else None}  new_pk={pk!r}")
    return pk


def main():
    ac = cap(
        "create-aircraft",
        {
            "callsign": "P7I",
            "registration": "P7I",
            "isMilitary": True,
            "type": "x",
            "operatorRef": "x",
            "newParameter": f"p7i-ac-{T}",
        },
        "Aircraft",
        ("aircraft", "Aircraft"),
    )
    rg = cap(
        "create-region",
        {
            "name": "P7I",
            "classification": "ADIZ",
            "geoJson": "{}",
            "newParameter": f"p7i-rg-{T}",
        },
        "Region",
        ("region", "Region"),
    )
    op = cap(
        "create-operator",
        {"name": "P7I", "kind": "airforce", "country": "XX"},
        "Operator",
        ("operator", "Operator"),
    )
    orp = cap(
        "create-orbit-pass",
        {
            "satelliteNoradId": "1",
            "regionId": rg,
            "startTs": now(),
            "endTs": now(),
            "maxElevation": 1.0,
        },
        "OrbitPass",
        ("orbit-pass", "OrbitPass"),
    )
    ne = cap(
        "create-news-event",
        {
            "aircraft": ac,
            "operators": op,
            "regions": rg,
            "newsEvents": "x",
            "confidence": 0.3,
            "entitiesJson": "[]",
            "lat": 1.0,
            "lon": 1.0,
            "source": "g",
            "summary": "s",
            "title": "t",
            "ts": now(),
            "url": "https://e.org",
        },
        "NewsEvent",
        ("news-event", "NewsEvent"),
    )

    print("\n=== create-anomaly 전체 에러 (모든 필수 valid refs) ===")
    b = pkset("Anomaly")
    err = ex(
        "create-anomaly",
        {
            "type": "emergency_squawk",
            "ts": now(),
            "lat": 1.0,
            "lon": 1.0,
            "confidence": 0.9,
            "status": "candidate",
            "explanation": "x",
            "aircraft": ac,
            "newsEvents": ne,
            "orbitPasses": orp,
            "newParameter": f"p7i-an-{T}",
            "newParameter1": "NP1VAL",
        },
    )
    a = pkset("Anomaly")
    new = a - b
    print("FULL ERROR:\n", err)
    print("Anomaly created despite error?", len(new) == 1, new)
    for pk in new:
        if pk:
            cln.append(("delete-anomaly", {"Anomaly": pk}))
            d = OO.get(ONT, "Anomaly", pk)
            print(
                f"  read-back: anomalyId={d.get('anomalyId')!r} status={d.get('status')!r} conf={d.get('confidence')!r} expl={d.get('explanation')!r} type={d.get('type')!r}"
            )

    print("\n=== 정리 ===")
    for action, params in reversed(cln):
        e = ex(action, params)
        print(f"  {action}({list(params.values())[0]!r}) err={e[:60] if e else 'OK'}")
    print("\n최종:")
    for t in ("Aircraft", "Region", "Operator", "OrbitPass", "NewsEvent", "Anomaly"):
        print(f"  {t}: {len(list(OO.list(ONT, t)))}")


if __name__ == "__main__":
    main()
