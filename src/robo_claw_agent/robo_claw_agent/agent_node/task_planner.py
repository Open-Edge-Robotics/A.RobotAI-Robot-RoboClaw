"""복합 명령 자동 분해(Task Decomposition) 오케스트레이터.

SLM(소형 모델)은 "회의실로 이동해 카메라를 보고 쓰레기가 있으면 정리해"류의 복합 명령을
한 번의 LLM 라운드에서 전체 스킬 체인으로 구조화하지 못하고 첫 절(또는 마지막 절)만 수행하는
경향이 있다. 이를 보완하기 위해, 복합 명령으로 판단되면 자연어 하위 지시문 리스트로 먼저
분해한 뒤 각 하위 지시문을 기존 ``LLMPlanner.run_llm_planning_loop`` 에 그대로 위임해
순차 실행한다. 스킬 선택/파라미터 채우기/재계획 로직은 일절 변경하지 않고 재사용한다.

단순 명령(복합이 아닌 경우)은 ``looks_compound`` 가 False를 반환해 기존 단일 라운드 경로가
그대로 사용되므로 비용/지연 영향이 없다.
"""

import json
import logging
import re
import threading
from typing import Any

logger = logging.getLogger(__name__)

# 순차 연결을 나타내는 명시적 접속 표현.
# "-아서/-어서/-해서/-가서"(예: "주방으로 가서 컵 집어줘")는 선행 동작을 전제로
# 다음 절이 이어지는 대표적인 순차 연결어미인데 기존 패턴에서 누락돼 있었다.
# 이 때문에 "가서 ~해줘" 류 복합 명령이 단일 명령으로 취급됐다.
_CONNECTIVE_RE = re.compile(
    r"한\s?다음|한\s?후|한\s?뒤|그리고|그\s?다음|그런\s?다음|았다가|었다가|했다가|"
    r"(?:아서|어서|해서|와서|가서|여서|면서)\s"
)

# 연결어미 "-고"(예: "이동하고 ", "가고 ") 후보. 어절 단위로만 잡는다.
# 과거에는 `[가-힣]+고\s` 를 그대로 썼는데, 이는 "고"로 끝나는 **명사 어절**까지
# 전부 삼켰다("냉장고 앞으로 가줘", "창고 앞에 가줘", "그거 말고 저거 가져와",
# "참고 자료 읽어줘"). 오탐 1건의 비용은 "분해 LLM 호출 1회"가 아니라
# "단계 수 × (1+재시도) 만큼의 계획 루프"라서 곱으로 늘어나므로, 접속어미로
# 오인되기 쉬운 명사는 예외 목록으로 걸러낸다.
_EOJEOL_GO_RE = re.compile(r"(?:(?<=\s)|^)(?P<word>[가-힣]+고)\s")

# "-고"로 끝나지만 연결어미가 아닌 명사/부사 어절.
_GO_ENDING_NON_CONNECTIVES: frozenset[str] = frozenset({
    "냉장고", "창고", "참고", "최고", "사고", "말고", "광고", "중고", "신고",
    "경고", "예고", "공고", "원고", "재고", "금고", "서고", "차고", "천고",
    "삼고", "회고", "완고", "빙고", "만고",
})


def _has_connective_ending(text: str) -> bool:
    """연결어미 "-고"가 쓰였는지(= 절이 이어지는지) 판정한다."""
    return any(
        match.group("word") not in _GO_ENDING_NON_CONNECTIVES
        for match in _EOJEOL_GO_RE.finditer(text)
    )


# 문장을 끝맺는 종결형 동사 어미 — 이게 2회 이상 등장하면 절이 여러 개 이어진 것으로 본다.
_SENTENCE_END_RE = re.compile(r"(해줘|해라|하세요|해요|해\b|한다|합니다)")
# 조건 연결 어미 ("~하면", "~있으면" 등)
_CONDITIONAL_RE = re.compile(r"(하면|있으면|되면|없으면)")

_DEFAULT_MAX_STEPS = 6
_DEFAULT_STEP_MAX_RETRIES = 1

# ExecuteTask 액션 서버가 결과를 기다리는 여유 시간(초) 계산에 쓰인다.
# 단순 명령(분해 없음)은 기존 마진을 그대로 유지해 회귀가 없도록 한다.
_SINGLE_TASK_WAIT_MARGIN_SEC = 300.0
# 복합 명령은 하위 단계 하나가 "5라운드 재계획 + 스킬 실행"을 온전히 한 번 거치는
# 시간을 이 값으로 어림잡고, 최대 단계 수 × (1+재시도 횟수)를 곱해 상한 이내로 추정한다.
_PER_STEP_WAIT_MARGIN_SEC = 300.0
_DEFAULT_WAIT_MARGIN_CAP_SEC = 1800.0


