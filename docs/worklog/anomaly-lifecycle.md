# anomaly-lifecycle.md — 이상징후 라이프사이클(자동 해소) + dropout 경계이탈 억제

- 날짜: 2026-07-06
- 담당: 실행 에이전트(opus). 종합·DR/CHANGELOG 반영은 메인(Fable).
- 근거: 팀리드 지시 — 수정된 룰로도 dropout이 시간당 ~78건 순증(87건 전부 candidate), 닫는 경로가 사람 클릭뿐 → 무인 운영에서 무한 누적. cf. [[dropout-semantics]](의미 재정의 선행 작업).
- 판정: **구현 완료**. 전체 스위트 회귀 0 (기준 409 passed/4 skipped → 422 passed/4 skipped, 신규 13건).

---

## 0. 결론 (한 줄)

dropout 후보의 무한 누적을 **양단**에서 막았다. (1) **자동 해소**: 침묵했던 기체가 복귀 관측되면 폴러가 candidate→`resolved`로 전이(반증 증거 = 복귀 관측을 evidenced_by로 링크). (2) **경계이탈 억제**: 관심영역 경계 부근에서 바깥으로 향하는 침묵은 표적 소실이 아니라 커버리지 이탈로 보고 후보 생성 자체를 억제. 사람 결정(confirmed/dismissed)·replay 자산은 불변.

---

## 1. 자동 해소 (반증 증거 기반)

### 설계
- 신규 상태값 `"resolved"` 추가(`ANOMALY_STATUSES`에 4번째 — status는 string이라 스키마 변경 아님). candidate/confirmed/dismissed와 달리 **폴러가 자동 전이**.
- 반증 조건: `status=candidate`인 `adsb_dropout` 중, involves→Aircraft 기체의 **최신 관측 ts > anomaly.ts**(= 침묵 시작 시각). 침묵 이후 새 관측 = 복귀.
- `resolution` dict(프론트 에이전트와 공유되는 고정 계약): `{"kind": "return_observed", "obs_id": <복귀 관측 id>, "resolved_at": <ts>}`.
- **복귀 관측을 evidenced_by 링크로도 추가**(대체 아님, 다중 근거 보존). "근거 없는 상태 전이 금지"(CLAUDE.md provenance 강제)의 연장 — 왜 해소됐는지 온톨로지에서 역추적 가능.
- 기체 식별은 **involves→Aircraft 링크**(`query_involves_ids`)로 역추적 — anomaly id 문자열 파싱 대신 온톨로지 SSOT.

### 재오픈/재발화
- resolved는 **자동 재오픈 없음**(scan_and_resolve가 status!=candidate를 건너뜀). 새 침묵은 `last.ts`가 달라 dedup상 **새 이벤트**로 정당하게 다시 발화한다(detect_adsb_dropout의 침묵-이벤트 단위 dedup이 보장, [[dropout-semantics]] §2.3).

### 배선 위치 (폴러 전용)
- `anomaly/actions.py::scan_and_resolve(store, now)` 신설 + `scan_and_create_all` **끝**(correlate 뒤)에서 호출. `scan_and_create_all`은 `opensky.ingest_cycle`·`adsbfi_tracks.ingest_cycle` **양쪽**이 부르는 공용 함수라 두 폴러 경로 모두 커버.
- **replay는 폴러가 없어**(scan_and_create_all 미호출) scan_and_resolve도 호출되지 않는다 → replay 자산 자동 불변. 테스트로 증명(§4).
- `created`(신규 생성) dict에는 섞지 않음 — 전이는 신규 아님(n_anom 카운트 오염 방지).

---

## 2. 경계이탈(커버리지 이탈) 억제

### 억제 vs 하향 — **억제** 선택 (팀리드 위임 판단)
저신뢰(conf 0.25) 후보도 닫으려면 사람 클릭이 필요해 누적을 못 막는다. 경계이탈은 명확한 benign 원인(관측 커버리지를 벗어남)이라, `on_ground`·mirror-present(2차 소스가 여전히 관측) 전면 억제와 **같은 계열**로 후보 생성 자체를 막는 게 정합적. 우리가 틀려 실제로 복귀하면 애초에 후보가 없어 무영향이고, 재침묵 시 새 이벤트로 정상 발화한다.

