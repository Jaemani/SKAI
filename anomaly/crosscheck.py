"""anomaly/crosscheck.py — ADS-B dropout 교차소스 판정 인터페이스 (DR-0007 결정 1).

CLAUDE.md 기술기준: **dropout 단정은 복수 소스로.** 단일 소스의 결측은 송신기 문제일
수 있으므로 단정하지 않는다(신뢰도만 낮춤). 이 모듈은 "2차 소스가 같은 구간에서 그 기체를
관측했는가?"를 묻는 인터페이스를 두고, 답에 따라 dropout 신뢰도를 조정한다.

confirm_absence(icao24, window) 의 반환 의미:
  - None  = 미확인(2차 소스 없음/모름) → **저신뢰 후보**(단정 금지 문구, confidence 0.4대).
  - True  = 2차 소스도 해당 기체를 **관측 못 함** = 부재 교차 확인 → confidence 0.7대로 상향.
  - False = 2차 소스는 여전히 관측 중 = 우리 쪽 결측은 센서 아티팩트 → **dropout 아님**(생성 안 함).

라이브 2차 소스(adsb.fi 등)는 ToS 검토 별도(DR-0007) — 여기선 인터페이스만. 데모 기본은
SyntheticMirrorSource(주입 시나리오가 미러 데이터를 제공). 기본값 NullCrossCheckSource는
항상 None(미확인) → 라이브에서 2차 소스 없이도 저신뢰 후보는 낼 수 있다.
"""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable


@runtime_checkable
class CrossCheckSource(Protocol):
    """dropout 교차 판정 소스. store 어댑터·explainer처럼 교체 가능."""

    def confirm_absence(self, icao24: str, window: tuple[int, int]) -> Optional[bool]:
        """window(초 구간) 동안 icao24 기체의 부재를 2차 소스로 확인.

        True=부재 확인 / False=여전히 관측(dropout 아님) / None=미확인.
        """
        ...


class NullCrossCheckSource:
    """기본 백엔드 — 2차 소스 미연결. 항상 None(미확인).

    라이브에서 라이선스된 2차 소스가 없을 때 사용. dropout은 저신뢰 후보로만 생성되고
    설명문에 '단정하지 않음'이 명시된다(단일 결측 단정 금지).
    """

    def confirm_absence(self, icao24: str, window: tuple[int, int]) -> Optional[bool]:
        return None


class SyntheticMirrorSource:
    """데모 백엔드 — 주입 시나리오가 제공하는 미러 데이터로 교차 판정.

    absent = 미러에서도 관측 안 된 기체(부재 교차 확인 → True).
    present = 미러에선 여전히 관측된 기체(dropout 아님 → False).
    둘 다 아니면 None(미확인). 주입기가 시나리오별로 이 집합을 채운다.
    """

    def __init__(
        self,
        absent: Optional[set[str]] = None,
        present: Optional[set[str]] = None,
    ):
        self.absent = set(absent or ())
        self.present = set(present or ())

    def confirm_absence(self, icao24: str, window: tuple[int, int]) -> Optional[bool]:
        if icao24 in self.absent:
            return True
        if icao24 in self.present:
            return False
        return None
