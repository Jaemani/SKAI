#!/usr/bin/env python
"""P0-B — OSDK 온톨로지 객체 read/write 왕복 (재실행 가능).

성공기준(P0-B): 생성 OSDK 설치 후 온톨로지 객체를 read → write → read-back.

경로(2026-07-04 확정):
  - READ  : 생성 OSDK `skai_osdk_sdk` (client.ontology.objects.Aircraft)
  - WRITE : 저수준 `foundry_sdk`의 `create-aircraft` 액션.
            (OSDK 발행 시 Action을 하나도 담지 않아 client.ontology.actions 가 비어 있고,
             OSDK 편집 API[edits]는 AIP Function 런타임 전용이라 클라이언트 단독 실행 불가.)
  - READ-BACK : 다시 OSDK로 방금 만든 객체를 primary key(icao24)로 조회.

주의: `create-aircraft` 액션은 파라미터가 callsign/isMilitary/registration 뿐이고
      primary key(icao24)를 받지 않는다 → icao24 는 서버가 UUID로 자동 부여한다.
      따라서 테스트 객체는 callsign="SKAITEST" + registration="P0TEST0" 로 식별한다.

시크릿: .env(FOUNDRY_TOKEN, FOUNDRY_HOSTNAME)에서만 로드. 값 출력 금지.
재실행: callsign=SKAITEST 객체가 이미 있으면 write 를 건너뛰고 read-back 만 수행
        (테스트 객체 1건 상한 준수).
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), os.pardir, ".env"))

ONT_RID = "ri.ontology.main.ontology.33d94264-3352-4354-aadf-840ccb0f2a0c"
TEST_CALLSIGN = "SKAITEST"
TEST_REGISTRATION = "P0TEST0"

HOST = os.environ["FOUNDRY_HOSTNAME"]
TOKEN = os.environ["FOUNDRY_TOKEN"]


def osdk_client():
    from skai_osdk_sdk import FoundryClient, UserTokenAuth

    return FoundryClient(auth=UserTokenAuth(TOKEN), hostname=HOST)


def platform_client():
    import foundry_sdk

    return foundry_sdk.FoundryClient(
        auth=foundry_sdk.UserTokenAuth(TOKEN), hostname=HOST
    )


def list_aircraft(osdk):
    """OSDK 로 Aircraft 전체를 파이썬 리스트로 반환."""
    return list(osdk.ontology.objects.Aircraft)


def find_test_aircraft(osdk):
    for ac in list_aircraft(osdk):
        if ac.callsign == TEST_CALLSIGN:
            return ac
    return None


def main():
    osdk = osdk_client()
    pf = platform_client()

    print("=== 1. OSDK READ (pre-write) ===")
    before = list_aircraft(osdk)
    print(f"현재 Aircraft 인스턴스: {len(before)}건")
    for ac in before[:10]:
        print(
            f"  icao24={ac.icao24} callsign={ac.callsign} "
            f"isMilitary={ac.is_military} registration={ac.registration}"
        )

    print("\n=== 2. WRITE (create-aircraft 액션) ===")
    existing = find_test_aircraft(osdk)
    if existing is not None:
        print(
            f"이미 존재하는 테스트 객체(callsign={TEST_CALLSIGN}) 발견 → write 건너뜀 "
            f"(재실행 안전, 1건 상한 준수). icao24={existing.icao24}"
        )
        new_pk = existing.icao24
    else:
        resp = pf.ontologies.Action.apply(
            ONT_RID,
            "create-aircraft",
            parameters={
                "callsign": TEST_CALLSIGN,
                "isMilitary": "false",
                "registration": TEST_REGISTRATION,
            },
            options={"returnEdits": "ALL"},
        )
        # 응답 edits 에서 방금 생성된 객체의 primary key 추출
        new_pk = None
        edits = getattr(resp, "edits", None)
        modified = getattr(edits, "edits", None) if edits is not None else None
        if modified:
            for e in modified:
                pk = getattr(e, "primary_key", None)
                if pk:
                    new_pk = pk
                    break
        print(f"WRITE OK. validation={getattr(resp, 'validation', None)!r}")
        print(f"생성된 icao24(primary key, 서버 자동부여 UUID) = {new_pk}")
        if new_pk is None:
            # returnEdits 로 못 잡으면 callsign 으로 재조회
            found = find_test_aircraft(osdk)
            new_pk = found.icao24 if found else None
            print(f"(returnEdits 미회수 → callsign 재조회로 pk 확보: {new_pk})")

    if new_pk is None:
        print("\n[BLOCKED] 생성 객체의 primary key 를 확보하지 못함.")
        return 2

    print("\n=== 3. OSDK READ-BACK (primary key 조회) ===")
    got = osdk.ontology.objects.Aircraft.get(new_pk)
    print(
        f"  icao24={got.icao24} callsign={got.callsign} "
        f"isMilitary={got.is_military} registration={got.registration}"
    )

    ok = got.callsign == TEST_CALLSIGN and got.registration == TEST_REGISTRATION
    print("\n=== 판정 ===")
    if ok:
        print(
            "ROUNDTRIP-OK — write(create-aircraft 액션) → OSDK read-back 일치 확인. "
            f"(icao24={got.icao24}, callsign={got.callsign})"
        )
        return 0
    print("PARTIAL — 객체는 존재하나 필드 불일치.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
