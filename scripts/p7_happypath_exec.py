#!/usr/bin/env python
"""P7 happy-path EXECUTE — news-event→anomaly→confirm 실행 가능성 확정.
UUID-PK 객체는 list 전후 diff로 포착(누락 없이 정리). validation.result로 실행성공 판정.
끝에서 전부 delete. before==after 확인."""

import os
import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

import foundry_sdk
from foundry_sdk.v2.ontologies.models import ApplyActionRequestOptions

ONT = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
HOST = os.environ["FOUNDRY_HOSTNAME"]
TOKEN = os.environ["FOUNDRY_TOKEN"]
EXECUTE = ApplyActionRequestOptions(mode="VALIDATE_AND_EXECUTE", return_edits="ALL")

c = foundry_sdk.FoundryClient(auth=foundry_sdk.UserTokenAuth(TOKEN), hostname=HOST)
A = c.ontologies.Action
OO = c.ontologies.OntologyObject
T = int(time.time())
cleanup = []  # (delete-action, {Param: pk})


def now():
    return datetime.now(timezone.utc).isoformat()


PK_FIELDS = (
    "obsId",
    "icao24",
    "anomalyId",
    "newsId",
    "id",
    "operatorId",
    "passId",
    "trackId",
    "noradId",
    "assessmentId",
    "weatherId",
)


def _get(o, k):
    if isinstance(o, dict):
        return o.get(k)
    return getattr(o, k, None)


def pks(t):
    out = set()
    for o in OO.list(ONT, t):
        pk = _get(o, "__primaryKey")
        if pk is None:
            for k in PK_FIELDS:
                v = _get(o, k)
                if v is not None:
                    pk = v
                    break
        out.add(pk)
    return out


def execute(action, params):
    """반환: (executed_bool, result_str, new_pk_or_None). validation.result로 판정."""
    try:
        resp = A.apply(ONT, action, parameters=params, options=EXECUTE)
    except Exception as e:
        return (
            False,
            f"EXC:{type(e).__name__}:{str(e).replace(chr(10), ' ')[:140]}",
            None,
        )
    val = getattr(resp, "validation", None)
    result = str(getattr(val, "result", "?"))
    pk = None
    edits = getattr(resp, "edits", None)
    mod = getattr(edits, "edits", None) if edits is not None else None
    if mod:
        for ed in mod:
            got = getattr(ed, "primary_key", None)
            if got:
                pk = got
                break
    return (result == "VALID"), result, pk


def sec(t):
    print("\n" + "=" * 66 + f"\n{t}\n" + "=" * 66)


def create_capture(action, params, otype, delete_action, delete_param):
    """생성 후 list-diff로 UUID PK 포착(edits에 pk 없을 때 대비)."""
    before = pks(otype)
    okv, result, pk = execute(action, params)
    after = pks(otype)
    new = after - before
    if pk is None and len(new) == 1:
        pk = next(iter(new))
    print(
        f"  {action}: exec={okv} result={result} pk={pk!r} (count {len(before)}→{len(after)})"
    )
    if pk is not None and len(after) > len(before):
        cleanup.append((delete_action, {delete_param: pk}))
    return pk if len(after) > len(before) else None