### 판정 로직 (`anomaly/rules.py::detect_adsb_dropout`)
- 마지막 관측이 **관심영역 경계에서 마진 이내 AND 기수(heading)가 바깥**이면 억제.
- **경계 = 민감구역 union bbox**(`ontology/geo.py::union_bbox`). OpArea ⊂ KADIZ 중첩이라 union = 가장 바깥(KADIZ) = 실제 폴러 fetch bbox = 실질 커버리지. **내부 경계 오판 방지가 핵심**: OpArea 안쪽 경계에서 KADIZ 내부로 이동하는 기체는 커버리지를 안 벗어나므로 억제하면 안 됨 → union이 이를 보장(OpArea edge는 union edge가 아님).
- **외향 판정**(`ontology/geo.py::heading_exits_bbox`): 근접한 각 경계의 외향 법선을 합해 heading 벡터(진북 시계방향, ADS-B true_track 규약)와 내적 → 양수(외향 성분)면 이탈. 경계와 나란하거나 안쪽이면 유지(보수적). **heading None이면 판정 불가 → 억제 안 함**(라이브 heading은 OpenSky true_track·adsb.fi track에서 채워짐 — 확인).
- **마진 상수** `DROPOUT_EDGE_MARGIN_DEG=0.5°`(근거 주석): 제트 순항(~250 m/s)이 침묵 임계(60s 폴 시 ~180s) 동안 이동하는 ~45km(≈0.4°)를 커버. 이 안에서 바깥으로 향하면 침묵 중 실제 경계를 넘었을 개연성. 0.5°는 약간 넉넉(억제 폭을 보수적으로 제한).
- 위/경도에 동일 도(°) 마진 적용(경도 1°가 37°N에서 물리적으로 더 짧음)은 근사 — 억제가 경도축에서 약간 관대해질 뿐이라 benign 억제 방향, 안전(주석에 명시).

### 배치 순서
`silence → active_window → region → on_ground → **boundary_exit** → crosscheck → confidence`. crosscheck(2차 소스 질의) 앞에 둬서 억제 대상엔 crosscheck 호출을 아낀다.

---

## 3. Foundry 동기 — **로컬 권위본(미러 없음)**, 갭 정직 기록

- confirm/dismiss는 `store_foundry.set_anomaly_status`가 `confirm-anomaly`/`dismiss-anomaly` 액션으로 Foundry 스파인에 미러한다. **그러나 resolved에 대응하는 `resolve-anomaly` 액션은 Foundry에 없다**(P7 배선 범위 밖 — ACTION 상수에 create/confirm/dismiss만 존재).
- 억지로 `set_anomaly_status("resolved")`를 태우면 Foundry 쪽은 confirmed/dismissed만 처리 → **조용한 no-op**(효과 없이 오해만). 그래서 배선하지 않고 **로컬 권위본**으로 둔다(CLAUDE.local "억지 배선 금지").
- `store_foundry.HybridStore.resolve_anomaly`를 명시 메서드로 두고 이 결정을 코드 주석에 기록(자동 __getattr__ 위임 대신 명시 — 갭이 의도적임을 드러냄).
- 정합성: 복귀 관측 evidenced_by는 **다중 근거**라 write_anomaly도 이미 로컬 권위본(Foundry엔 첫 근거 1건만 미러). resolved 상태·복귀 근거가 로컬에만 있는 건 기존 다중근거 로컬 권위 패턴과 일관.
- **갭**: SKAI_STORE=foundry 운용 시 Foundry Anomaly 객체의 status는 candidate로 남고(로컬만 resolved), 복귀 근거 엣지도 로컬만. read 경로는 로컬 권위본(query_anomalies는 로컬)이라 UI/서버 표시는 정확. Foundry 스파인 status 최신화가 필요하면 Foundry에 `resolve-anomaly` Modify 액션 추가 후 배선하면 됨(후속).

