"""connectors/adsbfi_tracks.py — adsb.fi 반경 질의 → KADIZ 항적 (OpenSky 대체 track 소스).

OpenSky 익명 크레딧 소진(429) 시 라이브 항적이 끊긴다. 이 커넥터는 무료·공개 2차 ADS-B
네트워크(adsb.fi)의 **반경 질의 엔드포인트**로 KADIZ bbox 항적을 받아 OpenSky와 **동일한
Observation/Aircraft 매핑 규약**으로 store에 write한다. 같은 icao24는 기존 dedup/custody가
흡수하므로 OpenSky와 공존 가능(둘 다 켜면 같은 기체의 관측이 두 소스에서 들어와도 Track은
icao24로 묶인다). `crosscheck_live`/`mil_enrich_live`의 자매 소스 — 같은 base URL·같은 ToS·같은
1 req/s 규율, 다른 엔드포인트(항적 스냅샷).

## 엔드포인트 & 실측 (2026-07-05 실호출)
- `GET https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{dist}` — 무인증, **1 req/s**,
  비상업·교육용 허용(adsb.fi 인용 필수). 반경 `{dist}` **해리(nm)** 내 항적을 반환.
- **dist 상한 = 250nm** (실측: dist=250 → HTTP 200, dist=300 → HTTP 400).
- 응답 구조(실측, `/v2/hex`·`/v2/mil`과 **다름** — 주의):
  `{aircraft: [...], now: float, ptime: float, resultCount: int}`.
  * top-level 배열 키가 `aircraft`이다(`ac` 아님). count 키는 `resultCount`(`total` 아님).
  * entry 필드(union, 실측): `hex, flight, lat, lon, alt_baro, gs, track, squawk, seen,
    seen_pos, t, type, dbFlags, r, dst, category, baro_rate, geom_rate, mlat, ...`.
  * `alt_baro`는 정수(피트) **또는 문자열 "ground"**(지상). 실측 81기 중 23기가 "ground".
  * `gs`(지상속도)는 **노트**, `track`은 진북 기준 도(°). `seen`은 마지막 메시지 이후 경과 초.
  * `dst`는 질의 중심으로부터 거리(nm, ≤ dist). `r`=등록기호, `t`=기종코드.

## 단위 변환 (필수 — Observation 규약 = OpenSky 단위)
`anomaly/rules.py`의 급기동 룰은 alt(미터)·velocity(m/s)로 변화율을 계산한다(rules.py 주석
"단위 — 고도=미터(OpenSky baro_altitude), 속도=m/s(OpenSky velocity)"). adsb.fi는 피트·노트로
주므로, 소스가 섞여도 Observation 계약이 한결같도록 **여기서 OpenSky 단위로 변환**한다:
  - `alt_baro`(ft) → `alt`(m)  ×0.3048.  "ground" → alt=None, on_ground=True.
  - `gs`(kt) → `velocity`(m/s)  ×0.514444.
  - `baro_rate`(ft/min) → attrs.vertical_rate(m/s)  ×0.00508.
안 하면 급기동 임계(6000ft/min·3m/s²)가 소스별로 어긋난다.

## 커버리지 (2점 분할 — dist 상한이 bbox 대각을 못 덮음)
KADIZ bbox(lat 32~39, lon 122~132)의 코너는 중심(35.5,127)에서 ≈322nm — 250nm 반경 1개로는
못 덮는다. EW축(더 긴 축)을 둘로 갈라 두 원으로 bbox를 덮는다(코너 여유 ≈7nm):
  - 서: (35.5, 124.5) dist 250   ·   동: (35.5, 129.5) dist 250
사이클당 **2호출**(팀 규율: 1req/s 준수 시 사이클당 2호출 허용). 실제 저해상 한계는 질의
기하가 아니라 **커뮤니티 수신기 커버리지**(특히 서해 너머 중국 방향은 항적·수신 희소)다.

## 리밋·격리 규율
- 사이클당 최대 2호출, 두 호출 사이 최소 간격 1.05s(1 req/s + 여유).
- 각 질의 실패(429·타임아웃·비200·이상응답)는 **그 질의만** skip(None) — 다른 질의는 그대로
  기여한다. 폴러 레벨에서 ingest_cycle 전체가 try/except로 옆 소스와 격리된다.

## 게이트
소스 선택은 `SKAI_POLL_SOURCES`에 `adsbfi` 포함으로 켠다(기본 미포함). opensky.py 폴러가
base 사이클 track 소스로 등록(opensky와 병렬·공존). 기본 라이브 소스는 불변.

## ToS (adsb.fi opendata 문서, crosscheck_live와 동일)
개인·비상업/교육용만. 데이터 라이선스·판매·임대 금지. **adsb.fi 인용 + 홈페이지 링크 필수**.
해커톤 데모 = 비상업·교육 → 허용. UI 크레딧 표기 필요(프론트 몫 — 기존 crosscheck 크레딧이
같은 소스라 커버되나 항적 용도 확대를 크레딧 문구에 반영 권장).
"""

