# 데모 공개 URL — 배포 경로 4가지

`Dockerfile.demo`가 replay(네트워크 0·결정적·SQLite) 모드만 컨테이너화한다. 컨테이너
시작 시 `docker-entrypoint-demo.sh`가 `scripts/demo.sh replay`와 동일하게 데모 DB를
재생성 → now 앵커 고정 → SKAI_OFFLINE=1로 서버를 기동한다. **로컬에 Docker/flyctl/
railway CLI 전부 미설치 확인됨(2026-07-05)** — (a)(b)(c)는 각 플랫폼이 원격으로 이미지를
빌드하므로 로컬 Docker 없이도 배포 가능하다. (d) cloudflared는 로컬에 이미 설치돼 있고
이 프로젝트에서 어제(2026-07-04) 실제 사용 이력이 있다(`data/cloudflared.log`) — **마감이
촉박하면 이 경로가 가장 빠르다.**

## (d) cloudflared 터널 — 가장 빠름 (~2분, 계정 불필요)

로컬에서 서버를 띄우고 순간 터널만 뚫는다. Docker/계정/빌드 대기 전부 불필요.

```bash
scripts/demo.sh replay                        # 로컬 :8000 기동(이미 검증됨)
cloudflared tunnel --url http://localhost:8000  # 무작위 *.trycloudflare.com URL 발급
```

주의: URL은 이 cloudflared 프로세스와 로컬 서버가 켜져 있는 동안만 유효(임시·무료
quick tunnel). 발표 중 노트북이 슬립/네트워크 전환되면 끊긴다 — 발표 직전 재기동 권장.
심사위원 다수가 동시 접속해 confirm/dismiss를 누르면 상태 공유됨(데모 특성상 허용,
README에 명시할 것). 재기동 시 DB가 초기화되어 이상징후 상태(confirmed/dismissed)도 리셋.

## (a) Fly.io (~10–15분, flyctl 설치 필요)

```bash
brew install flyctl && fly auth login
fly launch --dockerfile Dockerfile.demo --no-deploy   # fly.toml 생성, 앱 이름 지정
fly deploy                                             # 원격 빌드(로컬 Docker 불필요)
```
**주의(Render/Railway와 다름):** Fly는 `$PORT`를 자동 주입하지 않는다. 대신 `fly.toml`의
`internal_port`를 Dockerfile의 `EXPOSE`(여기선 8000)에서 자동 유추한다 — entrypoint
기본값도 8000이라 별도 설정 없이 맞아떨어지지만, 명시하려면 `fly.toml`에
`internal_port = 8000`을 확인하고 `[[services]]` 섹션의 포트도 8000으로 일치시킬 것.
무료 티어 1개 인스턴스로 충분.

## (b) Render (~10분, GitHub 연결만 하면 CLI 불필요)

1. Render 대시보드 → New → Web Service → 이 GitHub 저장소 선택.
2. Environment: **Docker**, Dockerfile Path: `Dockerfile.demo`.
3. Instance Type: Free(콜드스타트 있음) 또는 Starter.
4. Create Web Service → 자동 빌드·배포(로그에서 진행 확인). Render가 `$PORT`를 주입.

## (c) Railway (~10분, CLI 또는 웹 대시보드)

```bash
npm i -g @railway/cli && railway login
railway init && railway up                 # 저장소 루트에서 원격 빌드
```
또는 웹 대시보드 → New Project → Deploy from GitHub repo → Settings에서
Dockerfile Path를 `Dockerfile.demo`로 지정. Railway도 `$PORT`를 자동 주입.

## 공통 참고

- Render·Railway는 `$PORT`를 자동 주입하고 entrypoint가 이를 최우선으로 존중한다.
  Fly는 `$PORT`를 주입하지 않지만 Dockerfile `EXPOSE 8000` → `internal_port` 자동
  유추와 entrypoint 기본값(8000)이 일치해 셋 다 코드 수정 없이 그대로 배포된다.
- `SKAI_DEMO_ANCHOR`를 배포 환경변수로 고정하면(현재 값: `eval.run_eval.EVAL_NOW`,
  미상이면 1783000000 폴백) 매 재기동마다 동일한 now로 재현 가능.
- 컨테이너 재기동 = DB 초기화(의도된 동작, README에 고지 필요).
- 저장소 원격(`origin` = `github.com/Jaemani/SKAI`)은 이미 연결돼 있음. 단 이 문서가
  만든 신규 파일(Dockerfile.demo·requirements-demo.txt·docker-entrypoint-demo.sh)은
  **아직 커밋·푸시 안 됨**(지시에 따라 이번 세션에서 커밋하지 않음). Render의 GitHub
  연결 배포는 이 파일들이 GitHub에 반영돼 있어야 하므로 배포 전 커밋·푸시가 선행돼야
  한다. Fly(`fly deploy`)·Railway(`railway up`) CLI 경로는 로컬 디렉터리를 직접
  업로드하므로 GitHub 반영과 무관하게 즉시 가능.
