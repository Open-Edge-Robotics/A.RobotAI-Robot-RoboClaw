"""구조화된 장소·좌표·스킬 실행 경험의 SQLite 저장소입니다."""

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid4, uuid5

STRUCTURED_TYPES = frozenset(
    {"location", "object_location", "navigated_coordinate", "blocked_coordinate", "skill_episode"}
)

# 좌표가 필수인 종류(장소·객체 관측·이동/차단 좌표).
_COORD_TYPES = frozenset(
    {"location", "object_location", "navigated_coordinate", "blocked_coordinate"}
)
# 이름이 필수인 종류.
_NAMED_TYPES = frozenset({"location", "object_location"})


def name_key(value):
    return "".join(str(value).split()).casefold()


class StructuredMemoryStore:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS records (
                    id TEXT PRIMARY KEY, entry_type TEXT NOT NULL,
                    skill_name TEXT NOT NULL, success INTEGER,
                    text TEXT NOT NULL, metadata TEXT NOT NULL,
                    timestamp TEXT NOT NULL, x REAL, y REAL
                );
                CREATE INDEX IF NOT EXISTS records_kind_skill_time
                    ON records(entry_type, skill_name, success, timestamp);
                CREATE TABLE IF NOT EXISTS names (
                    record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
                    name_key TEXT NOT NULL, name TEXT NOT NULL,
                    PRIMARY KEY(record_id, name_key)
                );
                CREATE INDEX IF NOT EXISTS names_lookup ON names(name_key);
            """)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def entry(row):
        return {
            "id": row["id"],
            "text": row["text"],
            "metadata": json.loads(row["metadata"]),
            "timestamp": row["timestamp"],
        }

    def add(self, text, metadata):
        metadata = dict(metadata)
        kind = metadata["type"]
        if kind not in STRUCTURED_TYPES:
            raise ValueError("지원하지 않는 구조화 데이터 종류입니다.")
        if metadata.get("mocked"):
            return False
        x, y = metadata.get("x"), metadata.get("y")
        if kind in _COORD_TYPES:
            try:
                if isinstance(x, bool) or isinstance(y, bool):
                    raise ValueError("좌표에 bool을 사용할 수 없습니다.")
                x, y = float(x), float(y)
                if not math.isfinite(x) or not math.isfinite(y):
                    raise ValueError("좌표는 유한한 값이어야 합니다.")
            except (TypeError, ValueError) as exc:
                raise ValueError("올바른 x, y 좌표가 필요합니다.") from exc
            metadata.update(x=x, y=y)
        canonical = str(
            metadata.get("location_name")
            or metadata.get("name")
            or metadata.get("target_name")
            or ""
        ).strip()
        if kind in _NAMED_TYPES and not canonical:
            raise ValueError("장소 이름이 필요합니다.")
        names = [canonical] if canonical else []
        aliases = metadata.get("aliases", [])
        if not isinstance(aliases, list) or any(not isinstance(a, str) for a in aliases):
            raise ValueError("aliases는 문자열 목록이어야 합니다.")
        names += [a.strip() for a in aliases if a.strip()]
        if kind == "location":
            record_id = str(uuid5(NAMESPACE_URL, "place:" + name_key(canonical)))
        elif kind == "object_location":
            # 객체 관측은 같은 이름이면 최신 관측으로 갱신한다(장소 레코드와 키 공간 분리).
            record_id = str(uuid5(NAMESPACE_URL, "object:" + name_key(canonical)))
        else:
            record_id = str(uuid4())
        now = datetime.now().isoformat()
        with self.connect() as conn:
            if kind == "location":
                conn.execute("BEGIN IMMEDIATE")
                keys = [name_key(n) for n in names]
                conflict = conn.execute(
                    "SELECT 1 FROM names n JOIN records r ON r.id=n.record_id "
                    "WHERE r.entry_type='location' AND r.id<>? AND n.name_key IN ("
                    + ",".join("?" for _ in keys)
                    + ") LIMIT 1",
                    [record_id, *keys],
                ).fetchone()
                if conflict:
                    raise ValueError("이미 다른 장소에 등록된 이름 또는 별칭입니다.")
            conn.execute(
                """INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET text=excluded.text, metadata=excluded.metadata,
                timestamp=excluded.timestamp, x=excluded.x, y=excluded.y""",
                (
                    record_id,
                    kind,
                    str(metadata.get("skill_name", "")),
                    int(metadata["success"]) if "success" in metadata else None,
                    text,
                    json.dumps(metadata, ensure_ascii=False),
                    now,
                    x,
                    y,
                ),
            )
            conn.execute("DELETE FROM names WHERE record_id=?", (record_id,))
            conn.executemany(
                "INSERT OR REPLACE INTO names VALUES (?, ?, ?)",
                [(record_id, name_key(n), n) for n in names],
            )
        return True

    def records(
        self,
        *,
        entry_type=None,
        entry_types=None,
        skill_name="",
        success=None,
        limit=None,
        names=None,
    ):
        clauses, values = [], []
        if entry_type:
            clauses.append("r.entry_type=?")
            values.append(entry_type)
        if entry_types:
            kinds = list(entry_types)
            clauses.append("r.entry_type IN (" + ",".join("?" for _ in kinds) + ")")
            values.extend(kinds)
        if skill_name:
            clauses.append("r.skill_name=?")
            values.append(skill_name)
        if success is not None:
            clauses.append("r.success=?")
            values.append(int(success))
        if names is not None:
            keys = [name_key(n) for n in names if name_key(n)]
            if not keys:
                return []
            clauses.append(
                "r.id IN (SELECT record_id FROM names WHERE name_key IN ("
                + ",".join("?" for _ in keys)
                + "))"
            )
            values.extend(keys)
        query = "SELECT r.* FROM records r"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY r.timestamp, r.id"
        if limit is not None:
            if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
                raise ValueError("limit은 0 이상의 정수여야 합니다.")
            query += " LIMIT ?"
            values.append(limit)
        with self.connect() as conn:
            return [self.entry(row) for row in conn.execute(query, values)]

    @staticmethod
    def place(entry, alias=None):
        meta = entry["metadata"]
        canonical = meta.get("location_name") or meta.get("name")
        if alias and name_key(alias) != name_key(canonical):
            meta = {**meta, "alias_of": canonical}
        return {
            "name": alias or canonical,
            "position": {"x": meta["x"], "y": meta["y"]},
            "metadata": meta,
            "last_seen": entry["timestamp"],
        }

    def location(self, name, *, include_objects=False):
        kinds = ("location", "object_location") if include_objects else ("location",)
        entries = self.records(entry_types=kinds, names=[name])
        # 정확한 표준 이름은 같은 이름의 별칭보다 우선합니다.
        entries.sort(
            key=lambda e: (
                name_key(e["metadata"].get("location_name") or e["metadata"].get("name"))
                == name_key(name)
            )
        )
        if not entries:
            return None
        entry = entries[-1]
        canonical = entry["metadata"].get("location_name") or entry["metadata"].get("name")
        matched_name = next(
            (a for a in entry["metadata"].get("aliases", []) if name_key(a) == name_key(name)),
            canonical,
        )
        return self.place(entry, alias=matched_name)

    def locations(self, *, include_objects=False):
        kinds = ("location", "object_location") if include_objects else ("location",)
        result = []
        for entry in self.records(entry_types=kinds):
            result.append(self.place(entry))
            result.extend(self.place(entry, alias=a) for a in entry["metadata"].get("aliases", []))
        return result

    def nearest(self, x, y, radius_m=None):
        try:
            x, y = float(x), float(y)
            radius = None if radius_m is None else float(radius_m)
            if (
                not math.isfinite(x)
                or not math.isfinite(y)
                or (radius is not None and (not math.isfinite(radius) or radius < 0))
            ):
                return None
        except (TypeError, ValueError):
            return None
        with self.connect() as conn:
            row = conn.execute(
                """SELECT *, (x-?)*(x-?)+(y-?)*(y-?) AS distance2
                FROM records WHERE entry_type='location' ORDER BY distance2 LIMIT 1""",
                (x, x, y, y),
            ).fetchone()
        if row is None or (radius is not None and row["distance2"] > radius * radius):
            return None
        return {**self.place(self.entry(row)), "distance_m": round(math.sqrt(row["distance2"]), 3)}

    def delete(self, *, ids=None, source_path="", text_exact=""):
        clauses, values = [], []
        if ids:
            clauses.append("id IN (" + ",".join("?" for _ in ids) + ")")
            values.extend(ids)
        if source_path:
            clauses.append("json_extract(metadata, '$.source_path')=?")
            values.append(source_path)
        if text_exact:
            clauses.append("text=?")
            values.append(text_exact)
        if not clauses:
            return 0
        with self.connect() as conn:
            return conn.execute(
                "DELETE FROM records WHERE " + " OR ".join(clauses), values
            ).rowcount

    def clear(self):
        with self.connect() as conn:
            conn.execute("DELETE FROM records")

    def count(self):
        with self.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
