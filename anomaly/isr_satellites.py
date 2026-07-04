"""anomaly/isr_satellites.py — ISR(정찰·이미징·SAR) 위성 허용목록.

## 왜 이 모듈이 필요한가 (배경)
celestrak.py가 기존에 `stations,visual` GROUP을 받아왔다 — 이는 ISS·밝은 위성(육안관측용)
이지 ISR(정찰/이미징) 위성이 아니다. 그럼에도 detect_satellite_proximity가 이 통과들을
전부 저신뢰 Anomaly로 승격 → ISS 통과(정상 사건)까지 반복 경고가 됐고, correlation의
시공간 버킷이 이 OrbitPass 홍수와 곱해져 한 이상징후에 correlated_with 수십 건이 붙어
상관 신호가 노이즈가 됐다. 이 모듈은 "신호로 승격할 가치가 있는 위성"을 **공개 출처로
문서화된 이미징/SAR/정찰 위성**으로 한정하는 허용목록이다.

## 허용목록 vs 표시(표시층 분리 결정 — DR 참조)
- **표시(지도 지상궤적 레이어) = 전체 카탈로그**를 유지한다. celestrak.py가 받아온 GROUP의
  모든 통과를 지도에 그린다(상황인식엔 주변 위성 전부가 맥락이다).
- **신호 승격(Anomaly)·상관(correlated_with) = 허용목록만**. 이 목록 밖 위성의 통과는
  지도엔 남지만 Anomaly로 승격되지 않고 correlated_with 엣지도 만들지 않는다.
- 합성 시나리오(source=="synthetic")는 이 게이트를 **우회**한다 — replay 데모에서
  scripts/scenarios.py가 주입하는 위성 통과(가상 NORAD 9000x)가 계속 발화해야 하기 때문.

## TLE 수급 결정 (근거)
허용목록의 모든 위성은 Celestrak **`resource`(Earth Resources) GROUP** 한 곳에 들어있다
(2026-07-05 GP 데이터로 확인 — 이 파일의 모든 NORAD ID는 그 응답에서 직접 추출했다).
따라서 CATNR 개별 질의(위성당 1회 = 40~60회 HTTP)를 쓰지 않고 **resource GROUP 1회
fetch**로 전량을 커버한다(레이트리밋 존중 · 12h 캐시 유지). celestrak.py의 기본 GROUP을
이 모듈의 CELESTRAK_ISR_GROUPS로 교체한다(SSOT). 허용목록 ⊆ resource GROUP 이 성립하므로
허용목록에 있는 위성은 실 TLE·실 통과가 실제로 계산된다.

## 한계(정직)
- **허용목록에 없는 실제 정찰위성은 못 잡는다**: 목록은 공개 문서화된 대표 플랫폼만 담는다.
- **기밀·미분류 위성은 공개 TLE가 없다**: 전용 SIGINT/ELINT(예 미 NOSS/Intruder)·기밀 광학
  정찰은 Celestrak resource에 없거나 궤도요소가 비공개라 제외된다.
- **ICEYE·Capella 등 SAR 스몰샛 군집은 resource GROUP에 없다**(대개 `active` 대량 목록에만
  존재) — 단일 경량 fetch 설계를 지키려고 제외했다. 필요 시 별도 GROUP 추가로 확장 가능.
- NORAD ID는 카탈로그 재사용이 없으므로 안정적이나, 위성 궤도이탈(재진입) 시 통과가 사라진다
  (그건 정상 — resource GROUP fetch가 자동 반영).

## 출처
- NORAD 카탈로그 번호·이름: Celestrak GP `resource` GROUP
  (https://celestrak.org/NORAD/elements/gp.php?GROUP=resource&FORMAT=tle), 2026-07-05 확인.
- 각 계열의 임무(EO/SAR/정찰) 성격은 공개적으로 널리 문서화됨(운영기관 발표·공개 위성 카탈로그).
  중국 Yaogan/Gaofen은 공개 보도상 정찰·원격탐사 용도로 알려져 있다(단정 아님 — 임무 상세는 비공개).
"""

from __future__ import annotations

import os

# TLE 수급 GROUP (SSOT). celestrak.py의 기본 GROUP이 이를 참조한다.
# 허용목록 전량이 이 단일 GROUP에 있어 1회 fetch로 커버된다(위 docstring 근거).
CELESTRAK_ISR_GROUPS: tuple[str, ...] = ("resource",)

