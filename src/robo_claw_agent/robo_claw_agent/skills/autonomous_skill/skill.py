import logging
import time
from typing import Any

from robo_claw_agent.skill_manager import BaseSkill

from .action_nodes import SkillChainNode
from .actions import (
    AnalyzeCurrentScene,
    CheckGoalAchieved,
    EmergencyLowBattery,
    EnsurePlacesRegistered,
    ExecuteDecidedAction,
    GoalPlannerNode,
    LLMDecideAction,
    LocalObservationNode,
    LogObservationNode,
    RefreshPeerStatusCache,
    ResolveCleanupHandoff,
    SleepBetweenCycles,
    ValidateDecidedAction,
)
from .bt_runner import run_reactive_tick_loop, start_bt_thread, wire_blackboard
from .conditions import CheckAutonomousActive, CheckBatterySufficient
from .core import BTNode, FallbackNode, NodeStatus, ParallelNode, SequenceNode
from .globals import _AUTONOMOUS_ACTIVE, claim_autonomous, release_autonomous
from .navigation_nodes import ApproachObjectNode, ExploreNode, NavigateNode
from .patrol_nodes import InterestingSceneGate, PatrolNextPlaceNode
from .perception_nodes import MonitorObjectNode

logger = logging.getLogger(__name__)


class ReactiveNavigateSkill(BaseSkill):
    """
    지정된 목적지로 이동하면서 특정 객체(예: 사람)가 발견되면
    즉시 이동을 중단하고 해당 객체로 접근하는 반응형 이동 스킬.
    """

    name = "reactive_navigate"
    terminal_behavior = "background"
    description = (
        "목적지(target_location)로 이동하는 도중 특정 객체(interrupt_object)가 발견되면, "
        "즉시 기존 이동을 취소하고 발견된 객체 방향으로 접근합니다. "
        "예: {'target_location': '거실', 'interrupt_object': 'person'} "
        "중단: stop_autonomous 스킬을 사용하세요."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "target_location": {"type": "string"},
            "interrupt_object": {"type": "string", "default": "person"},
        },
        "required": ["target_location"],
        "additionalProperties": False,
    }
    side_effects = ("base_motion", "background_loop")

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        if not claim_autonomous(_AUTONOMOUS_ACTIVE):
            return {
                "success": False,
                "message": "이미 자율 행동 또는 반응형 이동이 실행 중입니다. stop_autonomous로 먼저 중단하세요.",
            }

        target_location = params.get("target_location", "")
        interrupt_object = params.get("interrupt_object", "person")

        if not target_location:
            return {"success": False, "message": "target_location 파라미터가 필요합니다."}

        def _run_bt_loop() -> None:
            blackboard: dict[str, Any] = {}

            check_active = CheckAutonomousActive()
            monitor_node = MonitorObjectNode(self, interrupt_object)
            nav_node = NavigateNode(self, target_location=target_location)

            parallel_branch = ParallelNode(
                "MonitorOrNavigate", [monitor_node, nav_node], success_count=1
            )

            approach_node = ApproachObjectNode(self)

            def check_interrupted() -> NodeStatus:
                if blackboard.get("detected_target_pos"):
                    return NodeStatus.SUCCESS
                return NodeStatus.FAILURE

            check_interrupted_node = BTNode("CheckInterrupted")
            check_interrupted_node.tick = check_interrupted
            check_interrupted_node._blackboard = blackboard

            reactive_sequence = SequenceNode(
                "ReactiveSequence", [check_interrupted_node, approach_node]
            )

            root = SequenceNode(
                "ReactiveRoot",
                [
                    check_active,
                    parallel_branch,
                    FallbackNode("HandleResult", [reactive_sequence, BTNode("Done")]),
                ],
            )
            root.children[2].children[1].tick = lambda: NodeStatus.SUCCESS

            wire_blackboard(
                [
                    check_active,
                    monitor_node,
                    nav_node,
                    parallel_branch,
                    approach_node,
                    reactive_sequence,
                    root,
                ],
                blackboard,
            )

            logger.info(
                "[ReactiveNavigate] BT loop started: moving to %s while monitoring %s",
                target_location,
                interrupt_object,
            )
            self.send_user_message(
                f"📍 {target_location}(으)로 이동을 시작합니다. 이동 중 {interrupt_object}(을)를 발견하면 즉시 접근할게요."
            )

            run_reactive_tick_loop(root, _AUTONOMOUS_ACTIVE, log_prefix="[ReactiveNavigate]")

            _AUTONOMOUS_ACTIVE.clear()
            logger.info("[ReactiveNavigate] BT loop ended")
            if blackboard.get("detected_target_pos"):
                self.send_user_message(f"✅ {interrupt_object} 발견 및 접근을 완료했습니다.")
            else:
                self.send_user_message(f"✅ {target_location} 이동을 완료했습니다.")

        start_bt_thread(_run_bt_loop)

        return {
            "success": True,
            "message": f"{target_location} 이동 및 {interrupt_object} 감시를 시작했습니다.",
        }


