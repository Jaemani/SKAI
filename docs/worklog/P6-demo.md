# P6-demo.md — 데모 패키징(이중 모드·네트워크 0 재생·now 앵커링) 실행 로그

- 날짜: 2026-07-04
- 담당: opus 실행 에이전트 (P6)
- 근거: PROMPTS.md P6 · **DR-0008**(이중 모드·now 앵커링·정직한 Foundry 이원화 — 그대로 집행) ·
  이월 P5 §10(스냅샷 재생·라이브 전유형 배선)·P4 §8-5(now 앵커링)·§8-7(assessment 목록)·P3 §6-5(GDELT 취약)
- 상태: **완료** — 테스트 **106/106**(기존 98 + P6 8) · 네트워크 0 재생 3중 증명 · replay 바이트 결정성 ·
  라이브 OpenSky 실호출(HTTP 200) · 스토리보드 4장 · 3분 대본(`demo.md`)

---

## 1. 무엇을 만들었나

3분 발표용 데모 패키지 — 이중 모드 기동, 네트워크 0 재현, now 앵커링, 대본, 심사 매핑.

```
scripts/demo.sh            데모 이중 모드 — replay(네트워크0)·live(실 API+합성)·stop·status
server/offline_guard.py    SKAI_OFFLINE 소켓 가드(루프백만 허용·외부 egress 차단·기록) = 네트워크0 증명
server/app.py              (수정) _now_anchor(SKAI_NOW_ANCHOR) → assess(now=앵커) 배선 + main()에 가드 설치
anomaly/actions.py         (수정) create_anomaly/create_from_draft/scan_and_create[_all]에 created_at 앵커 전파
demo.md (루트)             3분 대본 — 초단위 타임라인·화면·클릭·발화·폴백 + Foundry 이원화 + 심사 매핑 + 리허설 체크
tests/test_p6.py           8케이스(now 앵커 파싱·벽시계 무관성·created_at 앵커·replay 결정성·오프라인 가드)
docs/worklog/p6_step*.png  스토리보드 4장(지도 오프닝/질의 cites/confirm 후/서브그래프)
data/demo/skai_demo.db     재생 전용 DB(런타임 data/skai.db와 격리, 재생성 가능)
```

**설계 원칙 유지**: 루트 기획 8문서 무수정(§9 참조). git commit 없음. Foundry는 "준비 완료·대기"로
정직하게(로컬을 AIP인 척 금지 — DR-0008). 기존 98개 테스트 무변경 보존.

---

## 2. now 앵커링 (P4 §8-5 마감)

문제: 서버 `api_assess`가 `assess(store, query)`를 벽시계 now로 호출 → 스냅샷(과거 앵커) 데이터에
"지금(최근 30분)" 질의를 던지면 창이 어긋나 결과가 안 나옴.

해결(2요소):
1. **질의 창 앵커**: `server/app.py::_now_anchor()`가 `SKAI_NOW_ANCHOR` 환경변수를 읽어
   `assess(now=앵커)`로 전달. replay는 `SKAI_NOW_ANCHOR=EVAL_NOW`로 기동 → "지금"이 스냅샷 시각에
   고정. 라이브는 미설정 → None → 벽시계(정직한 "지금").
2. **탐지 시각 앵커**: `anomaly/actions.py`의 anomaly `created_at`이 유일한 벽시계 휘발 필드였음
   (`int(time.time())`). `scan_and_create_all(now=X)`가 `created_at=X`를 전파하도록 옵셔널 파라미터
   추가 → replay 스냅샷의 탐지 시각까지 앵커에 고정. 기본값 None=벽시계라 **라이브 동작 무변경**
   (test_scan_all_now_defaults_to_walltime로 보존 확인).

앵커 SSOT = `eval.EVAL_NOW`(1783000000 = 2026-07-02 13:46 UTC). 평가·데모가 같은 앵커를 써 표기가
"오늘"로 보이며 결정적. demo.sh가 `eval.run_eval`에서 읽어 하드코딩 중복을 피함.

---

## 3. 네트워크 0 — 3중 증명

replay 서버는 **구조상** 외부 fetch 경로가 없다(server.app는 store-read 전용, connectors를
import하지 않음). 그 사실을 런타임으로 강제·증명하는 3중 방어:

