#!/usr/bin/env python
"""P7 — 액션 파라미터 수용 실측 (VALIDATE_ONLY, 쓰기 없음).

VALIDATE_ONLY 모드로 실제 객체 생성 없이 파라미터 수용/거부를 확인한다.
핵심 질문:
  1. create-aircraft가 icao24(PK)를 받나? → 엔티티 해소 가능 여부
  2. create-observation이 aircraftIcao24(FK)·obsId(PK)를 받나? → observed_as·자연키 가능 여부
  3. ts(timestamp) 포맷 — ISO8601 문자열 수용?
"""

import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))

import foundry_sdk
from foundry_sdk.v2.ontologies.models import ApplyActionRequestOptions

ONT_RID = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
HOST = os.environ["FOUNDRY_HOSTNAME"]
TOKEN = os.environ["FOUNDRY_TOKEN"]

VALIDATE = ApplyActionRequestOptions(mode="VALIDATE_ONLY")


def probe(pf, action, params, label):
    print(f"\n--- PROBE: {label} ---")
    print(f"    action={action} params_keys={sorted(params.keys())}")
    try:
        resp = pf.ontologies.Action.apply(
            ONT_RID, action, parameters=params, options=VALIDATE
        )
        val = getattr(resp, "validation", None)
        result = getattr(val, "result", val)
        print(f"    VALIDATION result = {result!r}")
        # 파라미터별 검증 결과
        pv = getattr(val, "parameters", None)
        if pv:
            for pname, pres in pv.items():
                r = getattr(pres, "result", pres)
                if str(r) != "VALID":
                    print(f"      param {pname}: {r!r}")
    except Exception as e:
        msg = str(e)[:400]
        print(f"    EXCEPTION: {type(e).__name__}: {msg}")


def main():
    pf = foundry_sdk.FoundryClient(auth=foundry_sdk.UserTokenAuth(TOKEN), hostname=HOST)
    now_iso = datetime.now(timezone.utc).isoformat()

    # 1. create-aircraft: introspection 기준 필수 파라미터 (junk 포함)
    probe(
        pf,
        "create-aircraft",
        {
            "callsign": "PROBE",
            "isMilitary": False,
            "registration": "PRB000",
            "newParameter": "x",
            "newParameter1": False,
        },
        "create-aircraft 기준 파라미터(junk 포함)",
    )

    # 2. create-aircraft에 icao24(PK) 추가 → 수용/거부?
    probe(
        pf,
        "create-aircraft",
        {
            "callsign": "PROBE",
            "isMilitary": False,
            "registration": "PRB000",
            "newParameter": "x",
            "newParameter1": False,
            "icao24": "p7test1",
        },
        "create-aircraft + icao24(PK) 시도",
    )

    # 3. create-observation: introspection 기준 필수 파라미터
    base_obs = {
        "sourceUrl": "https://opensky-network.org/api/states/all",
        "squawk": "1200",
        "onGround": False,
        "heading": 90.0,
        "alt": 10000.0,
        "newParameter": "x",
        "lon": 124.0,
        "source": "opensky",
        "velocity": 200.0,
        "lat": 36.0,
        "ts": now_iso,
    }
    probe(
        pf, "create-observation", dict(base_obs), "create-observation 기준(ts=ISO8601)"
    )

    # 4. create-observation에 aircraftIcao24(FK) 추가 → observed_as 가능?
    probe(
        pf,
        "create-observation",
        {**base_obs, "aircraftIcao24": "p7test1"},
        "create-observation + aircraftIcao24(FK) 시도",
    )

    # 5. create-observation에 obsId(PK) 추가 → 자연키 가능?
    probe(
        pf,
        "create-observation",
        {**base_obs, "obsId": "p7test1-1700000000"},
        "create-observation + obsId(PK) 시도",
    )

    print("\nDONE (VALIDATE_ONLY — 실제 쓰기 없음)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
