"""server/live_status.py — 라이브 폴러 상태 사이드카(프론트 LIVE 인디케이터용).

DR-0011: 실시간 = 지속 폴링. 폴러(프로세스 A)와 서버(프로세스 B)는 DB 파일만 공유하므로,
폴러의 "마지막 폴링 시각·상태"를 서버가 읽을 수 있게 **DB 옆 JSON 사이드카**로 남긴다.
온톨로지가 아니라 운영 메타데이터라 store 스키마를 건드리지 않는다(경계 유지).

replay 모드는 폴러가 없어 사이드카가 없다 → read_status가 None을 돌려 서버는 LIVE 아님
(=재생/정적)으로 표시한다. 원자적 write(임시파일+rename)로 부분 읽기를 막는다.

이 모듈은 os/json/pathlib만 의존한다(httpx·커넥터·store 미의존) — 서버가 import해도
replay의 "커넥터 미import" 불변(offline_guard 근거)을 깨지 않는다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional


def status_path(db_path: str) -> Path:
    """DB 경로 → 사이드카 경로(<db>.live.json). :memory:는 CWD 임시 파일로."""
    if db_path == ":memory:":
        return Path("skai_memory.live.json")
    return Path(db_path + ".live.json")


def write_status(db_path: str, **fields) -> None:
    """폴러 상태를 원자적으로 기록(임시파일 write 후 rename)."""
    path = status_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(fields, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)  # 원자적 교체 — 서버가 반쪽 파일을 읽지 않게


def read_status(db_path: str) -> Optional[dict]:
    """사이드카를 읽어 dict 반환(없거나 깨졌으면 None → LIVE 아님으로 처리)."""
    path = status_path(db_path)
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def clear_status(db_path: str) -> None:
    """사이드카 제거(폴러 종료 시 선택적). 없으면 무시."""
    try:
        status_path(db_path).unlink(missing_ok=True)
    except OSError:
        pass
