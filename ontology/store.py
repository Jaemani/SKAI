"""OntologyStore 인터페이스 + provenance 강제.

DR-0003 결정: 커넥터·트랙·API는 이 인터페이스에만 의존한다.
Foundry가 뚫리면 store_foundry가 이 Protocol을 구현해 교체만 하면 된다.

**provenance 강제(이 프로젝트 환각방지 백본의 선행 구현)**:
증거 객체(Observation)의 source·source_url·ts 누락 write는 store 레벨에서 거부한다.
= ontology.md §3 "근거(evidence) 없는 객체는 Action이 거부"를 저장 레벨에서 못박음.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from ontology.model import (
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
)

# 증거 객체가 반드시 가져야 할 provenance 필드
REQUIRED_PROVENANCE = ("source", "source_url", "ts")


class ProvenanceError(ValueError):
    """provenance(source/source_url/ts) 누락 시 write 거부."""


class EvidenceError(ValueError):
    """evidence(근거 객체) 링크 없는 Anomaly 생성 거부.

    ontology.md §3 "근거 없는 Anomaly는 Action이 거부"를 저장 레벨에서 못박음.
    provenance(관측)와 짝을 이루는 파생객체(Anomaly)용 강제.
    """


def validate_provenance(obj) -> None:
    """증거 객체의 provenance를 검증. 누락 시 ProvenanceError.

    ts는 양의 Unix 시각, source/source_url은 비어있지 않은 문자열이어야 한다.
    source·source_url·ts·id를 가진 모든 증거 객체(Observation·NewsEvent·WeatherState)에
    적용된다(덕 타이핑 — P3에서 위성 이외의 라이브 소스도 같은 백본으로 강제).
    """
    missing: list[str] = []
    if not getattr(obj, "source", ""):
        missing.append("source")
    if not getattr(obj, "source_url", ""):
        missing.append("source_url")
    ts = getattr(obj, "ts", None)
    if ts is None or ts <= 0:
        missing.append("ts")
    if missing:
        raise ProvenanceError(
            f"provenance 누락 {missing} — 증거 객체는 {REQUIRED_PROVENANCE} 필수 "
            f"(환각방지 백본: 출처 없는 관측은 온톨로지에 들어갈 수 없다). "
            f"거부된 객체 id={getattr(obj, 'id', '?')!r}"
        )


def validate_evidence(anomaly: Anomaly, evidence: Sequence[str]) -> None:
    """Anomaly의 evidence(근거 Observation id 리스트)를 검증. 비어있으면 EvidenceError.

    validate_provenance의 파생객체판. 근거 링크 없는 Anomaly는 어떤 경로로도
    저장될 수 없다(ontology.md §3, Action 레벨 강제 + store 레벨 이중 방어).
    """
    if not evidence:
        raise EvidenceError(
            f"evidence 없음 — Anomaly는 evidenced_by 링크(근거 Observation) 필수 "
            f"(provenance 강제: 근거 없는 이상징후는 온톨로지에 들어갈 수 없다). "
            f"거부된 Anomaly id={anomaly.id!r} type={anomaly.type!r}"
        )


class SentenceEvidenceError(EvidenceError):
    """cites(근거 객체) 없는 문장이 SituationAssessment에 진입하려 할 때 거부.

    DR-0006 핵심: citation은 LLM 생성이 아니라 사실→문장 조립의 부산물이다. 근거 객체
    id를 갖지 못한 문장은 "근거 없는 주장"이므로 Assessment에 들어갈 수 없다
    (CLAUDE.md 원칙 4 = 이 프로젝트의 존재 이유). EvidenceError(Anomaly판)의 문장판.
    """


def validate_sentence_cites(
    assessment: SituationAssessment, sentences: Sequence[AssessmentSentence]
) -> None:
    """Assessment의 각 문장이 cites(근거 객체 id)를 갖는지 검증. 하나라도 비면 거부.

    문장 단위 검증 — 어떤 편의로도 cites 없는 문장이 저장되지 않게 한다(DR-0006).
    """
    if not sentences:
        raise SentenceEvidenceError(
            f"문장 없음 — SituationAssessment는 최소 1개의 근거 달린 문장 필수. "
            f"거부된 Assessment id={assessment.id!r} query={assessment.query!r}"
        )
    for i, s in enumerate(sentences):
        if not s.cites:
            raise SentenceEvidenceError(
                f"cites 없는 문장 거부 — 근거 객체 없는 주장은 Assessment에 못 들어간다 "
                f"(DR-0006: citation은 조립의 부산물). "
                f"거부된 Assessment id={assessment.id!r} 문장[{i}]={s.text[:60]!r}"
            )


@runtime_checkable
class OntologyStore(Protocol):
    """온톨로지 저장 어댑터 인터페이스.

    write_* = 객체 upsert, link = 관계 저장, query_* = read.
    관계(observed_as, composed_of)는 link()로 저장한다.
    """

    # ── write ──
    def write_region(self, region: Region) -> None: ...
    def write_aircraft(self, aircraft: Aircraft) -> None: ...
    def write_observation(self, obs: Observation) -> None:
        """provenance 강제 — 누락 시 ProvenanceError."""
        ...

    def write_track(self, track: Track) -> None: ...
    def write_anomaly(
        self,
        anomaly: Anomaly,
        evidence: Sequence[str],
        involves: Sequence[str] = (),
    ) -> None:
        """Anomaly upsert + evidenced_by/involves 링크를 원자적으로 저장.

        evidence(근거 Observation id)가 비어있으면 EvidenceError로 거부한다
        (근거 없는 Anomaly는 어떤 경로로도 저장 불가).
        """
        ...

    # ── write (P3 융합 객체) ──
    def write_satellite(self, satellite: Satellite) -> None: ...
    def write_orbitpass(self, orbit_pass: OrbitPass) -> None:
        """OrbitPass upsert + of→Satellite / over→Region 링크를 저장."""
        ...

    def write_weatherstate(self, weather: WeatherState) -> None:
        """provenance 강제 — 누락 시 ProvenanceError."""
        ...

    def write_newsevent(self, news: NewsEvent, mentions: Sequence[tuple] = ()) -> None:
        """NewsEvent upsert + mentions→(Region/Aircraft) 링크 저장.

        confidence는 NEWS_MAX_CONFIDENCE(0.4)로 clamp된다. provenance 강제.
        mentions = [(dst_type, dst_id), ...].
        """
        ...

    def write_operator(self, operator: Operator) -> None: ...

    # ── write (P4 산출 인텔) ──
    def write_assessment(self, assessment: SituationAssessment) -> None:
        """SituationAssessment upsert + 문장별 cites 강제 + aggregates/cites 링크.

        각 문장이 cites(근거 객체 id)를 갖지 못하면 SentenceEvidenceError로 거부한다
        (근거 없는 문장은 어떤 경로로도 Assessment에 못 들어감 = GenerateSituationAssessment
        액션의 provenance 강제). cites 객체 id는 aggregates→Anomaly / cites→그 외 링크로 저장.
        """
        ...

    def link(
        self,
        src_type: str,
        src_id: str,
        link_type: str,
        dst_type: str,
        dst_id: str,
    ) -> None: ...

    # ── read ──
    def query_regions(self) -> list[Region]: ...
    def query_aircraft(self) -> list[Aircraft]: ...
    def aircraft_map(self) -> dict[str, Aircraft]: ...
    def query_observations_for(self, icao24: str) -> list[Observation]: ...
    def query_latest_observations(self) -> list[Observation]: ...
    def query_all_observations(
        self, limit: Optional[int] = None
    ) -> list[Observation]: ...
    def get_observation(self, obs_id: str) -> Optional[Observation]: ...
    def query_tracks(self) -> list[Track]: ...
    def query_anomalies(self) -> list[Anomaly]: ...
    def get_anomaly(self, anomaly_id: str) -> Optional[Anomaly]: ...
    def set_anomaly_status(self, anomaly_id: str, status: str) -> Anomaly: ...
    def query_evidence_ids(self, anomaly_id: str) -> list[str]: ...
    def query_involves_ids(self, anomaly_id: str) -> list[str]: ...
    def query_evidence(self, anomaly_id: str) -> list[dict]: ...
    def query_involves(self, anomaly_id: str) -> list[dict]: ...
    def query_correlations(self, anomaly_id: str) -> list[dict]: ...
    def query_all_correlations(self) -> list[dict]: ...

    # ── read (P3 융합 객체) ──
    def query_satellites(self) -> list[Satellite]: ...
    def satellite_map(self) -> dict[str, Satellite]: ...
    def query_orbitpasses(self) -> list[OrbitPass]: ...
    def query_weather_latest(self) -> list[WeatherState]: ...
    def query_news(self) -> list[NewsEvent]: ...
    def query_operators(self) -> list[Operator]: ...
    def query_mentions(self, news_id: str) -> list[dict]: ...

    # ── read (P4 산출 인텔) ──
    def query_assessments(self) -> list[SituationAssessment]: ...
    def get_assessment(self, assessment_id: str) -> Optional[SituationAssessment]: ...
    def query_assessment_links(self, assessment_id: str) -> list[dict]: ...

    def counts(self) -> dict[str, int]: ...
