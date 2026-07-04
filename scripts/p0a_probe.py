#!/usr/bin/env python3
"""
P0-A: 공개소스 4종 생존 검증 프로브
재실행 가능 — 각 소스별 함수 분리, 실제 응답 스키마 출력.
"""

import json
import datetime
import math
from pprint import pformat

import httpx


# ──────────────────────────────────────────────
# 공통
# ──────────────────────────────────────────────
KADIZ_BBOX = {"lamin": 32, "lomin": 122, "lamax": 39, "lomax": 132}
TIMEOUT = 20  # 초


def _print_section(title: str) -> None:
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


# ──────────────────────────────────────────────
# 1. OpenSky
# ──────────────────────────────────────────────
OPENSKY_FIELD_NAMES = [
    "icao24",  # 0
    "callsign",  # 1
    "origin_country",  # 2
    "time_position",  # 3
    "last_contact",  # 4
    "longitude",  # 5
    "latitude",  # 6
    "baro_altitude",  # 7
    "on_ground",  # 8
    "velocity",  # 9
    "true_track",  # 10 (진북 기준 방향 °)
    "vertical_rate",  # 11
    "sensors",  # 12
    "geo_altitude",  # 13
    "squawk",  # 14
    "spi",  # 15
    "position_source",  # 16
    "category",  # 17 (optional)
]


def probe_opensky() -> dict:
    _print_section("1. OpenSky Network (KADIZ bbox, 익명)")
    url = "https://opensky-network.org/api/states/all"
    params = KADIZ_BBOX
    result = {"source": "opensky", "url": url, "params": params}

    try:
        resp = httpx.get(url, params=params, timeout=TIMEOUT)
        result["status_code"] = resp.status_code
        result["headers"] = dict(resp.headers)

        # rate-limit 관련 헤더 추출
        rl_headers = {
            k: v
            for k, v in resp.headers.items()
            if any(
                tok in k.lower() for tok in ["rate", "limit", "retry", "x-ratelimit"]
            )
        }
        result["rate_limit_headers"] = rl_headers
        print(f"상태: {resp.status_code}")
        print(f"rate-limit 헤더: {rl_headers or '(없음)'}")

        if resp.status_code == 200:
            data = resp.json()
            states = data.get("states") or []
            result["aircraft_count"] = len(states)
            result["response_time_utc"] = data.get("time")
            print(f"수신 항적 수: {len(states)}")
            print(
                f"API 시각(Unix): {data.get('time')}  →  "
                f"{datetime.datetime.utcfromtimestamp(data['time']).isoformat() if data.get('time') else 'N/A'} UTC"
            )

            # 스키마: 필드 인덱스 의미
            print("\n[스키마] states 배열 항목 인덱스 의미:")
            for i, name in enumerate(OPENSKY_FIELD_NAMES):
                print(f"  [{i:2d}] {name}")

            # 샘플 2~3건 출력
            samples = []
            for s in states[:3]:
                rec = {
                    OPENSKY_FIELD_NAMES[i]: s[i]
                    for i in range(min(len(s), len(OPENSKY_FIELD_NAMES)))
                }
                samples.append(rec)
            result["sample"] = samples
            print(f"\n[샘플 최대 3건]")
            for i, s in enumerate(samples):
                print(
                    f"  [{i}] icao24={s.get('icao24')} callsign={repr(s.get('callsign'))} "
                    f"lon={s.get('longitude')} lat={s.get('latitude')} alt={s.get('baro_altitude')} "
                    f"squawk={s.get('squawk')} country={s.get('origin_country')}"
                )

            result["status"] = "OK"
        elif resp.status_code == 429:
            print("→ 레이트리밋 초과 (429)")
            result["status"] = "RATE_LIMITED"
        else:
            print(f"→ 응답 비정상: {resp.text[:300]}")
            result["status"] = "FAIL"

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)
        print(f"오류: {e}")

    return result


# ──────────────────────────────────────────────
# 2. Celestrak TLE + sgp4
# ──────────────────────────────────────────────
def _parse_tle_lines(text: str) -> list[tuple[str, str, str]]:
    """TLE 텍스트 → (name, line1, line2) 리스트."""
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    result = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("1 ") or lines[i].startswith("2 "):
            i += 1
            continue
        if (
            i + 2 < len(lines)
            and lines[i + 1].startswith("1 ")
            and lines[i + 2].startswith("2 ")
        ):
            result.append((lines[i], lines[i + 1], lines[i + 2]))
            i += 3
        else:
            i += 1
    return result


