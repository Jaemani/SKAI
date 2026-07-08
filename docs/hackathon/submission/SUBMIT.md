# D4D 제출 양식 입력값 (2026-07-05)

제출 페이지: https://d4d.tech/submit?eventId=81e7ab91-7812-4441-8427-0e747267275d
**입력은 사용자가 직접** (이 문서는 붙여넣기용 준비물).

| 항목 | 값 |
|---|---|
| 프로젝트명 | `SKAI — Air ISR Fusion Copilot (공중·우주 상황인식 융합 코파일럿)` |
| 프로젝트 설명 | `docs/submission/description.md` 전문 붙여넣기 (2,727자/5,000 · GFM) |
| 트랙 | **T2 · OSINT & 국방인텔** |
| 저장소 | `https://github.com/Jaemani/SKAI` (public 확인 2026-07-05) |
| 데모 URL | `https://albuquerque-palace-motherboard-whether.trycloudflare.com` |
| 영상 URL | (선택 — 미제출) |
| 스크린샷 | `docs/submission/0N_*.png` 최대 8장 업로드 |

## 데모 URL 운영 (터널 + 라이브 모드)

**현재 운영 구성 (2026-07-05 저녁 확정) — 라이브 모드, 실항적 ~116기:**

```bash
SKAI_POLL_SOURCES=adsbfi,gdelt,metar,celestrak SKAI_CROSSCHECK=off \
METAR_ICAOS="RKSI,RKJB,RKPC,RKPK,RKTU" SKAI_POLL_INTERVAL=60 \
SKAI_COPILOT_LLM=template scripts/demo.sh live
```

- **항적 = adsb.fi**(OpenSky 익명 크레딧 소진·UTC 자정 리셋 → 대체 배선). KADIZ 2점 반경질의, 사이클당 2호출.
- **SKAI_CROSSCHECK=off인 이유(원칙)**: 항적 소스가 adsb.fi인 동안 교차확인도 adsb.fi면 동어반복(같은 네트워크) → dropout은 저신뢰(0.42) 후보로만. OpenSky 복귀 후 켜면 진짜 2소스 교차가 됨.
- 기상 5곳(인천·무안·제주·김해·청주), 코파일럿 서술 template(무인 URL 안정·쿼터 보호).
- 군용 시나리오 3건은 DB에 주입돼 있음(재기동해도 유지 — 재주입 금지, 중복 누적됨).

**불변 규칙**: 터널 프로세스(cloudflared, 로그 `data/cloudflared.log`)가 죽으면 URL이 바뀜 — **절대 재시작 금지**. 서버는 죽어도 위 커맨드로 재기동하면 같은 URL 복구. 감시 루프가 30초마다 자동 재기동 중. caffeinate 가동 중 — **맥북 뚜껑 열어둘 것**(심사위원이 밤에 열람 가능).

## 발표 당일(D-day 오전) 체크리스트

1. **09:00 KST(UTC 자정) OpenSky 크레딧 리셋** 후 원하면 항적 이중화 + 교차확인 복원:
   `SKAI_POLL_SOURCES=opensky,adsbfi,gdelt,metar,celestrak SKAI_CROSSCHECK=live ...` 로 재기동 (URL 불변).
2. 공개 URL 열어 항적·기상 5곳·군용 3건([합성] 배지)·뉴스 확인.
3. 무대 시연은 별도 로컬로: 결정적 재현이 필요하면 `demo.sh replay`(다른 포트 or 터널 서버 그대로 두고 로컬 브라우저), LLM 서술 시연은 `SKAI_COPILOT_LLM=claude`.
4. Foundry 스텝 ⑥: 사전 로그인 + `scripts/demo_foundry.sh` 리허설 1회 (demo.md §1 ⑥).
5. 리허설 딥링크 3개(demo.md §0) 클릭 확인.
