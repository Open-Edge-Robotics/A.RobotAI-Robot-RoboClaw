"""시나리오 테스트 보고서 생성 도메인 로직."""

from __future__ import annotations

from typing import Any

from robo_claw_dashboard.domain.scenario_models import StepResult

STATUS_BADGES: dict[str, str] = {
    "PASS": "✅ PASS",
    "FAIL": "❌ FAIL",
    "WARNING": "⚠️ WARN",
    "WARN": "⚠️ WARN",
    "SKIP": "➖ SKIP",
    "RUNNING": "⏳ RUNNING",
    "PENDING": "⏸ PENDING",
}


def _get_step_attr(step: StepResult | dict[str, Any], field_name: str, default: Any = "") -> Any:
    """StepResult 인스턴스 또는 딕셔너리에서 필드 값을 안전하게 조회한다."""
    if isinstance(step, StepResult):
        return getattr(step, field_name, default)
    if isinstance(step, dict):
        return step.get(field_name, default)
    return default


def summarize_steps(steps: list[StepResult | dict[str, Any]]) -> dict[str, int]:
    """실행된 스텝 결과 목록의 상태별 개수를 요약한다."""
    counts = {
        "total": len(steps),
        "pass": 0,
        "fail": 0,
        "warning": 0,
        "skip": 0,
        "pending": 0,
        "running": 0,
    }

    for step in steps:
        raw_status = str(_get_step_attr(step, "status", "")).strip().upper()
        if raw_status == "PASS":
            counts["pass"] += 1
        elif raw_status == "FAIL":
            counts["fail"] += 1
        elif raw_status in ("WARNING", "WARN"):
            counts["warning"] += 1
        elif raw_status == "SKIP":
            counts["skip"] += 1
        elif raw_status == "PENDING":
            counts["pending"] += 1
        elif raw_status == "RUNNING":
            counts["running"] += 1

    return counts


def build_markdown_report(
    *,
    scenario_name: str,
    robot_name: str,
    environment: str,
    run_id: str,
    started_at: str,
    finished_at: str,
    status: str,
    steps: list[StepResult | dict[str, Any]],
    llm_info: str = "",
) -> str:
    """테스트 시나리오 수행 결과를 마크다운 형식의 보고서 문자열로 생성한다."""
    summary = summarize_steps(steps)

    lines: list[str] = [
        "# RoboClaw 자동화 테스트 보고서",
        "",
        f"- **시나리오명**: {scenario_name}",
        f"- **실행 ID**: {run_id}",
        f"- **대상 로봇 및 환경**: `{robot_name}` (환경: `{environment}`)",
    ]

    if llm_info:
        lines.append(f"- **LLM 설정**: {llm_info}")

    lines.extend(
        [
            f"- **시작 시각**: {started_at}",
            f"- **종료 시각**: {finished_at}",
            f"- **전체 상태**: {status}",
            "",
            "## 요약",
            "",
            (
                f"- 총 테스트: {summary['total']}건 ("
                f"✅ PASS: {summary['pass']}, "
                f"❌ FAIL: {summary['fail']}, "
                f"⚠️ WARNING: {summary['warning']}, "
                f"➖ SKIP: {summary['skip']})"
            ),
            "",
            "## 상세 테스트 항목 결과",
            "",
            "| Step | 테스트 항목 | 상태 | 소요시간(ms) | 상세 내역 |",
            "| :--- | :--- | :---: | :---: | :--- |",
        ]
    )

    for step in steps:
        step_label = str(_get_step_attr(step, "step", "Step 1"))
        name = str(_get_step_attr(step, "name", "테스트"))
        raw_status = str(_get_step_attr(step, "status", "PENDING")).strip().upper()
        badge = STATUS_BADGES.get(raw_status, raw_status)
        duration_ms = _get_step_attr(step, "duration_ms", 0)
        detail = str(_get_step_attr(step, "detail", ""))
        image_path = str(_get_step_attr(step, "image_path", ""))

        detail_clean = detail.replace("\r\n", "<br>").replace("\n", "<br>").replace("|", "\\|")
        if image_path:
            detail_clean += f"<br>이미지: `{image_path}`"

        lines.append(f"| {step_label} | {name} | {badge} | {duration_ms} | {detail_clean} |")

    lines.append("")
    return "\n".join(lines)