from __future__ import annotations

import os
import time

import httpx

from anomaly.actions import scan_and_create_all
from anomaly.crosscheck import CrossCheckSource
from anomaly.explainer import get_explainer
from anomaly.mil_enrich import MilEnrichmentSource
from anomaly.military_db import resolve_is_military
from ontology.custody import rebuild_tracks
from ontology.model import Aircraft, Observation
from ontology.store_local import DEFAULT_DB, LocalOntologyStore

# base URL은 crosscheck_live/mil_enrich_live와 같은 SSOT(같은 소스, 다른 엔드포인트).
BASE_URL = "https://opendata.adsb.fi/api"
TIMEOUT = 8.0  # 초 — 느린 2차 소스가 폴러 사이클을 오래 막지 않게
MIN_INTERVAL = 1.05  # 사이클 내 두 호출 사이 최소 간격(초) — 1 req/s 존중(+5% 여유)
MAX_DIST_NM = 250  # adsb.fi 반경 상한(실측: 300은 HTTP 400)

# 단위 변환 상수 — Observation 계약(OpenSky 단위)에 맞춘다(모듈 docstring '단위 변환' 참조).
FT_TO_M = 0.3048  # 피트 → 미터 (alt_baro → alt)
KT_TO_MPS = 0.514444  # 노트 → m/s (gs → velocity)
FTMIN_TO_MPS = 0.00508  # ft/min → m/s (baro_rate → vertical_rate)

# 커버리지: KADIZ bbox 2점 분할(서·동). 각 (lat, lon, dist). 코너 여유 ≈7nm.
# 좌표 교체 쉽게 상수로 분리(OpenSky의 KADIZ_BBOX 상수와 같은 취지).
KADIZ_QUERY_POINTS = (
    (35.5, 124.5, MAX_DIST_NM),  # 서(황해 방향)
    (35.5, 129.5, MAX_DIST_NM),  # 동(동해 방향)
)

# API 시민성: 앱 식별 UA(crosscheck_live/mil_enrich_live와 동일 규약).
_USER_AGENT = "SKAI-AirISR/1.0 (hackathon; adsb.fi track fallback; contact via project)"


def source_url_for(lat: float, lon: float, dist: int) -> str:
    """provenance/citation용 실제 반경 질의 URL."""
    return f"{BASE_URL}/v2/lat/{lat}/lon/{lon}/dist/{dist}"


