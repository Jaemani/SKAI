"""OpenSky 커넥터 + 폴러 — KADIZ bbox 항적 → 온톨로지.

파이프: OpenSky states/all(bbox) → Event 정규화(mapping) → Aircraft/Observation write
        → observed_as 링크 → Track custody 재구성.

P0A gotcha 반영:
  - callsign strip / squawk str (mapping.py)
  - x-rate-limit-remaining 헤더 로깅 (익명 크레딧 잔여 감시)
  - states 배열 필드 인덱스는 P0A 실측 기준(mapping.OPENSKY_IDX)

폴링 규율(DR-0011 실시간, 러너웨이 금지):
  - 연속 루프. 간격 = SKAI_POLL_INTERVAL(기본 25초, 하위호환 POLL_INTERVAL), **하한 10초**
    (크레딧 안전 — 그 미만은 10초로 올린다). bbox는 KADIZ로 한정 유지.
  - MAX_CYCLES=0 이면 무한(라이브 기본). 유한 값은 검증용(N 사이클 후 종료).
  - **명시 실행 + SIGTERM/SIGINT 정리 종료**로만 돈다(자동 스케줄 없음). 대기는 인터럽트
    가능(신호 수신 시 현재 사이클 후 즉시 정리). 사이클마다 last_poll_ts를 사이드카에 기록
    (server.live_status → 프론트 LIVE 인디케이터).
"""

from __future__ import annotations

import os
import signal
import threading
import time

import httpx

from anomaly.actions import scan_and_create
from anomaly.explainer import get_explainer
from ontology import mapping
from ontology.custody import rebuild_tracks
from ontology.model import KADIZ_BBOX, KADIZ_REGION
from ontology.store_foundry import make_store
from ontology.store_local import DEFAULT_DB, LocalOntologyStore
from server.live_status import write_status

OPENSKY_URL = "https://opensky-network.org/api/states/all"
TIMEOUT = 20

DEFAULT_POLL_INTERVAL = 25  # 라이브 기본 간격(초)
MIN_POLL_INTERVAL = 10  # 크레딧 안전 하한 — 이 미만은 거부(상향)

# SIGTERM/SIGINT 수신 시 세팅 → 현재 사이클 후 루프 종료(러너웨이 방지).
_stop_event = threading.Event()


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
    interval: int = DEFAULT_POLL_INTERVAL,
    max_cycles: int = 0,
    db_path: str = DEFAULT_DB,
    stop_event: threading.Event | None = None,
) -> None:
    """연속 폴러 루프(DR-0011). max_cycles=0 이면 무한(라이브 기본), 유한은 검증용.

    간격은 MIN_POLL_INTERVAL(10초)로 하한을 둔다(크레딧 안전). 사이클마다 온톨로지 write +
    이상탐지(ingest_cycle) 후 last_poll_ts를 사이드카에 기록한다(프론트 LIVE 표시). 대기는
    stop_event로 인터럽트 가능 — SIGTERM/SIGINT 수신 시 현재 사이클을 마치고 정리 종료한다.
    스토어는 make_store()로 선택(SKAI_STORE=foundry면 HybridStore, 기본 로컬 — DR-0009).
    """
    stop_event = stop_event if stop_event is not None else _stop_event
    if interval < MIN_POLL_INTERVAL:
        print(
            f"[poller] interval {interval}s < 하한 {MIN_POLL_INTERVAL}s "
            f"→ {MIN_POLL_INTERVAL}s로 상향(크레딧 안전)"
        )
        interval = MIN_POLL_INTERVAL

    store = make_store(db_path)
    store.write_region(KADIZ_REGION)  # 관심지역 상수 등록
    print(
        f"[poller] db={db_path} interval={interval}s "
        f"max_cycles={'∞(연속)' if max_cycles == 0 else max_cycles} (SIGTERM으로 정리 종료)"
    )
    write_status(
        db_path,
        mode="starting",
        interval=interval,
        max_cycles=max_cycles,
        cycle=0,
        updated_at=int(time.time()),
    )

    with httpx.Client() as client:
        cycle = 0
        while not stop_event.is_set() and (max_cycles == 0 or cycle < max_cycles):
            cycle += 1
            poll_ts = int(time.time())
            try:
                n_obs, n_ac, n_anom = ingest_cycle(store, client, KADIZ_BBOX)
                status = "ok"
                print(
                    f"[cycle {cycle}] obs 처리={n_obs} 항공기={n_ac} "
                    f"신규Anomaly={n_anom} 누적={store.counts()}"
                )
            except Exception as e:  # 사이클 실패는 로깅만, 다음 사이클 진행
                status = "error"
                n_obs = n_ac = n_anom = 0
                print(f"[cycle {cycle}] 오류: {e!r}")
            # LIVE 상태 사이드카 갱신(프론트 인디케이터 — last_poll_ts·상태·카운트).
            write_status(
                db_path,
                mode="live",
                last_poll_ts=poll_ts,
                last_poll_status=status,
                interval=interval,
                max_cycles=max_cycles,
                cycle=cycle,
                counts=store.counts(),
                last_cycle={"obs": n_obs, "aircraft": n_ac, "new_anomalies": n_anom},
                updated_at=int(time.time()),
            )
            if max_cycles != 0 and cycle >= max_cycles:
                break
            # 인터럽트 가능한 대기 — 신호 수신 시 즉시 깨어나 정리 종료.
            if stop_event.wait(interval):
                break

    write_status(
        db_path,
        mode="stopped",
        last_poll_ts=int(time.time()),
        last_poll_status="stopped",
        interval=interval,
        max_cycles=max_cycles,
        cycle=cycle,
        counts=store.counts(),
        updated_at=int(time.time()),
    )
    print(f"[poller] 종료(cycle={cycle}). 최종 카운트={store.counts()}")


def _handle_signal(signum, frame) -> None:
    """SIGTERM/SIGINT → 정리 종료 신호(현재 사이클 후 루프 탈출)."""
    print(f"[poller] signal {signum} 수신 → 현재 사이클 후 정리 종료")
    _stop_event.set()


def main() -> None:
    # 간격: SKAI_POLL_INTERVAL 우선, 하위호환 POLL_INTERVAL, 기본 25초.
    interval = int(
        os.environ.get("SKAI_POLL_INTERVAL")
        or os.environ.get("POLL_INTERVAL")
        or DEFAULT_POLL_INTERVAL
    )
    # MAX_CYCLES: 기본 0 = 무한(라이브). 검증 시 유한값 지정.
    max_cycles = int(os.environ.get("MAX_CYCLES", "0"))
    db_path = os.environ.get("SKAI_DB", DEFAULT_DB)
    # 명시 실행 프로세스만 신호로 정리 종료(자동 스케줄 없음 — 러너웨이 금지).
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run_poller(interval=interval, max_cycles=max_cycles, db_path=db_path)


if __name__ == "__main__":
    main()
