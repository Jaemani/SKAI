"""server/offline_guard.py — 네트워크 0 증명용 소켓 가드 (P6 replay).

replay 데모는 "네트워크 호출 0"이 성공기준이다. 구조상 replay 서버는 커넥터를
import하지 않으므로 외부 fetch 경로 자체가 없지만(server.app는 store-read 전용),
그 사실을 **런타임으로 증명**하기 위해 소켓 레벨 가드를 둔다.

`SKAI_OFFLINE`가 참이면 `socket.socket.connect/connect_ex`를 감싸 **루프백 이외의
모든 외부 연결 시도를 차단·기록**한다(TLE 캐시·OpenSky·GDELT·METAR 등 모든 httpx
egress 포함). 루프백(127.0.0.0/8·::1·localhost)과 UNIX 소켓은 허용한다 —
uvicorn 자체 리스닝·로컬 IPC는 네트워크 egress가 아니다.

가드가 무엇도 차단하지 않았다는 것(blocked_attempts == 0)이 곧 "외부 요청 0"의 증거다.
반대로 무언가 외부로 나가려 하면 즉시 예외로 실패하고 stderr에 남는다(은폐 불가).
"""

from __future__ import annotations

import ipaddress
import os
import socket
import sys

# 차단된 외부 연결 시도 누적 카운터(테스트·검증 로그에서 읽는다). 0 = 외부 요청 0 증명.
blocked_attempts: int = 0
_installed: bool = False

# 루프백 호스트 허용 목록(문자열 비교용 — 이름 해석 없이 판정).
_LOOPBACK_NAMES = {"localhost", "localhost.localdomain", "", "0.0.0.0", "::"}


def _is_loopback(address) -> bool:
    """connect 대상 주소가 루프백/로컬이면 True(허용). 외부면 False(차단)."""
    # AF_UNIX 등 문자열 주소 = 로컬 IPC → 허용.
    if isinstance(address, str):
        return True
    if not isinstance(address, (tuple, list)) or not address:
        # 알 수 없는 주소 형태는 보수적으로 차단(외부 취급).
        return False
    host = address[0]
    if not isinstance(host, str):
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # 호스트명(외부 도메인) → 오프라인 모드에선 차단.
        return False


class OfflineViolation(OSError):
    """SKAI_OFFLINE에서 외부 연결을 시도했을 때 발생(네트워크 0 위반 신호)."""


def is_offline() -> bool:
    return os.environ.get("SKAI_OFFLINE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def install_offline_guard(*, force: bool = False) -> bool:
    """SKAI_OFFLINE가 참이면 소켓 connect 가드를 설치한다. 반환: 설치 여부.

    force=True면 환경변수와 무관하게 설치(테스트용). 멱등 — 두 번 설치하지 않는다.
    """
    global _installed
    if _installed:
        return True
    if not (force or is_offline()):
        return False

    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def _guarded_connect(self, address, *args, **kwargs):
        if not _is_loopback(address):
            global blocked_attempts
            blocked_attempts += 1
            msg = f"[offline-guard] 외부 연결 차단: {address!r} (SKAI_OFFLINE)"
            print(msg, file=sys.stderr, flush=True)
            raise OfflineViolation(msg)
        return real_connect(self, address, *args, **kwargs)

    def _guarded_connect_ex(self, address, *args, **kwargs):
        if not _is_loopback(address):
            global blocked_attempts
            blocked_attempts += 1
            print(
                f"[offline-guard] 외부 연결 차단(ex): {address!r} (SKAI_OFFLINE)",
                file=sys.stderr,
                flush=True,
            )
            return 111  # ECONNREFUSED — 호출자에게 연결 거부로 보고
        return real_connect_ex(self, address, *args, **kwargs)

    socket.socket.connect = _guarded_connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _guarded_connect_ex  # type: ignore[method-assign]
    _installed = True
    print(
        "[offline-guard] 활성 — 루프백만 허용, 외부 egress 차단(네트워크 0 모드)",
        file=sys.stderr,
        flush=True,
    )
    return True