def main():
    sec("셋업: aircraft / region / operator (링크 타겟)")
    ac = create_capture(
        "create-aircraft",
        {
            "callsign": "P7H",
            "registration": "P7H01",
            "isMilitary": True,
            "type": "RC-135",
            "operatorRef": "P7HAF",
            "newParameter": f"p7h-ac-{T}",
        },
        "Aircraft",
        "delete-aircraft",
        "Aircraft",
    )
    rg = create_capture(
        "create-region",
        {
            "name": "P7H-KADIZ",
            "classification": "ADIZ",
            "geoJson": "{}",
            "newParameter": f"p7h-rg-{T}",
        },
        "Region",
        "delete-region",
        "Region",
    )
    op = create_capture(
        "create-operator",
        {"name": "P7H-AF", "kind": "airforce", "country": "XX"},
        "Operator",
        "delete-operator",
        "Operator",
    )
    orp = create_capture(
        "create-orbit-pass",
        {
            "satelliteNoradId": "99999",
            "regionId": rg or "x",
            "startTs": now(),
            "endTs": now(),
            "maxElevation": 45.0,
        },
        "OrbitPass",
        "delete-orbit-pass",
        "OrbitPass",
    )

    sec("A. create-news-event — self newsEvents='<nonexistent>'로 EXECUTE 시도")
    # self-link ref가 '존재해야' 하는지, '있기만' 하면 되는지 확정
    ne = create_capture(
        "create-news-event",
        {
            "aircraft": ac or "x",
            "operators": op or "x",
            "regions": rg or "x",
            "newsEvents": "nonexistent-selfref",  # 존재하지 않는 self ref
            "confidence": 0.3,
            "entitiesJson": "[]",
            "lat": 36.5,
            "lon": 124.5,
            "source": "gdelt",
            "summary": "s",
            "title": "t",
            "ts": now(),
            "url": "https://example.org/p7h",
        },
        "NewsEvent",
        "delete-news-event",
        "NewsEvent",
    )
    if ne is None:
        print(
            "  → 첫 NewsEvent 생성 불가(self-link ref가 실존 객체를 요구) = OSINT 경로 하드블로커"
        )
    else:
        print(f"  → NewsEvent 생성됨(self ref는 present-only). newsId={ne!r}")

    sec("B. create-anomaly — 필수 링크(aircraft+newsEvents+orbitPasses) EXECUTE")
    an = None
    if ne and orp and ac:
        an = create_capture(
            "create-anomaly",
            {
                "type": "emergency_squawk",
                "ts": now(),
                "lat": 36.5,
                "lon": 124.5,
                "confidence": 0.9,
                "status": "candidate",
                "explanation": "P7H evidence-forced",
                "aircraft": ac,
                "newsEvents": ne,
                "orbitPasses": orp,
                "newParameter": f"p7h-an-{T}",
                "newParameter1": "SENTINEL_NP1",
            },
            "Anomaly",
            "delete-anomaly",
            "Anomaly",
        )
        if an:
            d = OO.get(ONT, "Anomaly", an)
            print(
                f"    read-back status={d.get('status')!r} confidence={d.get('confidence')!r} explanation={d.get('explanation')!r}"
            )
            print(
                f"    anomalyId={d.get('anomalyId')!r} (SENTINEL_NP1 어디에도 없으면 np1=orphan)"
            )
    else:
        print(
            "  SKIP — 필수 링크 ref(newsEvents 등) 미확보로 anomaly 생성 불가(cascade)"
        )

    sec("C. confirm-anomaly — status 전이")
    if an:
        okv, result, _ = execute("confirm-anomaly", {"anomaly": an})
        d = OO.get(ONT, "Anomaly", an)
        print(f"  confirm exec={okv} result={result} → status={d.get('status')!r}")
    else:
        print("  SKIP (anomaly 없음)")

    sec("D. set-region-alert-level — Region 타겟 없이 실행")
    b_rg = OO.get(ONT, "Region", rg) if rg else {}
    okv, result, _ = execute("set-region-alert-level", {"alertLevel": "RED"})
    a_rg = OO.get(ONT, "Region", rg) if rg else {}
    print(f"  exec={okv} result={result}")
    print(
        f"  Region.alertLevel: before={b_rg.get('alertLevel')!r} after={a_rg.get('alertLevel')!r} (무변화면 타겟불능 확인)"
    )

    sec("정리 (delete 역순)")
    for action, params in reversed(cleanup):
        okv, result, _ = execute(action, params)
        print(f"  {action}({list(params.values())[0]!r}) exec={okv} result={result}")

    sec("최종 카운트(테스트 타입)")
    for t in ("Aircraft", "Region", "Operator", "OrbitPass", "NewsEvent", "Anomaly"):
        print(f"  {t}: {len(list(OO.list(ONT, t)))}")
    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
