"""FastAPI 서버 — 온톨로지 store read → JSON API + Leaflet 정적 페이지.

DR-0003 얇은 프론트: 빌드 없는 FastAPI(JSON + 정적 서빙) + vanilla Leaflet.
store 인터페이스에만 의존 → Foundry 교체 시 무변경.

엔드포인트:
  GET  /api/observations           항공기별 최신 관측(현재 공중 상황, 마커용)
  GET  /api/tracks                 트랙(경로 폴리라인, has_gap 플래그)
  GET  /api/regions                관심지역 폴리곤(KADIZ)
  GET  /api/anomalies              이상징후 + 근거(evidence) Observation + involves
  POST /api/anomalies/{id}/confirm status candidate→confirmed (사람 승인)
  POST /api/anomalies/{id}/dismiss status candidate→dismissed (사람 기각)
  GET  /api/orbitpasses            위성 통과창(지상궤적·window·max_elev, P3)
  GET  /api/weather                지역 기상(공항별 최신, P3)
  GET  /api/news                   뉴스(저신뢰) + mentions 링크 (P3)
  GET  /api/counts                 소스별 객체 카운트 (P3 산출 요구)
  GET  /api/stats                  객체 카운트(평면)
  GET  /                           web/index.html (지도)
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from anomaly.actions import confirm_anomaly, dismiss_anomaly
from copilot.assessment import (
    _resolve_object,
    assess,
    assessment_to_summary_dict,
    build_subgraph,
)
from ontology.model import Anomaly
from ontology.store_foundry import current_backend, make_store
from ontology.store_local import DEFAULT_DB, LocalOntologyStore
from server.live_status import read_status

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
DB_PATH = os.environ.get("SKAI_DB", DEFAULT_DB)

# LIVE 판정 여유 — 마지막 폴링이 이 시간(초) 안이면 LIVE로 본다(간격 3배 또는 90초 중 큰 값).
_LIVE_STALE_FLOOR = 90


def _live_view() -> dict:
    """폴러 사이드카를 읽어 프론트 LIVE 인디케이터용 상태를 구성.

    폴러가 없거나(replay·정적) last_poll_ts가 오래됐으면 live=False. 있으면 신선도로 판정.
    반환: {live, last_poll_ts, mode, interval, cycle, last_poll_status, server_now, counts?}.
    """
    st = read_status(DB_PATH)
    now = int(time.time())
    if not st:
        return {
            "live": False,
            "last_poll_ts": None,
            "mode": None,
            "server_now": now,
            "store_backend": current_backend(),
        }
    last = st.get("last_poll_ts")
    interval = int(st.get("interval") or 25)
    stale_limit = max(interval * 3, _LIVE_STALE_FLOOR)
    live = bool(
        st.get("mode") == "live"
        and last is not None
        and (now - int(last)) <= stale_limit
    )
    return {
        "live": live,
        "last_poll_ts": last,
        "mode": st.get("mode"),
        "interval": interval,
        "cycle": st.get("cycle"),
        "last_poll_status": st.get("last_poll_status"),
        "last_cycle": st.get("last_cycle"),
        # 다중소스 폴러(DR-0012 갭#3): 소스별 마지막 폴 시각·상태 → 프론트가 소스별 신선도 표시.
        # 단일소스(구 사이드카)면 None(하위호환) — 프론트는 없으면 전체 last_poll_ts로 폴백.
        "sources": st.get("sources"),
        "source_last_poll": st.get("source_last_poll"),
        "source_last_status": st.get("source_last_status"),
        "server_now": now,
        # read 소스(로컬 SQLite냐 Palantir Foundry냐) — 프론트 "지금 어디서 read 중" 배지용.
        "store_backend": current_backend(),
    }


def _now_anchor() -> int | None:
    """SKAI_NOW_ANCHOR(스냅샷 시각) 파싱 — replay 모드 now 앵커링(P4 §8-5).

    설정 시 assess의 '지금'/시간창 해석 기준을 스냅샷 시각에 고정한다 → 질의가 언제
    실행돼도 같은 결과(재현성). 미설정(라이브)이면 None → 벽시계 now(정직한 '지금').
    """
    raw = os.environ.get("SKAI_NOW_ANCHOR", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


app = FastAPI(title="SKAI Air ISR — P4 코파일럿 (citation Assessment)")


class AssessRequest(BaseModel):
    """POST /api/assess 본문 — 자연어 질의 1건 + 선택적 선택 객체 id.

    focus_id: 프론트가 지도/타임라인에서 선택한 객체 id(엔티티/why 의도의 지시 대상).
    질의문의 "이 이상징후" 같은 지시어를 그 객체로 확정한다(선택). 미전송이면 None(하위호환).
    """

    query: str
    focus_id: Optional[str] = None


# Foundry read 모드(SKAI_STORE=foundry) 전용 HybridStore 캐시. 요청마다 FoundryClient(인증
# 핸드셰이크)를 새로 만들지 않도록 프로세스 1회만 구성한다. 기본·replay(SKAI_STORE 미설정)에선
# 절대 생성되지 않는다 — 순수 로컬 경로는 store_foundry·foundry_sdk를 건드리지 않는다.
_foundry_store = None


def _store():
    """읽기용 스토어. SKAI_STORE로 read 백엔드가 갈린다(make_store와 동일 게이트).

    - **기본·replay**(SKAI_STORE 미설정): 매 요청 LocalOntologyStore(DB_PATH)를 새로 만든다 —
      기존 동작 바이트 불변(오프라인/재현성 회귀 0). foundry_sdk를 import조차 하지 않는다.
    - **foundry 모드**(SKAI_STORE=foundry): 프로세스 1회 구성한 HybridStore를 재사용한다.
      read는 Aircraft·Observation·Track·OrbitPass·WeatherState·NewsEvent·Operator·Satellite를
      **Foundry에서**(저수준 SDK) 수행하고, Region·Anomaly·문장 cites·correlated_with·mentions
      등 provenance는 **로컬에서 보강**한다(HybridStore 라우팅). 어느 필드가 Foundry발/로컬발인지는
      HybridStore 라우팅 주석·docs/worklog/foundry-read-mode.md 표를 SSOT로 참조.
    """
    if current_backend() == "foundry":
        global _foundry_store
        if _foundry_store is None:
            _foundry_store = make_store(DB_PATH)  # HybridStore(.env 로드·크리덴셜 필요)
        return _foundry_store
    return LocalOntologyStore(DB_PATH)


@app.get("/api/observations")
def api_observations() -> list[dict]:
    """항공기별 최신 관측 1건 (지도 마커). callsign은 Aircraft에서 보강."""
    store = _store()
    ac_map = store.aircraft_map()
    out = []
    for o in store.query_latest_observations():
        ac = ac_map.get(o.aircraft_ref)
        out.append(
            {
                "icao24": o.aircraft_ref,
                "callsign": ac.callsign if ac else None,
                "ts": o.ts,
                "lat": o.lat,
                "lon": o.lon,
                "alt": o.alt,
                "velocity": o.velocity,
                "heading": o.heading,
                "squawk": o.squawk,
                "on_ground": o.on_ground,
                # provenance — 모든 관측은 출처로 역추적 가능해야 한다.
                "source": o.source,
                "source_url": o.source_url,
            }
        )
    return out


@app.get("/api/tracks")
def api_tracks() -> list[dict]:
    """트랙 경로(폴리라인). has_gap 이면 프론트에서 점선/색 구분."""
    store = _store()
    ac_map = store.aircraft_map()
    out = []
    for t in store.query_tracks():
        ac = ac_map.get(t.aircraft_ref)
        out.append(
            {
                "id": t.id,
                "icao24": t.aircraft_ref,
                "callsign": ac.callsign if ac else None,
                "start_ts": t.start_ts,
                "end_ts": t.end_ts,
                "has_gap": t.has_gap,
                "path": t.path,  # [[lat, lon], ...]
                "n_points": len(t.path),
            }
        )
    return out


@app.get("/api/regions")
def api_regions() -> list[dict]:
    """관심지역 폴리곤."""
    store = _store()
    return [
        {
            "id": r.id,
            "name": r.name,
            "classification": r.classification,
            "geo": r.geo,  # [[lat, lon], ...]
        }
        for r in store.query_regions()
    ]


def _anomaly_to_dict(store, a: Anomaly) -> dict:
    """Anomaly + 근거(evidence) Observation + involves Aircraft를 직렬화.

    타임라인 클릭 시 지도 하이라이트·근거 표시에 필요한 필드를 한 번에 내려준다
    (근거 Observation의 콜사인·시각·source_url = provenance 역추적). store는 LocalOntologyStore
    또는 HybridStore(foundry 모드) — 후자면 근거 Observation·aircraft_map은 Foundry read, Anomaly·
    evidence 링크·correlations는 로컬 보강 read다(HybridStore 라우팅).
    """
    ac_map = store.aircraft_map()
    evidence = []
    for obs_id in store.query_evidence_ids(a.id):
        o = store.get_observation(obs_id)
        if o is None:
            continue
        ac = ac_map.get(o.aircraft_ref)
        evidence.append(
            {
                "id": o.id,
                "icao24": o.aircraft_ref,
                "callsign": ac.callsign if ac else None,
                "ts": o.ts,
                "lat": o.lat,
                "lon": o.lon,
                "squawk": o.squawk,
                "source": o.source,
                "source_url": o.source_url,  # provenance 역추적 링크
            }
        )
    involves = []
    for icao24 in store.query_involves_ids(a.id):
        ac = ac_map.get(icao24)
        involves.append({"icao24": icao24, "callsign": ac.callsign if ac else None})
    # P5: 근거·주체·상관을 타입 무관하게(위성 근접=OrbitPass 근거·Satellite 주체) 노출.
    sat_map = store.satellite_map()
    evidence_objects = []
    for e in store.query_evidence(a.id):
        obj = _resolve_object(store, e["id"], ac_map, sat_map)
        evidence_objects.append(
            {
                "type": e["type"],
                "id": e["id"],
                "label": obj["label"] if obj else e["id"],
                "source_url": (obj.get("source_url", "") if obj else ""),
            }
        )
    correlations = []
    for c in store.query_correlations(a.id):
        obj = _resolve_object(store, c["dst_id"], ac_map, sat_map)
        correlations.append(
            {
                "type": c["dst_type"],
                "id": c["dst_id"],
                "label": obj["label"] if obj else c["dst_id"],
            }
        )
    return {
        "id": a.id,
        "type": a.type,
        "ts": a.ts,
        "confidence": a.confidence,
        "status": a.status,
        "lat": a.lat,
        "lon": a.lon,
        "explanation": a.explanation,
        "explainer_backend": a.explainer_backend,
        "created_at": a.created_at,
        "attrs": a.attrs,
        "evidence": evidence,  # 근거 Observation (provenance, P2~P4 하위호환)
        "evidence_objects": evidence_objects,  # 타입 무관 근거(P5 위성 근접=OrbitPass)
        "involves": involves,  # 이상징후 주체 Aircraft
        "correlations": correlations,  # correlated_with → Anomaly/OrbitPass/NewsEvent
    }


@app.get("/api/anomalies")
def api_anomalies() -> list[dict]:
    """이상징후 목록(시각 내림차순) + 근거·주체. 타임라인/마커용."""
    store = _store()
    return [_anomaly_to_dict(store, a) for a in store.query_anomalies()]


@app.post("/api/anomalies/{anomaly_id}/confirm")
def api_confirm_anomaly(anomaly_id: str) -> dict:
    """분석가 승인 — status candidate→confirmed (human-on-the-loop)."""
    store = _store()
    try:
        a = confirm_anomaly(store, anomaly_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Anomaly 없음: {anomaly_id}")
    return _anomaly_to_dict(store, a)


@app.post("/api/anomalies/{anomaly_id}/dismiss")
def api_dismiss_anomaly(anomaly_id: str) -> dict:
    """분석가 기각 — status candidate→dismissed (human-on-the-loop)."""
    store = _store()
    try:
        a = dismiss_anomaly(store, anomaly_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Anomaly 없음: {anomaly_id}")
    return _anomaly_to_dict(store, a)


@app.get("/api/orbitpasses")
def api_orbitpasses() -> list[dict]:
    """위성 통과창 (지상궤적 레이어). 위성명은 Satellite에서 보강 + of/over 링크."""
    store = _store()
    sat_map = store.satellite_map()
    out = []
    for p in store.query_orbitpasses():
        sat = sat_map.get(p.satellite_ref)
        out.append(
            {
                "id": p.id,
                "norad_id": p.satellite_ref,
                "name": sat.name if sat else p.satellite_ref,
                "object_type": sat.object_type if sat else None,
                "region_ref": p.region_ref,
                "start_ts": p.start_ts,
                "end_ts": p.end_ts,
                "max_elevation": p.max_elevation,
                "ground_track": p.ground_track,  # [[lat, lon], ...]
                "n_points": len(p.ground_track),
                # OrbitPass —of→ Satellite / —over→ Region (provenance)
                "source": p.source,
                "source_url": p.source_url,
            }
        )
    return out


@app.get("/api/weather")
def api_weather() -> list[dict]:
    """지역 기상 (공항별 최신). 단위: 시정=sm, 실링=ft, 풍속=kt."""
    store = _store()
    return [
        {
            "id": w.id,
            "region_ref": w.region_ref,
            "station": w.station,
            "ts": w.ts,
            "lat": w.lat,
            "lon": w.lon,
            "wind_dir": w.wind_dir,
            "wind_speed_kt": w.wind_speed_kt,
            "visibility_sm": w.visibility_sm,
            "ceiling_ft": w.ceiling_ft,
            "flight_category": w.flight_category,
            "conditions": w.conditions,  # rawOb 원문
            "source": w.source,
            "source_url": w.source_url,
            "attrs": w.attrs,
        }
        for w in store.query_weather_latest()
    ]


@app.get("/api/news")
def api_news() -> list[dict]:
    """뉴스(저신뢰) + mentions 링크. confidence·저신뢰 표시는 프론트에서."""
    store = _store()
    return [
        {
            "id": n.id,
            "source": n.source,
            "source_url": n.source_url,  # 원문 링크(citation)
            "ts": n.ts,
            "title": n.title,
            "confidence": n.confidence,
            "entities": n.entities,  # 매칭된 지역 별칭
            "attrs": n.attrs,
            "mentions": store.query_mentions(n.id),  # →Region/Aircraft 링크
        }
        for n in store.query_news()
    ]


@app.get("/api/counts")
def api_counts() -> dict:
    """소스별 객체 카운트 (P3 산출 요구). 4종 소스의 온톨로지 기여를 한눈에."""
    c = _store().counts()
    return {
        "objects": c,
        "by_source": {
            "opensky": {"aircraft": c["aircraft"], "observation": c["observation"]},
            "celestrak": {"satellite": c["satellite"], "orbitpass": c["orbitpass"]},
            "metar": {"weatherstate": c["weatherstate"]},
            "gdelt": {"newsevent": c["newsevent"]},
        },
    }


@app.get("/api/stats")
def api_stats() -> dict:
    """객체 카운트 (검증·상태표시용) + LIVE 요약(last_poll_ts·live).

    counts 키(테이블명→개수)는 그대로 두고 last_poll_ts·live만 덧붙인다(하위호환). 프론트가
    한 번의 폴링으로 카운트와 LIVE 상태를 함께 읽을 수 있게 한다(자세한 상태는 /api/live).
    """
    counts = _store().counts()
    lv = _live_view()
    return {
        **counts,
        "last_poll_ts": lv["last_poll_ts"],
        "live": lv["live"],
        # read 소스 노출(local|foundry) — 프론트가 "지금 Palantir에서 read 중"을 표시할 수 있게.
        "store_backend": lv["store_backend"],
    }


# ── P4 코파일럿 ────────────────────────────────────────────────────────────
@app.post("/api/assess")
def api_assess(req: AssessRequest) -> dict:
    """자연어 질의 → 의도 분류 → 의도별 cites 강제 SituationAssessment (DR-0011).

    응답: intent·slots·intent_meta(분류 투명성) + 파싱된 지역·시간창 + 문장별
    {text, cites, confidence, kind} + cited_objects(배지·하이라이트용 id→상세) + 종합
    confidence. 근거 없으면 no_evidence. focus_id는 엔티티/why 지시 대상으로 전달된다.
    """
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="질의가 비어 있습니다.")
    # replay 모드: SKAI_NOW_ANCHOR로 '지금'을 스냅샷 시각에 고정(재현성). 라이브는 None.
    return assess(_store(), query, now=_now_anchor(), focus_id=req.focus_id)


@app.get("/api/live")
def api_live() -> dict:
    """라이브 폴러 상태(프론트 LIVE 인디케이터). 폴러 없으면 live=False(replay·정적).

    반환: {live, last_poll_ts, mode, interval, cycle, last_poll_status, last_cycle, server_now}.
    프론트는 live=True면 LIVE 배지 + 마지막 갱신 경과시간(server_now - last_poll_ts)을 표시.
    """
    return _live_view()


@app.get("/api/assessments")
def api_assessments() -> list[dict]:
    """저장된 SituationAssessment 목록(생성 시각 내림차순)."""
    store = _store()
    return [assessment_to_summary_dict(a) for a in store.query_assessments()]


@app.get("/api/subgraph")
def api_subgraph(assessment_id: str) -> dict:
    """Assessment 중심 온톨로지 서브그래프(노드·엣지) — 프론트 자체 SVG 렌더용.

    aggregates→Anomaly→evidenced_by→Observation / cites→OrbitPass·WeatherState·NewsEvent.
    """
    sg = build_subgraph(_store(), assessment_id)
    if sg is None:
        raise HTTPException(status_code=404, detail=f"Assessment 없음: {assessment_id}")
    return sg


# 정적 페이지 (API 라우트 뒤에 마운트해야 /api/* 가 가려지지 않음)
if WEB_DIR.exists():
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")


def main() -> None:
    import uvicorn

    # replay(SKAI_OFFLINE): 외부 egress를 소켓 레벨로 차단·기록(네트워크 0 증명).
    # uvicorn import/기동 전에 설치해 리스닝 외 모든 외부 연결을 봉쇄한다.
    from server.offline_guard import install_offline_guard

    install_offline_guard()

    host = os.environ.get("SKAI_HOST", "127.0.0.1")
    port = int(os.environ.get("SKAI_PORT", "8000"))
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
