# DR-0011 — 대화형 코파일럿(의도분류+LLM) + 실시간 + UI 직관화

- 날짜: 2026-07-04
- 상태: 채택
- 근거: 사용자 지시(질의 개선·실시간·UI). DR-0006(결정적 파서)의 **의도적 확장**.

## 맥락
현 코파일럿은 지역(KADIZ)+시간창만 파싱하고 그 외 질문엔 고정 상황요약 템플릿을 반환 → 자유 질의에 "무시하는 것처럼" 보임. 사용자가 (1) 질의가 실제로 작동, (2) 실시간 데이터, (3) 직관적 UI를 요구.

## 결정
1. **의도분류 라우팅 + LLM 서술 하이브리드** (DR-0006 파서를 대체가 아니라 확장):
   - `copilot/intent.py` 신설. 규칙 기반 1차 분류(빠름·결정적) + 모호 시 LLM 분류 폴백. 의도: `situation_summary`(현행)·`count`·`filter`(operator/origin/military/type)·`entity_explain`(이 이상징후/기체/위성 뭐야)·`why`(왜 이상한가)·`correlation`(은닉 정황)·`weather`·`news`.
   - 각 의도가 **다른 tool read 조합**으로 사실(Fact, cites 보유)을 확정 → 문장 조립 → (옵션)LLM 서술.
   - **citation 불변식 유지(DR-0006)**: 사실은 온톨로지 read에서, 각 문장은 cites 보유, cites 없는 문장은 write_assessment 거부. LLM은 서술만 다듬고 cites 매핑 불변, 실패 시 템플릿 폴백.
2. **LLM은 live/interactive만, replay는 결정적**: `SKAI_EXPLAINER=claude`(또는 신규 `SKAI_COPILOT_LLM`)가 켜졌을 때만 서술 강화. **replay 모드(발표 백본)는 템플릿 유지 = 결정성 불변**(DR-0008 오프라인 재현). LLM 경로는 타임아웃+폴백으로 데모 안전.
3. **실시간 = 지속 폴링 + 자동 갱신**:
   - `connectors/opensky.py`의 폴러를 연속 루프(기본 25초 간격, 환경변수 조절)로. 실 데이터만(합성 주입 없음). 크레딧 절약 위해 bbox 한정·간격 하한.
   - 프론트 자동 refresh(폴링 주기와 맞춤) + **LIVE 인디케이터·마지막 갱신 시각·항적 이동**. 서버는 폴링(간단·견고), SSE는 과설계로 보류.
   - `scripts/demo.sh live`가 이 연속 모드 기동. replay는 무변경(결정적).
4. **UI 직관화**: 온보딩(첫 진입 안내)·질의창 강조·의도별 답변 렌더(카운트/필터/설명은 요약과 다른 표현)·로딩 상태·근거 배지 클릭 동선 명확화·LIVE 상태 표시. 정보 위계 정리(지도 중심, 패널 접기).

## 기각 대안
- LLM 자유 생성(citation 사후매칭) — 환각 방지 붕괴. 기각(DR-0006 불변식).
- SSE/WebSocket 실시간 — 폴링으로 충분, 복잡도만. 보류.
- replay에도 LLM — 결정성 훼손(발표 리스크). 기각.

## 영향
- DR-0006은 "결정적 파서 기본"에서 "결정적 라우팅 + 선택적 LLM 서술"로 진화(불변식은 계승).
- 되돌리기: intent.py 미사용 시 기존 situation_summary 경로로 폴백(파서 그대로 살아있음). LLM 끄면 전부 템플릿.
- 구현 순서: 백엔드(intent·tools·assess·poller·api) 먼저 → 프론트(UI·auto-refresh·렌더) — web/index.html 충돌 회피.
