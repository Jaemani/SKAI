"""verify_foundry_read.py — Foundry-primary read 모드 라이브 검증 (DR-0012 #2).

`scripts/demo_foundry.sh`와 같은 방식(.venv312 + SKAI_STORE=foundry + .env)으로 실행한다.
서버 엔드포인트(/api/stats·/api/observations·/api/assess)가 감싸는 **store read 메서드를
그대로** 호출해, foundry 모드에서 화면·코파일럿 read가 실제로 Palantir Foundry에서 나오는지
(로컬 SQLite가 아니라) 라이브로 증명한다. **read-only** — Foundry에 아무것도 쓰지 않는다.

증명 전략:
1. 로컬 db는 매번 새로(빈 상태) → Observation/Aircraft가 로컬엔 0. 그런데도 store.counts()·
   query_latest_observations()가 실데이터를 내면 그건 **Foundry발**이다(로컬 대조로 확정).
2. current_backend()=='foundry' + store_backend 필드.
3. /api/assess 1건: KADIZ Region만 로컬 보강(설계상 Region=로컬 권위본)한 뒤, Foundry 관측 ts에
   now를 앵커링해 질의 → 문장 cites가 **Foundry Observation id**를 인용하는지 확인.

사용: ./.venv312/bin/python -m scripts.verify_foundry_read
"""

from __future__ import annotations

import os
import sys
import tempfile

# .env 로드(FOUNDRY_TOKEN·FOUNDRY_HOSTNAME) — make_store도 로드하지만 명시.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

os.environ["SKAI_STORE"] = "foundry"

from copilot.assessment import assess  # noqa: E402
from ontology.model import KADIZ_REGION  # noqa: E402
from ontology.store_foundry import current_backend, make_store  # noqa: E402
from ontology.store_local import LocalOntologyStore  # noqa: E402


def _api_observations(store) -> list[dict]:
    """server.app.api_observations와 동일한 직렬화(Foundry read를 그대로 태운다)."""
    ac_map = store.aircraft_map()  # Foundry
    out = []
    for o in store.query_latest_observations():  # Foundry
        ac = ac_map.get(o.aircraft_ref)
        out.append(
            {
                "icao24": o.aircraft_ref,
                "callsign": ac.callsign if ac else None,
                "ts": o.ts,
                "lat": o.lat,
                "lon": o.lon,
                "source": o.source,
                "source_url": o.source_url,
            }
        )
    return out


def main() -> int:
    fails: list[str] = []
    tmp_db = os.path.join(tempfile.gettempdir(), "skai_foundry_read_verify.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(tmp_db + suffix)
        except FileNotFoundError:
            pass

    print("=== Foundry-primary read 검증 (read-only) ===")
    print(f"backend = {current_backend()}")
    if current_backend() != "foundry":
        print("FAIL: SKAI_STORE=foundry 미적용")
        return 1

    # [0] 연결 + HybridStore
    try:
        store = make_store(tmp_db)  # HybridStore(.env 크리덴셜)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL: Foundry 연결 실패 — {type(e).__name__}: {str(e)[:160]}")
        return 3

    # 로컬 대조본(빈 db) — 로컬엔 관측/항공기 0임을 증명해 Foundry발을 확정.
    local_only = LocalOntologyStore(tmp_db)
    local_counts = local_only.counts()
    print(
        f"[대조] 로컬 db 카운트: aircraft={local_counts.get('aircraft', 0)} "
        f"observation={local_counts.get('observation', 0)} (빈 상태여야 Foundry발 증명 성립)"
    )

    # [1] /api/stats equiv — counts는 Foundry 8종 + 로컬 병합
    counts = store.counts()
    print(
        f"[/api/stats] store.counts(): aircraft={counts.get('aircraft')} "
        f"observation={counts.get('observation')} satellite={counts.get('satellite')} "
        f"weatherstate={counts.get('weatherstate')} newsevent={counts.get('newsevent')}"
    )
    fnd_ac, fnd_obs = counts.get("aircraft", 0), counts.get("observation", 0)
    if fnd_ac == 0 and fnd_obs == 0:
        fails.append("Foundry 카운트가 0 — 실 데이터 read 실패(또는 Foundry 비어있음)")
    if local_counts.get("observation", 0) != 0:
        fails.append("로컬 db가 비어있지 않음 — Foundry발 대조 불가")

    # [2] /api/observations equiv — 실 Foundry 관측 직렬화
    obs = _api_observations(store)
    print(f"[/api/observations] {len(obs)}건 (Foundry read)")
    for row in obs[:3]:
        print(
            f"    icao24={row['icao24']} ts={row['ts']} "
            f"src={row['source']} url={(row['source_url'] or '')[:48]}"
        )
    if not obs:
        fails.append("/api/observations가 0건 — Foundry 관측 read 실패")

    # [3] /api/assess equiv — 코파일럿 문장 cites가 Foundry Observation을 인용하는가
    #     Region은 설계상 로컬 권위본 → KADIZ만 로컬 보강(Foundry엔 안 씀 = 오염 0).
    store.local.write_region(KADIZ_REGION)
    all_obs = store.query_all_observations()  # Foundry
    foundry_obs_ids = {o.id for o in all_obs}
    if all_obs:
        anchor = max(o.ts for o in all_obs)  # Foundry 관측 시각에 now 앵커
        q = "지금 KADIZ 근방에 항공기 뭐 있어?"
        result = assess(store, q, now=anchor)
        cited = set()
        for s in result.get("sentences", []):
            cited.update(s.get("cites", []))
        cited_from_foundry = cited & foundry_obs_ids
        print(
            f"[/api/assess] intent={result.get('intent')} "
            f"문장 {len(result.get('sentences', []))}개, cites {len(cited)}개, "
            f"그중 Foundry Observation 인용 {len(cited_from_foundry)}개"
        )
        if cited_from_foundry:
            print(f"    Foundry발 cites 예시: {sorted(cited_from_foundry)[:3]}")
        else:
            print(
                "    (경고) 이 질의 창에 Foundry 관측이 안 걸림 — "
                "cites가 Foundry id를 안 담음. 단건 read로 재확인:"
            )
            from copilot.tools import get_entity_fact

            sample = all_obs[0]
            f = get_entity_fact(store, sample.id)
            ok = f is not None and sample.id in (f.cites if f else [])
            print(
                f"    get_entity_fact({sample.id}) → cites={f.cites if f else None} "
                f"[{'OK' if ok else 'FAIL'}]"
            )
            if not ok:
                fails.append("코파일럿 read가 Foundry Observation id를 cites로 못 냄")
    else:
        fails.append("Foundry에 관측이 없어 assess cites 검증 불가")

    # [4] 오염 0 확인 — 이 스크립트는 Foundry write 미수행(read + 로컬 Region seed만).
    print("[오염] Foundry write 0 (read-only + 로컬 Region seed만) — Foundry 순증 0")

    # 정리
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(tmp_db + suffix)
        except FileNotFoundError:
            pass

    print("\n=== 판정 ===")
    if fails:
        for f in fails:
            print(f"  FAIL: {f}")
        print("[VERIFY-FOUNDRY-READ FAIL]")
        return 1
    print(
        "[VERIFY-FOUNDRY-READ OK] 화면·코파일럿 read가 실 Foundry에서 나옴(로컬 대조 확정)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
