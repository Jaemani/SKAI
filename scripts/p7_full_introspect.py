#!/usr/bin/env python
"""P7 전량 스키마 introspection (저수준 foundry_sdk, read-only).

전량(11 Object + 전체 Link + 전체 Action) 실측. 사용자 제작 타입만.
p7_introspect.py 대비: (1) action 필터를 'alert' 제외 heuristic 대신
known-set/화이트리스트로 교체(set-region-alert-level가 이제 실재),
(2) 모든 링크의 fk/cardinality 덤프, (3) create-* 액션 PK 파라미터명 표시.
시크릿 값 미출력.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))

import foundry_sdk

ONT_RID = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
HOST = os.environ["FOUNDRY_HOSTNAME"]
TOKEN = os.environ["FOUNDRY_TOKEN"]

# 사용자 제작 11 객체 (예제 온톨로지 제외용 화이트리스트)
USER_OBJECTS = {
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
}


def norm(lst):
    if lst and isinstance(lst[0], tuple) and lst[0][0] == "data":
        return lst[0][1]
    return lst


def main():
    c = foundry_sdk.FoundryClient(auth=foundry_sdk.UserTokenAuth(TOKEN), hostname=HOST)
    ot_api = c.ontologies.Ontology.ObjectType
    at_api = c.ontologies.Ontology.ActionType

    print("=" * 72)
    print("OBJECT TYPES (사용자 제작 11종)")
    print("=" * 72)
    object_types = norm(list(ot_api.list(ONT_RID)))
    seen_objs = []
    for ot in sorted(object_types, key=lambda o: str(getattr(o, "api_name", ""))):
        api_name = getattr(ot, "api_name", "?")
        if api_name not in USER_OBJECTS:
            continue
        seen_objs.append(api_name)
        pk = getattr(ot, "primary_key", None)
        status = getattr(ot, "status", "?")
        print(f"\n■ {api_name}  (PK={pk}, status={status})")
        props = getattr(ot, "properties", {}) or {}
        for pname in sorted(props):
            pdef = props[pname]
            dt = getattr(pdef, "data_type", None)
            dt_type = getattr(dt, "type", dt)
            print(f"   - {pname}: {dt_type}")
    missing_obj = USER_OBJECTS - set(seen_objs)
    print(f"\n[발견 객체 {len(seen_objs)}/11]  누락: {sorted(missing_obj) or '없음'}")

    print("\n" + "=" * 72)
    print("OUTGOING LINKS (객체별 — fk 있으면 FK링크, 없으면 MANY-MANY)")
    print("=" * 72)
    for api_name in seen_objs:
        try:
            links = norm(list(ot_api.list_outgoing_link_types(ONT_RID, api_name)))
        except Exception as e:
            print(f"\n■ {api_name}: 링크 조회 실패 {type(e).__name__}")
            continue
        print(f"\n■ {api_name}:")
        if not links:
            print("   (outgoing 링크 없음)")
        for lk in links:
            lk_api = getattr(lk, "api_name", "?")
            card = getattr(lk, "cardinality", "?")
            target = getattr(lk, "object_type_api_name", None) or getattr(
                lk, "linked_object_type_api_name", "?"
            )
            fk = getattr(lk, "foreign_key_property_api_name", None)
            form = "FK" if fk else "MANY-MANY"
            print(f"   - {lk_api} → {target} (card={card}, fk={fk}) [{form}]")

    print("\n" + "=" * 72)
    print("ACTION TYPES (전량 — 파라미터·required)")
    print("=" * 72)
    action_types = norm(list(at_api.list(ONT_RID)))
    # 사용자 액션 식별: create/edit/delete-<obj> + confirm/dismiss-anomaly + set-region-alert-level
    obj_kebab = {
        "aircraft",
        "observation",
        "region",
        "anomaly",
        "operator",
        "track",
        "satellite",
        "orbit-pass",
        "orbitpass",
        "weather-state",
        "weatherstate",
        "news-event",
        "newsevent",
        "situation-assessment",
        "situationassessment",
        "assessment",
        "news",
    }
    seen_actions = []
    for at in sorted(action_types, key=lambda a: str(getattr(a, "api_name", ""))):
        api_name = str(getattr(at, "api_name", "?"))
        low = api_name.lower()
        is_user = (
            (
                any(low.endswith(o) or f"-{o}" in low for o in obj_kebab)
                or low
                in ("confirm-anomaly", "dismiss-anomaly", "set-region-alert-level")
            )
            and "route" not in low
            and "example" not in low
        )
        if not is_user:
            continue
        seen_actions.append(api_name)
        status = getattr(at, "status", "?")
        params = getattr(at, "parameters", {}) or {}
        print(f"\n■ {api_name}  (status={status})")
        for pname in sorted(params):
            pdef = params[pname]
            dt = getattr(pdef, "data_type", None)
            dt_type = getattr(dt, "type", dt)
            required = getattr(pdef, "required", None)
            print(f"   - {pname}: {dt_type}  (required={required})")
    print(f"\n[발견 액션 {len(seen_actions)}]:")
    for a in seen_actions:
        print("   ", a)

    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
