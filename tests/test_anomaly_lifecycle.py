"""이상징후 라이프사이클 — 반증 증거 기반 자동 해소(resolved) + dropout 경계이탈 억제.

커버:
  1. 복귀 관측 → resolved 전이(+attrs.resolution 계약 + 복귀 관측 evidenced_by 링크).
  2. 침묵 지속 → candidate 유지(신선한 관측 없음).
  3. confirmed/dismissed(사람 결정) 불변 — 자동 해소가 절대 건드리지 않음.
  4. resolved는 자동 재오픈 없음(멱등).
  5. replay 자산(SHADOW1·GHOST2) candidate 유지 — 폴러 경로(scan_and_create_all) 내
     자동 해소가 돌아도, 경계이탈 억제가 발화해도 영향 없음.
  6. 경계이탈 억제(경계 근접+외향 → 억제) vs 중앙 침묵 발화 / 내향·heading None은 유지.
  7. geo 헬퍼(union_bbox·heading_exits_bbox) 단위.

실행: .venv/bin/python -m pytest tests/test_anomaly_lifecycle.py -v
"""

from __future__ import annotations

from anomaly.actions import scan_and_create_all, scan_and_resolve
from anomaly.crosscheck import NullCrossCheckSource
from anomaly.rules import (
    ANOMALY_TYPE_ADSB_DROPOUT,
    DROPOUT_EDGE_MARGIN_DEG,
    detect_adsb_dropout,
)
from ontology.geo import heading_exits_bbox, union_bbox
from ontology.model import (
    KADIZ_REGION,
    OPAREA_WEST_REGION,
    Aircraft,
    Anomaly,
    Observation,
    Track,
)
from ontology.store_local import LocalOntologyStore

SENSITIVE = [KADIZ_REGION, OPAREA_WEST_REGION]
KADIZ_UNION = (32.0, 122.0, 39.0, 132.0)  # union(KADIZ, OpArea) = KADIZ bbox


def _store(tmp_path, name="lc.db") -> LocalOntologyStore:
    return LocalOntologyStore(str(tmp_path / name))


def _obs(icao, ts, lat, lon, heading=None, on_ground=False, alt=9000.0):
    return Observation(
        id=f"{icao}-{ts}",
        aircraft_ref=icao,
        ts=ts,
        lat=lat,
        lon=lon,
        alt=alt,
        heading=heading,
        on_ground=on_ground,
        source="synthetic",
        source_url=f"synthetic://{icao}/{ts}",
    )


def _track(icao, lat, lon):
    return Track(
        id=f"track-{icao}",
        aircraft_ref=icao,
        start_ts=400,
        end_ts=1000,
        path=[[lat, lon - 0.05], [lat, lon]],
        has_gap=True,
    )


def _seed_dropout(store, icao="d1", ts=1000, lat=36.0, lon=127.0, status="candidate"):
    """앵커 관측(침묵 시작) + Aircraft + 후보 dropout Anomaly(involves·evidence 링크)를 심는다."""
    store.write_aircraft(Aircraft(icao24=icao))
    anchor = _obs(icao, ts, lat, lon)
    store.write_observation(anchor)
    aid = f"anomaly-{ANOMALY_TYPE_ADSB_DROPOUT}-{icao}-{ts}"
    a = Anomaly(
        id=aid,
        type=ANOMALY_TYPE_ADSB_DROPOUT,
        ts=ts,
        confidence=0.42,
        status=status,
        lat=lat,
        lon=lon,
    )
    store.write_anomaly(
        a,
        evidence=[("Observation", anchor.id)],
        involves=[("Aircraft", icao)],
    )
    return a, anchor


# ══════════════════════════════════════════════════════════════════════════════
# 1. 자동 해소 — 복귀 관측 → resolved
# ══════════════════════════════════════════════════════════════════════════════
def test_return_observed_resolves_candidate(tmp_path):
    store = _store(tmp_path)
    a, anchor = _seed_dropout(store, "d1", ts=1000, lat=36.0, lon=127.0)
    # 침묵 시작(ts=1000) 이후 복귀 관측.
    ret = _obs("d1", ts=1300, lat=36.1, lon=127.1)
    store.write_observation(ret)

    resolved = scan_and_resolve(store, now=1400)

    assert len(resolved) == 1
    got = store.get_anomaly(a.id)
    assert got.status == "resolved"
    # attrs.resolution 계약(프론트 에이전트와 공유되는 고정값).
    res = got.attrs["resolution"]
    assert res == {"kind": "return_observed", "obs_id": ret.id, "resolved_at": 1400}
    # 복귀 관측이 evidenced_by로 **추가**됐다(반증 증거의 provenance) — 앵커 근거도 보존(다중 근거).
    ev = store.query_evidence_ids(a.id)
    assert ret.id in ev
    assert anchor.id in ev


