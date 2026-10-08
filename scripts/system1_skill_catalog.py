#!/usr/bin/env python3
"""robo-claw 스킬 카탈로그 추출 (System 1 툴 테스트용).

``src/robo_claw_agent/robo_claw_agent/skills`` 의 스킬 클래스를 AST 로 정적 분석해
이름·카테고리·위험도·필수 인자·설명을 JSON 으로 만든다. ROS 2 없이 실행된다.

    python3 scripts/system1_skill_catalog.py            # validation/system1/skill_catalog.json 갱신
    python3 scripts/system1_skill_catalog.py --check    # 카탈로그가 소스와 같은지 검사(다르면 exit 1)

카테고리는 System 1 의 계층형(카테고리 → 스킬) 판단을 평가하기 위한 분류다.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = ROOT / "src" / "robo_claw_agent" / "robo_claw_agent" / "skills"
DEFAULT_OUT = ROOT / "validation" / "system1" / "skill_catalog.json"

CATEGORIES: dict[str, str] = {
    "info": "로봇 상태(배터리·좌표), 현재 위치 이름, 장소 좌표, 날짜·시간, ROS 토픽 같은 정보 조회",
    "navigation": "장소·좌표로 이동, 상대 이동, 회전, 방향 맞추기, 순찰, 이동 정지, 위치 재추정, 가상 장애물",
    "manipulation": "팔·그리퍼·헤드 제어, 물체 집기·내려놓기, 로봇 모드 전환",
    "perception": "물체 인식·탐지·위치 추정, 거리 측정, 주변 스캔, 물체 감시",
    "vision": "카메라 촬영, 카메라 이미지 분석·설명, 이미지에 표시",
    "map": "지도 캡처·분석·표시, 갈 수 있는 장소 찾기, 미탐사 영역 탐험",
    "knowledge": "기억·지식 저장소(RAG) 검색·저장·삭제·목록·상태, 관찰 기록, 경험 학습",
    "cooperation": "동료 로봇 목록·상태·능력 조회, 작업 위임·전달·방송, 자율 협동",
    "communication": "사용자에게 메신저로 메시지나 파일 보내기",
    "file": "작업 공간의 파일 목록·읽기·쓰기·수정·삭제, 이미지 파일 분석, 스크립트 실행",
    "autonomous": "스스로 판단하는 자율 행동, 조건 반응형 이동·작업과 그 중단",
    "motion": "CLOi 로봇에 등록된 제스처·모션 목록 조회와 실행·정지",
    "safety": "비상 정지와 비상 정지 해제",
    "butler": "Butler 로봇 전용 스크립트 목록 조회와 실행",
}

_MODULE_CATEGORY = {
    "autonomous_skill": "autonomous",
    "tidy_home_skill": "autonomous",
    "butler_skill": "butler",
    "cloid_motion_skill": "motion",
    "cooperate_skill": "cooperation",
    "cooperation_skill": "cooperation",
    "explore_skill": "map",
    "file_skill": "file",
    "hri_skill": "communication",
    "manipulation_skill": "manipulation",
    "map_skill": "map",
    "navigation_skill": "navigation",
    "perception_skill": "perception",
    "vision_skill": "vision",
}

# 모듈 단위 분류가 맞지 않는 스킬
_SKILL_CATEGORY = {
    "get_status": "info",
    "get_datetime": "info",
    "identify_location": "info",
    "get_location": "info",
    "list_topics": "info",
    "ros_command": "info",
    "emergency_stop": "safety",
    "reset_emergency_stop": "safety",
    "rag_status": "knowledge",
    "rag_reindex": "knowledge",
    "rag_add": "knowledge",
    "rag_add_file": "knowledge",
    "rag_delete": "knowledge",
    "rag_search": "knowledge",
    "rag_list": "knowledge",
    "log_observation": "knowledge",
    "reflect_skills": "knowledge",
}

_FIELDS = ("name", "description", "risk_level", "is_internal", "input_schema")


def _class_attrs(node: ast.ClassDef) -> dict[str, Any]:
    attrs: dict[str, Any] = {}
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
            target, value = stmt.targets[0], stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            target, value = stmt.target, stmt.value
        else:
            continue
        if not isinstance(target, ast.Name) or target.id not in _FIELDS:
            continue
        try:
            attrs[target.id] = ast.literal_eval(value)
        except (ValueError, SyntaxError):
            attrs[target.id] = None  # 동적 값(식) — 정적으로 알 수 없음
    return attrs


def _module_key(path: Path) -> str:
    rel = path.relative_to(SKILLS_DIR)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def extract_catalog(skills_dir: Path = SKILLS_DIR) -> dict[str, Any]:
    skills: dict[str, dict[str, Any]] = {}
    for path in sorted(skills_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            attrs = _class_attrs(node)
            name = attrs.get("name")
            if not isinstance(name, str) or not name:
                continue
            module = _module_key(path)
            schema = attrs.get("input_schema")
            if isinstance(schema, dict):
                required = list(schema.get("required") or [])
                properties = list((schema.get("properties") or {}).keys())
            elif schema is None and "input_schema" in attrs:
                required, properties = None, []  # 동적 스키마: 인자 필요 여부를 알 수 없음
            else:
                required, properties = [], []
            description = attrs.get("description")
            skills[name] = {
                "name": name,
                "category": _SKILL_CATEGORY.get(name) or _MODULE_CATEGORY.get(module, "other"),
                "module": module,
                "risk_level": attrs.get("risk_level") or "action",
                "internal": bool(attrs.get("is_internal")),
                "required": required,
                "properties": properties,
                "description": " ".join(description.split())
                if isinstance(description, str)
                else "",
                "source": str(path.relative_to(ROOT)),
            }
    return {
        "_comment": "scripts/system1_skill_catalog.py 가 생성한다. 직접 수정하지 않는다.",
        "categories": CATEGORIES,
        "skills": [skills[k] for k in sorted(skills)],
    }


def is_direct_candidate(skill: dict[str, Any]) -> bool:
    """robo-claw readonly scope 에서 System 1 이 직접 실행할 수 있는 스킬(read + 필수 인자 없음)."""
    return (
        not skill.get("internal")
        and skill.get("risk_level") == "read"
        and skill.get("required") == []
    )


def _dump(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="robo-claw 스킬 카탈로그 추출")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--check", action="store_true", help="파일이 최신인지 검사만 한다")
    args = parser.parse_args(argv)

    text = _dump(extract_catalog())
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != text:
            print(
                f"{args.out} 가 소스와 다릅니다. python3 scripts/system1_skill_catalog.py 로 갱신하세요."
            )
            return 1
        print(f"{args.out} 최신")
        return 0
    args.out.write_text(text, encoding="utf-8")
    catalog = json.loads(text)
    public = [s for s in catalog["skills"] if not s["internal"]]
    direct = [s["name"] for s in public if is_direct_candidate(s)]
    print(
        f"saved {args.out}: {len(catalog['skills'])} skills ({len(public)} public), direct candidates: {direct}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
