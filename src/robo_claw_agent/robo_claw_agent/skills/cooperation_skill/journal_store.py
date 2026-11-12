"""협동 저널의 비동기 SQLite 저장소.

저널은 실행 경로를 막지 않아야 하므로, 저장은 단일 스레드가 맡고 호출자는 큐에
넣기만 한다. 큐가 가득 차면 버린 건수를 세고, 저장 오류는 마지막 오류 문자열과
실패 건수로 노출한다. 보존 행 수를 넘는 오래된 이벤트는 같은 트랜잭션에서 지운다.

소유권:
- connection 은 writer 스레드만 소유하고 스레드가 종료할 때 스스로 닫는다.
  ``close`` 는 종료 신호만 보내고 join 하므로, 외부 스레드가 열린 connection 을
  닫아 쓰기와 경쟁하는 일이 없다.
- ``close(timeout)`` 안에 끝나지 않으면 ``close_timeout`` 오류와 남은 레코드 drop
  건수를 남긴다(조용히 사라지지 않는다).

권한:
- 새로 만든 디렉터리는 0700, 새로 만든 DB 파일은 0600 으로 조인다. 이미 존재하던
  디렉터리의 권한은 바꾸지 않는다.

보존:
- 재시작 후에도 보존 행이 남아 있다는 것만 보장한다. 이벤트로부터 GUI 상태를
  복원(restore)하는 기능은 없다.

ROS2/네트워크 의존이 없다.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import sqlite3
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .journal import CooperationEvent

logger = logging.getLogger(__name__)

DEFAULT_EVENT_QUEUE_SIZE = 256
DEFAULT_RETENTION_ROWS = 5000

_DIRECTORY_MODE = 0o700
_DATABASE_MODE = 0o600

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cooperation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_id TEXT NOT NULL,
    sequence INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    timestamp REAL NOT NULL,
    robot_id TEXT NOT NULL,
    peer_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    reply_to TEXT NOT NULL,
    status TEXT NOT NULL,
    content TEXT NOT NULL,
    details TEXT NOT NULL
)
"""

_INSERT_SQL = (
    "INSERT INTO cooperation_events ("
    "stream_id, sequence, event_type, timestamp, robot_id, peer_id, session_id,"
    "task_id, message_id, reply_to, status, content, details"
    ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
)

_PRUNE_SQL = (
    "DELETE FROM cooperation_events WHERE id <= ("
    "SELECT COALESCE(MAX(id), 0) - ? FROM cooperation_events)"
)


def dump_details(details: Mapping[str, Any]) -> str:
    """details dict 를 JSON 문자열로 만든다."""
    return json.dumps(dict(details), ensure_ascii=False)


def ensure_private_directory(directory: Path, *, mode: int = _DIRECTORY_MODE) -> None:
    """디렉터리를 만들고, 이번에 새로 만든 것만 ``mode`` 로 조인다."""
    created: list[Path] = []
    current = directory
    while not current.exists():
        created.append(current)
        if current.parent == current:
            break
        current = current.parent
    directory.mkdir(parents=True, exist_ok=True, mode=mode)
    for path in created:
        if not path.is_dir():
            continue
        try:
            os.chmod(path, mode)
        except OSError as exc:  # 권한 변경 실패는 치명적이지 않다.
            logger.debug("[Coop] chmod failed for %s: %s", path, exc)


