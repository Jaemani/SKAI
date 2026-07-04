"""FoundryOntologyStore — 스텁 (크리덴셜 도착 시 구현).

DR-0003: 저장 어댑터 분리의 핵심. 이 클래스는 OntologyStore 인터페이스를 구현하되
현재는 모든 메서드가 NotImplementedError를 던진다. Foundry Developer Tier
크리덴셜이 도착하면 아래 TODO를 채워 store_local과 교체한다(커넥터·API 무변경).

## 왜 지금 비어있나 (docs/worklog/P0B-foundry.md 요약)
- Foundry 온톨로지 스키마(Object/Link/Action Type) 생성은 **Ontology Manager UI 전용** —
  생성 API가 존재하지 않는다(P0B §2-2). 즉 Aircraft Object Type / observed_as Link /
  CreateAnomaly Action은 사용자가 브라우저에서 직접 만들어야 한다.
- OSDK는 그 위에서 발행(Developer Console UI) → pip 설치 → 타입드 read/write.
- 로컬엔 FOUNDRY_*/PALANTIR_*/OSDK_* 크리덴셜·SDK·CLI가 하나도 없음(P0B §1).

## 크리덴셜 도착 시 구현 순서 (TODO)
1. 사용자가 Ontology Manager UI에서 ontology.md §1~§3의 Object/Link/Action Type 생성.
2. Developer Console에서 OSDK(Python) 발행 → **.venv312**(Python 3.12)에 설치.
   (OSDK 요구사항: Python >=3.9, <3.13 — 그래서 .venv312 예약. P0B §2-4)
       pip install <PACKAGE> --extra-index-url "https://:$FOUNDRY_TOKEN@<INDEX-URL>"
3. 인증 (토큰은 환경변수 FOUNDRY_TOKEN, 값은 앱 Overview 페이지):
       from foundry_sdk import UserTokenAuth
       auth = UserTokenAuth(hostname=os.environ["FOUNDRY_HOST"],
                            token=os.environ["FOUNDRY_TOKEN"])
       client = FoundryClient(auth=auth, hostname=os.environ["FOUNDRY_HOST"])
4. 아래 각 메서드를 OSDK 객체/액션 호출로 구현:
   - write_observation → CreateObservation Action (또는 Object write)
   - link → Link Type 인스턴스 생성 (observed_as / composed_of)
   - query_* → client.ontology.objects.<Type>.iterate() / where(...)
   - provenance 강제는 Foundry에서도 Action 레벨(evidence 필수)로 이중 방어.
"""

from __future__ import annotations

from typing import Optional, Sequence

from ontology.model import (
    Aircraft,
    Anomaly,
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

_BLOCKED_MSG = (
    "FoundryOntologyStore는 아직 미구현(BLOCKED). "
    "크리덴셜 도착 전까지 store_local.LocalOntologyStore 사용. "
    "구현 순서는 이 파일 상단 TODO · docs/worklog/P0B-foundry.md 참조."
)


class FoundryOntologyStore:
    """OntologyStore 인터페이스 스텁. 모든 메서드가 NotImplementedError."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(_BLOCKED_MSG)

    def write_region(self, region: Region) -> None:
        raise NotImplementedError(_BLOCKED_MSG)

    def write_aircraft(self, aircraft: Aircraft) -> None:
        raise NotImplementedError(_BLOCKED_MSG)

    def write_observation(self, obs: Observation) -> None:
        raise NotImplementedError(_BLOCKED_MSG)

    def write_track(self, track: Track) -> None:
        raise NotImplementedError(_BLOCKED_MSG)

    def write_anomaly(
        self, anomaly: Anomaly, evidence: Sequence[str], involves: Sequence[str] = ()
    ) -> None:
        # Foundry 이관 시: CreateAnomaly Action(evidence 링크 필수)으로 구현.
        raise NotImplementedError(_BLOCKED_MSG)

    def write_satellite(self, satellite: Satellite) -> None:
        raise NotImplementedError(_BLOCKED_MSG)

    def write_orbitpass(self, orbit_pass: OrbitPass) -> None:
        # Foundry 이관 시: OrbitPass Object + of/over Link Type 인스턴스로 구현.
        raise NotImplementedError(_BLOCKED_MSG)

    def write_weatherstate(self, weather: WeatherState) -> None:
        raise NotImplementedError(_BLOCKED_MSG)

    def write_newsevent(self, news: NewsEvent, mentions: Sequence[tuple] = ()) -> None:
        # Foundry 이관 시: NewsEvent Object + mentions Link Type 인스턴스로 구현.
        raise NotImplementedError(_BLOCKED_MSG)

    def write_operator(self, operator: Operator) -> None:
        raise NotImplementedError(_BLOCKED_MSG)

    def write_assessment(self, assessment: SituationAssessment) -> None:
        # Foundry 이관 시: GenerateSituationAssessment Action(문장별 cites 링크 필수)으로 구현.
        raise NotImplementedError(_BLOCKED_MSG)

    def delete_future_orbitpasses_for(self, satellite_ref: str, now_ts: int) -> int:
        raise NotImplementedError(_BLOCKED_MSG)

    def link(
        self, src_type: str, src_id: str, link_type: str, dst_type: str, dst_id: str
    ) -> None:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_regions(self) -> list[Region]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_aircraft(self) -> list[Aircraft]:
        raise NotImplementedError(_BLOCKED_MSG)

    def aircraft_map(self) -> dict[str, Aircraft]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_observations_for(self, icao24: str) -> list[Observation]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_latest_observations(self) -> list[Observation]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_all_observations(self, limit: Optional[int] = None) -> list[Observation]:
        raise NotImplementedError(_BLOCKED_MSG)

    def get_observation(self, obs_id: str) -> Optional[Observation]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_tracks(self) -> list[Track]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_anomalies(self) -> list[Anomaly]:
        raise NotImplementedError(_BLOCKED_MSG)

    def get_anomaly(self, anomaly_id: str) -> Optional[Anomaly]:
        raise NotImplementedError(_BLOCKED_MSG)

    def set_anomaly_status(self, anomaly_id: str, status: str) -> Anomaly:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_evidence_ids(self, anomaly_id: str) -> list[str]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_involves_ids(self, anomaly_id: str) -> list[str]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_evidence(self, anomaly_id: str) -> list[dict]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_involves(self, anomaly_id: str) -> list[dict]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_correlations(self, anomaly_id: str) -> list[dict]:
        # Foundry 이관 시: Anomaly —correlated_with→ Link Type 인스턴스 질의로 구현.
        raise NotImplementedError(_BLOCKED_MSG)

    def query_all_correlations(self) -> list[dict]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_satellites(self) -> list[Satellite]:
        raise NotImplementedError(_BLOCKED_MSG)

    def satellite_map(self) -> dict[str, Satellite]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_orbitpasses(self) -> list[OrbitPass]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_weather_latest(self) -> list[WeatherState]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_news(self) -> list[NewsEvent]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_operators(self) -> list[Operator]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_mentions(self, news_id: str) -> list[dict]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_assessments(self) -> list[SituationAssessment]:
        raise NotImplementedError(_BLOCKED_MSG)

    def get_assessment(self, assessment_id: str) -> Optional[SituationAssessment]:
        raise NotImplementedError(_BLOCKED_MSG)

    def query_assessment_links(self, assessment_id: str) -> list[dict]:
        raise NotImplementedError(_BLOCKED_MSG)

    def counts(self) -> dict[str, int]:
        raise NotImplementedError(_BLOCKED_MSG)
