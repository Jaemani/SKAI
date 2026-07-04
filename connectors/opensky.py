"""OpenSky 커넥터 + 다중소스 라이브 폴러 — KADIZ bbox 항적 + 뉴스·기상·위성 → 온톨로지.

파이프(OpenSky): states/all(bbox) → Event 정규화(mapping) → Aircraft/Observation write
        → observed_as 링크 → Track custody 재구성.

P0A gotcha 반영:
  - callsign strip / squawk str (mapping.py)
  - x-rate-limit-remaining 헤더 로깅 (익명 크레딧 잔여 감시)
  - states 배열 필드 인덱스는 P0A 실측 기준(mapping.OPENSKY_IDX)

폴링 규율(DR-0011 실시간, DR-0012 갭#3 다중소스, 러너웨이 금지):
  - 연속 루프. base 간격 = SKAI_POLL_INTERVAL(기본 25초, 하위호환 POLL_INTERVAL), **하한 10초**
    (크레딧 안전 — 그 미만은 10초로 올린다). bbox는 KADIZ로 한정 유지.
  - **소스별 due 스케줄링**(DR-0012 갭#3): 항적(OpenSky)은 매 사이클, 뉴스(GDELT) 5분,
    기상(METAR) 30분, 위성 TLE(Celestrak) 12h — 각 소스의 마지막 폴 이후 주기가 도래한
    소스만 fetch한다. 각 커넥터의 기존 간격 규율(GDELT 5초 강제·Celestrak 12h 파일 캐시)은
    그대로 유지된다. StealthMole은 선택(SKAI_POLL_SOURCES에 명시 + 키 있을 때만).
  - 폴링 소스는 SKAI_POLL_SOURCES(쉼표구분, 기본 opensky,gdelt,metar,celestrak)로 설정.
    **run_poller() 함수 기본은 OpenSky만**(하위호환 — 미설정 프로그래밍 호출·기존 테스트 불변);
    다중소스는 main()/커맨드 레이어에서 SKAI_POLL_SOURCES 기본값으로 켠다. OpenSky-only로
    되돌리려면 SKAI_POLL_SOURCES=opensky.
  - MAX_CYCLES=0 이면 무한(라이브 기본). 유한 값은 검증용(N 사이클 후 종료).
  - **명시 실행 + SIGTERM/SIGINT 정리 종료**로만 돈다(자동 스케줄 없음). 대기는 인터럽트
    가능(신호 수신 시 현재 사이클 후 즉시 정리). 사이클마다 last_poll_ts + 소스별 last_poll을
    사이드카에 기록(server.live_status → 프론트 LIVE·소스별 신선도 인디케이터).
  - 각 소스 실패는 개별 격리 — 한 소스가 죽어도 루프는 계속 돌고 로깅만 한다.
"""

from __future__ import annotations

import os
import signal
import threading
import time

import httpx

from anomaly.actions import scan_and_create_all
from anomaly.crosscheck import CrossCheckSource
from anomaly.explainer import get_explainer
from anomaly.mil_enrich import MilEnrichmentSource
from connectors import (
    adsbfi_tracks,
    celestrak,
    crosscheck_live,
    gdelt,
    metar,
    mil_enrich_live,
    rss,
)
from ontology import mapping
from ontology.custody import rebuild_tracks
from ontology.model import KADIZ_BBOX, KADIZ_REGION
from ontology.store_foundry import make_store
from ontology.store_local import DEFAULT_DB, LocalOntologyStore
from server.live_status import write_status

OPENSKY_URL = "https://opensky-network.org/api/states/all"
TIMEOUT = 20

DEFAULT_POLL_INTERVAL = 25  # 라이브 base 간격(초)
MIN_POLL_INTERVAL = 10  # 크레딧 안전 하한 — 이 미만은 거부(상향)