| 증명 | 방법 | 결과 |
|---|---|---|
| ① 소켓 가드 | `SKAI_OFFLINE=1` → `socket.connect/connect_ex` 감싸 루프백 외 차단·기록 | **차단 시도 0 · 가드 활성 1**(외부로 나가려는 시도 자체가 0) |
| ② lsof | 서버 PID의 네트워크 소켓 확인 | **1 LISTEN(127.0.0.1:8000 루프백) · 외부 ESTABLISHED 0** |
| ③ import | 서버 로드 시 `sys.modules`의 connectors | **없음**(순수 store-read — fetch 코드 미로드) |

가드는 루프백(127.0.0.0/8·::1·localhost)·UNIX 소켓만 허용, 외부 IP/도메인은 `OfflineViolation`으로
즉시 실패시키고 stderr에 남긴다(은폐 불가). TLE 캐시·OpenSky·GDELT·METAR 등 모든 httpx egress가 이
경로로 봉쇄된다. 오프라인 상태에서도 재생 질의 3개가 즉시 유의미한 응답을 낸다(§4).

---

## 4. 검증 결과 (성공기준 5항목 — 실행 증명)

| # | 기준 | 결과 |
|---|---|---|
| 1 | **네트워크 0 재현** — 외부 차단 상태에서 질의 3개 유의미 응답 + 이상탐지 5종 + confirm 동작, 로그로 증명 | **OK** — 질의 3개 각 문장 38·이상징후 9·no_evidence=false. 탐지 5종(비상스쿽·dropout·로이터링·군용·위성근접). confirm(SHADOW7 dropout→confirmed) 동작. 네트워크 0 §3(외부 소켓 0·가드 차단 0·connectors 미로드). |
| 2 | **replay 연속 2회 결정성** | **OK** — `demo.sh replay` 2회 빌드의 (assess×3 + anomalies + subgraph) SHA-256 **바이트 동일**(`cb437a09…`). PYTHONHASHSEED=0 고정으로 직렬화 순서까지 결정적. 내용 자체는 hashseed 무관 결정적(정규화 비교로 별도 확인). |
| 3 | **live 모드 1회 기동·정상**(OpenSky 최소 사이클) | **OK** — `LIVE_MAX_CYCLES=1`에서 **OpenSky HTTP 200**(x-rate-limit-remaining=390), 실 항적 21기 수집 후 폴러 클린 종료. 내러티브 합성 1건 가미. 라이브 질의 응답 문장 16(실항적 22·이상징후 3·통과 23·뉴스 5). 가드 미설치(네트워크 필요). |
| 4 | **기존 98 + 신규(now 앵커·결정성·오프라인) 전부 통과** | **OK** — **106/106**(98 + P6 8). |
| 5 | **검증 후 프로세스 정리** | **OK** — 서버/폴러 정지·pid 정리·포트 8000 해제·잔여 headless 브라우저 0. |

**제약 준수**: git commit 없음 · 루트 기획 8문서 무수정(§9) · Foundry "준비 완료·대기" 정직 문구
(로컬을 AIP인 척 안 함) · 코드/문서 주석 한국어.

---

## 5. 스토리보드 (재생 모드 · 1680×1050)

| 파일 | 스텝 | 담는 것 |
|---|---|---|
| `p6_step1_map.png` | ① 지도 오프닝 | KADIZ·OpArea·항적·위성궤적·gap 트랙 + 자동선택 상세(**상태:후보**=confirm 전) + 4소스 카운트 |
| `p6_step2_cites.png` | ② 질의① cites | "해석(투명성)" + 문장별 파란 cites 배지 + 좌측 근거/correlated_with + confirm 버튼(활성) |
| `p6_step3_confirmed.png` | ③ confirm 후 | 상세 하단 "상태: **확인됨**", 버튼 비활성 = Action 상태전이 영속 |
| `p6_step4_subgraph.png` | ④ 서브그래프 | 중앙 상황평가 객체 + correlated_with(청록 점선) dropout↔위성↔뉴스 + 범례 |

캡처 함정: headless **Brave**가 이 환경(인터랙티브 Brave 세션 동시 실행)에서 GPU 프로세스 경합으로
about:blank조차 행에 걸림(rc=124). **Google Chrome** `--headless=old`는 스크린샷을 파일에 쓴 뒤 종료를
안 할 뿐이라 `timeout`으로 감싸면 파일 확보 후 프로세스만 정리됨 → 이 경로로 캡처. (P5까지의 Brave
`--headless=new` 경로는 인터랙티브 세션 미실행 시엔 동작.)

