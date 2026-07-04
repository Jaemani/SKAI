"""공용 엔티티 링킹 — 뉴스/OSINT 텍스트 ↔ 온톨로지 실체(NewsEvent —mentions→).

gdelt·rss·stealthmole 세 커넥터가 공유하는 링킹 SSOT. 기존에 gdelt 내부에 흩어져 있던
지역 별칭 사전 + 콜사인 substring 매칭을 여기로 승격하고, 오퍼레이터 사전과 icao24 hex
패턴을 추가한다.

# 정직한 범위 (EVALUATION.md §3 "엔티티 해소=얕음" 판정에 대한 응답)
이 모듈은 **진짜 NER(문맥이해 개체명 인식)이 아니다.** 다음 셋의 조합이다:
  1. **지역 별칭 사전**(REGION_ALIASES) — 키워드 substring 매칭.
  2. **콜사인/icao24 패턴**(정규식) — 텍스트에서 후보 토큰 추출.
  3. **오퍼레이터 명칭 사전**(OPERATOR_ALIASES) — 항공사·공군 별칭 매칭.
그리고 (2)는 **실존 대조**(store에 실재하는 Aircraft만)를 통과해야 링크된다 — 무존재 링크 금지.
즉 EVALUATION의 "키워드 매칭뿐"에서 "사전+패턴+실존대조"로 한 단계 올라가지만,
문맥이해 NER이라고 주장하지 않는다. 링킹 방식별 신뢰 라벨
(콜사인 exact > icao24 hex > 오퍼레이터 사전 > 지역 키워드)을 NewsEvent.attrs['linking']에
기록해 provenance(왜 이 링크가 생겼나)를 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ontology.model import NEWS_MAX_CONFIDENCE, NewsEvent, Operator

# ──────────────────────────────────────────────────────────────────────────────
# 1. 지역 별칭 사전 (구 gdelt.REGION_ALIASES 승격 — SSOT).
#    region_id → 별칭 리스트. 현재 데모 관심지역은 KADIZ 1곳(좌표 교체는 model.KADIZ_BBOX).
# ──────────────────────────────────────────────────────────────────────────────
REGION_ALIASES: dict[str, list[str]] = {
    "KADIZ": [
        "KADIZ",
        "Korea Air Defense Identification Zone",
        "한국방공식별구역",
        "Korean air defense",
        "South Korea air defense",
        "Korea airspace",
        # 쿼리가 한반도 공역으로 스코프됨 → 아래 일반 ADIZ 표현도 KADIZ로 링킹(실측 회수).
        "air defense zone",
        "defense identification zone",
        "한반도",
        "Korean Peninsula",
        "서해",
        "West Sea",
        "Yellow Sea",
        "동중국해",
        "East China Sea",
        "대한해협",
        "Korea Strait",
    ],
}


def match_regions(text: str) -> list[tuple[str, str]]:
    """텍스트 → [(매칭 별칭, region_id), ...]. 등록된 모든 지역 별칭 대조(대소문자 무시)."""
    if not text:
        return []
    low = text.lower()
    out: list[tuple[str, str]] = []
    for region_id, aliases in REGION_ALIASES.items():
        for alias in aliases:
            if alias.lower() in low:
                out.append((alias, region_id))
    return out


def match_region_aliases(text: str) -> list[str]:
    """하위호환(구 gdelt.match_region_aliases): KADIZ 별칭만 반환(기존 시그니처 유지).

    gdelt.gdelt_response_to_news가 entities 세팅에 쓰며 test_p3가 직접 검증하므로
    반환 형태(별칭 문자열 리스트)를 그대로 보존한다.
    """
    if not text:
        return []
    low = text.lower()
    return [a for a in REGION_ALIASES["KADIZ"] if a.lower() in low]


# ──────────────────────────────────────────────────────────────────────────────
# 2. 오퍼레이터(항공사·공군) 사전 + 시드 객체.
#    mentions→Operator 링크가 실재 Operator 객체를 가리키도록 시드를 store에 적재한다
#    (ensure_operator_seeds). 이로써 그동안 스키마만 있던 Operator 객체 타입이 내용을 갖는다.
# ──────────────────────────────────────────────────────────────────────────────
OPERATOR_SEEDS: list[Operator] = [
    Operator(id="op-kal", name="대한항공 (Korean Air)", kind="airline", country="KR"),
    Operator(
        id="op-aar", name="아시아나항공 (Asiana Airlines)", kind="airline", country="KR"
    ),
    Operator(
        id="op-usaf", name="미 공군 (U.S. Air Force)", kind="airforce", country="US"
    ),
    Operator(
        id="op-plaaf",
        name="중국 인민해방군 공군 (PLAAF)",
        kind="airforce",
        country="CN",
    ),
    Operator(
        id="op-ruaf", name="러시아 항공우주군 (VKS)", kind="airforce", country="RU"
    ),
    Operator(
        id="op-rokaf", name="대한민국 공군 (ROKAF)", kind="airforce", country="KR"
    ),
    Operator(
        id="op-jasdf", name="일본 항공자위대 (JASDF)", kind="airforce", country="JP"
    ),
]

# operator_id → 별칭 리스트. ASCII 별칭은 단어경계(\b) 매칭, 비-ASCII(한글)는 substring.
OPERATOR_ALIASES: dict[str, list[str]] = {
    "op-kal": ["Korean Air", "Korean Air Lines", "대한항공", "KAL"],
    "op-aar": ["Asiana Airlines", "Asiana", "아시아나", "AAR"],
    "op-usaf": ["U.S. Air Force", "US Air Force", "United States Air Force", "USAF"],
    "op-plaaf": [
        "People's Liberation Army Air Force",
        "PLA Air Force",
        "PLAAF",
        "Chinese Air Force",
    ],
    "op-ruaf": ["Russian Aerospace Forces", "Russian Air Force", "러시아 공군", "VKS"],
    "op-rokaf": [
        "Republic of Korea Air Force",
        "South Korean Air Force",
        "대한민국 공군",
        "한국 공군",
        "ROKAF",
    ],
    "op-jasdf": [
        "Japan Air Self-Defense Force",
        "일본 항공자위대",
        "JASDF",
    ],
}


def _compile_operator_matchers() -> list[tuple[str, str, "re.Pattern | None"]]:
    """(operator_id, 별칭, 매처) 리스트. ASCII=\\b 정규식, 한글=None(substring 폴백).

    '\\b'는 한글 경계를 못 잡으므로 비-ASCII 별칭은 substring으로 처리한다.
    ASCII 단어경계는 'Korean Air'가 'Korean airline'에 오탐되지 않게 막는다.
    """
    out: list[tuple[str, str, re.Pattern | None]] = []
    for op_id, aliases in OPERATOR_ALIASES.items():
        for alias in aliases:
            if alias.isascii():
                pat = re.compile(r"\b" + re.escape(alias) + r"\b", re.IGNORECASE)
                out.append((op_id, alias, pat))
            else:
                out.append((op_id, alias, None))
    return out


_OPERATOR_MATCHERS = _compile_operator_matchers()


# ──────────────────────────────────────────────────────────────────────────────
# 3. 항공기 패턴(콜사인·icao24 hex) — 실존 대조 게이트.
# ──────────────────────────────────────────────────────────────────────────────
# 콜사인: 항공사/군 콜사인 관행(문자 2~3 + 숫자 2~4). 예: KAL092, RCH459, COBRA51은 미포함.
_CALLSIGN_RE = re.compile(r"\b[A-Z]{2,3}[0-9]{2,4}\b")
# icao24: 6자리 소문자 hex(OpenSky 표기). 6-hex 단어는 오탐 소지 → 반드시 실존 set 대조.
_ICAO24_RE = re.compile(r"\b[0-9a-f]{6}\b")


@dataclass(frozen=True)
class AircraftIndex:
    """실존 대조용 인덱스. callsign(대문자)→icao24 맵 + icao24(소문자) 집합."""

    callsign_to_icao: dict[str, str]
    icao24_set: set[str]


def build_aircraft_index(store) -> AircraftIndex:
    """store의 실존 Aircraft로 링킹 인덱스 구성(무존재 링크 방지의 근거)."""
    acs = store.query_aircraft()
    cs2icao: dict[str, str] = {}
    icaos: set[str] = set()
    for a in acs:
        icaos.add((a.icao24 or "").lower())
        cs = (a.callsign or "").strip().upper()
        if cs:
            cs2icao[cs] = a.icao24
    return AircraftIndex(callsign_to_icao=cs2icao, icao24_set=icaos)


# ──────────────────────────────────────────────────────────────────────────────
# 4. Mention (링킹 1건의 provenance 단위).
# ──────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Mention:
    """뉴스 텍스트 → 실체 링크 1건. method/label로 왜·얼마나 신뢰하는지 기록한다."""

    dst_type: str  # Region | Operator | Aircraft
    dst_id: str
    method: str  # callsign_exact | icao24_hex | operator_name | region_keyword
    matched: str  # 매칭된 토큰/별칭(감사용)
    label: str  # high | medium | low (방식별 신뢰 라벨)


# 링킹 방식 강도(같은 대상이 여러 방식으로 잡히면 강한 것만 링크로 남김).
_METHOD_STRENGTH = {
    "callsign_exact": 3,
    "icao24_hex": 2,
    "operator_name": 1,
    "region_keyword": 0,
}


def match_operators(text: str) -> list[Mention]:
    """오퍼레이터 명칭 사전 매칭 → Operator Mention 리스트(label=medium)."""
    if not text:
        return []
    low = text.lower()
    out: list[Mention] = []
    for op_id, alias, pat in _OPERATOR_MATCHERS:
        hit = pat.search(text) if pat is not None else (alias.lower() in low)
        if hit:
            out.append(Mention("Operator", op_id, "operator_name", alias, "medium"))
    return out


def match_aircraft(text: str, index: AircraftIndex) -> list[Mention]:
    """콜사인·icao24 패턴 추출 → **실존 Aircraft만** Aircraft Mention(label=high).

    무존재 링크 금지: 패턴이 잡혀도 index에 실재하지 않으면 링크하지 않는다.
    """
    if not text or index is None:
        return []
    out: list[Mention] = []
    upper = text.upper()
    for token in _CALLSIGN_RE.findall(upper):
        icao = index.callsign_to_icao.get(token)
        if icao:  # 실존 대조 통과
            out.append(Mention("Aircraft", icao, "callsign_exact", token, "high"))
    low = text.lower()
    for hexid in _ICAO24_RE.findall(low):
        if hexid in index.icao24_set:  # 실존 대조 통과
            out.append(Mention("Aircraft", hexid, "icao24_hex", hexid, "high"))
    return out


def _dedup_by_target(mentions: list[Mention]) -> list[Mention]:
    """(dst_type, dst_id)별로 가장 강한 method의 Mention 하나만 남긴다(링크 유일화)."""
    best: dict[tuple[str, str], Mention] = {}
    for m in mentions:
        key = (m.dst_type, m.dst_id)
        cur = best.get(key)
        if cur is None or _METHOD_STRENGTH[m.method] > _METHOD_STRENGTH[cur.method]:
            best[key] = m
    return list(best.values())


def link_newsevent(
    nv: NewsEvent,
    *,
    aircraft_index: AircraftIndex | None = None,
    base_confidence: float | None = None,
) -> list[tuple[str, str]]:
    """nv.title(+summary)에서 mentions 추출 → (dst_type, dst_id) 튜플 리스트 반환.

    nv를 in-place 보강한다:
      - entities: 매칭된 모든 토큰/별칭(감사·표시용).
      - attrs['linking']: 방식별 provenance 레코드(type/id/method/matched/label).
      - confidence: 방식별 소폭 상향(지역+0.05·오퍼레이터+0.05·항공기exact+0.10), ≤ 0.4 clamp.

    실존 대조: 항공기는 aircraft_index에 실재하는 것만 링크(무존재 링크 금지).
    base_confidence: 커넥터별 기준 신뢰도(gdelt/rss 0.30, stealthmole 0.25). None이면 nv 현재값.
    반환 튜플은 store.write_newsevent(nv, mentions=...)에 그대로 넘긴다.
    """
    text = f"{nv.title or ''} {nv.summary or ''}".strip()

    all_meta: list[Mention] = []
    for alias, region_id in match_regions(text):
        all_meta.append(Mention("Region", region_id, "region_keyword", alias, "low"))
    all_meta.extend(match_operators(text))
    if aircraft_index is not None:
        all_meta.extend(match_aircraft(text, aircraft_index))

    # entities: 모든 매칭 토큰(중복 제거·정렬) — 감사/표시용.
    nv.entities = sorted({m.matched for m in all_meta})

    # 링크: (type,id) 유일화(가장 강한 방식 보존).
    links = _dedup_by_target(all_meta)
    nv.attrs["linking"] = [
        {
            "type": m.dst_type,
            "id": m.dst_id,
            "method": m.method,
            "matched": m.matched,
            "label": m.label,
        }
        for m in links
    ]

    # confidence 상향(교차/사전 매칭일수록 조금 더 신뢰) — 상한 0.4 유지.
    base = base_confidence if base_confidence is not None else nv.confidence
    conf = base
    kinds = {m.dst_type for m in links}
    if "Region" in kinds:
        conf += 0.05
    if "Operator" in kinds:
        conf += 0.05
    if any(m.method in ("callsign_exact", "icao24_hex") for m in links):
        conf += 0.10
    nv.confidence = round(min(conf, NEWS_MAX_CONFIDENCE), 4)

    return [(m.dst_type, m.dst_id) for m in links]


def ensure_operator_seeds(store) -> int:
    """시드 Operator 객체를 store에 idempotent write(INSERT OR REPLACE). 반환: 시드 수.

    mentions→Operator 링크가 실재 객체를 가리키도록 커넥터 ingest 시작 시 1회 호출한다.
    """
    for op in OPERATOR_SEEDS:
        store.write_operator(op)
    return len(OPERATOR_SEEDS)
