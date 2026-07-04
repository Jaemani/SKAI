#!/usr/bin/env python
"""P7 — Foundry 온톨로지 라이브 introspection (저수준 foundry_sdk, read-only).

목적: 사용자의 스키마 확장(Aircraft 보강·Observation·observed_as·OSDK 재발행) 후
      실제 온톨로지 상태를 실측해 v0.1 스펙(ontology.md) 대비 갭을 확정한다. 추측 금지.

출력: Object Type별 속성(이름·타입·PK), Action Type별 파라미터, Aircraft의 outgoing
      link type(observed_as 형태). 사용자 제작 타입만 필터(Example* 예제 온톨로지 제외).
시크릿: .env에서만 로드, 값 출력 금지.
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))

import foundry_sdk

ONT_RID = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
HOST = os.environ["FOUNDRY_HOSTNAME"]
TOKEN = os.environ["FOUNDRY_TOKEN"]

# 예제 온톨로지(마켓플레이스/튜토리얼) 접두어 — 사용자 제작 타입과 구분.
EXAMPLE_PREFIXES = ("Example", "ontology")


def is_user_made(api_name: str) -> bool:
    return not any(str(api_name).startswith(p) for p in EXAMPLE_PREFIXES)


def dump(obj, attrs):
    out = []
    for a in attrs:
        v = getattr(obj, a, None)
        if v is not None:
            out.append(f"{a}={v!r}")
    return " ".join(out)


def main():
    c = foundry_sdk.FoundryClient(auth=foundry_sdk.UserTokenAuth(TOKEN), hostname=HOST)
    ot_api = c.ontologies.Ontology.ObjectType
    at_api = c.ontologies.Ontology.ActionType

    print("=" * 70)
    print("OBJECT TYPES (사용자 제작만)")
    print("=" * 70)
    object_types = list(ot_api.list(ONT_RID))
    # SDK가 ('data',[...]) 튜플로 낼 수 있어 정규화
    if (
        object_types
        and isinstance(object_types[0], tuple)
        and object_types[0][0] == "data"
    ):
        object_types = object_types[0][1]
    user_ots = []
    for ot in object_types:
        api_name = getattr(ot, "api_name", "?")
        if not is_user_made(api_name):
            continue
        user_ots.append(ot)
        pk = getattr(ot, "primary_key", None)
        status = getattr(ot, "status", "?")
        print(f"\n■ ObjectType: {api_name}  (PK={pk}, status={status})")
        display = getattr(ot, "display_name", None)
        if display:
            print(f"   display_name={display!r}")
        props = getattr(ot, "properties", {}) or {}
        for pname, pdef in props.items():
            dt = getattr(pdef, "data_type", None)
            dt_type = getattr(dt, "type", dt)
            print(f"   - {pname}: {dt_type}")

    print("\n" + "=" * 70)
    print("OUTGOING LINK TYPES (사용자 ObjectType 기준)")
    print("=" * 70)
    for ot in user_ots:
        api_name = getattr(ot, "api_name", "?")
        try:
            links = list(ot_api.list_outgoing_link_types(ONT_RID, api_name))
            if links and isinstance(links[0], tuple) and links[0][0] == "data":
                links = links[0][1]
        except Exception as e:
            print(f"\n{api_name}: link 조회 실패 {e!r}")
            continue
        print(f"\n■ {api_name} outgoing links:")
        if not links:
            print("   (없음)")
        for lk in links:
            lk_api = getattr(lk, "api_name", "?")
            card = getattr(lk, "cardinality", "?")
            target = getattr(lk, "object_type_api_name", None) or getattr(
                lk, "linked_object_type_api_name", "?"
            )
            fk = getattr(lk, "foreign_key_property_api_name", None)
            print(f"   - {lk_api} → {target}  (card={card}, fk={fk})")

    print("\n" + "=" * 70)
    print("ACTION TYPES (사용자 제작만 — 이름에 example/route 없는 것)")
    print("=" * 70)
    action_types = list(at_api.list(ONT_RID))
    if (
        action_types
        and isinstance(action_types[0], tuple)
        and action_types[0][0] == "data"
    ):
        action_types = action_types[0][1]
    for at in action_types:
        api_name = getattr(at, "api_name", "?")
        low = str(api_name).lower()
        # 예제 route-alert 계열 제외 heuristic
        if "route" in low or "alert" in low or "example" in low:
            continue
        status = getattr(at, "status", "?")
        print(f"\n■ ActionType: {api_name}  (status={status})")
        params = getattr(at, "parameters", {}) or {}
        for pname, pdef in params.items():
            dt = getattr(pdef, "data_type", None)
            dt_type = getattr(dt, "type", dt)
            required = getattr(pdef, "required", None)
            print(f"   - {pname}: {dt_type}  (required={required})")

    print("\nDONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