# 다중소스 폴링(DR-0012 갭#3) ------------------------------------------------
# 커맨드 레이어(main/demo.sh live) 기본 소스 — 항적 + 뉴스·기상·위성.
# rss는 기본 미포함(선택) — 외부 피드 안정성을 관측한 뒤 승격. 실패는 개별 격리되지만
# 기본 라이브 경로는 검증된 소스로 유지하고, 활성은 SKAI_POLL_SOURCES=...,rss로 옵트인한다.
DEFAULT_LIVE_SOURCES = ("opensky", "gdelt", "metar", "celestrak")
STEALTHMOLE_POLL_INTERVAL = (
    30 * 60
)  # 선택 소스(쿼터 보수적 30분). 커넥터 자체가 키 없으면 no-op.
# 보조 소스(OpenSky 제외)별 최소 폴 간격(초). 값은 각 커넥터 SSOT를 참조(architecture.md 주기).
SOURCE_INTERVALS = {
    "gdelt": gdelt.GDELT_POLL_INTERVAL,  # 뉴스 5분
    "rss": rss.RSS_POLL_INTERVAL,  # 보조 뉴스 15분(선택 — 기본 미포함)
    "metar": metar.METAR_POLL_INTERVAL,  # 기상 30분
    "celestrak": celestrak.TLE_POLL_INTERVAL,  # TLE 12h
    "stealthmole": STEALTHMOLE_POLL_INTERVAL,  # 선택 30분
}

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
    store: LocalOntologyStore,
    client: httpx.Client,
    bbox: dict,
    crosscheck: CrossCheckSource | None = None,
    mil_enrich: MilEnrichmentSource | None = None,
) -> tuple[int, int, int]:
    """1 폴링 사이클: fetch → write(Aircraft/Observation/observed_as) → Track 재구성
    → 이상탐지(P5 전 유형 룰 → CreateAnomaly + 상관 영속).

    이상탐지는 scan_and_create_all(비상 스쿽 + dropout + 로이터링 + 군용기 + 위성 근접)로
    전 유형을 스캔한다. dropout은 crosscheck(2차 소스)로 교차 판정 — 기본 Null(미확인·저신뢰),
    SKAI_CROSSCHECK=live 게이트 시 라이브 2차 소스(adsb.fi)로 상향 판정(crosscheck_live).
    군용기 접근은 mil_enrich(공개 DB 플래그)로 라이브 식별을 저신뢰 보강 — 기본 Null(휴리스틱만),
    SKAI_MIL_ENRICH=live 게이트 시 adsb.fi /v2/mil dbFlags로 보강(mil_enrich_live).

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
    # 이상탐지: P5 전 유형 스캔 → CreateAnomaly(evidence 강제) + 상관 영속.
    # dedup으로 같은 이상은 중복 생성 안 됨. 백엔드는 SKAI_EXPLAINER로 선택(기본 template).
    # crosscheck는 dropout 교차 판정용(기본 Null → 저신뢰; SKAI_CROSSCHECK=live 시 라이브 2차 소스).
    created = scan_and_create_all(
        store, crosscheck=crosscheck, explainer=get_explainer(), mil_enrich=mil_enrich
    )
    n_anom = sum(len(v) for v in created.values())
    return n_obs, len(icaos), n_anom


# base 사이클 항적 소스(매 사이클 fetch, due 스케줄 아님). 하나 이상 있어야 "순수 뉴스만
# 폴링"(비의도)을 막는다. opensky 크레딧 소진 시 adsbfi 단독으로도 항적 base가 성립한다.
TRACK_SOURCES = ("opensky", "adsbfi")


def resolve_sources(sources) -> list[str]:
    """폴링 소스 리스트 정규화. None → OpenSky-only(하위호환·기존 테스트 불변).

    다중소스는 호출측(main)이 SKAI_POLL_SOURCES 기본값으로 명시 전달한다. 항적 base 소스
    (opensky/adsbfi)가 하나도 없으면 opensky를 선두 보장(순수 뉴스만 폴링 방지) — 단 adsbfi
    등 다른 track 소스가 명시됐으면 그것이 base를 담당하므로 opensky를 강제하지 않는다
    (OpenSky 크레딧 소진 시 adsbfi 단독 운용). 알 수 없는 이름은 무시(오타·미지원 방어).
    """
    # opensky·adsbfi는 base 사이클 track 소스(due 스케줄 아님) → known에 명시.
    # 나머지(gdelt·metar·celestrak·…)는 SOURCE_INTERVALS의 due 스케줄 소스.
    known = {*TRACK_SOURCES, *SOURCE_INTERVALS.keys()}
    if sources is None:
        return ["opensky"]
    out: list[str] = []
    for s in sources:
        s = s.strip().lower()
        if s and s in known and s not in out:
            out.append(s)
    # 항적 base 소스가 하나도 없을 때만 opensky를 선두 보장. adsbfi 등이 명시됐으면 유지
    # (기존 동작 불변: opensky만/뉴스만 명시하던 config는 여전히 opensky가 선두에 붙는다).
    if not any(s in TRACK_SOURCES for s in out):
        out.insert(0, "opensky")
    return out


def due_sources(
    last_poll: dict, now: int, intervals: dict, sources: list[str]
) -> list[str]:
    """이번 사이클에 폴할 **보조** 소스(OpenSky 제외) 목록 — 순수 함수(스케줄링 단위 테스트 대상).

    각 보조 소스는 마지막 폴 이후 자기 주기(intervals[src])가 도래했을 때만 due. last_poll에
    없거나 0이면(아직 미폴) 즉시 due — 라이브 기동 첫 사이클에 각 소스가 1회씩 fetch된다.
    OpenSky는 매 사이클 base 경로가 담당하므로 여기서 제외한다.
    """
    out: list[str] = []
    for s in sources:
        if s == "opensky":
            continue
        iv = intervals.get(s)
        if iv is None:
            continue
        if now - int(last_poll.get(s, 0)) >= iv:
            out.append(s)
    return out


def _ingest_source(src: str, store) -> str:
    """보조 소스 1회 ingest(기존 커넥터 함수 재사용) → 로깅용 요약 문자열.

    실패는 호출측 루프에서 격리(try/except)한다 — 여기선 커넥터 예외를 그대로 전파.
    StealthMole은 선택 소스라 지연 import(PyJWT·dotenv 의존을 기본 경로에서 배제).
    """
    if src == "gdelt":
        n_news, n_men = gdelt.ingest(
            store
        )  # 실 GDELT URL의 NewsEvent(저신뢰) + mentions
        return f"news={n_news} mentions={n_men}"
    if src == "rss":
        counts = rss.ingest(store)  # 공개 RSS 피드 NewsEvent(저신뢰) + mentions
        return f"rss_news={counts.get('total')} mentions={counts.get('mentions')}"
    if src == "metar":
        n = metar.ingest(store)  # WeatherState(라이브 실황)
        return f"weather={n}"
    if src == "celestrak":
        # OrbitPass 재계산 + 미래 pass stale 정리(delete_future)는 커넥터 ingest 내부 로직 유지.
        n_sat, n_pass, n_skip, n_del = celestrak.ingest(store)
        return f"sat={n_sat} pass={n_pass} skip={n_skip} future_del={n_del}"
    if src == "stealthmole":
        from connectors import stealthmole  # 선택 소스 — 키 없으면 커넥터가 no-op

        counts = stealthmole.ingest(store)
        return f"sm={counts}"
    return "unknown"


def run_poller(
    interval: int = DEFAULT_POLL_INTERVAL,
    max_cycles: int = 0,
    db_path: str = DEFAULT_DB,
    stop_event: threading.Event | None = None,
    sources=None,
) -> None:
    """연속 다중소스 폴러 루프(DR-0011 실시간 · DR-0012 갭#3). max_cycles=0 이면 무한(라이브 기본).

    소스별 due 스케줄링: 항적(OpenSky)은 매 사이클, 보조 소스(GDELT 5분·METAR 30분·Celestrak
    12h·선택 StealthMole)는 각자 주기 도래 시에만 fetch한다. base 간격은 MIN_POLL_INTERVAL(10초)
    하한(크레딧 안전). 각 소스 실패는 개별 격리 — 한 소스가 죽어도 루프는 지속·로깅만. 사이클마다
    last_poll_ts + 소스별 last_poll을 사이드카에 기록(프론트 LIVE·소스별 신선도). 대기는
    stop_event로 인터럽트 가능 — SIGTERM/SIGINT 수신 시 현재 사이클을 마치고 정리 종료한다.
    스토어는 make_store()로 선택(SKAI_STORE=foundry면 HybridStore, 기본 로컬 — DR-0009).

    sources=None(기본)이면 OpenSky-only(하위호환). 다중소스는 main()이 SKAI_POLL_SOURCES로 켠다.
    """
    stop_event = stop_event if stop_event is not None else _stop_event
    sources = resolve_sources(sources)
    if interval < MIN_POLL_INTERVAL:
        print(
            f"[poller] interval {interval}s < 하한 {MIN_POLL_INTERVAL}s "
            f"→ {MIN_POLL_INTERVAL}s로 상향(크레딧 안전)"
        )
        interval = MIN_POLL_INTERVAL

    store = make_store(db_path)
    store.write_region(KADIZ_REGION)  # 관심지역 상수 등록
    # dropout 교차 판정 소스 — 사이클 간 1개 인스턴스 재사용(캐시·레이트리밋 상태 보존).
    # 기본 Null(미확인·저신뢰), SKAI_CROSSCHECK=live 게이트 시 라이브 2차 소스(adsb.fi).
    crosscheck = crosscheck_live.make_crosscheck()
    # 군용 식별 보강 소스 — 사이클 간 1개 인스턴스 재사용(/v2/mil 스냅샷 캐시·60s 리밋 보존).
    # 기본 Null(휴리스틱만), SKAI_MIL_ENRICH=live 게이트 시 adsb.fi /v2/mil dbFlags 보강.
    mil_enrich = mil_enrich_live.make_mil_enrichment()
    # 소스별 마지막 폴 시각(0=미폴 → 첫 사이클에 due) + 마지막 상태.
    last_poll: dict[str, int] = {s: 0 for s in sources}
    last_status: dict[str, str] = {s: "pending" for s in sources}
    print(
        f"[poller] db={db_path} interval={interval}s sources={sources} "
        f"max_cycles={'∞(연속)' if max_cycles == 0 else max_cycles} (SIGTERM으로 정리 종료)"
    )
    write_status(
        db_path,
        mode="starting",
        interval=interval,
        max_cycles=max_cycles,
        cycle=0,
        sources=sources,
        source_last_poll=dict(last_poll),
        updated_at=int(time.time()),
    )

    with httpx.Client() as client:
        cycle = 0
        while not stop_event.is_set() and (max_cycles == 0 or cycle < max_cycles):
            cycle += 1
            poll_ts = int(time.time())
            # ── 항적(OpenSky): 매 사이클 base 경로 ──
            n_obs = n_ac = n_anom = 0
            if "opensky" in sources:
                try:
                    n_obs, n_ac, n_anom = ingest_cycle(
                        store,
                        client,
                        KADIZ_BBOX,
                        crosscheck=crosscheck,
                        mil_enrich=mil_enrich,
                    )
                    status = "ok"
                    last_poll["opensky"] = poll_ts
                    last_status["opensky"] = "ok"
                    print(
                        f"[cycle {cycle}] obs 처리={n_obs} 항공기={n_ac} "
                        f"신규Anomaly={n_anom} 누적={store.counts()}"
                    )
                except Exception as e:  # 사이클 실패는 로깅만, 다음 사이클 진행
                    status = "error"
                    last_status["opensky"] = "error"
                    print(f"[cycle {cycle}] opensky 오류(격리): {e!r}")
            else:
                status = "ok"
            # ── 항적(adsb.fi): OpenSky 크레딧 소진 시 대체/공존 track 소스(매 사이클) ──
            # opensky와 병렬 base 경로. 같은 icao24는 dedup/custody가 흡수(공존 가능).
            # 실패는 격리 — adsb.fi가 죽어도 opensky·옆 소스·루프는 지속(로깅만).
            if "adsbfi" in sources:
                try:
                    a_obs, a_ac, a_anom = adsbfi_tracks.ingest_cycle(
                        store, client, crosscheck=crosscheck, mil_enrich=mil_enrich
                    )
                    n_obs += a_obs
                    n_ac += a_ac
                    n_anom += a_anom
                    last_poll["adsbfi"] = poll_ts
                    last_status["adsbfi"] = "ok"
                    # opensky 없이 adsbfi가 단독 track 소스면 사이클 상태를 adsbfi가 반영.
                    if "opensky" not in sources:
                        status = "ok"
                    print(
                        f"[cycle {cycle}] adsbfi obs={a_obs} 항공기={a_ac} "
                        f"신규Anomaly={a_anom} 누적={store.counts()}"
                    )
                except Exception as e:  # 사이클 실패는 로깅만, 다음 사이클 진행
                    last_status["adsbfi"] = "error"
                    if "opensky" not in sources:
                        status = "error"
                    print(f"[cycle {cycle}] adsbfi 오류(격리): {e!r}")
            # ── 보조 소스(뉴스·기상·위성·선택 SM): 주기 도래분만, 실패는 개별 격리 ──
            for src in due_sources(last_poll, poll_ts, SOURCE_INTERVALS, sources):
                try:
                    summary = _ingest_source(src, store)
                    last_poll[src] = poll_ts
                    last_status[src] = "ok"
                    print(f"[cycle {cycle}] {src} 폴 완료: {summary}")
                except Exception as e:  # 한 소스 실패가 루프·타 소스를 죽이지 않게 격리
                    last_status[src] = "error"
                    print(f"[cycle {cycle}] {src} 오류(격리, 루프 지속): {e!r}")
            # LIVE 상태 사이드카 갱신(프론트 인디케이터 — last_poll_ts·상태·카운트·소스별 신선도).
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
                sources=sources,
                source_last_poll=dict(last_poll),
                source_last_status=dict(last_status),
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
        sources=sources,
        source_last_poll=dict(last_poll),
        source_last_status=dict(last_status),
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
    # 폴링 소스: SKAI_POLL_SOURCES(쉼표구분). 미설정 시 커맨드 레이어 기본 = 다중소스
    # (항적+뉴스+기상+위성 — DR-0012 갭#3). OpenSky-only 회귀는 SKAI_POLL_SOURCES=opensky.
    raw_sources = os.environ.get("SKAI_POLL_SOURCES", ",".join(DEFAULT_LIVE_SOURCES))
    sources = [s for s in raw_sources.split(",") if s.strip()]
    # 명시 실행 프로세스만 신호로 정리 종료(자동 스케줄 없음 — 러너웨이 금지).
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    run_poller(
        interval=interval, max_cycles=max_cycles, db_path=db_path, sources=sources
    )


if __name__ == "__main__":
    main()
