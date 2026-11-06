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
- `<memory-dir>/memory.json.kb.collections/*.json`(또는 `*.kb.json`)을 읽고,
  `place_migration.repromote_places`로 장소 후보를 SQLite(`*.structured.db`)에 기록한다.
- namespace가 바뀐 로봇은 모든 파일을 한 번에 모아 오래된 것부터 처리한다(같은 이름은
  최신 항목이 이긴다). 대상 namespace는 가장 최근에 갱신된 구조화 DB의 namespace이며
  `--target-namespace`로 바꿀 수 있다.
- 기본값은 **기존 값 유지**다(`overwrite=False`). 레거시 좌표가 현재 값보다 오래된
  경우가 많아 이동 목적지가 과거 좌표로 되돌아가는 것을 막는다. 필요하면 `--overwrite`.
- 로봇 라벨(`Former` 등)과 `현재 위치` 같은 지시어 이름은 이관하지 않는다.
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


def _load_entries(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries", data) if isinstance(data, dict) else data
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


def _active_namespace(memory_dir: Path) -> str:
    """가장 최근에 갱신된 구조화 DB의 namespace(=지금 쓰는 namespace)를 고른다."""
    dbs = sorted(
        (memory_dir / "memory.json.kb.collections").glob("*.structured.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return dbs[0].name.split("--", 1)[0] if dbs else ""


def _robot_labels(namespace: str) -> set[str]:
    """namespace에서 로봇 라벨을 얻는다(`former_0047_konjiam` → {former, konjiam})."""
    tokens = namespace.replace("-", "_").replace("--", "_").split("_")
    return {t.lower() for t in tokens if t and not t.isdigit()}


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
    parser.add_argument(
        "--target-namespace",
        help="모든 후보를 기록할 namespace (기본: 가장 최근 구조화 DB의 namespace)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="이미 저장된 이름도 레거시 값으로 갱신 (기본: 기존 값 유지)",
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

    target = args.target_namespace or _active_namespace(memory_dir)
    if not target:
        print(
            "대상 namespace 를 알 수 없습니다. --target-namespace 로 지정하세요.", file=sys.stderr
        )
        return 2

    # namespace가 바뀐 로봇(예: former_0045_konjiam → former_arm_konjiam)은 과거 파일에만
    # 장소가 남아 있다. 모든 파일을 한 번에 모아 오래된 것부터 처리하고, 같은 이름은
    # 최신 항목이 이기게 한다.
    entries: list[dict] = []
    for path in files:
        entries.extend(_load_entries(path))
    entries.sort(key=lambda e: str(e.get("timestamp") or ""))

    memory = MemoryManager(
        storage_path=str(memory_dir / "memory.json"),
        backend="json",
        local_rag_namespace=target,
    )
    stats = repromote_places(
        memory._structured_store,
        entries,
        dry_run=args.dry_run,
        overwrite=args.overwrite,
        exclude_names=_robot_labels(target),
    )
    print(
        f"files={len(files)} entries={len(entries)} target={target} "
        f"labels={sorted(_robot_labels(target))}\n"
        f"planned={stats['planned']} inserted={stats['inserted']} kept={stats['kept']} "
        f"conflicts={stats['conflicts']} invalid={stats['invalid']}"
        f"{' (dry-run)' if args.dry_run else ''}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
