# DR-0008 — P6: 데모 패키징 + 스냅샷 재생

- 날짜: 2026-07-04
- 상태: 채택
- 근거: P5 검증 통과 (`docs/worklog/P5-eval.md`) · PROMPTS.md P6 · P4 §8-5(now 앵커링)·P5 이월

## 맥락
P6 = 3분 발표용 라이브 데모 + 네트워크 부재 대비 백업. 현재 라이브(OpenSky·Celestrak·METAR)와 합성(5종 이상탐지·내러티브)이 모두 동작하나 데모 기동이 수동 조립이고, GDELT는 라이브 취약(P3 §6-5), Foundry는 여전히 사용자 UI 대기.

## 결정
1. **데모 이중 모드**: `scripts/demo.sh live`(실 API 폴링 + 합성 내러티브 가미) / `scripts/demo.sh replay`(**네트워크 0** — 선언적 시나리오 세트 + now 앵커링으로 완전 재현). replay가 발표 기본, live는 임팩트용 오프닝. 스냅샷은 별도 데모 DB 파일로 준비(런타임 DB와 격리).
2. **now 앵커링 구현**(P4 §8-5): replay 모드에서 assess·룰·상관의 기준 시각을 스냅샷 시각에 고정 — "지금" 질의가 언제 돌려도 같은 결과.
3. **demo.md 대본(3분)**: 문제 1문장 → 라이브 지도 → 질의① 상황평가(문장별 cites) → 이상징후 **confirm(Action 상태전이)** → **은닉 정황 서브그래프**(correlated_with) → 평가 수치(P/R·맨몸 비교) → 심사 4항목 클로징. 각 스텝에 화면·클릭·발화 명시 + 스텝별 스크린샷(스토리보드).
4. **"AIP 얕지 않음" 시연은 정직하게 이원화**: 로컬 스택으로 Action→상태전이→provenance를 실연하고, Foundry 이관 경로(store_foundry 스텁 + aip-integration.md §0-보강)를 "설계상 준비 완료·크리덴셜 대기"로 제시. Foundry가 개통되면 대본의 해당 스텝을 실 Foundry 화면으로 교체.
5. **심사 매핑 슬라이드 골자**: README 지표표 4항목 각 한 문장 + 화면 증거 매핑.

## 기각 대안
- 라이브 단일 모드 — 발표장 네트워크·API 리스크에 데모 전체가 인질. 기각(PROMPTS P6 명시).
- Foundry 미개통 은폐(로컬을 AIP인 척) — 심사(Palantir 멘토 포함) 앞에서 즉발 리스크 + 정직성 위반. 기각.

## 영향
- P6 완료 시 PROMPTS P0~P6 전 단계 소화(P0-B Foundry 왕복만 사용자 대기). 이후 작업 = Foundry 이관·git 커밋(사용자 결정)·발표 리허설.