---

## 6. 리허설 체크리스트 (요약 — 상세는 demo.md §2)

재생 기동 후 매번 동일해야 정상:
- 질의①(지금) 창=최근 30분, 이상징후 9·항적 11, 문장 38 전부 cites.
- 질의②(최근 1시간) 창=최근 1시간, 위성 상관 문장 노출.
- 질의③(서해 기상·뉴스) 창=최근 30분(기본), 기상 RKSI MVFR·뉴스 0.35, `&sg=1` 서브그래프 자동.
- 탐지 5종: CES7500 0.95 · SHADOW1/7 0.72(교차확인)·GHOST2 0.42(미확인) · ORBIT3 0.60 · RCH451/FALCON9 0.55 · SYN-RECON 0.40.
- confirm: CES7500 [확인]→"확인됨"·버튼 비활성(재기동 시 후보 초기화).

### 6-F. 스텝 ⑥ Foundry 라이브 실연 사전조건 (2026-07-04 추가)

`scripts/demo_foundry.sh`(demo.md §1 ⑥). 발표 전 아래를 준비·리허설한다:

- **.env**: `FOUNDRY_TOKEN`·`FOUNDRY_HOSTNAME`·`FOUNDRY_OSDK_INDEX` 존재(값 출력 금지). 토큰 만료 여부
  사전 점검(만료 시 이 스텝만 폴백).
- **.venv312**: Foundry SDK(Python 3.12) 환경. `.venv312/bin/python -c "import foundry_sdk"` 통과 확인
  (foundry_sdk 1.97.0). 메인 `.venv`(3.14)엔 SDK 없음 — 셸 래퍼가 자동으로 `.venv312`를 쓴다.
- **Palantir 로그인 탭 미리 열기**: 발표 브라우저에 Foundry 로그인 세션을 미리 띄워 둔다(발표 중 로그인
  금지). **Object Explorer**를 해당 온톨로지에서 열어 Aircraft·Observation·Anomaly Object Type 목록
  위치를 손에 익힌다(방금 생성된 객체가 목록 상단/검색에 뜨는 자리).
- **사전 실행 리허설**: 발표 흐름대로 스텝 ⑤ 중(또는 ⑥ 직전) `scripts/demo_foundry.sh`를 돌려
  `[DEMO-FOUNDRY-OK]`와 (a)~(c) 각 OK, 콘솔이 안내하는 anomalyId를 확인. 그 anomalyId로 Object
  Explorer에서 Anomaly를 찾아 evidenced_by·involves·status를 여는 동선을 1회 이상 연습.
- **폴백 리허설**: 네트워크를 끊고(또는 토큰을 임시로 비워) 실행 → 실패 메시지 + "replay 전환" 안내가
  뜨는지 확인. 실패 시 이 스텝만 스킵하고 `demo.sh replay`로 계속, ⑦을 22초로 늘려 흡수(전체 3분 유지).
- **정리**: 리허설 뒤 `scripts/demo_foundry.sh cleanup`으로 합성 데모 자산(Anomaly·합성 Observation·
  Aircraft) 삭제. 실 hex 인제스트분은 실데이터라 보존(재실행 시 dedup). 재실행마다 직전 합성 자산은
  시작 시 자동 정리되므로 누적되지 않는다.

**실측 검증(2026-07-04, 이 세션)**: `.venv312`로 리허설 back-to-back 2회 [DEMO-FOUNDRY-OK] — (a) 실
항적 dedup 인제스트(observed_as FK traverse), (b) 합성 비상 스쿽 → evidenced_by/involves 엣지 형성
(§12 무해 ApplyActionFailed 흡수), (c) confirm→confirmed 전이. 정리 후 합성 자산 순증 0. 회귀: pytest
178 통과, `demo.sh replay`(SKAI_STORE 미설정=로컬) 무변경. 상세는 P7-foundry-migration.md §11~§13.

---

## 7. assessment 히스토리 패널 (P4 §8-7) — 생략 결정

"질의마다 인텔 객체가 쌓임" 서사는 이미 (1) 서브그래프 중앙의 **SituationAssessment 객체 노드**,
(2) confirm 상태전이 영속, (3) `/api/assessments` 엔드포인트로 실증됨. 3열 UI가 이미 조밀해 목록
패널은 한계효용 대비 화면 잡음·스크린샷 churn만 늘림(과설계). DR-0008 대본도 미요구 → 생략,
demo.md 부록 C에 사유 기록. (웹 무변경 = P5 스크린샷·기존 테스트 보존.)