def _eci_to_latlon(
    x: float, y: float, z: float, t: datetime.datetime
) -> tuple[float, float, float]:
    """
    ECI (km) → 위도(°)·경도(°)·고도(km).
    단순 구형 지구 근사 (sgp4가 이미 정밀 계산함 — 여기서는 데모 확인용).
    """
    r = math.sqrt(x**2 + y**2 + z**2)
    lat = math.degrees(math.asin(z / r))
    # GMST 근사
    jd = (
        t - datetime.datetime(2000, 1, 1, 12, tzinfo=datetime.timezone.utc)
    ).total_seconds() / 86400.0 + 2451545.0
    gmst = (280.46061837 + 360.98564736629 * (jd - 2451545.0)) % 360.0
    lon = math.degrees(math.atan2(y, x)) - gmst
    lon = (lon + 360) % 360
    if lon > 180:
        lon -= 360
    alt = r - 6371.0
    return lat, lon, alt


def probe_celestrak_sgp4() -> dict:
    _print_section("2. Celestrak TLE + sgp4 (stations group)")
    url = "https://celestrak.org/NORAD/elements/gp.php"
    params = {"GROUP": "stations", "FORMAT": "tle"}
    result = {"source": "celestrak_sgp4", "url": url, "params": params}

    try:
        resp = httpx.get(url, params=params, timeout=TIMEOUT)
        result["status_code"] = resp.status_code
        print(f"상태: {resp.status_code}  크기: {len(resp.text)} bytes")

        if resp.status_code == 200:
            tles = _parse_tle_lines(resp.text)
            result["tle_count"] = len(tles)
            print(f"파싱된 TLE 수: {len(tles)}")

            if tles:
                name, l1, l2 = tles[0]
                print(f"\n첫 위성: {name}")
                print(f"  Line1: {l1}")
                print(f"  Line2: {l2}")

                # sgp4로 현재 위치 계산
                from sgp4.api import Satrec, jday

                sat = Satrec.twoline2rv(l1, l2)
                now = datetime.datetime.now(datetime.timezone.utc)
                jd_val, fr = jday(
                    now.year,
                    now.month,
                    now.day,
                    now.hour,
                    now.minute,
                    now.second + now.microsecond / 1e6,
                )
                e, r, v = sat.sgp4(jd_val, fr)
                if e == 0:
                    lat, lon, alt_km = _eci_to_latlon(r[0], r[1], r[2], now)
                    result["sgp4_sample"] = {
                        "satellite": name,
                        "computed_at_utc": now.isoformat(),
                        "eci_km": {
                            "x": round(r[0], 2),
                            "y": round(r[1], 2),
                            "z": round(r[2], 2),
                        },
                        "lat": round(lat, 4),
                        "lon": round(lon, 4),
                        "alt_km": round(alt_km, 1),
                    }
                    print(f"\n[sgp4 계산 결과]")
                    print(f"  위성: {name}")
                    print(f"  시각: {now.isoformat()}")
                    print(f"  ECI (km): x={r[0]:.1f} y={r[1]:.1f} z={r[2]:.1f}")
                    print(
                        f"  위도: {lat:.4f}°  경도: {lon:.4f}°  고도: {alt_km:.1f} km"
                    )

                    # KADIZ bbox 통과 판정
                    in_kadiz = (
                        KADIZ_BBOX["lamin"] <= lat <= KADIZ_BBOX["lamax"]
                        and KADIZ_BBOX["lomin"] <= lon <= KADIZ_BBOX["lomax"]
                    )
                    result["sgp4_sample"]["in_kadiz_bbox"] = in_kadiz
                    print(f"  KADIZ bbox 내: {in_kadiz}")
                    print("  → 통과 판정 로직 동작 확인 완료 (bool)")
                else:
                    result["sgp4_error_code"] = e
                    print(f"sgp4 오류 코드: {e} (위성 수명 만료 등)")

                # 나머지 위성 목록 (이름만)
                result["all_satellite_names"] = [t[0] for t in tles]
                print(
                    f"\n전체 위성 목록 ({len(tles)}개): {[t[0] for t in tles[:8]]} ..."
                )

            result["status"] = "OK"
        else:
            print(f"→ 응답 비정상: {resp.text[:200]}")
            result["status"] = "FAIL"

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)
        print(f"오류: {e}")

    return result


# ──────────────────────────────────────────────
# 3. METAR (RKSI = 인천국제공항)
# ──────────────────────────────────────────────
def probe_metar() -> dict:
    _print_section("3. METAR — RKSI (인천국제공항)")
    url = "https://aviationweather.gov/api/data/metar"
    params = {"ids": "RKSI", "format": "json"}
    result = {"source": "metar", "url": url, "params": params}

    try:
        resp = httpx.get(url, params=params, timeout=TIMEOUT)
        result["status_code"] = resp.status_code
        print(f"상태: {resp.status_code}")

        if resp.status_code == 200:
            data = resp.json()
            result["raw_response"] = data
            print(f"레코드 수: {len(data)}")

            if data:
                rec = data[0]
                result["schema_fields"] = list(rec.keys())
                result["sample"] = rec

                print(f"\n[응답 스키마 필드]")
                for k in rec.keys():
                    print(f"  {k}: {repr(rec[k])}")

            result["status"] = "OK"
        else:
            print(f"→ 응답 비정상: {resp.text[:300]}")
            result["status"] = "FAIL"

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)
        print(f"오류: {e}")

    return result