def _num(value) -> float | None:
    """숫자면 float, 아니면 None. alt_baro='ground' 같은 비수치·None 방어."""
    if isinstance(value, bool):  # bool은 int의 하위형이라 명시 배제
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def entry_to_observation(
    entry: dict, source_url: str, fetched_at: int
) -> tuple[Aircraft, Observation] | None:
    """adsb.fi 반경 응답 entry 1건 → (Aircraft, Observation). 위치 없으면 None(스킵).

    OpenSky의 event_to_aircraft/event_to_observation와 같은 규약으로 매핑하되, 단위를
    OpenSky(alt=m·velocity=m/s)로 변환한다(모듈 docstring 참조). ts는 `seen`(마지막 메시지
    이후 경과 초)을 fetched_at에서 빼 보정한다 = OpenSky last_contact와 같은 관측시각 의미.
    """
    hexid = (entry.get("hex") or "").strip().lower()
    if not hexid:
        return None
    lat, lon = _num(entry.get("lat")), _num(entry.get("lon"))
    if lat is None or lon is None:
        return None  # 위치 없는 entry는 증거로 못 씀(OpenSky와 동일 규율)

    # callsign: 공백 패딩 strip(OpenSky gotcha 1과 동일 방어).
    flight = entry.get("flight")
    callsign = flight.strip() if isinstance(flight, str) else None
    callsign = callsign or None

    # squawk: adsb.fi는 이미 str("3217") 또는 None. 문자열 보존(OpenSky gotcha 2 == "7700").
    squawk_raw = entry.get("squawk")
    squawk = str(squawk_raw) if squawk_raw is not None else None

    # alt_baro: 정수(피트)→미터. "ground"(지상)→alt=None + on_ground=True.
    alt_raw = entry.get("alt_baro")
    on_ground = isinstance(alt_raw, str) and alt_raw.strip().lower() == "ground"
    alt_ft = _num(alt_raw)
    alt_m = alt_ft * FT_TO_M if alt_ft is not None else None

    # gs(노트)→velocity(m/s). track(도)은 OpenSky true_track과 같은 단위 → 그대로.
    gs_kt = _num(entry.get("gs"))
    velocity_mps = gs_kt * KT_TO_MPS if gs_kt is not None else None
    heading = _num(entry.get("track"))

    # vertical_rate(attrs): baro_rate(ft/min)→m/s(OpenSky vertical_rate 단위와 일치).
    baro_rate = _num(entry.get("baro_rate"))
    vertical_rate_mps = baro_rate * FTMIN_TO_MPS if baro_rate is not None else None

    # ts = fetched_at - seen(경과 초). seen 없으면 fetched_at.
    seen = _num(entry.get("seen"))
    ts = fetched_at - int(round(seen)) if seen is not None else fetched_at

    registration = entry.get("r") or None
    ac_type = entry.get("t") or None

    aircraft = Aircraft(
        icao24=hexid,
        callsign=callsign,
        registration=registration,  # adsb.fi 제공(OpenSky엔 없는 실데이터 보강)
        type=ac_type,  # 기종코드(A21N 등)
        # is_military: 관측 플래그로 단정 안 함(OpenSky 매핑과 동일). 군용 판정은
        # mil_enrich(adsb.fi dbFlags) + 콜사인·대역 휴리스틱 경로가 담당.
        is_military=False,
    )
    obs = Observation(
        id=f"{hexid}-{ts}",  # (icao24, ts) 자연 dedup 키(OpenSky와 동일)
        aircraft_ref=hexid,
        ts=ts,
        lat=lat,
        lon=lon,
        alt=alt_m,
        velocity=velocity_mps,
        heading=heading,
        squawk=squawk,
        on_ground=on_ground,
        source="adsbfi",
        source_url=source_url,  # provenance 강제 통과(실제 질의 URL)
        attrs={
            "origin_country": None,  # 반경 엔드포인트 미제공(OpenSky 키 병렬 유지)
            "position_source": None,
            "vertical_rate": vertical_rate_mps,
            # adsb.fi 고유(디버깅·provenance): 원 필드 보존.
            "adsbfi_msg_type": entry.get("type"),  # adsb_icao / mlat / tisb 등
            "aircraft_type": ac_type,
            "registration": registration,
            "category": entry.get("category"),
            "dst_nm": round(d, 1)
            if (d := _num(entry.get("dst"))) is not None
            else None,
            "seen_s": seen,
        },
    )
    return aircraft, obs


def response_to_pairs(
    data: dict | None, source_url: str, fetched_at: int
) -> list[tuple[Aircraft, Observation]]:
    """반경 응답(dict) → (Aircraft, Observation) 리스트 (순수 매핑 — 테스트 대상).

    `aircraft` 배열이 null/부재면 [](방어). 위치 없는 entry는 스킵.
    """
    aircraft_list = (data or {}).get("aircraft") or []
    out: list[tuple[Aircraft, Observation]] = []
    for entry in aircraft_list:
        if not isinstance(entry, dict):
            continue
        pair = entry_to_observation(entry, source_url, fetched_at)
        if pair is not None:
            out.append(pair)
    return out


def fetch_point(
    client: httpx.Client, lat: float, lon: float, dist: int
) -> tuple[dict | None, str]:
    """반경 질의 1회 → (응답 dict 또는 None, source_url). 오류는 삼켜 None(질의만 skip).

    429/타임아웃/비200/이상응답은 모두 None으로 격리 — 한 질의 실패가 다른 질의·옆 소스에
    영향 주지 않게(gdelt.fetch_articles와 같은 방어 패턴).
    """
    url = source_url_for(lat, lon, dist)
    try:
        # UA는 요청마다 실어, 폴러의 공용 client(UA 없음)로 호출돼도 API 시민성 유지.
        resp = client.get(url, timeout=TIMEOUT, headers={"User-Agent": _USER_AGENT})
    except httpx.HTTPError as e:
        print(f"[adsbfi] 연결 오류(질의 건너뜀) {url}: {e!r}")
        return None, url
    if resp.status_code == 429:
        print(f"[adsbfi] 429 레이트리밋 — 질의 건너뜀(우회 안 함) {url}")
        return None, url
    if resp.status_code != 200:
        print(f"[adsbfi] HTTP {resp.status_code} 질의 건너뜀 {url}")
        return None, url
    try:
        data = resp.json()
    except Exception as e:
        print(f"[adsbfi] JSON 파싱 실패 {url}: {e!r}")
        return None, url
    if not isinstance(data, dict):
        print(f"[adsbfi] 이상 응답(dict 아님) {url}")
        return None, url
    return data, url