def looks_compound(instruction: str) -> bool:
    """비용 없는 휴리스틱으로 복합 명령 여부를 가늠한다.

    거짓 음성(복합인데 False) → 기존 단일 라운드 경로로 처리되어 회귀 없음.
    거짓 양성(단순인데 True) → 분해 LLM 호출이 스스로 1단계로 되돌리므로 비용만 소폭 증가.
    이 비대칭성 때문에 과탐지 쪽으로 관대하게 설계한다.
    """
    text = (instruction or "").strip()
    if not text:
        return False

    if _CONNECTIVE_RE.search(text) or _has_connective_ending(text):
        return True

    if len(_SENTENCE_END_RE.findall(text)) >= 2:
        return True

    # 조건절("~하면")과 행동 지시가 함께 있는 긴 문장은 선행 절과 결합된 복합 명령일
    # 가능성이 높다고 보고 관대하게 잡는다(과탐지 비용이 낮으므로).
    if _CONDITIONAL_RE.search(text) and _SENTENCE_END_RE.search(text) and len(text) > 12:
        return True

    return False


# 단계 간에 넘길 결과 데이터 요약의 최대 길이(문자). 소형 모델의 컨텍스트를
# 지키기 위해 넘치면 잘라낸다.
_STEP_RESULT_SUMMARY_CHARS = 800

# collect_task_context 가 삽입하는 로봇 상태 시스템 메시지의 식별 접두어.
_ROBOT_STATE_PREFIX = "[로봇 현재 상태]"


def _summarize_step_results(results: list[Any]) -> str:
    """단계에서 실행된 스킬들의 결과 데이터를 한 줄 요약으로 압축한다."""
    from .utils import _omit_base64_data

    parts: list[str] = []
    for res in results:
        data = getattr(res, "result_data", None)
        if not isinstance(data, dict) or not data:
            continue
        try:
            rendered = json.dumps(_omit_base64_data(data), ensure_ascii=False)
        except (TypeError, ValueError):
            rendered = str(data)
        parts.append(f"{getattr(res, 'skill_name', '?')}={rendered}")

    text = " | ".join(parts)
    if len(text) > _STEP_RESULT_SUMMARY_CHARS:
        return text[:_STEP_RESULT_SUMMARY_CHARS] + " …(생략)"
    return text


