#!/usr/bin/env python3
"""네이티브 도구(tool) 호출 vs 기존 JSON 응답 경로 A/B 평가.

두 경로를 **같은 스킬 카탈로그·같은 프롬프트 조립 코드**로 비교한다.

- 기준선(json): 스킬 목록을 프롬프트에 평문으로 넣고, 응답을 JSON envelope
  (``{"skill"|"skills", ...}``) 로 받는다. (``llm_tool_calling=false``)
- 툴(tool): 스킬을 LLM API 의 ``tools`` 파라미터로 등록하고, 네이티브 도구
  호출로 스킬을 선택한다. JSON 파싱이 없다. (``llm_tool_calling=true``)

프롬프트는 ``agent_node.prompts.assemble_system_prompt`` 을, 도구 스펙은
``skills.tool_specs.build_tool_specs`` 을 그대로 사용한다 — 평가용 프롬프트를
따로 만들지 않는다(실제 배포와 어긋나면 측정이 무의미하다).

지표
  - selection accuracy : 기대 스킬을 고른 비율
  - no-tool rate       : 아무 스킬도 고르지 못한 비율 (JSON 경로에서는 "파싱 실패/무응답")
  - prompt tokens      : 프롬프트 평가 토큰 수 (prompt_eval_count)
  - latency            : 요청 왕복 시간(초)

사용법
    # 스킬 로딩에 ROS 환경이 필요하다(ros2 pkg 가 아니라 파이썬 import 경로).
    ./scripts/run_in_ros_env.sh --ws python3 scripts/eval_tool_calling.py \\
        --model qwen3.5:9b --repeats 3 --json-out /tmp/tool_calling_ab.json

    # 한쪽 경로만 재현(프롬프트/페이로드 미리보기 포함)
    ./scripts/run_in_ros_env.sh --ws python3 scripts/eval_tool_calling.py --mode tool --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATION = ROOT / "validation" / "system1"
DEFAULT_CASES = VALIDATION / "tool_calling_cases.jsonl"
DEFAULT_GUIDE = ROOT / "src" / "robo_claw_bringup" / "config" / "SKILLS.stretch3.md"

# 실제 배포(robo_claw.launch.py)와 동일한 기본 스킬 모듈 목록.
DEFAULT_SKILL_MODULES = (
    "robo_claw_agent.skills.navigation_skill",
    "robo_claw_agent.skills.manipulation_skill",
    "robo_claw_agent.skills.perception_skill",
    "robo_claw_agent.skills.system_skill",
    "robo_claw_agent.skills.vision_skill",
    "robo_claw_agent.skills.map_skill",
    "robo_claw_agent.skills.hri_skill",
    "robo_claw_agent.skills.cooperate_skill",
    "robo_claw_agent.skills.cooperation_skill",
    "robo_claw_agent.skills.file_skill",
    "robo_claw_agent.skills.explore_skill",
    "robo_claw_agent.skills.autonomous_skill",
    "robo_claw_agent.skills.tidy_home_skill",
)

# 평가 시 로봇 상태 컨텍스트(실제 실행과 같은 형태).
_ROBOT_STATE_CONTEXT = (
    '[로봇 현재 상태]\n{"pose": {"x": 1.2, "y": 0.4, "yaw": 0.1}, "mode": "idle"}\n\n'
    '[로봇 자가진단 상태 (Health State)]\n{"hardware_connected": true}'
)


def _load_cases(path: Path) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fp:
        for line in fp:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


class _FakeNode:
    """프롬프트 조립/스킬 로딩에 필요한 최소 노드 표면.

    ROS 노드를 띄우지 않고도 실제 코드(``assemble_system_prompt``,
    ``SkillManager``)를 그대로 사용하기 위한 스텁이다.
    """

    def __init__(self, *, backend: str, skills_guide: str, system_prompt: str) -> None:
        self._system_prompt_base_cache = None
        self._tool_prompt_base_cache = None
        self._system_prompt = system_prompt
        self._skills_guide = skills_guide
        self._script_skills_guide = ""
        self._robot_limits = ""
        self._troubleshooting_guide = ""
        self._agent_workspace_dir = ""
        self._startup_knowledge_ctx = ""
        self._robot_soul = ""
        self._skills = None
        self._backend = backend
        self._logger = logging.getLogger("eval_tool_calling")

    def get_logger(self):
        return self._logger

    def get_parameter(self, name):
        class _Param:
            value = ""

        param = _Param()
        if name == "manipulation_backend":
            param.value = self._backend
        return param

    def _skill_lessons_section(self) -> str:
        return ""


def _build_node_and_skills(args) -> tuple[Any, list[dict[str, Any]]]:
    """실제 스킬 카탈로그를 로드하고 프롬프트 조립용 노드를 만든다."""
    sys.path.insert(0, str(ROOT / "src" / "robo_claw_agent"))
    from robo_claw_agent.agent_node import prompts as prompts_mod
    from robo_claw_agent.skills.manager import SkillManager

    guide = ""
    if args.skills_guide and Path(args.skills_guide).exists():
        guide = prompts_mod.load_skills_guide(args.skills_guide)

    node = _FakeNode(
        backend=args.backend,
        skills_guide=guide,
        system_prompt=prompts_mod.load_system_prompt(args.system_prompt_file),
    )
    manager = SkillManager(node=node)
    for module in args.skill_modules:
        manager.load_from_module(module)
    node._skills = manager

    skills = manager.list_skills(include_internal=False)
    return node, skills


def _chat(base_url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def _json_arm(
    base_url: str,
    model: str,
    prompt: str,
    instruction: str,
    options: dict[str, Any],
    think: bool,
    timeout: float,
) -> dict[str, Any]:
    """기준선: JSON envelope 응답 경로 (``llm_tool_calling=false``)."""
    from robo_claw_agent.agent_node.utils import parse_llm_plan

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "system", "content": _ROBOT_STATE_CONTEXT},
            {"role": "user", "content": instruction},
        ],
        "stream": False,
        "think": think,
        "options": options,
        "format": "json",
    }
    started = time.time()
    data = _chat(base_url, payload, timeout)
    elapsed = time.time() - started
    text = (data.get("message") or {}).get("content", "") or ""
    plan = parse_llm_plan(text)
    chosen = plan.skills[0].skill if plan.skills else None
    return {
        "chosen": chosen,
        "num_skills": len(plan.skills),
        "parse_failed": plan.parse_failed,
        "prompt_tokens": data.get("prompt_eval_count"),
        "eval_tokens": data.get("eval_count"),
        "elapsed": round(elapsed, 3),
    }


def _tool_arm(
    base_url: str,
    model: str,
    prompt: str,
    instruction: str,
    tools: list[dict[str, Any]],
    options: dict[str, Any],
    think: bool,
    timeout: float,
) -> dict[str, Any]:
    """신규: 네이티브 도구 호출 경로 (``llm_tool_calling=true``)."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "system", "content": _ROBOT_STATE_CONTEXT},
            {"role": "user", "content": instruction},
        ],
        "stream": False,
        "think": think,
        "options": options,
        "tools": tools,
    }
    started = time.time()
    data = _chat(base_url, payload, timeout)
    elapsed = time.time() - started
    message = data.get("message") or {}
    calls = message.get("tool_calls") or []
    chosen = None
    if calls:
        chosen = ((calls[0] or {}).get("function") or {}).get("name")
    return {
        "chosen": chosen,
        "num_skills": len(calls),
        "parse_failed": False,
        "prompt_tokens": data.get("prompt_eval_count"),
        "eval_tokens": data.get("eval_count"),
        "elapsed": round(elapsed, 3),
    }


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(records)
    if not total:
        return {}
    correct = sum(1 for r in records if r["chosen"] == r["expected_skill"])
    no_tool = sum(1 for r in records if not r["chosen"])
    prompt_tokens = [r["prompt_tokens"] or 0 for r in records]
    latencies = [r["elapsed"] for r in records]
    return {
        "n": total,
        "correct": correct,
        "accuracy": round(correct / total, 4),
        "no_tool_rate": round(no_tool / total, 4),
        "prompt_tokens_avg": round(sum(prompt_tokens) / total, 1),
        "prompt_tokens_max": max(prompt_tokens),
        "latency_avg": round(sum(latencies) / total, 3),
        "latency_max": round(max(latencies), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    parser.add_argument("--model", default=os.environ.get("RC_LLM_MODEL", "qwen3.5:9b"))
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--mode",
        choices=("json", "tool", "both"),
        default="both",
        help="비교할 경로 (기본: both)",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--think", action="store_true", help="think 모드로 요청")
    parser.add_argument("--backend", default="stretch", help="manipulation_backend (stretch|cloid|moveit)")
    parser.add_argument("--skills-guide", default=str(DEFAULT_GUIDE))
    parser.add_argument("--system-prompt-file", default=None)
    parser.add_argument(
        "--skill-modules",
        nargs="*",
        default=list(DEFAULT_SKILL_MODULES),
        help="로딩할 스킬 모듈 (기본: 실제 배포와 동일)",
    )
    parser.add_argument("--json-out", default=None, help="결과 JSON 저장 경로")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="요청 없이 프롬프트/도구 스펙 크기만 출력",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    node, skills = _build_node_and_skills(args)
    from robo_claw_agent.agent_node.prompts import assemble_system_prompt
    from robo_claw_agent.skills.tool_specs import build_tool_specs

    json_prompt = assemble_system_prompt(node, tool_mode=False)
    tool_prompt = assemble_system_prompt(node, tool_mode=True)
    tools = build_tool_specs(skills)

    print(f"스킬 {len(skills)}개")
    print(f"  기준선(json) 프롬프트: {len(json_prompt):,}자")
    print(f"  툴(tool)   프롬프트: {len(tool_prompt):,}자")
    print(f"  도구 스펙 {len(tools)}개")

    if args.dry_run:
        out = ROOT / "build" / "tool_calling_prompts"
        out.mkdir(parents=True, exist_ok=True)
        (out / "prompt_json.txt").write_text(json_prompt, encoding="utf-8")
        (out / "prompt_tool.txt").write_text(tool_prompt, encoding="utf-8")
        (out / "tools.json").write_text(
            json.dumps(tools, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"  프롬프트/스펙 저장: {out}")
        return 0

    cases = _load_cases(Path(args.cases))
    options = {"temperature": args.temperature}
    arms = []
    if args.mode in ("json", "both"):
        arms.append(("json", json_prompt, None))
    if args.mode in ("tool", "both"):
        arms.append(("tool", tool_prompt, tools))

    results: dict[str, list[dict[str, Any]]] = {name: [] for name, _p, _t in arms}
    for case in cases:
        for arm, prompt, arm_tools in arms:
            for repeat in range(args.repeats):
                try:
                    if arm == "json":
                        record = _json_arm(
                            args.base_url,
                            args.model,
                            prompt,
                            case["instruction"],
                            options,
                            args.think,
                            args.timeout,
                        )
                    else:
                        record = _tool_arm(
                            args.base_url,
                            args.model,
                            prompt,
                            case["instruction"],
                            arm_tools,
                            options,
                            args.think,
                            args.timeout,
                        )
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    record = {
                        "chosen": None,
                        "num_skills": 0,
                        "parse_failed": True,
                        "prompt_tokens": 0,
                        "eval_tokens": 0,
                        "elapsed": 0.0,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                record.update(
                    {
                        "arm": arm,
                        "id": case.get("id"),
                        "instruction": case["instruction"],
                        "expected_skill": case.get("expected_skill"),
                        "repeat": repeat,
                    }
                )
                results[arm].append(record)
                mark = "OK  " if record["chosen"] == case.get("expected_skill") else "MISS"
                print(
                    f"{mark} {arm:4s} {case['instruction'][:28]:30s} -> {record['chosen']}",
                    flush=True,
                )

    print("\n===== SUMMARY =====")
    summary = {arm: _summarize(records) for arm, records in results.items()}
    for arm, stats in summary.items():
        if not stats:
            continue
        print(
            f"{arm:5s} 선택정확도 {stats['correct']}/{stats['n']} "
            f"({stats['accuracy'] * 100:.1f}%)  "
            f"무도구응답 {stats['no_tool_rate'] * 100:.1f}%  "
            f"프롬프트토큰 avg={stats['prompt_tokens_avg']:.0f} max={stats['prompt_tokens_max']}  "
            f"지연 avg={stats['latency_avg']:.2f}s"
        )
    if len(summary) == 2:
        json_stats = summary.get("json", {})
        tool_stats = summary.get("tool", {})
        if json_stats and tool_stats:
            print(
                f"\n개선: 선택정확도 {json_stats['accuracy'] * 100:.1f}% → "
                f"{tool_stats['accuracy'] * 100:.1f}% "
                f"({(tool_stats['accuracy'] - json_stats['accuracy']) * 100:+.1f}%p), "
                f"프롬프트토큰 {json_stats['prompt_tokens_avg']:.0f} → "
                f"{tool_stats['prompt_tokens_avg']:.0f} "
                f"({(tool_stats['prompt_tokens_avg'] - json_stats['prompt_tokens_avg']):+.0f})"
            )

    if args.json_out:
        payload = {"summary": summary, "results": results}
        Path(args.json_out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"\n결과 저장: {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
