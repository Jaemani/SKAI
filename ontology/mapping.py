"""Event → 온톨로지 객체 매핑 (data-sources.md 공통포맷 → ontology.md 객체).

OpenSky 상태벡터의 필드 인덱스는 P0A-sources.md 실측 기준(§1 스키마 표).
P0A gotcha 반영:
  1. callsign 공백 패딩 → .strip()
  2. squawk는 str → 문자열로 보존("7700" 비교용)
"""

from __future__ import annotations

from typing import Optional

from ontology.model import Aircraft, Event, Observation

# OpenSky states 배열 필드 인덱스 (P0A-sources.md §1 실측)
OPENSKY_IDX = {
    "icao24": 0,
    "callsign": 1,
    "origin_country": 2,
    "time_position": 3,
    "last_contact": 4,
    "longitude": 5,
    "latitude": 6,
    "baro_altitude": 7,
    "on_ground": 8,
    "velocity": 9,
    "true_track": 10,  # 진북 기준 heading
    "vertical_rate": 11,
    "geo_altitude": 13,
    "squawk": 14,
    "position_source": 16,
}


def opensky_state_to_event(
    state: list, source_url: str, fetched_at: int
) -> Optional[Event]:
    """OpenSky 상태벡터 1건 → Event(kind="aircraft").

    위치(lat/lon)가 없는 상태벡터는 Observation을 만들 수 없으므로 None 반환(스킵).
    """

    def g(name: str):
        i = OPENSKY_IDX[name]
        return state[i] if i < len(state) else None

    icao24 = g("icao24")
    if not icao24:
        return None

    lat, lon = g("latitude"), g("longitude")
    if lat is None or lon is None:
        return None  # 위치 없는 상태벡터는 증거로 못 씀

    # gotcha 1: callsign 공백 패딩 strip
    callsign_raw = g("callsign")
    callsign = callsign_raw.strip() if isinstance(callsign_raw, str) else None
    callsign = callsign or None  # 빈 문자열 → None

    # gotcha 2: squawk는 str로 보존 (7500/7600/7700 문자열 비교)
    squawk_raw = g("squawk")
    squawk = str(squawk_raw) if squawk_raw is not None else None

    # ts = last_contact (P0A 매핑). 없으면 fetched_at 폴백.
    ts = g("last_contact")
    ts = int(ts) if ts is not None else fetched_at

    alt_raw = g("baro_altitude")
    attrs = {
        "icao24": icao24,
        "callsign": callsign,
        "origin_country": g("origin_country"),
        "velocity": g("velocity"),
        "heading": g("true_track"),
        "vertical_rate": g("vertical_rate"),
        "squawk": squawk,
        "on_ground": bool(g("on_ground")),
        "position_source": g("position_source"),
        "time_position": g("time_position"),
    }

    return Event(
        id=f"opensky-{icao24}-{ts}",
        source="opensky",
        source_url=source_url,
        fetched_at=fetched_at,
        kind="aircraft",
        ts=ts,
        lat=float(lat),
        lon=float(lon),
        alt=(float(alt_raw) if alt_raw is not None else None),
        attrs=attrs,
        confidence=1.0,
    )


def event_to_aircraft(event: Event) -> Aircraft:
    """Event(aircraft) → Aircraft 객체 (엔티티 해소 대상)."""
    a = event.attrs
    return Aircraft(
        icao24=a["icao24"],
        callsign=a.get("callsign"),
        # is_military: OpenSky 익명은 군용기를 필터링하는 경우가 많음(P0A 주의).
        # 신뢰 신호가 없어 P1에서는 False. 군용 판정은 P5(교차소스)에서.
        is_military=False,
    )


def event_to_observation(event: Event) -> Observation:
    """Event(aircraft) → Observation 증거 객체. id = f"{icao24}-{ts}" (자연 dedup)."""
    a = event.attrs
    icao24 = a["icao24"]
    return Observation(
        id=f"{icao24}-{event.ts}",
        aircraft_ref=icao24,
        ts=event.ts,
        lat=event.lat,
        lon=event.lon,
        alt=event.alt,
        velocity=a.get("velocity"),
        heading=a.get("heading"),
        squawk=a.get("squawk"),
        on_ground=a.get("on_ground", False),
        source=event.source,
        source_url=event.source_url,
        attrs={
            "origin_country": a.get("origin_country"),
            "position_source": a.get("position_source"),
            "vertical_rate": a.get("vertical_rate"),
        },
    )