class ConditionReactiveSkill(BaseSkill):
    """
    포그라운드 태스크(이동 또는 탐험)를 수행하면서 특정 객체가 감지되면
    즉시 포그라운드를 중단하고 지정된 스킬 체인을 실행하는 조건부 반응 스킬.

    foreground_task: "navigate" | "explore"
    foreground_params: foreground_task에 전달할 파라미터 (navigate 시 target_location 필요)
    trigger_object: 감시 대상 객체명 (COCO 클래스명)
    on_trigger_skills: 트리거 시 순차 실행할 스킬 목록
                       [{"skill": "approach_object", "params": {...}}, ...]
    min_score: 감지 신뢰도 임계값 (기본 0.5)
    """

    name = "condition_reactive"
    terminal_behavior = "background"
    description = (
        "포그라운드 태스크(이동 또는 탐험)를 수행하면서 특정 객체(trigger_object)가 감지되면 "
        "즉시 포그라운드를 중단하고 on_trigger_skills로 지정된 스킬 체인을 실행합니다. "
        "예: '탐험 중 화분 발견 시 접근 후 이미지 전송' — "
        "{'foreground_task': 'explore', 'trigger_object': 'potted plant', "
        "'on_trigger_skills': [{'skill': 'approach_object', 'params': {}}, {'skill': 'capture_camera_image', 'params': {}}]}. "
        "navigate 시: foreground_params에 target_location 또는 x, y 필요. "
        "중단: stop_autonomous 스킬을 사용하세요."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "foreground_task": {"type": "string", "enum": ["navigate", "explore"]},
            "foreground_params": {"type": "object"},
            "trigger_object": {"type": "string"},
            "on_trigger_skills": {"type": "array", "items": {"type": "object"}},
            "min_score": {"type": "number", "default": 0.5, "minimum": 0.0, "maximum": 1.0},
        },
        "required": ["foreground_task", "foreground_params", "trigger_object", "on_trigger_skills"],
        "additionalProperties": False,
    }
    side_effects = ("base_motion", "background_loop", "skill_chain")

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        if not claim_autonomous(_AUTONOMOUS_ACTIVE):
            return {
                "success": False,
                "message": "이미 자율 행동 또는 반응형 태스크가 실행 중입니다. stop_autonomous로 먼저 중단하세요.",
            }

        foreground_task = self.get_string_param(params, "foreground_task", "navigate").lower()
        foreground_params: dict[str, Any] = params.get("foreground_params", {})
        trigger_object = self.get_string_param(params, "trigger_object", "")
        on_trigger_skills = params.get("on_trigger_skills", [])
        min_score = float(params.get("min_score", 0.5))

        if not trigger_object:
            return {"success": False, "message": "trigger_object 파라미터가 필요합니다."}
        if not on_trigger_skills:
            return {"success": False, "message": "on_trigger_skills 파라미터가 필요합니다."}
        if foreground_task not in ("navigate", "explore"):
            return {
                "success": False,
                "message": f"지원하지 않는 foreground_task: '{foreground_task}'. 'navigate' 또는 'explore'를 사용하세요.",
            }

        def _run_bt_loop() -> None:
            blackboard: dict[str, Any] = {}

            if foreground_task == "navigate":
                target_location = foreground_params.get("target_location", "")
                x = foreground_params.get("x")
                y = foreground_params.get("y")
                fg_node: BTNode = NavigateNode(
                    self,
                    target_location=target_location,
                    x=float(x) if x is not None else None,
                    y=float(y) if y is not None else None,
                )
            else:
                exploration_radius = float(foreground_params.get("exploration_radius", 5.0))
                fg_node = ExploreNode(self, exploration_radius=exploration_radius)

            check_active = CheckAutonomousActive()
            monitor_node = MonitorObjectNode(self, trigger_object, min_score=min_score)

            parallel_branch = ParallelNode(
                "MonitorOrForeground",
                [monitor_node, fg_node],
                success_count=1,
            )

            def _check_interrupted() -> NodeStatus:
                if blackboard.get("detected_target_pos"):
                    return NodeStatus.SUCCESS
                return NodeStatus.FAILURE

            check_interrupted = BTNode("CheckInterrupted")
            check_interrupted.tick = _check_interrupted

            skill_chain_node = SkillChainNode(self, on_trigger_skills)
            done_node = BTNode("Done")
            done_node.tick = lambda: NodeStatus.SUCCESS

            on_trigger_seq = SequenceNode(
                "OnTriggerSequence", [check_interrupted, skill_chain_node]
            )

            root = SequenceNode(
                "ConditionReactiveRoot",
                [
                    check_active,
                    parallel_branch,
                    FallbackNode("HandleResult", [on_trigger_seq, done_node]),
                ],
            )

            wire_blackboard(
                [
                    check_active,
                    monitor_node,
                    fg_node,
                    parallel_branch,
                    check_interrupted,
                    skill_chain_node,
                    on_trigger_seq,
                    root,
                ],
                blackboard,
            )

            if foreground_task == "navigate":
                dest = (
                    foreground_params.get("target_location")
                    or f"({foreground_params.get('x')}, {foreground_params.get('y')})"
                )
                start_msg = f"📍 {dest}(으)로 이동하면서 '{trigger_object}'(을)를 감시합니다."
            else:
                start_msg = (
                    f"🗺️ 탐험을 시작합니다. '{trigger_object}'(을)를 발견하면 즉시 반응합니다."
                )

            logger.info(
                "[ConditionalReaction] BT loop started: foreground=%s, trigger=%s",
                foreground_task,
                trigger_object,
            )
            self.send_user_message(start_msg)

            run_reactive_tick_loop(root, _AUTONOMOUS_ACTIVE, log_prefix="[ConditionalReaction]")

            _AUTONOMOUS_ACTIVE.clear()
            logger.info("[ConditionalReaction] BT loop ended")

            if blackboard.get("detected_target_pos"):
                self.send_user_message(
                    f"✅ '{trigger_object}' 감지 후 스킬 체인 실행을 완료했습니다."
                )
            else:
                self.send_user_message(
                    f"✅ 포그라운드 태스크가 완료됐습니다. ('{trigger_object}' 미감지)"
                )

        start_bt_thread(_run_bt_loop)

        return {
            "success": True,
            "message": f"조건부 반응 태스크를 시작했습니다. (foreground={foreground_task}, trigger={trigger_object})",
            "foreground_task": foreground_task,
            "trigger_object": trigger_object,
        }


