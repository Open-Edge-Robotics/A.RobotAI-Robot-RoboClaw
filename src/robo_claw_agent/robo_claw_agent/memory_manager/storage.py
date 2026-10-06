import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class StorageMixin:
    """디스크 I/O (SQLite / JSON) 전담"""

    # Manager에서 초기화될 상태 변수들에 대한 힌트
    _backend: str
    _storage_path: str
    _event_path: Path
    _long_term: list[dict[str, Any]]

    def _save_data(self, path: Path, data: Any) -> None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error("Failed to save (%s): %s", path, e)

    def _load_json(self, path: Path) -> Any:
        if not path.exists():
            return []
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error("Failed to load (%s): %s", path, e)
            return []

    def _persist_event(self, entry: dict[str, Any]) -> None:
        if self._backend == "json":
            self._long_term.append(entry)
            self._save_data(self._event_path, self._long_term)
            return

        with sqlite3.connect(self._storage_path) as conn:
            conn.execute(
                "INSERT INTO events (timestamp, event_type, data_json) VALUES (?, ?, ?)",
                (
                    entry["timestamp"],
                    entry["type"],
                    json.dumps(entry["data"], ensure_ascii=False),
                ),
            )
            conn.commit()

    def _get_persisted_events(
        self, limit: int | None = None
    ) -> list[dict[str, Any]]:
        if self._backend == "json":
            return self._long_term if limit is None else self._long_term[-limit:]

        query = (
            "SELECT timestamp, event_type, data_json FROM events ORDER BY id DESC"
            if limit is None
            else "SELECT timestamp, event_type, data_json FROM events ORDER BY id DESC LIMIT ?"
        )
        params: tuple[Any, ...] = () if limit is None else (limit,)
        with sqlite3.connect(self._storage_path) as conn:
            rows = conn.execute(query, params).fetchall()

        events = [
            {
                "timestamp": row[0],
                "type": row[1],
                "data": json.loads(row[2]),
            }
            for row in rows
        ]
        events.reverse()
        return events

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self._storage_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    data_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def _count_sqlite(self) -> int:
        if self._backend != "sqlite":
            return 0
        with sqlite3.connect(self._storage_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0]) if row else 0
