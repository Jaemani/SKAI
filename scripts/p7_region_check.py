#!/usr/bin/env python
"""현재 Region/전 타입 객체 실사 — 유출분 식별·정리."""

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"), override=True)

import foundry_sdk

ONT = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
c = foundry_sdk.FoundryClient(
    auth=foundry_sdk.UserTokenAuth(os.environ["FOUNDRY_TOKEN"]),
    hostname=os.environ["FOUNDRY_HOSTNAME"],
)
OO = c.ontologies.OntologyObject

TYPES = (
    "Aircraft",
    "Observation",
    "Region",
    "Anomaly",
    "Operator",
    "Track",
    "Satellite",
    "OrbitPass",
    "WeatherState",
    "NewsEvent",
    "SituationAssessment",
)

for t in TYPES:
    objs = list(OO.list(ONT, t))
    print(f"\n{t}: {len(objs)}")
    for o in objs:
        d = o if isinstance(o, dict) else {}
        # 주요 필드만
        keys = (
            "icao24",
            "obsId",
            "id",
            "name",
            "anomalyId",
            "alertLevel",
            "classification",
            "callsign",
            "newsId",
        )
        shown = {k: d.get(k) for k in keys if k in d}
        print(f"   {shown}")
