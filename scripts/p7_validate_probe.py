#!/usr/bin/env python
"""P7 정밀 VALIDATE 프로브 — validation.result + per-param 결과를 실제로 읽는다.
쓰기 없음(VALIDATE_ONLY). 어떤 필수 파라미터가 무엇을 막는지 확정."""

import json
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

import foundry_sdk
from foundry_sdk.v2.ontologies.models import ApplyActionRequestOptions

ONT = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
HOST = os.environ["FOUNDRY_HOSTNAME"]
TOKEN = os.environ["FOUNDRY_TOKEN"]
VALIDATE = ApplyActionRequestOptions(mode="VALIDATE_ONLY")

c = foundry_sdk.FoundryClient(auth=foundry_sdk.UserTokenAuth(TOKEN), hostname=HOST)
A = c.ontologies.Action


def now():
    return datetime.now(timezone.utc).isoformat()


def probe(label, action, params):
    print(f"\n--- {label} ---")
    print(f"    action={action}  keys={sorted(params)}")
    try:
        resp = A.apply(ONT, action, parameters=params, options=VALIDATE)
    except Exception as e:
        print(f"    EXC {type(e).__name__}: {str(e).replace(chr(10), ' ')[:160]}")
        return
    val = getattr(resp, "validation", None)
    result = getattr(val, "result", None)
    print(f"    validation.result = {result!r}")
    pv = getattr(val, "parameters", None) or {}
    for pname, pres in pv.items():
        r = getattr(pres, "result", pres)
        if str(r) != "VALID":
            evals = getattr(pres, "evaluated_constraints", None)
            print(f"      param {pname}: {r!r}  {evals if evals else ''}")


def main():
    # create-news-event: self-link(newsEvents) 필수가 첫 생성을 막나?
    ne_full = {
        "aircraft": "x",
        "operators": "x",
        "regions": "x",
        "newsEvents": "x",
        "confidence": 0.3,
        "entitiesJson": "[]",
        "lat": 36.5,
        "lon": 124.5,
        "source": "gdelt",
        "summary": "s",
        "title": "t",
        "ts": now(),
        "url": "https://example.org/p7f",
    }
    probe(
        "news-event: 모든 필수 제공(존재X ref='x')", "create-news-event", dict(ne_full)
    )
    ne_no_self = {k: v for k, v in ne_full.items() if k != "newsEvents"}
    probe("news-event: self newsEvents 생략", "create-news-event", ne_no_self)

    # create-anomaly: 링크 전무 시 어떤 파라미터가 INVALID?
    an_base = {
        "type": "emergency_squawk",
        "ts": now(),
        "lat": 36.5,
        "lon": 124.5,
        "newParameter": "x",
        "newParameter1": "x",
    }
    probe(
        "anomaly: 링크 전무(aircraft/newsEvents/orbitPasses 생략)",
        "create-anomaly",
        dict(an_base),
    )
    probe(
        "anomaly: 링크 3종 제공(ref='x')",
        "create-anomaly",
        {**an_base, "aircraft": "x", "newsEvents": "x", "orbitPasses": "x"},
    )
    # newParameter1 생략 시?
    probe(
        "anomaly: newParameter1 생략",
        "create-anomaly",
        {k: v for k, v in an_base.items() if k != "newParameter1"},
    )

    # set-region-alert-level: 대상 Region 없이 alertLevel만 → VALID?
    probe(
        "set-region-alert-level: alertLevel만",
        "set-region-alert-level",
        {"alertLevel": "RED"},
    )
    probe("set-region-alert-level: 파라미터 전무", "set-region-alert-level", {})

    # confirm/dismiss-anomaly: anomaly ref 필요 확인
    probe("confirm-anomaly: anomaly='x'", "confirm-anomaly", {"anomaly": "x"})
    probe("confirm-anomaly: 파라미터 전무", "confirm-anomaly", {})

    # create-observation: trackId 없이도 정상? (composed_of 못 채움 확인은 introspection)
    probe(
        "observation: 정상 필수",
        "create-observation",
        {
            "aircraftIcao24": "x",
            "lat": 1.0,
            "lon": 1.0,
            "onGround": False,
            "source": "x",
            "sourceUrl": "x",
            "ts": now(),
            "newParameter": "x",
        },
    )

    print("\nDONE (VALIDATE_ONLY, 쓰기 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
