"""anomaly/mil_enrich.py — 라이브 군용 식별 보강 인터페이스 (DR-0013 결정 5).

CLAUDE.md 정체성: **OSINT 위험징후 탐지.** 군용기 서사는 이중 경로다 —
  - ADS-B **ON**  : 콜사인 휴리스틱(`military_db`) + **공개 커뮤니티 DB 플래그**(이 모듈)로
                    저신뢰 식별. "휴리스틱"보다 한 단계 상향된 근거지만 여전히 저신뢰.
  - ADS-B **OFF** : 애초에 안 보임 → 기체가 아니라 **부재(dropout)**를 탐지(`crosscheck`).

이 모듈은 그중 ON 경로를 보강한다. store 어댑터·explainer·crosscheck처럼 교체 가능한
인터페이스만 두고, 라이브 구현(adsb.fi `/v2/mil` dbFlags)은 `connectors/mil_enrich_live.py`.
rules.py(anomaly 레이어)가 connectors를 import하지 않도록 인터페이스·Null만 여기(anomaly)에 둔다
— `anomaly/crosscheck.py` ↔ `connectors/crosscheck_live.py` 와 동일한 레이어 분리.

lookup(icao24) 반환 의미:
  - (True, reason) = 공개 커뮤니티 ADS-B DB가 이 hex를 **군용으로 플래그**(dbFlags & 1).
                     reason은 provenance 문구(출처 명시) — explainer가 그대로 서술한다.
  - None           = DB에 없음 / 미확인(스냅샷 실패·미갱신). **군용 아님을 단정하지 않는다**
                     — 부재는 민간 증거가 아니다(트랜스폰더 OFF이거나 DB 미수록일 수 있음).
                     이 경우 콜사인·대역 휴리스틱(`military_db`) 경로가 그대로 판정한다.

기본 백엔드는 NullMilEnrichment(항상 None) — 라이브 보강 없이도 휴리스틱 판정은 낸다.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class MilEnrichmentSource(Protocol):
    """군용 식별 보강 소스. Aircraft.is_military·콜사인 휴리스틱과 병렬로 쓰이는 저신뢰 신호."""

    def lookup(self, icao24: str) -> Optional[tuple[bool, str]]:
        """hex가 공개 DB에 군용으로 플래그돼 있으면 (True, 근거문구), 아니면/미확인이면 None."""
        ...


class NullMilEnrichment:
    """기본 백엔드 — 라이브 보강 미연결. 항상 None(신호 없음).

    라이브에서 보강 소스를 켜지 않았을 때 사용. 군용 판정은 `military_db`의 콜사인·대역
    휴리스틱만으로 이뤄진다(저신뢰). 게이트 off 시 동작이 종전과 완전히 동일하게 유지된다.
    """

    def lookup(self, icao24: str) -> Optional[tuple[bool, str]]:
        return None
