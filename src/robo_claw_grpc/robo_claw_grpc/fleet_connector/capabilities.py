"""Build a structured capability manifest from SKILLS.md and runtime skills."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_SKILL = re.compile(r"^[a-z][a-z0-9_]{1,79}$")
_CODE = re.compile(r"`([a-z][a-z0-9_]{1,79})`")
_MOTION = {"navigate_to", "move_relative", "follow_waypoints", "rotate"}
_DANGEROUS = {"delete_file", "execute_shell", "power_tool"}


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    name: str
    version: str = "1.0"
    risk_level: str = "normal"
    input_schema_json: str = "{}"
    requirements: tuple[str, ...] = ()


def build_capabilities(
    skills_file: str,
    runtime_skills: set[str] | None = None,
) -> list[CapabilityManifest]:
    if not skills_file or not Path(skills_file).is_file():
        return []
    text = Path(skills_file).read_text(encoding="utf-8")
    documented: dict[str, str] = {}
    for line in text.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        names = _CODE.findall(line)
        if not names:
            continue
        name = names[0]
        if not _SKILL.fullmatch(name):
            continue
        columns = [column.strip().lower() for column in line.strip().strip("|").split("|")]
        risk = "read" if "informational" in columns else "normal"
        if name in _MOTION:
            risk = "motion"
        if name in _DANGEROUS:
            risk = "dangerous"
        documented[name] = risk
    if not documented:
        for name in _CODE.findall(text):
            if _SKILL.fullmatch(name):
                documented.setdefault(name, "normal")
    if runtime_skills is not None:
        documented = {name: risk for name, risk in documented.items() if name in runtime_skills}
    return [
        CapabilityManifest(name=name, risk_level=risk) for name, risk in sorted(documented.items())
    ]
