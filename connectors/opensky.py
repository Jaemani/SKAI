"""OpenSky 커넥터 + 폴러 — KADIZ bbox 항적 → 온톨로지.

파이프: OpenSky states/all(bbox) → Event 정규화(mapping) → Aircraft/Observation write
        → observed_as 링크 → Track custody 재구성.

P0A gotcha 반영:
  - callsign strip / squawk str (mapping.py)
  - x-rate-limit-remaining 헤더 로깅 (익명 크레딧 잔여 감시)
  - states 배열 필드 인덱스는 P0A 실측 기준(mapping.OPENSKY_IDX)

폴링 규율(러너웨이 금지):
  - POLL_INTERVAL(기본 15초), MAX_CYCLES(기본 4 — 3~4 사이클 후 자동 종료).
  - MAX_CYCLES=0 이면 무한(개발용, 명시적 opt-in). run_p1.sh stop 으로 중지.
"""

from __future__ import annotations

import os
import time

import httpx

from anomaly.actions import scan_and_create
from anomaly.explainer import get_explainer
from ontology import mapping
from ontology.custody import rebuild_tracks
from ontology.model import KADIZ_BBOX, KADIZ_REGION
from ontology.store_foundry import make_store
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

OPENSKY_URL = "https://opensky-network.org/api/states/all"
TIMEOUT = 20


def source_url_for(bbox: dict) -> str:
    """provenance/citation용 실제 질의 URL (bbox 파라미터 포함)."""
    return (
        f"{OPENSKY_URL}?lamin={bbox['lamin']}&lomin={bbox['lomin']}"
        f"&lamax={bbox['lamax']}&lomax={bbox['lomax']}"
    )


def fetch_states(client: httpx.Client, bbox: dict) -> tuple[list, str]:
    """OpenSky 1회 호출 → (states 배열, source_url). 크레딧 잔여 로깅."""
    resp = client.get(OPENSKY_URL, params=bbox, timeout=TIMEOUT)
    remaining = resp.headers.get("x-rate-limit-remaining", "?")
    print(f"[opensky] HTTP {resp.status_code}  x-rate-limit-remaining={remaining}")
    resp.raise_for_status()
    data = resp.json()
    states = data.get("states") or []
    return states, source_url_for(bbox)


def ingest_cycle(
    store: LocalOntologyStore, client: httpx.Client, bbox: dict
) -> tuple[int, int, int]:
    """1 폴링 사이클: fetch → write(Aircraft/Observation/observed_as) → Track 재구성
    → 이상탐지(비상 스쿽 룰 → CreateAnomaly).

    반환: (이번 사이클 처리 관측 수, 등장 항공기 수, 신규 Anomaly 수).
    """
    fetched_at = int(time.time())
    states, source_url = fetch_states(client, bbox)

    n_obs = 0
    icaos: set[str] = set()
    for state in states:
        event = mapping.opensky_state_to_event(state, source_url, fetched_at)
        if event is None:  # 위치 없는 상태벡터 스킵
            continue
        aircraft = mapping.event_to_aircraft(event)
        obs = mapping.event_to_observation(event)
        store.write_aircraft(aircraft)
        store.write_observation(obs)  # provenance 강제 통과 시에만 저장
        # Aircraft —observed_as→ Observation (ontology.md §2)
        store.link("Aircraft", aircraft.icao24, "observed_as", "Observation", obs.id)
        n_obs += 1
        icaos.add(aircraft.icao24)

    rebuild_tracks(store)
    # 이상탐지: 최신 관측을 비상 스쿽 룰에 통과 → CreateAnomaly(evidence 강제).
    # dedup으로 같은 비상은 중복 생성 안 됨. 백엔드는 SKAI_EXPLAINER로 선택(기본 template).
    new_anomalies = scan_and_create(store, explainer=get_explainer())
    return n_obs, len(icaos), len(new_anomalies)


def run_poller(
    interval: int = 15,
    max_cycles: int = 4,
    db_path: str = DEFAULT_DB,
) -> None:
    """폴러 루프. max_cycles=0 이면 무한(개발용).

    스토어는 make_store()로 선택 — SKAI_STORE=foundry면 HybridStore(Aircraft·Observation은
    Foundry), 미설정이면 LocalOntologyStore(기본, 데모 재현성). DR-0009.
    """
    store = make_store(db_path)
    store.write_region(KADIZ_REGION)  # 관심지역 상수 등록
    print(
        f"[poller] db={db_path} interval={interval}s "
        f"max_cycles={'∞' if max_cycles == 0 else max_cycles}"
    )

    with httpx.Client() as client:
        cycle = 0
        while max_cycles == 0 or cycle < max_cycles:
            cycle += 1
            try:
                n_obs, n_ac, n_anom = ingest_cycle(store, client, KADIZ_BBOX)
                print(
                    f"[cycle {cycle}] obs 처리={n_obs} 항공기={n_ac} "
                    f"신규Anomaly={n_anom} 누적={store.counts()}"
                )
            except Exception as e:  # 사이클 실패는 로깅만, 다음 사이클 진행
                print(f"[cycle {cycle}] 오류: {e!r}")
            if max_cycles != 0 and cycle >= max_cycles:
                break
            time.sleep(interval)

    print(f"[poller] 종료. 최종 카운트={store.counts()}")


def main() -> None:
    interval = int(os.environ.get("POLL_INTERVAL", "15"))
    max_cycles = int(os.environ.get("MAX_CYCLES", "4"))
    db_path = os.environ.get("SKAI_DB", DEFAULT_DB)
    run_poller(interval=interval, max_cycles=max_cycles, db_path=db_path)


if __name__ == "__main__":
    main()
