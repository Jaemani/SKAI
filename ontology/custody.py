"""Track custody — Observation을 icao24로 묶어 Track 재구성.

연속 관측 간격 > GAP_THRESHOLD_SECONDS(90s)면 has_gap=True.
ADS-B dropout 후보의 1차 신호(단정은 P5에서 교차소스로).
"""

from __future__ import annotations

from ontology.model import GAP_THRESHOLD_SECONDS, Observation, Track


def build_track(aircraft_ref: str, observations: list[Observation]) -> Track:
    """한 항공기의 Observation들 → Track. 시간순 정렬 후 gap 판정.

    연속한 두 관측의 ts 간격이 GAP_THRESHOLD_SECONDS를 넘으면 has_gap=True.
    """
    obs = sorted(observations, key=lambda o: o.ts)
    has_gap = any((b.ts - a.ts) > GAP_THRESHOLD_SECONDS for a, b in zip(obs, obs[1:]))
    path = [[o.lat, o.lon] for o in obs]
    return Track(
        id=f"track-{aircraft_ref}",
        aircraft_ref=aircraft_ref,
        start_ts=obs[0].ts,
        end_ts=obs[-1].ts,
        path=path,
        has_gap=has_gap,
    )


def rebuild_tracks(store) -> int:
    """store의 모든 항공기에 대해 Track 재구성 + composed_of 링크 저장.

    반환: 만들어진 Track 수. (각 사이클 후 호출 — bbox 규모에선 비용 무시가능)
    """
    n = 0
    for ac in store.query_aircraft():
        obs = store.query_observations_for(ac.icao24)
        if not obs:
            continue
        track = build_track(ac.icao24, obs)
        store.write_track(track)
        # Track —composed_of→ Observation (ontology.md §2)
        for o in obs:
            store.link("Track", track.id, "composed_of", "Observation", o.id)
        n += 1
    return n