def ingest_cycle(
    store: LocalOntologyStore,
    client: httpx.Client,
    query_points=KADIZ_QUERY_POINTS,
    crosscheck: CrossCheckSource | None = None,
    mil_enrich: MilEnrichmentSource | None = None,
    min_interval: float = MIN_INTERVAL,
) -> tuple[int, int, int]:
    """1 폴링 사이클: 반경 질의(2점) → write(Aircraft/Observation/observed_as) → Track 재구성
    → 이상탐지(scan_and_create_all). opensky.ingest_cycle과 **같은 인터페이스·같은 파이프**.

    각 질의 사이 min_interval(1.05s) 강제(1 req/s). 질의별 실패는 그 질의만 skip(fetch_point가
    None 반환) — 나머지 질의는 그대로 기여한다. 이상탐지는 store 전체를 스캔하므로(OpenSky와
    공유), adsb.fi 관측도 같은 룰로 판정된다. crosscheck/mil_enrich는 폴러가 재사용 인스턴스로
    주입(기본 Null·게이트).

    반환: (처리 관측 수, 등장 항공기 수, 신규 Anomaly 수).
    """
    fetched_at = int(time.time())
    # 사이클 시작 시점 스냅샷 — 기존 is_military 판정 보존(단조: 한 번 True면 계속 True)용.
    ac_map_before = store.aircraft_map()
    n_obs = 0
    icaos: set[str] = set()
    for i, (lat, lon, dist) in enumerate(query_points):
        if i > 0:  # 두 번째 질의부터 최소 간격 확보(1 req/s).
            time.sleep(min_interval)
        data, source_url = fetch_point(client, lat, lon, dist)
        if data is None:  # 이 질의만 skip(다른 질의는 계속).
            continue
        for aircraft, obs in response_to_pairs(data, source_url, fetched_at):
            # 군용 판정 영속 — opensky.ingest_cycle과 동일 규율(REPLACE라 안 실으면 소실).
            existing = ac_map_before.get(aircraft.icao24)
            aircraft.is_military = resolve_is_military(
                existing.is_military if existing else False,
                aircraft.icao24,
                aircraft.callsign,
                mil_enrich,
            )
            store.write_aircraft(aircraft)
            store.write_observation(obs)  # provenance 강제 통과 시에만 저장
            # Aircraft —observed_as→ Observation (ontology.md §2)
            store.link(
                "Aircraft", aircraft.icao24, "observed_as", "Observation", obs.id
            )
            n_obs += 1
            icaos.add(aircraft.icao24)

    rebuild_tracks(store)
    # 이상탐지: store 전체 스캔(OpenSky base 경로와 동일). dedup으로 중복 생성 안 됨.
    created = scan_and_create_all(
        store, crosscheck=crosscheck, explainer=get_explainer(), mil_enrich=mil_enrich
    )
    n_anom = sum(len(v) for v in created.values())
    return n_obs, len(icaos), n_anom


def ingest(store: LocalOntologyStore) -> tuple[int, int, int]:
    """단발 ingest(자체 client) — 검증·CLI용. 폴러는 ingest_cycle을 직접 호출(client 재사용).

    crosscheck/mil_enrich는 게이트 팩토리로 해석(기본 Null). 반환: (obs, aircraft, anomaly).
    """
    from connectors import crosscheck_live, mil_enrich_live

    with httpx.Client(headers={"User-Agent": _USER_AGENT}) as client:
        return ingest_cycle(
            store,
            client,
            crosscheck=crosscheck_live.make_crosscheck(),
            mil_enrich=mil_enrich_live.make_mil_enrichment(),
        )


def main() -> None:
    db_path = os.environ.get("SKAI_DB", DEFAULT_DB)
    store = LocalOntologyStore(db_path)
    from ontology.model import KADIZ_REGION

    store.write_region(KADIZ_REGION)
    n_obs, n_ac, n_anom = ingest(store)
    print(
        f"[adsbfi] obs 처리={n_obs} 항공기={n_ac} 신규Anomaly={n_anom} "
        f"누적={store.counts()}"
    )


if __name__ == "__main__":
    main()
