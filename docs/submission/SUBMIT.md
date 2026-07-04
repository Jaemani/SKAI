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

## 데모 URL 운영 주의 (터널)

- cloudflared quick tunnel → **발표·심사 동안 이 맥북이 켜져 있고 아래 두 프로세스가 살아 있어야 함**:
  - replay 서버: `scripts/demo.sh replay` (localhost:8000)
  - 터널: `cloudflared tunnel --url http://localhost:8000` (로그: `data/cloudflared.log`)
  - 잠자기 방지: `caffeinate -dims` 실행 중
- **터널 프로세스가 죽으면 URL이 바뀐다**(재기동 시 새 랜덤 URL) — 제출 후 터널은 절대 재시작하지 말 것. 서버(demo.sh)는 죽어도 재기동하면 같은 URL로 복구됨(터널이 :8000을 가리키므로).
- 검증 완료: 외부에서 index 200, `POST /api/assess` 질의① 정상 응답 (2026-07-05).
- 심사위원이 confirm/dismiss를 누르면 상태가 공유 DB에 반영됨(재기동 시 초기화) — 데모 성격상 허용.
- 복구 절차(서버만 죽었을 때): `scripts/demo.sh replay` 한 번. (터널·URL 불변)