# ──────────────────────────────────────────────
# 4. GDELT
# ──────────────────────────────────────────────
def probe_gdelt() -> dict:
    _print_section("4. GDELT (KADIZ / 한반도 공역 키워드)")
    # 쿼리: KADIZ 또는 Korea airspace 관련, 최근 3일 내
    query = '("KADIZ" OR "Korea Air Defense Identification Zone" OR "한국방공식별구역")'
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    params = {
        "query": query,
        "mode": "artlist",
        "maxrecords": "5",
        "format": "json",
        "timespan": "3d",  # 최근 3일
    }
    result = {"source": "gdelt", "url": url, "params": params}

    try:
        resp = httpx.get(url, params=params, timeout=TIMEOUT)
        result["status_code"] = resp.status_code
        print(f"상태: {resp.status_code}")
        print(f"쿼리: {query}")
        print(f"timespan: 3d")

        if resp.status_code == 200:
            text = resp.text
            # GDELT는 빈 결과 시 빈 JSON 또는 {"articles":null} 반환
            try:
                data = resp.json()
            except Exception:
                print(f"JSON 파싱 실패. 원문(200자): {text[:200]}")
                result["status"] = "PARTIAL"
                result["raw_text"] = text[:500]
                return result

            articles = data.get("articles") or []
            result["article_count"] = len(articles)
            result["raw_keys"] = list(data.keys())
            print(f"최상위 키: {list(data.keys())}")
            print(f"기사 수: {len(articles)}")

            if articles:
                # 스키마 필드
                result["article_schema"] = list(articles[0].keys())
                print(f"\n[기사 필드 스키마]: {list(articles[0].keys())}")

                # 샘플 2건
                samples = []
                for a in articles[:2]:
                    sample = {
                        "url": a.get("url", ""),
                        "title": a.get("title", ""),
                        "seendate": a.get("seendate", ""),
                        "sourcecountry": a.get("sourcecountry", ""),
                        "language": a.get("language", ""),
                        "domain": a.get("domain", ""),
                    }
                    samples.append(sample)
                    print(f"  title: {sample['title'][:80]}")
                    print(f"  url:   {sample['url'][:80]}")
                    print(
                        f"  date:  {sample['seendate']}  country: {sample['sourcecountry']}"
                    )
                    print()
                result["sample"] = samples
                result["status"] = "OK"
            else:
                print("→ 기사 0건 (쿼리 결과 없음 — timespan 또는 키워드 조정 필요)")
                # 폴백: 더 넓은 쿼리 시도
                print("→ 폴백 쿼리 시도: Korea airspace military")
                params2 = {
                    "query": "Korea airspace military",
                    "mode": "artlist",
                    "maxrecords": "5",
                    "format": "json",
                    "timespan": "3d",
                }
                resp2 = httpx.get(url, params=params2, timeout=TIMEOUT)
                result["fallback_status_code"] = resp2.status_code
                if resp2.status_code == 200:
                    try:
                        data2 = resp2.json()
                        arts2 = data2.get("articles") or []
                        result["fallback_article_count"] = len(arts2)
                        print(f"  폴백 기사 수: {len(arts2)}")
                        if arts2:
                            result["fallback_schema"] = list(arts2[0].keys())
                            result["fallback_sample"] = [
                                {
                                    "title": a.get("title", "")[:80],
                                    "url": a.get("url", "")[:80],
                                }
                                for a in arts2[:2]
                            ]
                            for a in arts2[:2]:
                                print(f"  title: {a.get('title', '')[:80]}")
                            result["status"] = "OK_FALLBACK"
                        else:
                            result["status"] = "PARTIAL"
                    except Exception as e2:
                        result["fallback_error"] = str(e2)
                        result["status"] = "PARTIAL"
                else:
                    result["status"] = "PARTIAL"

        else:
            print(f"→ 응답 비정상: {resp.text[:300]}")
            result["status"] = "FAIL"

    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = str(e)
        print(f"오류: {e}")

    return result


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def main() -> None:
    print(f"\nP0-A 공개소스 생존 검증  {datetime.datetime.utcnow().isoformat()} UTC")

    results = {}
    results["opensky"] = probe_opensky()
    results["celestrak_sgp4"] = probe_celestrak_sgp4()
    results["metar"] = probe_metar()
    results["gdelt"] = probe_gdelt()

    # 요약
    _print_section("요약")
    for k, v in results.items():
        status = v.get("status", "?")
        print(f"  {k:20s}: {status}")

    # JSON 저장 (worklog용)
    out_path = "/Users/ma/SKAI/docs/worklog/p0a_probe_result.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n→ 전체 결과 저장: {out_path}")


if __name__ == "__main__":
    main()
