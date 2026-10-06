"""Persistent robot-side command idempotency and result replay journal."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass


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
    def __init__(self, path: str) -> None:
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
                "FROM fleet_commands WHERE delivered=0 AND status!='RUNNING' ORDER BY updated_at"
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