---

## 8. 잔여 리스크 / P6-이후 이슈

1. **재생 질의 3개의 본문 유사**: 지역+시간창 결정적 파서(DR-0006)라 3개 질의의 이상징후 집합은
   같고 "해석" 시간창만 달라짐(의미 필터링은 비목표). 발표는 각 질의로 **다른 단면**을 조명하는
   프레이밍으로 흡수(demo.md §2 주). 심사 중 "질문을 무시한다" 오해 방지 위해 프레이밍 준수 필요.
2. **재생 질의①이 문장 38개**(이상징후 9 + 상관 26): P5 상관 공간 임계 300km가 관대해 같은 now·같은
   지역 9건이 광폭 클러스터로 묶임(P5 §3 기록). 화면은 스크롤로 충분하나, 발표는 상단 요약·cites
   배지·서브그래프에 집중(전 문장 낭독 아님). 정밀화는 라이브 OpArea 세분화 시 자동(P5 §10-3).
3. **live 모드가 런타임 DB(data/skai.db)에 합성 주입**: 설계상 라이브 오프너 동작이나, 런타임 DB가
   과거 P3~P5 누적(위성 95·통과 100)이라 narrative 주입 시 satellite_proximity가 다수(13) 승격됨.
   깔끔한 라이브 오프너를 원하면 기동 전 런타임 DB 리셋 고려(선택). 발표 백본은 격리 재생 DB라 무관.
4. **claude 서술 폴리시**: 데모 기본 template(재현성). `SKAI_EXPLAINER=claude`는 실호출 검증됨(P5 §6)이나
   중첩 세션 타임아웃 가능 → 발표엔 template 유지 권장.
5. **Foundry**: ~~크리덴셜 대기~~ → **해소(2026-07-04)**. 스키마 구축 + `store_foundry` 실배선 완료
   (P7 §11~§13). 스텝 ⑥이 실 Foundry 실연으로 교체됨(`scripts/demo_foundry.sh` + demo.md §1 ⑥ · §3 ·
   본 문서 §6-F). 잔여: create-anomaly가 매 EXECUTE마다 무해 ApplyActionFailed를 던지나 코드가 read-back
   으로 흡수(§12, 데모 무영향). over(OrbitPass→Region)·within(Observation→Region) 링크 미채움(데모 필수
   아님). 발표 시 Foundry/네트워크 실패는 스텝 ⑥ 폴백으로 흡수(로컬 데모 지속).

---

## 9. 루트 기획문서와의 정합 (무수정 확인)

- **P6에서 루트 8문서(README·CLAUDE·direction·ontology·aip-integration·data-sources·architecture·PROMPTS)
  무수정**. `git status`상 `aip-integration.md`·`data-sources.md`가 M으로 뜨나, 이는 **본 세션 3시간 전
  (00:16~00:17) P0-B Foundry 검증이 남긴 변경**(§0-보강, P0B-foundry.md 참조)으로 P6와 무관.
- demo.md는 루트 신규 파일(기획문서 아님 — 발표 대본). CLAUDE.local.md 문서 분리 규칙상 worklog는
  `docs/worklog/`, 대본은 산출물로 루트 허용.
- now 앵커·created_at 변경은 온톨로지 스키마·API 계약 무변경(옵셔널 파라미터 추가·env 배선만).

---

## 10. 되돌리기

- 신규 삭제: `scripts/demo.sh` · `server/offline_guard.py` · `tests/test_p6.py` · `demo.md` ·
  `docs/worklog/p6_step*.png`(4) · `docs/worklog/P6-demo.md` · `data/demo/`(재생성 가능).
- 기존 역편집:
  - `server/app.py`: `_now_anchor()` 추가·`api_assess`의 `now=_now_anchor()`·`main()`의 가드 설치 3곳.
  - `anomaly/actions.py`: `create_anomaly`/`create_from_draft`/`scan_and_create`/`scan_and_create_all`의
    `created_at` 파라미터 및 전파(4곳). 되돌리면 `created_at=int(time.time())` 원복.
- 온톨로지 스키마 v0.1 유지(신규 테이블 없음). 런타임 산출물(data/·*.db)은 gitignore. 데모 DB 재생성 가능.
- 루트 기획 8문서 무변경(§9).