---

## 4. 변경 파일

| 파일 | 변경 |
|---|---|
| `ontology/model.py` | `ANOMALY_STATUSES`에 `"resolved"` 추가(+근거 주석). |
| `ontology/geo.py` | `union_bbox(regions)`·`heading_exits_bbox(...)` 신설(+`Bbox` 별칭). |
| `anomaly/rules.py` | `DROPOUT_EDGE_MARGIN_DEG=0.5` 상수 + detect_adsb_dropout에 경계이탈 억제(union 1회 계산·on_ground 뒤·crosscheck 앞). geo import 확장. |
| `anomaly/actions.py` | `scan_and_resolve(store, now)` 신설 + `scan_and_create_all` 끝에서 호출. `ANOMALY_TYPE_ADSB_DROPOUT` import. |
| `ontology/store_local.py` | `LocalOntologyStore.resolve_anomaly(anomaly_id, obs_id, resolved_at)` — status→resolved + attrs.resolution + 복귀 관측 evidenced_by(멱등). |
| `ontology/store.py` | Protocol에 `resolve_anomaly` 시그니처. |
| `ontology/store_foundry.py` | `HybridStore.resolve_anomaly` — 로컬 권위본(Foundry 미러 없음, 갭 주석). |
| `copilot/assessment.py` | `_STATUS_KO`에 `"resolved": "해소됨"`(신규 status가 copilot 서술에서 raw 영문으로 새지 않게 — 내가 도입한 status의 라벨 SSOT 보완). |
| `tests/test_anomaly_lifecycle.py` | 신규 13건(아래). |

---

## 5. 테스트 (신규 13건, 회귀 0)

- 복귀→resolved 전이(+resolution 계약·복귀 evidenced_by·앵커 근거 보존), 복귀 다건 시 최신 관측 사용.
- 침묵 지속→candidate 유지(신선 관측 없음).
- confirmed/dismissed 불변(복귀 관측 있어도 자동 해소 대상 아님).
- resolved 재오픈 없음(멱등).
- **replay 자산(SHADOW1·GHOST2) candidate 유지** — 폴러 경로 scan_and_create_all(내부 scan_and_resolve 포함) 돌려도 불변(복귀 없음·경계억제 안 걸림).
- **폴러 2사이클 통합** — scan_and_create_all만으로 dropout 생성→복귀 시 resolved(end-to-end 폴러 배선 증명).
- 경계이탈 억제(경계 근접+외향→억제) vs 중앙 침묵 발화 / 경계 근접+내향→유지 / heading None→유지.
- geo 헬퍼(union_bbox 중첩·[]→None, heading_exits_bbox 6-케이스+None 방어).

측정: `409 passed, 4 skipped`(기준) → `422 passed, 4 skipped`. 회귀 0.

---

## 6. 한계·미사용 (정직)

- **Foundry resolved 미러 없음**(§3) — 로컬 권위본. Foundry 운용 시 스파인 status는 candidate로 남음. 후속: Foundry `resolve-anomaly` 액션 추가 시 배선.
- **경계이탈 heading 의존** — heading None이면 억제 안 함(보수적). 라이브 두 항적 소스는 heading을 채우나, 결측 관측은 억제에서 빠져 누적에 다시 노출될 수 있음(자동 해소가 뒷단에서 커버).
- **마진은 도(°) 단위 근사** — 위/경도 물리 길이 차 무보정(§2). 커버리지 이탈 휴리스틱엔 충분, 정밀 지오 필요 시 haversine 기반 마진으로 교체 가능.
- **자동 해소는 최신 관측 1건으로 판정** — "복귀 순간(gap 직후 첫 관측)"이 아니라 "현재도 관측 중"을 근거로 씀(현 상황 인식엔 더 적합). 복귀 시점 정밀 표기가 필요하면 첫-복귀 관측 조회로 교체 가능.
- resolved 전이 카운트를 폴러 로그에 노출하지 않음(scan_and_create_all 반환 계약 유지 위해). 상태는 store·UI에 반영되므로 관측은 가능.
