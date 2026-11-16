"""Persistent robot-side command idempotency and result replay journal.

보존 정책:
- 행 상한을 두고 insert 할 때마다 오래된 행을 같은 transaction 에서 지운다. 상한이
  없으면 Maestro 가 계속 명령을 보낼 때 DB 파일이 무한히 자란다.
- 중복 차단은 보존 범위 안에서만 보장한다. 상한을 넘겨 밀려난 오래된 idempotency
  key 는 더 이상 조회되지 않으므로, 그 key 로 다시 명령이 오면 재실행될 수 있다.
  보존 행 수는 이 재실행 창(window)과 저장 크기의 trade-off 다.
- 미전달 결과 조회도 상한을 둔다. 재연결 직후 전량을 한 번에 보내면 burst 가
  생기므로 최근 N 건만 보낸다. Maestro 는 TaskUpdate 를 중복 제거하므로, 최신을
  우선 보내야 재연결후 진행 상황이 바로 복구된다(오래된 결과는 보존 상한으로 사라진다).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass

# 5000 건은 일반 운용에서 재연결 replay 창으로 충분하고(명령 1건당 1행),
# SQLite 파일도 수 MB 이내로 유지된다. 협동 저널(공용 기본값)과 같은 값이다.
DEFAULT_RETENTION_ROWS = 5000

# 재연결 1회당 replay 상한. 남은(더 오래된) 결과는 다음 재연결에서 보내거나 보존
# 상한에 밀려 사라진다.
DEFAULT_UNDELIVERED_LIMIT = 500


@dataclass(frozen=True, slots=True)
class JournalResult:
    idempotency_key: str
    command_id: str
    task_id: str
    status: str
    message: str
    result_json: str
    error_code: str
    delivered: bool


class CommandJournal:
    def __init__(
        self,
        path: str,
        *,
        retention_rows: int = DEFAULT_RETENTION_ROWS,
        undelivered_limit: int = DEFAULT_UNDELIVERED_LIMIT,
    ) -> None:
        self._retention_rows = max(1, int(retention_rows))
        self._undelivered_limit = max(1, int(undelivered_limit))
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS fleet_commands (
            idempotency_key TEXT PRIMARY KEY, command_id TEXT NOT NULL,
            task_id TEXT NOT NULL, status TEXT NOT NULL, message TEXT NOT NULL,
            result_json TEXT NOT NULL, error_code TEXT NOT NULL,
            delivered INTEGER NOT NULL DEFAULT 0, updated_at REAL NOT NULL)"""
        )
        self._db.commit()

    def get(self, key: str) -> JournalResult | None:
        with self._lock:
            row = self._db.execute(
                "SELECT idempotency_key,command_id,task_id,status,message,result_json,error_code,delivered "
                "FROM fleet_commands WHERE idempotency_key=?",
                (key,),
            ).fetchone()
        return JournalResult(*row[:-1], bool(row[-1])) if row else None

    def begin(self, key: str, command_id: str, task_id: str) -> bool:
        with self._lock:
            try:
                self._db.execute(
                    "INSERT INTO fleet_commands VALUES (?,?,?,?,?,?,?,?,?)",
                    (key, command_id, task_id, "RUNNING", "", "{}", "", 0, time.time()),
                )
                self._db.execute(
                    "DELETE FROM fleet_commands WHERE rowid NOT IN ("
                    "SELECT rowid FROM fleet_commands ORDER BY rowid DESC LIMIT ?)",
                    (self._retention_rows,),
                )
                self._db.commit()
                return True
            except sqlite3.IntegrityError:
                self._db.rollback()
                return False

    def complete(
        self,
        key: str,
        *,
        status: str,
        message: str,
        result: dict,
        error_code: str = "",
    ) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE fleet_commands SET status=?,message=?,result_json=?,error_code=?,delivered=0,updated_at=? "
                "WHERE idempotency_key=?",
                (status, message, json.dumps(result), error_code, time.time(), key),
            )
            self._db.commit()

    def undelivered(self) -> list[JournalResult]:
        with self._lock:
            rows = self._db.execute(
                "SELECT idempotency_key,command_id,task_id,status,message,result_json,error_code,delivered "
                "FROM fleet_commands WHERE delivered=0 AND status!='RUNNING' "
                "ORDER BY updated_at DESC,rowid DESC LIMIT ?",
                (self._undelivered_limit,),
            ).fetchall()
        return [JournalResult(*row[:-1], bool(row[-1])) for row in rows]

    def mark_delivered(self, key: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE fleet_commands SET delivered=1,updated_at=? WHERE idempotency_key=?",
                (time.time(), key),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