def test_return_uses_latest_observation(tmp_path):
    # 복귀가 여러 건이면 최신 관측을 반증 근거로 쓴다(현재 관측 중임을 증명).
    store = _store(tmp_path)
    a, _ = _seed_dropout(store, "d1", ts=1000)
    store.write_observation(_obs("d1", ts=1200, lat=36.1, lon=127.1))
    latest = _obs("d1", ts=1400, lat=36.2, lon=127.2)
    store.write_observation(latest)

    scan_and_resolve(store, now=1500)

    assert store.get_anomaly(a.id).attrs["resolution"]["obs_id"] == latest.id


# ══════════════════════════════════════════════════════════════════════════════
# 2. 침묵 지속 → candidate 유지
# ══════════════════════════════════════════════════════════════════════════════
def test_persistent_silence_keeps_candidate(tmp_path):
    store = _store(tmp_path)
    a, _ = _seed_dropout(store, "d1", ts=1000)
    # 새 관측 없음 → latest = 앵커(ts=1000) = anomaly.ts → 미해소.
    resolved = scan_and_resolve(store, now=5000)
    assert resolved == []
    got = store.get_anomaly(a.id)
    assert got.status == "candidate"
    assert "resolution" not in got.attrs


# ══════════════════════════════════════════════════════════════════════════════
# 3. confirmed/dismissed(사람 결정) 불변
# ══════════════════════════════════════════════════════════════════════════════
def test_human_decisions_never_auto_resolved(tmp_path):
    for i, human_status in enumerate(("confirmed", "dismissed")):
        store = _store(tmp_path, name=f"{human_status}.db")
        a, _ = _seed_dropout(store, f"h{i}", ts=1000, status=human_status)
        # 복귀 관측이 있어도(반증 존재) 사람 결정은 자동 해소 대상 아님.
        store.write_observation(_obs(f"h{i}", ts=1300, lat=36.1, lon=127.1))

        resolved = scan_and_resolve(store, now=1400)

        assert resolved == []
        got = store.get_anomaly(a.id)
        assert got.status == human_status
        assert "resolution" not in got.attrs


# ══════════════════════════════════════════════════════════════════════════════
# 4. resolved 자동 재오픈 없음(멱등)
# ══════════════════════════════════════════════════════════════════════════════
def test_resolved_not_reopened(tmp_path):
    store = _store(tmp_path)
    a, _ = _seed_dropout(store, "d1", ts=1000)
    store.write_observation(_obs("d1", ts=1300, lat=36.1, lon=127.1))
    scan_and_resolve(store, now=1400)
    assert store.get_anomaly(a.id).status == "resolved"

    # 다음 사이클: 새 관측이 더 들어와도 이미 resolved → 재전이 없음(새 침묵=새 이벤트는 dedup 담당).
    store.write_observation(_obs("d1", ts=1500, lat=36.2, lon=127.2))
    again = scan_and_resolve(store, now=1600)
    assert again == []
    assert store.get_anomaly(a.id).status == "resolved"


# ══════════════════════════════════════════════════════════════════════════════
# 5. replay 자산 candidate 유지 (폴러 경로 scan_and_create_all 내 자동 해소·경계억제 무영향)
# ══════════════════════════════════════════════════════════════════════════════
def test_replay_assets_stay_candidate(tmp_path):
    from scripts.scenarios import apply_scenario, scenario_by_id

    # SHADOW1(dropout_confirmed) · GHOST2(dropout_unconfirmed) · SHADOW7(narrative_hidden).
    # 셋 다 관심영역 내부(커버리지 edge 밖) + 복귀 관측 없음 → 경계억제·자동해소 무영향.
    for sid, callsign in (
        ("dropout_confirmed", "SHADOW1"),
        ("dropout_unconfirmed", "GHOST2"),
        ("narrative_hidden", "SHADOW7"),
    ):
        store = _store(tmp_path, name=f"{sid}.db")
        now = 1783000000
        sc = scenario_by_id(sid)
        mirror = apply_scenario(store, sc, now)
        # 폴러 경로 = scan_and_create_all(내부에서 scan_and_resolve까지 호출).
        scan_and_create_all(store, now=now, crosscheck=mirror)

        drops = [
            a for a in store.query_anomalies() if a.type == ANOMALY_TYPE_ADSB_DROPOUT
        ]
        assert len(drops) == 1, (
            f"{callsign}: dropout 후보 1건이어야(경계억제에 안 걸림)"
        )
        assert drops[0].status == "candidate", f"{callsign}: 복귀 없어 candidate 유지"
        assert "resolution" not in drops[0].attrs