class SqliteEventWriter:
    """이벤트를 단일 스레드에서 SQLite 로 기록하는 비동기 writer."""

    def __init__(
        self,
        path: Path,
        *,
        stream_id: str,
        retention_rows: int = DEFAULT_RETENTION_ROWS,
        queue_size: int = DEFAULT_EVENT_QUEUE_SIZE,
    ) -> None:
        self._path = path
        self._stream_id = stream_id
        self._retention_rows = max(1, int(retention_rows))
        self._queue: queue.Queue[CooperationEvent | None] = queue.Queue(
            maxsize=max(1, int(queue_size))
        )
        self._lock = threading.Lock()
        self._dropped = 0
        self._written = 0
        self._failures = 0
        self._error: str | None = None
        self._closed = False
        self._connection: sqlite3.Connection | None = None
        self._thread: threading.Thread | None = None
        try:
            ensure_private_directory(self._path.parent)
            is_new_file = not self._path.exists()
            self._connection = sqlite3.connect(str(self._path), check_same_thread=False)
            self._connection.execute(_CREATE_TABLE_SQL)
            self._connection.commit()
            if is_new_file:
                try:
                    os.chmod(self._path, _DATABASE_MODE)
                except OSError as exc:
                    logger.debug("[Coop] chmod failed for %s: %s", self._path, exc)
        except (sqlite3.Error, OSError) as exc:
            self._connection = None
            self._error = f"{type(exc).__name__}: {exc}"
            logger.warning("[Coop] SQLite persistence disabled: %s", self._error)
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="cooperation-journal")
        self._thread.start()

    @property
    def enabled(self) -> bool:
        return self._connection is not None and self._thread is not None

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    @property
    def written(self) -> int:
        with self._lock:
            return self._written

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def pending(self) -> int:
        return self._queue.qsize()

    def submit(self, event: CooperationEvent) -> None:
        """이벤트를 저장 큐에 넣는다. 넣지 못하면 버린 건수를 센다."""
        if not self.enabled or self._closed:
            return
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            with self._lock:
                self._dropped += 1

    def close(self, timeout: float = 2.0) -> None:
        """종료 신호를 보내고 writer 스레드가 스스로 connection 을 닫을 때까지 기다린다."""
        self._closed = True
        thread = self._thread
        if thread is None or not thread.is_alive():
            return
        self._signal_shutdown()
        thread.join(timeout=max(0.0, timeout))
        if thread.is_alive():
            self._count_unflushed_records()

    def _signal_shutdown(self) -> None:
        """큐가 가득 차 있어도 종료 신호를 반드시 넣는다(밀려난 레코드는 drop 카운트)."""
        while True:
            try:
                self._queue.put_nowait(None)
                return
            except queue.Full:
                try:
                    dropped = self._queue.get_nowait()
                except queue.Empty:
                    continue
                if dropped is not None:
                    with self._lock:
                        self._dropped += 1

    def _count_unflushed_records(self) -> None:
        """join 타임아웃 후 남은 레코드를 버린 것으로 세고 오류로 노출한다."""
        remaining = 0
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not None:
                remaining += 1
        with self._lock:
            self._dropped += remaining
            if self._error is None:
                self._error = f"close_timeout: {remaining} record(s) not flushed"
        # Draining pending records also removes the original shutdown sentinel.
        # Restore it so an in-flight write can finish and close its connection.
        self._signal_shutdown()
        logger.warning("[Coop] journal writer close timed out (remaining=%d)", remaining)

    def _run(self) -> None:
        try:
            while True:
                event = self._queue.get()
                if event is None:
                    return
                try:
                    self._write_event(event)
                except Exception as exc:  # noqa: BLE001 - writer 는 죽지 않는다
                    self._record_write_failure(exc)
        finally:
            connection = self._connection
            self._connection = None
            if connection is not None:
                try:
                    connection.close()
                except sqlite3.Error as exc:
                    logger.debug("[Coop] journal connection close failed: %s", exc)

    def _record_write_failure(self, exc: BaseException) -> None:
        connection = self._connection
        if connection is not None:
            try:
                connection.rollback()
            except sqlite3.Error:
                pass
        with self._lock:
            self._failures += 1
            self._error = f"{type(exc).__name__}: {exc}"
        logger.warning("[Coop] SQLite write failed: %s", exc)

    def _write_event(self, event: CooperationEvent) -> None:
        connection = self._connection
        if connection is None:
            return
        payload = event.as_dict()
        connection.execute(
            _INSERT_SQL,
            (
                self._stream_id,
                payload["sequence"],
                payload["event_type"],
                payload["timestamp"],
                payload["robot_id"],
                payload["peer_id"],
                payload["session_id"],
                payload["task_id"],
                payload["message_id"],
                payload["reply_to"],
                payload["status"],
                payload["content"],
                dump_details(payload["details"]),
            ),
        )
        connection.execute(_PRUNE_SQL, (self._retention_rows,))
        connection.commit()
        with self._lock:
            self._written += 1