class TaskDecomposer:
    """복합 명령을 하위 지시문으로 분해하고 순차 실행하는 오케스트레이터."""

    def __init__(self, node: Any) -> None:
        self.node = node

    def looks_compound(self, instruction: str) -> bool:
        return looks_compound(instruction)

    def estimate_wait_margin_sec(self, instruction: str) -> float:
        """ExecuteTask 액션 서버가 결과를 기다릴 여유 시간(초)을 추정한다.

        단순 명령은 기존 고정 마진(``_SINGLE_TASK_WAIT_MARGIN_SEC``)을 그대로 반환해
        회귀가 없다. 복합 명령은 여러 단계를 순차 실행하고 단계별로 재시도까지 할 수
        있으므로, 단일 단계 마진에 "최대 단계 수 × (1+재시도 횟수)"를 곱해 필요한
        여유를 넉넉히 잡되, 무한정 대기를 막기 위해 설정된 상한(cap)을 넘지 않게 한다.
        """
        if not self.looks_compound(instruction):
            return _SINGLE_TASK_WAIT_MARGIN_SEC

        node = self.node
        max_steps = max(
            1, int(getattr(node, "_task_decomposition_max_steps", _DEFAULT_MAX_STEPS))
        )
        max_retries = max(
            0, int(getattr(node, "_task_step_max_retries", _DEFAULT_STEP_MAX_RETRIES))
        )
        cap = float(
            getattr(
                node,
                "_task_decomposition_wait_margin_cap_sec",
                _DEFAULT_WAIT_MARGIN_CAP_SEC,
            )
        )
        margin = _PER_STEP_WAIT_MARGIN_SEC * max_steps * (1 + max_retries)
        return min(margin, cap)

    async def run(
        self,
        goal_handle: Any,
        instruction: str,
        messages: list[dict[str, str]],
        robot_summary: dict,
        timeout: float,
        send_fb: Any,
    ) -> tuple[str, list[Any], bool, list[threading.Event]]:
        """복합 명령을 분해해 하위 지시문을 순차 실행한다.

        반환 계약은 ``LLMPlanner.run_llm_planning_loop`` 와 동일하게 맞춘다:
        (result_msg, skill_results, task_success, pending_send_events).
        """
        node = self.node
        max_steps = int(getattr(node, "_task_decomposition_max_steps", _DEFAULT_MAX_STEPS))
        max_retries = int(getattr(node, "_task_step_max_retries", _DEFAULT_STEP_MAX_RETRIES))

        steps = await self._decompose(instruction, messages, max_steps)
        if not steps:
            steps = [instruction]
        steps = steps[:max_steps]

        node.get_logger().info(
            f"TaskDecomposer: '{instruction}' -> {len(steps)}단계로 분해: {steps}"
        )

        # 마지막 사용자 메시지(원본 복합 명령)를 제외한 컨텍스트만 베이스로 재사용한다.
        base_messages = messages[:-1] if messages and messages[-1].get("role") == "user" else list(messages)

        all_skill_results: list[Any] = []
        all_pending_events: list[threading.Event] = []
        step_summaries: list[str] = []
        overall_success = True
        final_msg = ""

        total = len(steps)
        for i, step in enumerate(steps):
            progress = 10 + int(70 * (i / max(total, 1)))
            send_fb(f"단계 {i + 1}/{total}: {step}", progress)
            node.get_logger().info(f"TaskDecomposer: 단계 {i + 1}/{total} 시작 — {step}")

            step_messages = await self._with_fresh_robot_state(base_messages, refresh=i > 0)
            if total > 1:
                step_messages.append({
                    "role": "system",
                    "content": (
                        f"[전체 명령]\n{instruction}\n"
                        f"(현재 {i + 1}/{total}번째 하위 단계를 수행 중입니다. "
                        "이 단계에만 집중하되 전체 맥락을 참고하세요.)"
                    ),
                })
            if step_summaries:
                step_messages.append({
                    "role": "system",
                    "content": "[이전 단계 요약]\n" + "\n".join(step_summaries),
                })
            step_messages.append({"role": "user", "content": step})

            attempt = 0
            step_success = False
            step_msg = ""
            step_results: list[Any] = []
            while attempt <= max_retries and not step_success:
                attempt += 1
                step_msg, step_results, step_success, step_events = (
                    await node._planner.run_llm_planning_loop(
                        goal_handle, step, list(step_messages), robot_summary, timeout, send_fb,
                    )
                )
                all_pending_events.extend(step_events)
                if not step_success and attempt <= max_retries:
                    node.get_logger().warning(
                        f"TaskDecomposer: 단계 {i + 1} 시도 {attempt} 실패 — 재시도: {step_msg}"
                    )

            all_skill_results.extend(step_results)
            summary = f"{i + 1}. '{step}' -> {'성공' if step_success else '실패'}: {step_msg}"
            # 다음 단계의 판단 근거가 되는 실제 결과 데이터를 함께 넘긴다.
            # 문자열 한 줄만 넘기면 "가서 보고 있으면 정리해" 류 조건부 명령에서
            # 관측 결과(감지 객체·좌표·분석 내용)가 유실돼 분해가 오히려 불리해진다.
            detail = _summarize_step_results(step_results)
            if detail:
                summary += f"\n   [결과 데이터] {detail}"
            step_summaries.append(summary)

            if not step_success:
                overall_success = False
                final_msg = (
                    f"{i + 1}/{total} 단계 진행 중 실패로 중단. "
                    f"실패한 단계: '{step}'. 사유: {step_msg}"
                )
                node.get_logger().error(f"TaskDecomposer: {final_msg}")
                send_fb(f"단계 {i + 1} 실패로 중단", 90)
                break
        else:
            final_msg = f"모든 단계({total}) 완료. " + (step_summaries[-1] if step_summaries else "")

        return final_msg, all_skill_results, overall_success, all_pending_events

    async def _with_fresh_robot_state(
        self, base_messages: list[dict[str, str]], refresh: bool
    ) -> list[dict[str, str]]:
        """베이스 컨텍스트를 복사하되 로봇 상태 시스템 메시지를 최신값으로 교체한다.

        ``collect_task_context`` 는 태스크 시작 시점의 상태를 한 번만 담는다.
        분해 경로는 단계가 진행될수록 그 스냅샷이 낡아(위치·배터리·모드) 소형
        모델이 "이미 목적지에 있다"고 오판할 수 있으므로, 2번째 단계부터는 매
        단계 시작 시 다시 읽어 덮어쓴다. 상태 조회에 실패하면 기존 스냅샷을
        그대로 쓴다(분해 자체를 실패시키지 않는다).
        """
        messages = list(base_messages)
        if not refresh:
            return messages

        node = self.node
        try:
            status_skill = node._skills._skills.get("get_status")
            if not hasattr(status_skill, "get_robot_summary"):
                return messages
            summary = await node._run_blocking(
                "get_robot_summary_step", status_skill.get_robot_summary
            )
            health = node._get_robot_health_state(summary)
            content = (
                f"{_ROBOT_STATE_PREFIX}\n{json.dumps(summary, ensure_ascii=False)}\n\n"
                f"[로봇 자가진단 상태 (Health State)]\n{json.dumps(health, ensure_ascii=False)}"
            )
        except Exception as exc:  # noqa: BLE001
            node.get_logger().warning(
                f"TaskDecomposer: 단계 시작 시 로봇 상태 갱신 실패 (이전 값 사용): {exc}"
            )
            return messages

        for idx, msg in enumerate(messages):
            if msg.get("role") == "system" and str(msg.get("content", "")).startswith(
                _ROBOT_STATE_PREFIX
            ):
                messages[idx] = {"role": "system", "content": content}
                return messages

        messages.insert(0, {"role": "system", "content": content})
        return messages

    async def _decompose(
        self, instruction: str, messages: list[dict[str, str]], max_steps: int
    ) -> list[str]:
        """LLM으로 복합 명령을 순서가 있는 자연어 하위 지시문 리스트로 분해한다."""
        node = self.node
        if not getattr(node, "_llm", None):
            return []

        context_lines = []
        for m in messages:
            if m.get("role") == "system" and m.get("content"):
                context_lines.append(m["content"])
        context_str = "\n\n".join(context_lines[-3:]) if context_lines else "없음"

        system_prompt = f"""당신은 로봇 에이전트의 명령 분해기입니다.
사용자의 복합 자연어 명령을 로봇이 한 번에 하나씩 순차 수행할 수 있는 단순 하위 지시문으로 분해하세요.

[참고 컨텍스트]
{context_str}

[분해 규칙]
1. 순차적으로 실행 가능한 단위로 쪼개세요. 각 단계는 이전 단계의 완료를 전제로 합니다.
2. 조건부 표현("~하면", "~있으면" 등)이 포함된 절은 미리 스킬 단위로 쪼개지 말고 하나의
   하위 지시문으로 그대로 남겨두세요. 조건 충족 여부는 실행 시점에 로봇이 관측 후 판단합니다.
3. 명령이 이미 단순한 단일 동작이면 1단계로 반환하세요.
4. 최대 {max_steps}단계까지만 분해하세요.

[응답 형식 — 반드시 JSON만 출력]
{{"steps": [{{"instruction": "<하위 지시문 1>"}}, {{"instruction": "<하위 지시문 2>"}}, ...]}}

JSON 외의 텍스트를 출력하지 마세요."""

        decompose_messages = [
            {
                "role": "user",
                "content": f"복합 명령: {instruction}\n\n이 명령을 순차 실행 가능한 하위 지시문으로 분해해주세요.",
            }
        ]

        try:
            # 계획 루프와 동일하게 JSON 강제를 건다. 이게 빠지면 (Ollama 기준
            # format="json" 미적용) 소형 모델이 산문으로 답할 확률이 크게 올라
            # 분해 호출 1회를 통째로 버리고 단일 단계로 폴백하게 된다.
            response = await node._run_blocking(
                "task_decompose",
                node._llm.chat,
                decompose_messages,
                system_prompt=system_prompt,
                response_format={"type": "json_object"},
            )
            from .utils import _extract_first_json_object

            clean = re.sub(r"```(?:json)?\s*\n?", "", response).strip()
            json_str = _extract_first_json_object(clean)
            if "{" not in json_str:
                node.get_logger().warning(f"TaskDecomposer: 분해 응답에 JSON 없음: {response}")
                return []
            decision = json.loads(json_str)
            steps = decision.get("steps", [])

            validated: list[str] = []
            for s in steps:
                if isinstance(s, dict) and s.get("instruction"):
                    validated.append(str(s["instruction"]))
                elif isinstance(s, str) and s.strip():
                    validated.append(s.strip())
            return validated
        except Exception as exc:
            node.get_logger().error(f"TaskDecomposer: LLM 분해 실패: {exc}")
            return []
