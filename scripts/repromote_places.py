#!/usr/bin/env python3
"""레거시 지식 베이스의 장소 항목을 SQLite 구조화 저장소로 승격·이관한다.

사용 예:

    # 1) 무엇이 옮겨질지 먼저 확인 (파일 수정 없음)
    python3 scripts/repromote_places.py --dry-run

    # 2) 실제 반영 (에이전트를 멈추고 실행할 것)
    python3 scripts/repromote_places.py

    # 로봇 컨테이너 안에서 실행할 때
    python3 scripts/repromote_places.py --memory-dir /ros2_ws/agent_workspace/memory

동작:
- `<memory-dir>/memory.json.kb.collections/*.json`(또는 `*.kb.json`)을 namespace 별로 읽고,
  `place_migration.repromote_places`로 장소 후보를 SQLite(`*.structured.db`)에 기록한다.
- 이미 SQLite에 있는 항목은 이름 키 UPSERT로 갱신된다. 다른 장소가 쓰는 이름/별칭과
  충돌하면 건너뛰고 충돌 수를 보고한다.
- 파일을 삭제하지 않는다. JSON 지식 베이스는 그대로 두고 장소만 구조화 저장소로 복사한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from robo_claw_agent.memory_manager.manager import MemoryManager
from robo_claw_agent.memory_manager.place_migration import repromote_places


def _default_memory_dir() -> Path:
    return Path.home() / ".robo_claw"


def _kb_files(memory_dir: Path) -> list[Path]:
    """namespace 별 지식 베이스 JSON 목록을 만든다.

    namespace 디렉터리(`memory.json.kb.collections/`)가 있으면 그 안의 `*.json`,
    없으면 메모리 디렉터리의 `*.kb.json`을 사용한다(구 버전 레이아웃).
    """
    collections = memory_dir / "memory.json.kb.collections"
    if collections.is_dir():
        return sorted(p for p in collections.glob("*.json") if p.is_file())
    return sorted(p for p in memory_dir.glob("*.kb.json") if p.is_file())


def _namespace_from(path: Path) -> str:
    """`former_arm_konjiam--75d7e90c17a0.json` → `former_arm_konjiam`."""
    stem = path.name
    for suffix in (".kb.json", ".json"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem.split("--", 1)[0] if "--" in stem else ""


def _load_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", data) if isinstance(data, dict) else data
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--memory-dir",
        default=str(_default_memory_dir()),
        help="메모리 디렉터리 (기본: ~/.robo_claw)",
    )
    parser.add_argument(
        "--kb",
        help="특정 지식 베이스 JSON 파일만 처리",
    )
    parser.add_argument("--dry-run", action="store_true", help="기록하지 않고 대상 수만 보고")
    args = parser.parse_args(argv)

    memory_dir = Path(args.memory_dir).expanduser()
    if not memory_dir.is_dir():
        print(f"메모리 디렉터리를 찾을 수 없습니다: {memory_dir}", file=sys.stderr)
        return 2

    files = [Path(args.kb).expanduser()] if args.kb else _kb_files(memory_dir)
    if not files:
        print(f"지식 베이스 JSON 을 찾지 못했습니다: {memory_dir}", file=sys.stderr)
        return 1

    total = {"planned": 0, "inserted": 0, "conflicts": 0, "invalid": 0}
    for path in files:
        namespace = _namespace_from(path)
        entries = _load_entries(path)
        memory = MemoryManager(
            storage_path=str(memory_dir / "memory.json"),
            backend="json",
            local_rag_namespace=namespace,
        )
        stats = repromote_places(memory._structured_store, entries, dry_run=args.dry_run)
        for key in total:
            total[key] += stats[key]
        print(
            f"[{namespace or '(root)'}] entries={len(entries)} "
            f"planned={stats['planned']} inserted={stats['inserted']} "
            f"conflicts={stats['conflicts']} invalid={stats['invalid']}"
            f"{' (dry-run)' if args.dry_run else ''}"
        )
    print(
        f"합계: planned={total['planned']} inserted={total['inserted']} "
        f"conflicts={total['conflicts']} invalid={total['invalid']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
