"""LocalOntologyStore — SQLite 구현 ("보험" 지위).

DR-0003: SQLite는 plan A가 아니라 Foundry가 뚫릴 때까지의 보험이다.
ontology.md §1 스키마를 그대로 미러하고, 관계는 generic link 테이블로 저장한다.
Foundry가 뚫리면 store_foundry가 같은 OntologyStore 인터페이스를 구현해 교체만 하면 된다.

provenance 강제는 store.validate_provenance로 write_observation에서 집행한다.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional, Sequence

from ontology.model import (
    ANOMALY_STATUSES,
    NEWS_MAX_CONFIDENCE,
    Aircraft,
    Anomaly,
    AssessmentSentence,
    NewsEvent,
    Observation,
    Operator,
    OrbitPass,
    Region,
    Satellite,
    SituationAssessment,
    Track,
    WeatherState,
    cite_object_type,
)
from ontology.store import (
    validate_evidence,
    validate_provenance,
    validate_sentence_cites,
)

DEFAULT_DB = str(Path(__file__).resolve().parent.parent / "data" / "skai.db")


def _normalize_links(items: Sequence, default_type: str) -> list[tuple[str, str]]:
    """evidence/involves 원소를 (dst_type, dst_id) 튜플로 정규화.

    문자열이면 default_type을 붙인다(P2 하위호환: evidence=[obs_id] → ("Observation", obs_id)).
    튜플이면 그대로(P5 타입드 근거: ("OrbitPass", pass_id) 등). 이로써 write_anomaly가
    Observation 외 근거 객체(OrbitPass)·Aircraft 외 주체(Satellite)도 담을 수 있다.
    """
    out: list[tuple[str, str]] = []
    for it in items:
        if isinstance(it, (tuple, list)):
            out.append((it[0], it[1]))
        else:
            out.append((default_type, it))
    return out


class LocalOntologyStore:
    """SQLite 기반 OntologyStore 구현.

    프로세스마다(폴러/서버) 자체 인스턴스를 갖는다. 메서드 호출마다 연결을 열어
    스레드 안전성을 확보하고, WAL 모드로 폴러(write)-서버(read) 동시성을 허용한다.
    """

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # :memory: 는 연결마다 별도 DB이므로 테스트에선 파일 경로를 쓸 것.
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS region (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    classification TEXT,
                    geo_json TEXT
                );
                CREATE TABLE IF NOT EXISTS aircraft (
                    icao24 TEXT PRIMARY KEY,
                    callsign TEXT,
                    registration TEXT,
                    operator_ref TEXT,
                    type TEXT,
                    is_military INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS observation (
                    id TEXT PRIMARY KEY,
                    aircraft_ref TEXT,
                    ts INTEGER,
                    lat REAL,
                    lon REAL,
                    alt REAL,
                    velocity REAL,
                    heading REAL,
                    squawk TEXT,
                    on_ground INTEGER,
                    source TEXT,
                    source_url TEXT,
                    attrs_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_obs_ac_ts
                    ON observation(aircraft_ref, ts);
                CREATE TABLE IF NOT EXISTS track (
                    id TEXT PRIMARY KEY,
                    aircraft_ref TEXT,
                    start_ts INTEGER,
                    end_ts INTEGER,
                    has_gap INTEGER,
                    path_json TEXT
                );
                CREATE TABLE IF NOT EXISTS anomaly (
                    id TEXT PRIMARY KEY,
                    type TEXT,
                    ts INTEGER,
                    confidence REAL,
                    status TEXT,
                    lat REAL,
                    lon REAL,
                    explanation TEXT,
                    explainer_backend TEXT,
                    created_at INTEGER,
                    attrs_json TEXT
                );
                CREATE TABLE IF NOT EXISTS link (
                    src_type TEXT,
                    src_id TEXT,
                    link_type TEXT,
                    dst_type TEXT,
                    dst_id TEXT,
                    attrs_json TEXT,
                    UNIQUE(src_type, src_id, link_type, dst_type, dst_id)
                );
                -- P3 융합 객체 (기존 DB 비파괴: IF NOT EXISTS) --
                CREATE TABLE IF NOT EXISTS satellite (
                    norad_id TEXT PRIMARY KEY,
                    name TEXT,
                    operator_ref TEXT,
                    object_type TEXT,
                    tle_epoch TEXT,
                    source TEXT,
                    source_url TEXT
                );
                CREATE TABLE IF NOT EXISTS orbitpass (
                    id TEXT PRIMARY KEY,
                    satellite_ref TEXT,
                    region_ref TEXT,
                    start_ts INTEGER,
                    end_ts INTEGER,
                    max_elevation REAL,
                    ground_track_json TEXT,
                    source TEXT,
                    source_url TEXT
                );
                CREATE TABLE IF NOT EXISTS weatherstate (
                    id TEXT PRIMARY KEY,
                    region_ref TEXT,
                    ts INTEGER,
                    station TEXT,
                    lat REAL,
                    lon REAL,
                    wind_dir INTEGER,
                    wind_speed_kt REAL,
                    visibility_sm REAL,
                    ceiling_ft INTEGER,
                    flight_category TEXT,
                    conditions TEXT,
                    source TEXT,
                    source_url TEXT,
                    attrs_json TEXT
                );
                CREATE TABLE IF NOT EXISTS newsevent (
                    id TEXT PRIMARY KEY,
                    source TEXT,
                    source_url TEXT,
                    ts INTEGER,
                    title TEXT,
                    summary TEXT,
                    lat REAL,
                    lon REAL,
                    confidence REAL,
                    entities_json TEXT,
                    attrs_json TEXT
                );
                CREATE TABLE IF NOT EXISTS operator (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    kind TEXT,
                    country TEXT
                );
                -- P4 산출 인텔 (기존 DB 비파괴: IF NOT EXISTS) --
                CREATE TABLE IF NOT EXISTS assessment (
                    id TEXT PRIMARY KEY,
                    region_ref TEXT,
                    window_start INTEGER,
                    window_end INTEGER,
                    query TEXT,
                    summary TEXT,
                    confidence REAL,
                    produced_by TEXT,
                    created_at INTEGER,
                    window_label TEXT,
                    sentences_json TEXT,
                    attrs_json TEXT
                );
                """
            )
            # 마이그레이션(기존 DB 비파괴): link.attrs_json 컬럼이 없으면 추가한다.
            # correlated_with 링크에 "왜 상관인가"(시간차·공간관계)를 실을 곳. IF NOT EXISTS
            # CREATE는 이미 있는 테이블에 컬럼을 더하지 못하므로 여기서 ALTER로 보강한다.
            # 기존 링크는 attrs_json=NULL로 그대로 읽힌다(하위호환 — 읽기 안 깨짐).
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(link)")}
            if "attrs_json" not in cols:
                conn.execute("ALTER TABLE link ADD COLUMN attrs_json TEXT")

    # ── write ──────────────────────────────────
    def write_region(self, region: Region) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO region (id, name, classification, geo_json) "
                "VALUES (?, ?, ?, ?)",
                (region.id, region.name, region.classification, json.dumps(region.geo)),
            )

    def write_aircraft(self, aircraft: Aircraft) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO aircraft "
                "(icao24, callsign, registration, operator_ref, type, is_military) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    aircraft.icao24,
                    aircraft.callsign,
                    aircraft.registration,
                    aircraft.operator_ref,
                    aircraft.type,
                    int(aircraft.is_military),
                ),
            )

    def write_observation(self, obs: Observation) -> None:
        # provenance 강제 — 누락이면 여기서 ProvenanceError로 거부.
        validate_provenance(obs)
        with self._connect() as conn:
            # id = (icao24, ts) 자연키 → 중복 관측은 무시(자연 dedup).
            conn.execute(
                "INSERT OR IGNORE INTO observation "
                "(id, aircraft_ref, ts, lat, lon, alt, velocity, heading, squawk, "
                " on_ground, source, source_url, attrs_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    obs.id,
                    obs.aircraft_ref,
                    obs.ts,
                    obs.lat,
                    obs.lon,
                    obs.alt,
                    obs.velocity,
                    obs.heading,
                    obs.squawk,
                    int(obs.on_ground),
                    obs.source,
                    obs.source_url,
                    json.dumps(obs.attrs, ensure_ascii=False),
                ),
            )

    def write_track(self, track: Track) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO track "
                "(id, aircraft_ref, start_ts, end_ts, has_gap, path_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    track.id,
                    track.aircraft_ref,
                    track.start_ts,
                    track.end_ts,
                    int(track.has_gap),
                    json.dumps(track.path),
                ),
            )

    def write_anomaly(
        self,
        anomaly: Anomaly,
        evidence: Sequence[str],
        involves: Sequence[str] = (),
    ) -> None:
        # evidence 강제 — 근거 Observation id가 비어있으면 EvidenceError로 거부.
        # (검증을 write 이전에 두어 근거 없는 Anomaly는 한 건도 저장되지 않게 함)
        validate_evidence(anomaly, evidence)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO anomaly "
                "(id, type, ts, confidence, status, lat, lon, explanation, "
                " explainer_backend, created_at, attrs_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    anomaly.id,
                    anomaly.type,
                    anomaly.ts,
                    anomaly.confidence,
                    anomaly.status,
                    anomaly.lat,
                    anomaly.lon,
                    anomaly.explanation,
                    anomaly.explainer_backend,
                    anomaly.created_at,
                    json.dumps(anomaly.attrs, ensure_ascii=False),
                ),
            )
            # Anomaly —evidenced_by→ 근거 객체 (ontology.md §2, provenance 백본).
            # P5: 근거 타입이 유형마다 다르다(Observation / OrbitPass). 정규화로 타입 보존.
            for dst_type, dst_id in _normalize_links(evidence, "Observation"):
                conn.execute(
                    "INSERT OR IGNORE INTO link "
                    "(src_type, src_id, link_type, dst_type, dst_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("Anomaly", anomaly.id, "evidenced_by", dst_type, dst_id),
                )
            # Anomaly —involves→ 주체 (ontology.md §2). P5: Aircraft 외 Satellite도.
            for dst_type, dst_id in _normalize_links(involves, "Aircraft"):
                conn.execute(
                    "INSERT OR IGNORE INTO link "
                    "(src_type, src_id, link_type, dst_type, dst_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("Anomaly", anomaly.id, "involves", dst_type, dst_id),
                )

    def link(
        self,
        src_type: str,
        src_id: str,
        link_type: str,
        dst_type: str,
        dst_id: str,
        attrs: Optional[dict] = None,
    ) -> None:
        """generic 링크 upsert. attrs 미지정이면 기존과 동일(INSERT OR IGNORE, 멱등).

        attrs 지정 시(correlated_with 사유 등) 충돌해도 attrs_json을 갱신한다 — 재실행마다
        결정적 사유를 새로 계산해 넣으므로, 마이그레이션 전에 만들어진 attrs=NULL 링크도
        다음 실행에서 사유가 채워진다. attrs 미지정 경로는 손대지 않아 다른 링크 타입의
        기존 멱등 동작이 보존된다(하위호환).
        """
        with self._connect() as conn:
            if attrs is None:
                conn.execute(
                    "INSERT OR IGNORE INTO link "
                    "(src_type, src_id, link_type, dst_type, dst_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (src_type, src_id, link_type, dst_type, dst_id),
                )
            else:
                conn.execute(
                    "INSERT INTO link "
                    "(src_type, src_id, link_type, dst_type, dst_id, attrs_json) "
                    "VALUES (?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(src_type, src_id, link_type, dst_type, dst_id) "
                    "DO UPDATE SET attrs_json=excluded.attrs_json",
                    (
                        src_type,
                        src_id,
                        link_type,
                        dst_type,
                        dst_id,
                        json.dumps(attrs, ensure_ascii=False),
                    ),
                )

    # ── write (P3 융합 객체) ────────────────────
    def write_satellite(self, satellite: Satellite) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO satellite "
                "(norad_id, name, operator_ref, object_type, tle_epoch, source, source_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    satellite.norad_id,
                    satellite.name,
                    satellite.operator_ref,
                    satellite.object_type,
                    satellite.tle_epoch,
                    satellite.source,
                    satellite.source_url,
                ),
            )

    def write_orbitpass(self, orbit_pass: OrbitPass) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO orbitpass "
                "(id, satellite_ref, region_ref, start_ts, end_ts, max_elevation, "
                " ground_track_json, source, source_url) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    orbit_pass.id,
                    orbit_pass.satellite_ref,
                    orbit_pass.region_ref,
                    orbit_pass.start_ts,
                    orbit_pass.end_ts,
                    orbit_pass.max_elevation,
                    json.dumps(orbit_pass.ground_track),
                    orbit_pass.source,
                    orbit_pass.source_url,
                ),
            )
            # OrbitPass —of→ Satellite / —over→ Region (ontology.md §2, 시공간 상관)
            conn.execute(
                "INSERT OR IGNORE INTO link "
                "(src_type, src_id, link_type, dst_type, dst_id) VALUES (?, ?, ?, ?, ?)",
                (
                    "OrbitPass",
                    orbit_pass.id,
                    "of",
                    "Satellite",
                    orbit_pass.satellite_ref,
                ),
            )
            conn.execute(
                "INSERT OR IGNORE INTO link "
                "(src_type, src_id, link_type, dst_type, dst_id) VALUES (?, ?, ?, ?, ?)",
                ("OrbitPass", orbit_pass.id, "over", "Region", orbit_pass.region_ref),
            )

    def write_weatherstate(self, weather: WeatherState) -> None:
        # provenance 강제 — source/source_url/ts 누락이면 ProvenanceError로 거부.
        validate_provenance(weather)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO weatherstate "
                "(id, region_ref, ts, station, lat, lon, wind_dir, wind_speed_kt, "
                " visibility_sm, ceiling_ft, flight_category, conditions, "
                " source, source_url, attrs_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    weather.id,
                    weather.region_ref,
                    weather.ts,
                    weather.station,
                    weather.lat,
                    weather.lon,
                    weather.wind_dir,
                    weather.wind_speed_kt,
                    weather.visibility_sm,
                    weather.ceiling_ft,
                    weather.flight_category,
                    weather.conditions,
                    weather.source,
                    weather.source_url,
                    json.dumps(weather.attrs, ensure_ascii=False),
                ),
            )

    def write_newsevent(self, news: NewsEvent, mentions: Sequence[tuple] = ()) -> None:
        # provenance 강제(뉴스도 증거 객체) + confidence 상한 clamp(DR-0005).
        validate_provenance(news)
        confidence = min(news.confidence, NEWS_MAX_CONFIDENCE)
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO newsevent "
                "(id, source, source_url, ts, title, summary, lat, lon, confidence, "
                " entities_json, attrs_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    news.id,
                    news.source,
                    news.source_url,
                    news.ts,
                    news.title,
                    news.summary,
                    news.lat,
                    news.lon,
                    confidence,
                    json.dumps(news.entities, ensure_ascii=False),
                    json.dumps(news.attrs, ensure_ascii=False),
                ),
            )
            # NewsEvent —mentions→ Region/Aircraft (ontology.md §2, 엔티티 링킹)
            for dst_type, dst_id in mentions:
                conn.execute(
                    "INSERT OR IGNORE INTO link "
                    "(src_type, src_id, link_type, dst_type, dst_id) "
                    "VALUES (?, ?, ?, ?, ?)",
                    ("NewsEvent", news.id, "mentions", dst_type, dst_id),
                )

    def write_operator(self, operator: Operator) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO operator (id, name, kind, country) "
                "VALUES (?, ?, ?, ?)",
                (operator.id, operator.name, operator.kind, operator.country),
            )

    # ── write (P4 산출 인텔) ────────────────────
    def write_assessment(self, assessment: SituationAssessment) -> None:
        # 문장별 cites 강제 — cites 없는 문장이 하나라도 있으면 SentenceEvidenceError로 거부.
        # (검증을 write 이전에 두어 근거 없는 문장을 가진 Assessment는 저장되지 않게 함)
        validate_sentence_cites(assessment, assessment.sentences)
        sentences_json = json.dumps(
            [
                {
                    "text": s.text,
                    "cites": s.cites,
                    "confidence": s.confidence,
                    "kind": s.kind,
                }
                for s in assessment.sentences
            ],
            ensure_ascii=False,
        )
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO assessment "
                "(id, region_ref, window_start, window_end, query, summary, "
                " confidence, produced_by, created_at, window_label, "
                " sentences_json, attrs_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    assessment.id,
                    assessment.region_ref,
                    assessment.window_start,
                    assessment.window_end,
                    assessment.query,
                    assessment.summary,
                    assessment.confidence,
                    assessment.produced_by,
                    assessment.created_at,
                    assessment.window_label,
                    sentences_json,
                    json.dumps(assessment.attrs, ensure_ascii=False),
                ),
            )
            # SituationAssessment —aggregates→ Anomaly / —cites→ 그 외 근거 객체
            # (ontology.md §2, provenance 그래프). 문장 cites의 합집합에서 링크를 만든다.
            # 재작성(같은 id) 시 이전 링크를 먼저 지운다 — cites가 줄어든 재생성이 stale
            # 링크를 남기지 않게(INSERT OR REPLACE 행 upsert와 링크 upsert 정합).
            conn.execute(
                "DELETE FROM link WHERE src_type='SituationAssessment' AND src_id=?",
                (assessment.id,),
            )
            seen: set[tuple] = set()
            for s in assessment.sentences:
                for cite_id in s.cites:
                    obj_type = cite_object_type(cite_id)
                    link_type = "aggregates" if obj_type == "Anomaly" else "cites"
                    key = (link_type, obj_type, cite_id)
                    if key in seen:
                        continue
                    seen.add(key)
                    conn.execute(
                        "INSERT OR IGNORE INTO link "
                        "(src_type, src_id, link_type, dst_type, dst_id) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (
                            "SituationAssessment",
                            assessment.id,
                            link_type,
                            obj_type,
                            cite_id,
                        ),
                    )

    def delete_future_orbitpasses_for(self, satellite_ref: str, now_ts: int) -> int:
        """한 위성의 미래 통과창(start_ts >= now_ts)을 삭제 + 그 of/over 링크 제거.

        P3 이월 #1: 통과창은 "now 이후 12h"를 계산 → 폴러 반복 실행마다 신규 id가 쌓여
        과거 계산의 미래 통과가 stale로 잔존한다. 재계산 직전 해당 위성의 **미래** pass만
        지우고(과거는 관측 이력으로 보존), 새 계산 결과로 대체한다. 반환: 삭제된 pass 수.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id FROM orbitpass WHERE satellite_ref = ? AND start_ts >= ?",
                (satellite_ref, now_ts),
            ).fetchall()
            ids = [r["id"] for r in rows]
            for pid in ids:
                conn.execute("DELETE FROM orbitpass WHERE id = ?", (pid,))
                conn.execute(
                    "DELETE FROM link WHERE src_type='OrbitPass' AND src_id=?",
                    (pid,),
                )
        return len(ids)

    # ── read ───────────────────────────────────
    def query_regions(self) -> list[Region]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM region").fetchall()
        return [
            Region(
                id=r["id"],
                name=r["name"],
                classification=r["classification"],
                geo=json.loads(r["geo_json"]),
            )
            for r in rows
        ]

    def query_aircraft(self) -> list[Aircraft]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM aircraft").fetchall()
        return [self._row_to_aircraft(r) for r in rows]

    def aircraft_map(self) -> dict[str, Aircraft]:
        return {a.icao24: a for a in self.query_aircraft()}

    def query_observations_for(self, icao24: str) -> list[Observation]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM observation WHERE aircraft_ref = ? ORDER BY ts",
                (icao24,),
            ).fetchall()
        return [self._row_to_obs(r) for r in rows]

    def query_latest_observations(self) -> list[Observation]:
        """항공기별 최신 관측 1건 (현재 공중 상황 = 지도 마커용)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT o.* FROM observation o "
                "JOIN (SELECT aircraft_ref, MAX(ts) AS mts FROM observation "
                "      GROUP BY aircraft_ref) m "
                "ON o.aircraft_ref = m.aircraft_ref AND o.ts = m.mts"
            ).fetchall()
        return [self._row_to_obs(r) for r in rows]

    def query_all_observations(self, limit: Optional[int] = None) -> list[Observation]:
        sql = "SELECT * FROM observation ORDER BY ts DESC"
        if limit:
            sql += f" LIMIT {int(limit)}"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        return [self._row_to_obs(r) for r in rows]

    def get_observation(self, obs_id: str) -> Optional[Observation]:
        """Observation 1건 조회 (Anomaly 근거 표시용). 없으면 None."""
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM observation WHERE id = ?", (obs_id,)
            ).fetchone()
        return self._row_to_obs(r) if r else None

    def query_tracks(self) -> list[Track]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM track").fetchall()
        return [
            Track(
                id=r["id"],
                aircraft_ref=r["aircraft_ref"],
                start_ts=r["start_ts"],
                end_ts=r["end_ts"],
                path=json.loads(r["path_json"]),
                has_gap=bool(r["has_gap"]),
            )
            for r in rows
        ]

    def query_anomalies(self) -> list[Anomaly]:
        """모든 Anomaly (타임라인용, 시각 내림차순)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM anomaly ORDER BY ts DESC").fetchall()
        return [self._row_to_anomaly(r) for r in rows]

    def get_anomaly(self, anomaly_id: str) -> Optional[Anomaly]:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM anomaly WHERE id = ?", (anomaly_id,)
            ).fetchone()
        return self._row_to_anomaly(r) if r else None

    def set_anomaly_status(self, anomaly_id: str, status: str) -> Anomaly:
        """status 전이(candidate→confirmed/dismissed)를 영속. 없으면 KeyError.

        status 값은 ANOMALY_STATUSES에 속해야 한다(잘못된 값은 ValueError).
        """
        if status not in ANOMALY_STATUSES:
            raise ValueError(
                f"허용되지 않은 status={status!r} (가능: {ANOMALY_STATUSES})"
            )
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE anomaly SET status = ? WHERE id = ?", (status, anomaly_id)
            )
            if cur.rowcount == 0:
                raise KeyError(f"Anomaly 없음: id={anomaly_id!r}")
        got = self.get_anomaly(anomaly_id)
        assert got is not None  # 방금 UPDATE 성공 → 반드시 존재
        return got

    def resolve_anomaly(
        self, anomaly_id: str, obs_id: str, resolved_at: int
    ) -> Anomaly:
        """반증 증거 기반 자동 해소 — status→resolved + attrs.resolution + 복귀 관측 evidenced_by.

        actions.scan_and_resolve가 status=candidate인 adsb_dropout에 대해서만 호출한다(사람 결정
        confirmed/dismissed는 건드리지 않음 — 호출측 필터). 근거 없는 상태 전이 금지 원칙의 연장:
        복귀 관측(obs_id)을 evidenced_by 링크로도 남겨 "왜 해소됐나"를 온톨로지에서 역추적 가능하게
        한다(반증 증거의 provenance). resolution dict = 프론트 에이전트와 공유되는 고정 계약.
        없으면 KeyError.
        """
        a = self.get_anomaly(anomaly_id)
        if a is None:
            raise KeyError(f"Anomaly 없음: id={anomaly_id!r}")
        a.attrs["resolution"] = {
            "kind": "return_observed",
            "obs_id": obs_id,
            "resolved_at": int(resolved_at),
        }
        with self._connect() as conn:
            conn.execute(
                "UPDATE anomaly SET status = ?, attrs_json = ? WHERE id = ?",
                ("resolved", json.dumps(a.attrs, ensure_ascii=False), anomaly_id),
            )
            # Anomaly —evidenced_by→ 복귀 Observation (반증 증거의 provenance). 멱등(OR IGNORE) —
            # 탐지 시 근거(침묵 시작 관측)에 복귀 관측을 **추가**한다(대체 아님, 다중 근거 보존).
            conn.execute(
                "INSERT OR IGNORE INTO link "
                "(src_type, src_id, link_type, dst_type, dst_id) "
                "VALUES (?, ?, ?, ?, ?)",
                ("Anomaly", anomaly_id, "evidenced_by", "Observation", obs_id),
            )
        got = self.get_anomaly(anomaly_id)
        assert got is not None  # 방금 UPDATE 성공 → 반드시 존재
        return got

    # ── B2 staged human review (방법 B) ──────────────
    # Anomaly에 proposed_explanation·review_status는 별도 컬럼 없이 attrs(JSON)에 미러한다
    # (스키마 마이그레이션 회피). 본 explanation 컬럼은 approve 전까지 불변 = 스테이징의 핵심.
    def propose_explanation(
        self, anomaly_id: str, explanation: str, review_status: str = "pending"
    ) -> Anomaly:
        """제안 — attrs.proposed_explanation·review_status 기록. 본 explanation 불변. 없으면 KeyError."""
        a = self.get_anomaly(anomaly_id)
        if a is None:
            raise KeyError(f"Anomaly 없음: id={anomaly_id!r}")
        a.attrs["proposed_explanation"] = explanation
        a.attrs["review_status"] = review_status
        with self._connect() as conn:
            conn.execute(
                "UPDATE anomaly SET attrs_json = ? WHERE id = ?",
                (json.dumps(a.attrs, ensure_ascii=False), anomaly_id),
            )
        got = self.get_anomaly(anomaly_id)
        assert got is not None
        return got

    def approve_explanation(self, anomaly_id: str) -> Anomaly:
        """승인 — explanation←attrs.proposed_explanation 복사 + review_status=approved. 없으면 KeyError."""
        a = self.get_anomaly(anomaly_id)
        if a is None:
            raise KeyError(f"Anomaly 없음: id={anomaly_id!r}")
        proposed = a.attrs.get("proposed_explanation")
        if proposed:
            a.explanation = proposed
        a.attrs["review_status"] = "approved"
        with self._connect() as conn:
            conn.execute(
                "UPDATE anomaly SET explanation = ?, attrs_json = ? WHERE id = ?",
                (a.explanation, json.dumps(a.attrs, ensure_ascii=False), anomaly_id),
            )
        got = self.get_anomaly(anomaly_id)
        assert got is not None
        return got

    def reject_explanation(self, anomaly_id: str) -> Anomaly:
        """기각 — review_status=rejected. 본 explanation·proposed 불변. 없으면 KeyError."""
        a = self.get_anomaly(anomaly_id)
        if a is None:
            raise KeyError(f"Anomaly 없음: id={anomaly_id!r}")
        a.attrs["review_status"] = "rejected"
        with self._connect() as conn:
            conn.execute(
                "UPDATE anomaly SET attrs_json = ? WHERE id = ?",
                (json.dumps(a.attrs, ensure_ascii=False), anomaly_id),
            )
        got = self.get_anomaly(anomaly_id)
        assert got is not None
        return got

    def query_evidence_ids(self, anomaly_id: str) -> list[str]:
        """Anomaly —evidenced_by→ Observation 링크의 대상 Observation id 목록."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dst_id FROM link WHERE src_type='Anomaly' AND src_id=? "
                "AND link_type='evidenced_by' AND dst_type='Observation'",
                (anomaly_id,),
            ).fetchall()
        return [r["dst_id"] for r in rows]

    def query_involves_ids(self, anomaly_id: str) -> list[str]:
        """Anomaly —involves→ Aircraft 링크의 대상 Aircraft icao24 목록."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dst_id FROM link WHERE src_type='Anomaly' AND src_id=? "
                "AND link_type='involves' AND dst_type='Aircraft'",
                (anomaly_id,),
            ).fetchall()
        return [r["dst_id"] for r in rows]

    def query_evidence(self, anomaly_id: str) -> list[dict]:
        """Anomaly —evidenced_by→ 근거 객체 (타입 포함, P5 위성 근접 등 비-Observation).

        반환 원소 = {type, id}. query_evidence_ids(Observation 한정, P4용)와 달리 모든
        근거 타입(Observation/OrbitPass 등)을 낸다. 서버 상세·평가 근거표에 쓴다.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dst_type, dst_id FROM link WHERE src_type='Anomaly' AND src_id=? "
                "AND link_type='evidenced_by'",
                (anomaly_id,),
            ).fetchall()
        return [{"type": r["dst_type"], "id": r["dst_id"]} for r in rows]

    def query_involves(self, anomaly_id: str) -> list[dict]:
        """Anomaly —involves→ 주체 (타입 포함, P5 Satellite 등). 반환 {type, id}."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dst_type, dst_id FROM link WHERE src_type='Anomaly' AND src_id=? "
                "AND link_type='involves'",
                (anomaly_id,),
            ).fetchall()
        return [{"type": r["dst_type"], "id": r["dst_id"]} for r in rows]

    def query_correlations(self, anomaly_id: str) -> list[dict]:
        """Anomaly —correlated_with→ (Anomaly/NewsEvent/OrbitPass) 상관 링크 (P5).

        교차소스 내러티브의 영속 링크(ontology.md §2). correlated_with는 대칭이라
        anomaly↔anomaly는 정준방향(작은 id→큰 id)으로 저장되므로, 이 질의는 src·dst
        양방향을 모아 항상 **상대 객체**를 dst로 정규화해 반환한다.
        반환 {dst_type, dst_id, reason}. reason = "왜 상관인가"(시간차·공간관계) dict 또는
        None(마이그레이션 전 생성된 구링크). UI가 상관 근거 표시에 쓴다.
        """
        with self._connect() as conn:
            out_rows = conn.execute(
                "SELECT dst_type, dst_id, attrs_json FROM link "
                "WHERE src_type='Anomaly' AND src_id=? AND link_type='correlated_with'",
                (anomaly_id,),
            ).fetchall()
            # 반대방향(다른 Anomaly가 이 Anomaly를 correlated_with로 가리킴)
            in_rows = conn.execute(
                "SELECT src_type, src_id, attrs_json FROM link "
                "WHERE dst_type='Anomaly' AND dst_id=? "
                "AND link_type='correlated_with' AND src_type='Anomaly'",
                (anomaly_id,),
            ).fetchall()
        out = [
            {
                "dst_type": r["dst_type"],
                "dst_id": r["dst_id"],
                "reason": json.loads(r["attrs_json"]) if r["attrs_json"] else None,
            }
            for r in out_rows
        ]
        out += [
            {
                "dst_type": r["src_type"],
                "dst_id": r["src_id"],
                "reason": json.loads(r["attrs_json"]) if r["attrs_json"] else None,
            }
            for r in in_rows
        ]
        return out

    def query_all_correlations(self) -> list[dict]:
        """모든 correlated_with 링크(평가·서브그래프용).

        반환 {src_id, dst_type, dst_id, reason}. reason = 사유 dict 또는 None(구링크).
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT src_id, dst_type, dst_id, attrs_json FROM link "
                "WHERE src_type='Anomaly' AND link_type='correlated_with'"
            ).fetchall()
        return [
            {
                "src_id": r["src_id"],
                "dst_type": r["dst_type"],
                "dst_id": r["dst_id"],
                "reason": json.loads(r["attrs_json"]) if r["attrs_json"] else None,
            }
            for r in rows
        ]

    # ── read (P3 융합 객체) ────────────────────
    def query_satellites(self) -> list[Satellite]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM satellite").fetchall()
        return [self._row_to_satellite(r) for r in rows]

    def satellite_map(self) -> dict[str, Satellite]:
        return {s.norad_id: s for s in self.query_satellites()}

    def query_orbitpasses(self) -> list[OrbitPass]:
        """모든 OrbitPass (지상궤적 레이어용, 통과 시작시각 순)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM orbitpass ORDER BY start_ts").fetchall()
        return [self._row_to_orbitpass(r) for r in rows]

    def query_weather_latest(self) -> list[WeatherState]:
        """공항별 최신 기상 1건 (기상 카드용)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT w.* FROM weatherstate w "
                "JOIN (SELECT station, MAX(ts) AS mts FROM weatherstate "
                "      GROUP BY station) m "
                "ON w.station = m.station AND w.ts = m.mts"
            ).fetchall()
        return [self._row_to_weather(r) for r in rows]

    def query_news(self) -> list[NewsEvent]:
        """모든 NewsEvent (뉴스 패널용, 시각 내림차순)."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM newsevent ORDER BY ts DESC").fetchall()
        return [self._row_to_news(r) for r in rows]

    def query_operators(self) -> list[Operator]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM operator").fetchall()
        return [
            Operator(id=r["id"], name=r["name"], kind=r["kind"], country=r["country"])
            for r in rows
        ]

    def query_mentions(self, news_id: str) -> list[dict]:
        """NewsEvent —mentions→ (Region/Aircraft/Operator) 링크의 대상 목록."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT dst_type, dst_id FROM link WHERE src_type='NewsEvent' "
                "AND src_id=? AND link_type='mentions'",
                (news_id,),
            ).fetchall()
        return [{"type": r["dst_type"], "id": r["dst_id"]} for r in rows]

    # ── read (P4 산출 인텔) ─────────────────────
    def query_assessments(self) -> list[SituationAssessment]:
        """모든 SituationAssessment (생성 시각 내림차순 — 최신 답변 먼저)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM assessment ORDER BY created_at DESC"
            ).fetchall()
        return [self._row_to_assessment(r) for r in rows]

    def get_assessment(self, assessment_id: str) -> Optional[SituationAssessment]:
        with self._connect() as conn:
            r = conn.execute(
                "SELECT * FROM assessment WHERE id = ?", (assessment_id,)
            ).fetchone()
        return self._row_to_assessment(r) if r else None

    def query_assessment_links(self, assessment_id: str) -> list[dict]:
        """SituationAssessment —aggregates/cites→ 대상 링크 목록(서브그래프 뷰용).

        반환 원소 = {link_type, dst_type, dst_id}. aggregates→Anomaly / cites→그 외.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT link_type, dst_type, dst_id FROM link "
                "WHERE src_type='SituationAssessment' AND src_id=?",
                (assessment_id,),
            ).fetchall()
        return [
            {
                "link_type": r["link_type"],
                "dst_type": r["dst_type"],
                "dst_id": r["dst_id"],
            }
            for r in rows
        ]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        with self._connect() as conn:
            for t in (
                "aircraft",
                "observation",
                "track",
                "region",
                "anomaly",
                "satellite",
                "orbitpass",
                "weatherstate",
                "newsevent",
                "operator",
                "assessment",
                "link",
            ):
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out

    # ── row 헬퍼 ───────────────────────────────
    @staticmethod
    def _row_to_aircraft(r: sqlite3.Row) -> Aircraft:
        return Aircraft(
            icao24=r["icao24"],
            callsign=r["callsign"],
            registration=r["registration"],
            operator_ref=r["operator_ref"],
            type=r["type"],
            is_military=bool(r["is_military"]),
        )

    @staticmethod
    def _row_to_anomaly(r: sqlite3.Row) -> Anomaly:
        return Anomaly(
            id=r["id"],
            type=r["type"],
            ts=r["ts"],
            confidence=r["confidence"],
            status=r["status"],
            lat=r["lat"],
            lon=r["lon"],
            explanation=r["explanation"],
            explainer_backend=r["explainer_backend"],
            created_at=r["created_at"],
            attrs=json.loads(r["attrs_json"]) if r["attrs_json"] else {},
        )

    @staticmethod
    def _row_to_obs(r: sqlite3.Row) -> Observation:
        return Observation(
            id=r["id"],
            aircraft_ref=r["aircraft_ref"],
            ts=r["ts"],
            lat=r["lat"],
            lon=r["lon"],
            alt=r["alt"],
            velocity=r["velocity"],
            heading=r["heading"],
            squawk=r["squawk"],
            on_ground=bool(r["on_ground"]),
            source=r["source"],
            source_url=r["source_url"],
            attrs=json.loads(r["attrs_json"]) if r["attrs_json"] else {},
        )

    @staticmethod
    def _row_to_satellite(r: sqlite3.Row) -> Satellite:
        return Satellite(
            norad_id=r["norad_id"],
            name=r["name"],
            operator_ref=r["operator_ref"],
            object_type=r["object_type"],
            tle_epoch=r["tle_epoch"],
            source=r["source"],
            source_url=r["source_url"],
        )

    @staticmethod
    def _row_to_orbitpass(r: sqlite3.Row) -> OrbitPass:
        return OrbitPass(
            id=r["id"],
            satellite_ref=r["satellite_ref"],
            region_ref=r["region_ref"],
            start_ts=r["start_ts"],
            end_ts=r["end_ts"],
            max_elevation=r["max_elevation"],
            ground_track=json.loads(r["ground_track_json"])
            if r["ground_track_json"]
            else [],
            source=r["source"],
            source_url=r["source_url"],
        )

    @staticmethod
    def _row_to_weather(r: sqlite3.Row) -> WeatherState:
        return WeatherState(
            id=r["id"],
            region_ref=r["region_ref"],
            ts=r["ts"],
            station=r["station"],
            lat=r["lat"],
            lon=r["lon"],
            wind_dir=r["wind_dir"],
            wind_speed_kt=r["wind_speed_kt"],
            visibility_sm=r["visibility_sm"],
            ceiling_ft=r["ceiling_ft"],
            flight_category=r["flight_category"],
            conditions=r["conditions"],
            source=r["source"],
            source_url=r["source_url"],
            attrs=json.loads(r["attrs_json"]) if r["attrs_json"] else {},
        )

    @staticmethod
    def _row_to_news(r: sqlite3.Row) -> NewsEvent:
        return NewsEvent(
            id=r["id"],
            source=r["source"],
            source_url=r["source_url"],
            ts=r["ts"],
            title=r["title"],
            summary=r["summary"],
            lat=r["lat"],
            lon=r["lon"],
            confidence=r["confidence"],
            entities=json.loads(r["entities_json"]) if r["entities_json"] else [],
            attrs=json.loads(r["attrs_json"]) if r["attrs_json"] else {},
        )

    @staticmethod
    def _row_to_assessment(r: sqlite3.Row) -> SituationAssessment:
        raw = json.loads(r["sentences_json"]) if r["sentences_json"] else []
        sentences = [
            AssessmentSentence(
                text=s["text"],
                cites=s.get("cites", []),
                confidence=s.get("confidence", 0.0),
                kind=s.get("kind", ""),
            )
            for s in raw
        ]
        return SituationAssessment(
            id=r["id"],
            region_ref=r["region_ref"],
            window_start=r["window_start"],
            window_end=r["window_end"],
            query=r["query"],
            summary=r["summary"],
            sentences=sentences,
            confidence=r["confidence"],
            produced_by=r["produced_by"],
            created_at=r["created_at"],
            window_label=r["window_label"] or "",
            attrs=json.loads(r["attrs_json"]) if r["attrs_json"] else {},
        )