# 위성 유형 라벨(주석·UI용): EO=전자광학 이미징, SAR=합성개구레이더, RECON=공개보도상 정찰.
# ── ISR 위성 허용목록 ──────────────────────────────────────────────────────────
# key = NORAD 카탈로그 번호(str). value = (이름, 유형, 국가/운영기관).
# 모든 NORAD ID는 Celestrak resource GROUP GP 데이터에서 직접 확인(2026-07-05). 추측 없음.
ISR_ALLOWLIST: dict[str, tuple[str, str, str]] = {
    # ── 한국 (KARI) — 다목적실용위성 아리랑 ──
    "29268": ("KOMPSAT-2 (아리랑2)", "EO", "KR/KARI"),
    "38338": ("KOMPSAT-3 (아리랑3)", "EO", "KR/KARI"),
    "40536": ("KOMPSAT-3A (아리랑3A)", "EO/IR", "KR/KARI"),
    "39227": ("KOMPSAT-5 (아리랑5)", "SAR", "KR/KARI"),
    # ── ESA/EU — Copernicus Sentinel ──
    "39634": ("Sentinel-1A", "SAR", "EU/ESA"),
    "40697": ("Sentinel-2A", "EO", "EU/ESA"),
    "42063": ("Sentinel-2B", "EO", "EU/ESA"),
    "60989": ("Sentinel-2C", "EO", "EU/ESA"),
    # ── Maxar (미국) — 고해상 상용 광학 ──
    "32060": ("WorldView-1", "EO", "US/Maxar"),
    "35946": ("WorldView-2", "EO", "US/Maxar"),
    "40115": ("WorldView-3", "EO", "US/Maxar"),
    "33331": ("GeoEye-1", "EO", "US/Maxar"),
    # ── Planet (미국) — SkySat 군집(대표 표본) ──
    "41601": ("SkySat-C1", "EO", "US/Planet"),
    "41773": ("SkySat-C2", "EO", "US/Planet"),
    "41774": ("SkySat-C3", "EO", "US/Planet"),
    "41771": ("SkySat-C4", "EO", "US/Planet"),
    "41772": ("SkySat-C5", "EO", "US/Planet"),
    "42992": ("SkySat-C6", "EO", "US/Planet"),
    "42987": ("SkySat-C11", "EO", "US/Planet"),
    # ── Airbus (프랑스/EU) — Pleiades·SPOT ──
    "38012": ("Pleiades 1A", "EO", "FR/Airbus"),
    "39019": ("Pleiades 1B", "EO", "FR/Airbus"),
    "48268": ("Pleiades Neo 3", "EO", "FR/Airbus"),
    "49070": ("Pleiades Neo 4", "EO", "FR/Airbus"),
    "38755": ("SPOT 6", "EO", "FR/Airbus"),
    "40053": ("SPOT 7", "EO", "FR/Airbus"),
    # ── 이탈리아 (ASI) — COSMO-SkyMed SAR ──
    "31598": ("COSMO-SkyMed 1", "SAR", "IT/ASI"),
    "32376": ("COSMO-SkyMed 2", "SAR", "IT/ASI"),
    "33412": ("COSMO-SkyMed 3", "SAR", "IT/ASI"),
    "37216": ("COSMO-SkyMed 4", "SAR", "IT/ASI"),
    # ── 캐나다 / 독일 — SAR ──
    "32382": ("RADARSAT-2", "SAR", "CA/MDA"),
    "31698": ("TerraSAR-X", "SAR", "DE/DLR"),
    # ── 중국 — Yaogan(요감·공개보도상 정찰) ──
    "32289": ("Yaogan-3", "RECON", "CN"),
    "33446": ("Yaogan-4", "RECON", "CN"),
    "36110": ("Yaogan-7", "RECON", "CN"),
    "36834": ("Yaogan-10", "RECON", "CN"),
    "40143": ("Yaogan-21", "RECON", "CN"),
    "40275": ("Yaogan-22", "RECON", "CN"),
    "40310": ("Yaogan-24", "RECON", "CN"),
    "40362": ("Yaogan-26", "RECON", "CN"),
    "40878": ("Yaogan-27", "RECON", "CN"),
    "41026": ("Yaogan-28", "RECON", "CN"),
    "41038": ("Yaogan-29", "RECON", "CN"),
    # ── 중국 — Gaofen(고분·CHEOS 고해상 관측) ──
    "39150": ("Gaofen-1", "EO", "CN"),
    "40118": ("Gaofen-2", "EO", "CN"),
    "41727": ("Gaofen-3", "SAR", "CN"),
    "41194": ("Gaofen-4", "EO(GEO)", "CN"),
    "40701": ("Gaofen-8", "EO", "CN"),
    "40894": ("Gaofen-9 01", "EO", "CN"),
}


def allowlist_enabled() -> bool:
    """허용목록 게이트 on 여부. 기본 on(기본값이 문제였으므로 게이트를 기본 켬).

    SKAI_SAT_ALLOWLIST 를 off/0/false/no 로 두면 게이트를 끈다(디버깅·기존 무게이트 동작
    복원용 탈출구). 미설정·그 외 값은 on.
    """
    v = os.environ.get("SKAI_SAT_ALLOWLIST", "").strip().lower()
    return v not in ("off", "0", "false", "no")


def is_isr_satellite(norad_id: str | None) -> bool:
    """norad_id가 ISR 허용목록에 있으면 True. 게이트가 꺼져 있으면 항상 True(무게이트).

    norad_id는 문자열 NORAD 카탈로그 번호(OrbitPass.satellite_ref). None/미상은 False.
    """
    if not allowlist_enabled():
        return True
    if norad_id is None:
        return False
    return norad_id in ISR_ALLOWLIST


def is_signal_promotable_pass(source: str | None, norad_id: str | None) -> bool:
    """이 위성 통과를 신호(Anomaly·correlated_with)로 승격해도 되는가?

    - 합성(source=="synthetic")은 우회 → replay 데모의 가상 통과가 계속 발화(수락 기준).
    - 그 외(실 celestrak 통과)는 허용목록에 든 위성만 승격.
    detect_satellite_proximity·correlation이 공용으로 쓰는 단일 판정(로직 SSOT).
    """
    if source == "synthetic":
        return True
    return is_isr_satellite(norad_id)
