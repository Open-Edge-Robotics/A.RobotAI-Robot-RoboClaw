"""Build the FleetControl capability manifest.

The primary source is the agent's registered skills (``query_state``), which carry
risk level and input schema. ``SKILLS.md`` catalog tables are a fallback for when
the agent cannot be reached.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_SKILL = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_CODE = re.compile(r"`([a-z][a-z0-9_]{1,79})`")
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
_SKILL_HEADERS = {"스킬", "스킬명", "skill", "skills", "skill name", "skill_name"}
# Agent SkillManager risk levels -> Maestro capability risk levels.
_RUNTIME_RISK = {"read": "read", "dangerous": "dangerous"}
_MOTION = {"navigate_to", "move_relative", "follow_waypoints", "rotate"}
_DANGEROUS = {"delete_file", "execute_shell", "power_tool"}


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    name: str
    version: str = "1.0"
    risk_level: str = "normal"
    input_schema_json: str = "{}"
    requirements: tuple[str, ...] = ()


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _tables(text: str) -> list[list[str]]:
    """Group consecutive Markdown table lines into tables."""
    tables: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("|"):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _skill_rows(table: list[str]) -> list[tuple[str, list[str]]]:
    """Return (skill name, cells) for each skill row.

    A table with a header row takes names only from its skill column and is skipped
    when it has none (parameter, frame or joint tables). A header-less table keeps the
    legacy rule of using the first code span in each row.
    """
    has_header = len(table) >= 2 and all(
        _SEPARATOR_CELL.fullmatch(cell) for cell in _cells(table[1]) if cell
    )
    if has_header:
        header = [cell.strip("`* ").lower() for cell in _cells(table[0])]
        column = next((i for i, cell in enumerate(header) if cell in _SKILL_HEADERS), None)
        if column is None:
            return []
        rows = []
        for line in table[2:]:
            cells = _cells(line)
            names = _CODE.findall(cells[column]) if column < len(cells) else []
            if names:
                rows.append((names[0], cells))
        return rows
    rows = []
    for line in table:
        names = _CODE.findall(line)
        if names:
            rows.append((names[0], _cells(line)))
    return rows


def build_capabilities(
    skills_file: str,
    runtime_skills: set[str] | None = None,
) -> list[CapabilityManifest]:
    if not skills_file or not Path(skills_file).is_file():
        logger.warning(
            "SKILLS.md not found at %r; FleetControl capabilities will be empty. "
            "Set RC_SKILLS_GUIDE_FILE to an existing file.",
            skills_file,
        )
        return []
    text = Path(skills_file).read_text(encoding="utf-8")
    documented: dict[str, str] = {}
    for table in _tables(text):
        for name, cells in _skill_rows(table):
            if not _SKILL.fullmatch(name):
                continue
            columns = [cell.lower() for cell in cells]
            risk = "read" if "informational" in columns else "normal"
            if name in _MOTION:
                risk = "motion"
            if name in _DANGEROUS:
                risk = "dangerous"
            documented[name] = risk
    if not documented:
        # Per-robot guides are prose about how to use skills (including ones that are
        # unavailable), so their code spans are not a skill catalog.
        logger.warning(
            "No skill catalog table in %r; FleetControl capabilities will be empty.",
            skills_file,
        )
    if runtime_skills is not None:
        documented = {name: risk for name, risk in documented.items() if name in runtime_skills}
    return [
        CapabilityManifest(name=name, risk_level=risk) for name, risk in sorted(documented.items())
    ]


def capabilities_from_skills(skills: list[dict]) -> list[CapabilityManifest]:
    """Convert ``SkillManager.list_skills()`` entries into public capabilities."""
    manifest: dict[str, CapabilityManifest] = {}
    for skill in skills:
        name = str(skill.get("name", ""))
        if skill.get("is_internal") or not _SKILL.fullmatch(name):
            continue
        risk = _RUNTIME_RISK.get(str(skill.get("risk_level", "")), "normal")
        if name in _MOTION and risk == "normal":
            risk = "motion"
        if name in _DANGEROUS:
            risk = "dangerous"
        schema = skill.get("input_schema")
        manifest[name] = CapabilityManifest(
            name=name,
            risk_level=risk,
            input_schema_json=json.dumps(schema, ensure_ascii=False)
            if isinstance(schema, dict)
            else "{}",
        )
    return [manifest[name] for name in sorted(manifest)]
