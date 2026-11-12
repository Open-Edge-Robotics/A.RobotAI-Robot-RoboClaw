"""Check actual agent navigation evidence at the dashboard boundary."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from robo_claw_agent.agent_node.map_goal.contract import MapGoalContext
from robo_claw_agent.agent_node.map_goal.dispatch import MapGoalNavigationGuard, apply_task_outcome
from robo_claw_agent.agent_node.map_goal.scope import MapGoalScope
from robo_claw_agent.agent_node.map_goal.tracker import get_map_goal_tracker
from robo_claw_agent.skill_manager import SkillResult
from robo_claw_dashboard.domain.map_goal import (
    evidence_from_skill_results,
    merge_records,
    new_record,
)

pytestmark = pytest.mark.integration


def context():
    now = time.time()
    return MapGoalContext(
        request_id="wire-contract-1",
        robot_id="former0047",
        x=-0.25,
        y=0.75,
        frame_id="map",
        geometry_id="a" * 64,
        map_topic="/map",
        created_at=now,
        expires_at=now + 60,
    )


def actual_evidence(scope, status="reached"):
    guard = MapGoalNavigationGuard(SimpleNamespace(node=SimpleNamespace()), scope)
    return guard.evidence(
        status=status, message="fake target result", reason="", cancel_confirmed=False
    )


def local_record(ctx):
    return new_record(
        request_id=ctx.request_id,
        robot_id=ctx.robot_id,
        target={"x": ctx.x, "y": ctx.y, "frame_id": ctx.frame_id},
        geometry_id=ctx.geometry_id,
        created_at=ctx.created_at,
    )


def test_actual_navigation_evidence_can_be_read_without_fabricating_a_target_wrapper():
    ctx = context()
    proof = actual_evidence(MapGoalScope(ctx, "map"))
    assert "target" not in proof
    result = evidence_from_skill_results(
        [
            {
                "skill_name": "navigate_to",
                "code": 0,
                "result_json": json.dumps({"map_goal_request": proof}),
            }
        ],
        request_id=ctx.request_id,
        target=local_record(ctx)["target"],
    )
    assert result is not None
    assert result["status"] == "reached"
    assert result["target"] == local_record(ctx)["target"]
    assert result["execution_started"] is False


@pytest.mark.parametrize("status", ["running", "unknown", "cancel_requested"])
def test_later_transport_updates_cannot_hide_same_goal_authoritative_terminal(status):
    ctx = context()
    local = local_record(ctx)
    local.update(status=status, updated_at=ctx.created_at + 20)
    agent = local_record(ctx)
    agent.update(status="reached", updated_at=ctx.created_at + 10)
    merged = merge_records(local, agent)
    assert merged["status"] == "reached"


def test_readonly_tool_output_cannot_fabricate_navigation_completion():
    ctx = context()
    scope = MapGoalScope(ctx, "map")
    node = SimpleNamespace()
    tracker = get_map_goal_tracker(node)
    tracker.admit(ctx)
    proof = actual_evidence(scope)
    result = apply_task_outcome(
        node,
        scope,
        [SkillResult("get_status", True, "fake status", 0.0, {"map_goal_request": proof})],
        "status only",
    )
    assert result["status"] != "reached"


def test_correlated_validation_rejection_is_not_a_navigation_success():
    from robo_claw_agent.agent_node.map_goal.rejection import rejection_result

    ctx = context()
    request = {
        key: getattr(ctx, key)
        for key in [
            "request_id",
            "robot_id",
            "x",
            "y",
            "frame_id",
            "geometry_id",
            "map_topic",
            "created_at",
            "expires_at",
        ]
    }
    request["type"] = "dashboard_map_goal_v1"
    report = rejection_result(
        SimpleNamespace(),
        SimpleNamespace(request=SimpleNamespace(context_json=json.dumps(request))),
        "expired before execution",
    )
    assert report is not None
    proof = evidence_from_skill_results(
        [
            {
                "skill_name": report.skill_name,
                "code": 1,
                "result_json": json.dumps(report.result_data),
            }
        ],
        request_id=ctx.request_id,
        target=local_record(ctx)["target"],
    )
    assert proof is not None
    assert proof["status"] == "failed"
    assert proof["reason"] == "navigation_not_executed"
    assert proof["execution_started"] is False


def test_missing_result_after_navigation_dispatch_does_not_release_the_lease():
    ctx = context()
    scope = MapGoalScope(ctx, "map")
    node = SimpleNamespace()
    tracker = get_map_goal_tracker(node)
    tracker.admit(ctx)
    scope.claim_dispatch()
    scope.execution_started = True
    tracker.transition(ctx.request_id, "running", execution_started=True)
    result = apply_task_outcome(node, scope, [], "unexpected exception after dispatch")
    assert result["status"] == "unknown"
    assert result["can_submit"] is False
    assert result["cancel_confirmed"] is False


def test_terminal_from_another_robot_is_not_same_goal_evidence():
    ctx = context()
    local = local_record(ctx)
    other = local_record(ctx)
    other.update(robot_id="cloid01", status="reached", updated_at=ctx.created_at + 10)
    assert merge_records(local, other)["status"] != "reached"
