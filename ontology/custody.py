"""Track custody — Observation을 icao24로 묶어 Track 재구성.

연속 관측 간격 > gap_threshold_seconds()면 has_gap=True — 임계는 실제 폴 간격에 맞춰
상향된다(60s 폴이면 180s). 경로 재구성용 gap 표식이며, dropout 발화는 rules.py가 "지금
끊겨 있음"(now−마지막관측)으로 별도 판정한다(has_gap 이력만으로 발화하지 않는다).
"""

from __future__ import annotations

from ontology.model import Observation, Track, gap_threshold_seconds


def build_track(aircraft_ref: str, observations: list[Observation]) -> Track:
    """한 항공기의 Observation들 → Track. 시간순 정렬 후 gap 판정.

    연속한 두 관측의 ts 간격이 gap_threshold_seconds()(폴 간격 인지)를 넘으면 has_gap=True.
    """
    obs = sorted(observations, key=lambda o: o.ts)
    threshold = gap_threshold_seconds()
    has_gap = any((b.ts - a.ts) > threshold for a, b in zip(obs, obs[1:]))
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