def test_poller_cycle_dropout_then_resolve(tmp_path):
    """폴러 2사이클 통합 — scan_and_create_all(공용 함수)만으로 dropout 생성→복귀 시 resolved."""
    from ontology.custody import rebuild_tracks

    store = _store(tmp_path, name="cycle.db")
    store.write_region(KADIZ_REGION)
    store.write_region(OPAREA_WEST_REGION)
    icao = "cyc1"
    store.write_aircraft(Aircraft(icao24=icao))
    lat, lon = 35.5, 127.0  # KADIZ 중앙(경계억제 무관)
    for ts in (100, 160, 220):
        o = _obs(icao, ts, lat, lon)
        store.write_observation(o)
        store.link("Aircraft", icao, "observed_as", "Observation", o.id)
    rebuild_tracks(store)

    # 사이클 1: 침묵(now1 − 마지막 220 = 600 > 임계) → dropout 후보 생성.
    now1 = 220 + 600
    scan_and_create_all(store, now=now1)
    drops = [a for a in store.query_anomalies() if a.type == ANOMALY_TYPE_ADSB_DROPOUT]
    assert len(drops) == 1 and drops[0].status == "candidate"

    # 사이클 2: 복귀 관측 도착 → 같은 함수가 자동 해소.
    ret = _obs(icao, ts=now1 + 60, lat=lat + 0.05, lon=lon + 0.05)
    store.write_observation(ret)
    store.link("Aircraft", icao, "observed_as", "Observation", ret.id)
    rebuild_tracks(store)
    scan_and_create_all(store, now=now1 + 70)

    got = store.get_anomaly(drops[0].id)
    assert got.status == "resolved"
    assert got.attrs["resolution"]["obs_id"] == ret.id
    assert ret.id in store.query_evidence_ids(got.id)


# ══════════════════════════════════════════════════════════════════════════════
# 6. 경계이탈 억제 vs 중앙 침묵 발화
# ══════════════════════════════════════════════════════════════════════════════
def _dropout_drafts(icao, lat, lon, heading):
    last = _obs(icao, ts=1000, lat=lat, lon=lon, heading=heading)
    return detect_adsb_dropout(
        [_track(icao, lat, lon)],
        {icao: last},
        SENSITIVE,
        now=2000,
        crosscheck=NullCrossCheckSource(),
    )


def test_boundary_exit_outward_suppressed():
    # KADIZ 동쪽 경계(lomax=132) 부근 + 동향(heading=90=동) → 커버리지 이탈로 억제.
    assert _dropout_drafts("edge_e", 36.0, 131.8, heading=90.0) == []


def test_center_silence_still_fires():
    # KADIZ 중앙 침묵 → 경계와 무관, 정상 발화(억제 영향 없음).
    drafts = _dropout_drafts("mid", 35.5, 127.0, heading=90.0)
    assert len(drafts) == 1
    assert drafts[0].type == ANOMALY_TYPE_ADSB_DROPOUT


def test_boundary_near_but_heading_inward_fires():
    # 경계 부근이나 기수가 안쪽(서향=270) → 이탈 아님, 발화 유지.
    drafts = _dropout_drafts("edge_in", 36.0, 131.8, heading=270.0)
    assert len(drafts) == 1


def test_boundary_near_heading_none_fires():
    # 경계 부근이나 heading 미상 → 판정 불가 → 억제 안 함(보수적), 발화 유지.
    drafts = _dropout_drafts("edge_none", 36.0, 131.8, heading=None)
    assert len(drafts) == 1


# ══════════════════════════════════════════════════════════════════════════════
# 7. geo 헬퍼 단위
# ══════════════════════════════════════════════════════════════════════════════
def test_union_bbox_nested_regions():
    # OpArea ⊂ KADIZ → union = KADIZ bbox(가장 바깥 = 커버리지 경계).
    assert union_bbox([KADIZ_REGION, OPAREA_WEST_REGION]) == KADIZ_UNION
    assert union_bbox([]) is None


def test_heading_exits_bbox_cases():
    m = DROPOUT_EDGE_MARGIN_DEG
    # 동쪽 경계 부근: 동향 True / 서향(안쪽) False / 경계평행(북향) False(보수적).
    assert heading_exits_bbox(36.0, 131.8, 90.0, KADIZ_UNION, m) is True
    assert heading_exits_bbox(36.0, 131.8, 270.0, KADIZ_UNION, m) is False
    assert heading_exits_bbox(36.0, 131.8, 0.0, KADIZ_UNION, m) is False
    # 중앙(경계 근접 아님) → False.
    assert heading_exits_bbox(35.5, 127.0, 90.0, KADIZ_UNION, m) is False
    # 코너(북동) + 북동향 → True.
    assert heading_exits_bbox(38.8, 131.8, 45.0, KADIZ_UNION, m) is True
    # None 입력 방어.
    assert heading_exits_bbox(36.0, 131.8, None, KADIZ_UNION, m) is False
    assert heading_exits_bbox(None, 131.8, 90.0, KADIZ_UNION, m) is False
    assert heading_exits_bbox(36.0, 131.8, 90.0, None, m) is False