class AutonomousActSkill(BaseSkill):
    """
    '스스로 판단해서 행동해' 명령을 처리하는 자율 행동 스킬.
    순수 Python BT 구조로 탐험-분석-결정-실행 사이클을 반복합니다.
    """

    name = "autonomous_act"
    terminal_behavior = "background"
    description = (
        "로봇이 스스로 상황을 판단하고 자율적으로 행동합니다. "
        "mode='patrol'(기본): '집안 정리해줘'와 같은 요청에서 기억된 시맨틱 맵/RAG 장소를 순찰하고 "
        "장소가 없으면 제자리에서 주변을 관찰하며 발견한 정리 대상을 처리합니다. 자율 행동 중 프론티어 "
        "탐험은 하지 않습니다. 조작 능력이 있으면 직접 정리하고, 조작 능력이 없으면 연결과 매니퓰레이션 "
        "능력이 확인된 동료 한 대에게 해당 작업만 요청해 결과를 확인합니다. "
        "mode='goal': 목표(goal 파라미터)를 받아 LLM이 다단계 실행 계획을 수립하고 순차 실행하며, "
        "자신이 수행할 수 없는 단계는 동료에게 위임합니다. "
        "배터리 신호가 없거나 부족해도 기본적으로 체크만 수행하며 루프를 종료하지 않습니다. "
        "파라미터: mode(str, 'patrol'|'goal', 기본 'patrol'), "
        "goal(str, mode='goal' 시 필수 — 자연어 목표), "
        "min_battery_pct(float, 기본 20.0), "
        "allow_unknown_battery(bool, 기본 True), "
        "check_only_battery(bool, 기본 True), "
        "cycle_interval_sec(float, 기본 3.0), "
        "max_cycles(int, 기본 0=무제한). "
        "min_coverage_pct, exploration_radius, has_map은 호환성을 위해 수용하지만 사용하지 않습니다. "
        "탐험은 별도 explore 스킬을 명시적으로 요청하세요. "
        "중단: stop_autonomous 스킬을 사용하세요."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["patrol", "goal"], "default": "patrol"},
            "goal": {"type": "string"},
            "min_battery_pct": {"type": "number", "default": 20.0},
            "allow_unknown_battery": {"type": "boolean", "default": True},
            "check_only_battery": {"type": "boolean", "default": True},
            "min_coverage_pct": {
                "type": "number",
                "default": 40.0,
                "deprecated": True,
                "description": "호환성 전용이며 자율 순찰에서 사용하지 않습니다.",
            },
            "exploration_radius": {
                "type": "number",
                "default": 3.0,
                "deprecated": True,
                "description": "호환성 전용이며 자율 순찰에서 사용하지 않습니다.",
            },
            "cycle_interval_sec": {"type": "number", "default": 3.0},
            "max_cycles": {"type": "integer", "default": 0},
            "has_map": {
                "type": ["boolean", "null"],
                "default": None,
                "deprecated": True,
                "description": "호환성 전용이며 자율 순찰에서 사용하지 않습니다.",
            },
        },
        "additionalProperties": False,
    }
    side_effects = ("base_motion", "arm_motion", "gripper_motion", "background_loop")

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        if self.node is None:
            return {"success": False, "message": "ROS 노드에 접근할 수 없습니다."}

        try:
            min_battery = float(params.get("min_battery_pct", 20.0))
            allow_unknown_bat = bool(params.get("allow_unknown_battery", True))
            check_only_bat = bool(params.get("check_only_battery", True))
            cycle_interval = float(params.get("cycle_interval_sec", 3.0))
            max_cycles = int(params.get("max_cycles", 0))
        except (TypeError, ValueError) as exc:
            return {"success": False, "message": f"자율 행동 파라미터가 잘못되었습니다: {exc}"}

        mode = str(params.get("mode", "patrol")).lower().strip()
        goal = str(params.get("goal", "")).strip()
        if mode not in ("patrol", "goal"):
            return {
                "success": False,
                "message": f"지원하지 않는 mode: '{mode}'. 'patrol' 또는 'goal'을 사용하세요.",
            }
        if mode == "goal" and not goal:
            return {
                "success": False,
                "message": "mode='goal'일 때 goal 파라미터가 필요합니다.",
            }
        if not 0.0 <= min_battery <= 100.0 or cycle_interval < 0.1 or max_cycles < 0:
            return {
                "success": False,
                "message": "min_battery_pct는 0~100, cycle_interval_sec는 0.1 이상, max_cycles는 0 이상이어야 합니다.",
            }

        if not claim_autonomous(_AUTONOMOUS_ACTIVE):
            return {
                "success": False,
                "message": "이미 자율 행동이 실행 중입니다. stop_autonomous로 먼저 중단하세요.",
            }

        try:
            if mode == "goal":
                _run_loop = self._build_goal_loop(
                    goal, min_battery, cycle_interval, max_cycles, allow_unknown_bat, check_only_bat
                )
            else:
                _run_loop = self._build_patrol_loop(
                    min_battery, cycle_interval, max_cycles, allow_unknown_bat, check_only_bat
                )
            start_bt_thread(_run_loop)
        except Exception as exc:
            release_autonomous()
            logger.exception("[AutonomousAct] Failed to start background loop")
            return {"success": False, "message": f"자율 행동을 시작하지 못했습니다: {exc}"}

        mode_desc = f"목표 지시형 모드 (목표: {goal})" if mode == "goal" else "순찰 관찰 모드"
        return {
            "success": True,
            "message": (
                f"자율 행동을 시작합니다. 모드: {mode_desc}. (배터리 임계: {min_battery}%)"
            ),
            "mode": mode,
            "goal": goal if mode == "goal" else None,
            "min_battery_pct": min_battery,
            "check_only_battery": check_only_bat,
        }

    def _build_patrol_loop(
        self,
        min_battery: float,
        cycle_interval: float,
        max_cycles: int,
        allow_unknown_battery: bool = True,
        check_only_battery: bool = True,
    ) -> Any:
        """기억된 좌표 순찰과 장소 부재 시 제자리 관찰 루프를 생성한다."""

        def _run_bt_loop_impl() -> None:
            blackboard: dict[str, Any] = {
                "cycle_count": 0,
                "task_queue": [],
                "task_history": [],
                "patrol_has_destination": False,
                "stationary_observation_only": True,
            }

            check_active = CheckAutonomousActive()
            check_battery = CheckBatterySufficient(
                self,
                min_battery,
                allow_unknown=allow_unknown_battery,
                check_only=check_only_battery,
            )
            emergency_bat = EmergencyLowBattery(self, check_only=check_only_battery)

            ensure_places = EnsurePlacesRegistered(self)
            patrol_next_place = PatrolNextPlaceNode(self)
            local_observation = LocalObservationNode(self)
            active_after_local_scan = CheckAutonomousActive()
            refresh_peer_status = RefreshPeerStatusCache(self)
            analyze_scene = AnalyzeCurrentScene(self)
            log_obs_node = LogObservationNode(self)
            interesting_gate = InterestingSceneGate(self)
            resolve_cleanup_handoff = ResolveCleanupHandoff(self)
            llm_decide = LLMDecideAction(self)
            validate_decision = ValidateDecidedAction(self)
            execute_action = ExecuteDecidedAction(self)
            sleep_node = SleepBetweenCycles(cycle_interval)

            # 자율협동 — 동료 로봇이 보낸 메시지를 수집해 blackboard 에 담는다.
            from robo_claw_agent.skills.cooperation_skill.bt_nodes import (
                ProcessPeerMessages,
            )

            process_peer_messages = ProcessPeerMessages(self)

            # 특이사항이 감지됐을 때만 LLM 판단(decide->validate->execute)을 수행하고,
            # 그 외엔 done_node로 곧바로 다음 사이클(순찰)로 넘어가 LLM 풀 호출을 아낀다.
            done_node = BTNode("Done")
            done_node.tick = lambda: NodeStatus.SUCCESS
            decide_and_act = SequenceNode(
                "DecideAndAct",
                [
                    interesting_gate,
                    resolve_cleanup_handoff,
                    llm_decide,
                    validate_decision,
                    execute_action,
                ],
            )
            act_on_interesting = FallbackNode("ActOnInteresting", [decide_and_act, done_node])

            # 지도/씬분석/판단 등 사이클 내부 작업은 일시적 실패(카메라 타임아웃, LLM 파싱
            # 오류 등)가 있어도 자율 행동 세션 전체를 끝내면 안 되므로 CycleDone으로 감싸
            # 항상 SUCCESS를 반환시킨다. check_active/safety_gate의 FAILURE만 세션을 끝낸다.
            cycle_done_node = BTNode("CycleDone")
            cycle_done_node.tick = lambda: NodeStatus.SUCCESS
            per_cycle_work = SequenceNode(
                "PerCycleWork",
                [
                    ensure_places,
                    patrol_next_place,
                    local_observation,
                    active_after_local_scan,
                    refresh_peer_status,
                    process_peer_messages,
                    analyze_scene,
                    log_obs_node,
                    act_on_interesting,
                ],
            )
            per_cycle_guarded = FallbackNode("PerCycleGuarded", [per_cycle_work, cycle_done_node])

            all_nodes = [
                check_active,
                check_battery,
                emergency_bat,
                ensure_places,
                patrol_next_place,
                local_observation,
                active_after_local_scan,
                refresh_peer_status,
                process_peer_messages,
                analyze_scene,
                log_obs_node,
                interesting_gate,
                resolve_cleanup_handoff,
                llm_decide,
                validate_decision,
                execute_action,
                sleep_node,
            ]
            wire_blackboard(all_nodes, blackboard)

            safety_gate = FallbackNode("SafetyGate", [check_battery, emergency_bat])
            root = SequenceNode(
                "AutonomousActRoot",
                [
                    check_active,
                    safety_gate,
                    per_cycle_guarded,
                    sleep_node,
                ],
            )

            logger.info("[AutonomousAct] BT loop started")
            self.send_user_message(
                "자율 행동을 시작합니다. 기억된 장소를 순찰하고, 장소가 없으면 제자리에서 주변을 관찰합니다."
            )

            while _AUTONOMOUS_ACTIVE.is_set():
                blackboard["cycle_count"] += 1
                cycle = blackboard["cycle_count"]

                if max_cycles > 0 and cycle > max_cycles:
                    logger.info("[AutonomousAct] Reached max cycles (%d). Stopping.", max_cycles)
                    self.send_user_message(f"설정된 최대 사이클({max_cycles})에 도달했습니다.")
                    break

                logger.info("[AutonomousAct cycle %d] Running BT tick", cycle)
                try:
                    status = root.tick()
                    if status == NodeStatus.FAILURE:
                        logger.info("[AutonomousAct] BT root FAILURE — stopping")
                        break
                except Exception as exc:
                    logger.exception("[AutonomousAct] BT tick exception: %s", exc)
                    time.sleep(2.0)

            _AUTONOMOUS_ACTIVE.clear()
            logger.info("[AutonomousAct] BT loop ended")
            self.send_user_message("자율 행동을 종료했습니다.")

        def _run_bt_loop() -> None:
            try:
                _run_bt_loop_impl()
            finally:
                release_autonomous()

        return _run_bt_loop

    def _build_goal_loop(
        self,
        goal: str,
        min_battery: float,
        cycle_interval: float,
        max_cycles: int,
        allow_unknown_battery: bool = True,
        check_only_battery: bool = True,
    ) -> Any:
        """goal 모드 BT 루프를 생성한다. 목표를 다단계 plan으로 분해 후 순차 실행."""

        def _run_goal_loop_impl() -> None:
            blackboard: dict[str, Any] = {
                "cycle_count": 0,
                "task_queue": [],
                "task_history": [],
                "goal": goal,
                "goal_achieved": False,
                "planning_failure_count": 0,
            }

            check_active = CheckAutonomousActive()
            check_battery = CheckBatterySufficient(
                self,
                min_battery,
                allow_unknown=allow_unknown_battery,
                check_only=check_only_battery,
            )
            emergency_bat = EmergencyLowBattery(self, check_only=check_only_battery)
            goal_planner = GoalPlannerNode(self, goal)
            validate_decision = ValidateDecidedAction(self)
            execute_action = ExecuteDecidedAction(self)
            check_goal = CheckGoalAchieved(self)
            sleep_node = SleepBetweenCycles(cycle_interval)

            plan_and_execute = SequenceNode(
                "PlanAndExecute",
                [
                    goal_planner,
                    validate_decision,
                    execute_action,
                    check_goal,
                ],
            )

            cycle_done_node = BTNode("GoalCycleDone")
            cycle_done_node.tick = lambda: NodeStatus.SUCCESS
            per_cycle_guarded = FallbackNode(
                "GoalCycleGuarded", [plan_and_execute, cycle_done_node]
            )

            all_nodes = [
                check_active,
                check_battery,
                emergency_bat,
                goal_planner,
                validate_decision,
                execute_action,
                check_goal,
                sleep_node,
            ]
            wire_blackboard(all_nodes, blackboard)

            safety_gate = FallbackNode("SafetyGate", [check_battery, emergency_bat])
            root = SequenceNode(
                "GoalActRoot",
                [
                    check_active,
                    safety_gate,
                    per_cycle_guarded,
                    sleep_node,
                ],
            )

            logger.info("[AutonomousAct] goal-mode BT loop started: %s", goal)
            self.send_user_message(f"목표 지시형 자율 행동을 시작합니다. 목표: {goal}")

            while _AUTONOMOUS_ACTIVE.is_set():
                blackboard["cycle_count"] += 1
                cycle = blackboard["cycle_count"]

                if max_cycles > 0 and cycle > max_cycles:
                    logger.info("[AutonomousAct] Reached max cycles (%d). Stopping.", max_cycles)
                    self.send_user_message(f"설정된 최대 사이클({max_cycles})에 도달했습니다.")
                    break

                logger.info("[AutonomousAct goal cycle %d] Running BT tick", cycle)
                try:
                    status = root.tick()
                    if status == NodeStatus.FAILURE:
                        logger.info("[AutonomousAct] BT root FAILURE — stopping")
                        break
                    if blackboard.get("goal_achieved"):
                        logger.info("[AutonomousAct] Goal achieved — stopping loop")
                        break
                    if blackboard.get("planning_failure_count", 0) >= 2:
                        reason = blackboard.get("planning_failure_reason", "계획 수립 실패")
                        self.send_user_message(
                            f"목표 계획을 안전하게 중단했습니다: {reason}"
                        )
                        logger.warning(
                            "[AutonomousAct] Planning failed repeatedly; stopping goal loop"
                        )
                        break
                except Exception as exc:
                    logger.exception("[AutonomousAct] BT tick exception: %s", exc)
                    time.sleep(2.0)

            _AUTONOMOUS_ACTIVE.clear()
            logger.info("[AutonomousAct] goal-mode BT loop ended")
            self.send_user_message("자율 행동을 종료했습니다.")

        def _run_goal_loop() -> None:
            try:
                _run_goal_loop_impl()
            finally:
                release_autonomous()

        return _run_goal_loop


class StopAutonomousSkill(BaseSkill):
    """자율 행동 루프를 즉시 중단합니다."""

    name = "stop_autonomous"
    input_schema = {"type": "object", "properties": {}, "additionalProperties": False}
    description = (
        "현재 진행 중인 자율 행동 루프를 즉시 중단합니다. "
        "탐험만 중단하려면 stop_explore, 이동만 중단하려면 stop을 사용하세요."
    )

    def execute(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        if not _AUTONOMOUS_ACTIVE.is_set():
            return {"success": False, "message": "현재 진행 중인 자율 행동이 없습니다."}

        release_autonomous()

        try:
            from robo_claw_agent.skills.navigation_skill import (
                _cancel_active_goal,  # type: ignore[import]
            )

            _cancel_active_goal()
        except Exception as exc:
            logger.warning("[AutonomousAct] Error while cancelling goal: %s", exc)

        logger.info("[AutonomousAct] stop_autonomous called — loop stop requested")
        return {"success": True, "message": "자율 행동을 중단했습니다."}
