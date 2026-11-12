"""대시보드 지도 선택 목표(map goal)의 순수 계약.

대시보드는 지도에서 고른 단일 지점을 표준 문장 + 검증된 컨텍스트로 보낸다.
에이전트는 컨텍스트의 좌표/프레임이 이 모듈의 정규화 규칙과 정확히 일치하고,
선택 시점의 지도 지오메트리가 지금과 같고, 목표 셀이 확인된 자유 셀일 때만
실제 주행을 허용한다.

ROS/네트워크 의존성이 없으므로 테스트에서 그대로 임포트할 수 있다. 좌표/프레임은
지시문이 아니라 데이터이며, 이 모듈은 문자열을 만들거나 검증만 한다.
"""

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...geo_utils import yaw_from_quaternion

CONTEXT_TYPE = "dashboard_map_goal_v1"
# 지도에서 목표를 고른 뒤 실제 주행을 시작할 수 있는 선택 유효 시간(초).
SELECTION_WINDOW_SEC = 60.0
# 지도 목표 요청 하나가 주행에 쓸 수 있는 전체 시간 예산(초).
NAV_BUDGET_SEC = 180.0
COORD_DECIMALS = 6
GEOMETRY_DECIMALS = 12
MAX_COORD_ABS_M = 1_000_000.0
# LLM 이 되돌려준 좌표가 선택 좌표와 같은지 판정하는 허용 오차(m).
COORD_TOLERANCE_M = 1e-6

# 로봇 ID/프레임/토픽처럼 메시지에 다시 실리는 식별자는 개행·공백 없는
# 제한된 문자만 허용한다(프롬프트 주입과 경로 해석을 막는다).
_IDENT_RE = re.compile(r"/?[A-Za-z0-9_][A-Za-z0-9_.:/-]{0,159}")
_REQUEST_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_GEOMETRY_ID_RE = re.compile(r"[0-9a-f]{64}")

_CONTEXT_KEYS = frozenset(
    {
        "type",
        "request_id",
        "robot_id",
        "x",
        "y",
        "frame_id",
        "geometry_id",
        "map_topic",
        "created_at",
        "expires_at",
    }
)

CANONICAL_MESSAGE_TEMPLATE = (
    "지도에서 선택한 목표로 이동해 주세요. 로봇: {robot_id}. 기준 프레임: {frame_id}. "
    "목표 좌표: x={x:.6f} m, y={y:.6f} m. 필요한 주행 안전 절차를 먼저 적용하고, "
    "목표 좌표와 프레임을 변경하지 마세요."
)


class MapGoalError(ValueError):
    """지도 목표 컨텍스트/좌표가 계약을 어겼을 때 발생한다."""


def now_epoch() -> float:
    """선택 유효 시간/기록 타임스탬프에 쓰는 벽시계(초)."""
    return time.time()


def normalize_zero(value: float) -> float:
    """-0.0 을 0.0 으로 정규화한다(직렬화/포맷 문자열이 갈라지지 않게)."""
    return 0.0 if value == 0.0 else value


def quantize(value: Any, decimals: int) -> float:
    """유한한 float 를 지정 소수 자릿수로 반올림하고 -0.0 을 정규화한다."""
    if isinstance(value, bool):
        raise MapGoalError(f"숫자 대신 bool 값이 전달되었습니다: {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MapGoalError(f"숫자가 아닌 값입니다: {value!r}") from exc
    if not math.isfinite(number):
        raise MapGoalError(f"유한하지 않은 값입니다: {value!r}")
    return normalize_zero(round(number, decimals))


def canonical_coordinate(value: Any) -> float:
    """지도 목표 좌표/좌표 비교에 쓰는 정규 좌표(소수 6자리)."""
    number = quantize(value, COORD_DECIMALS)
    if abs(number) > MAX_COORD_ABS_M:
        raise MapGoalError(f"좌표가 허용 범위(|{MAX_COORD_ABS_M:.0f}| m)를 벗어났습니다: {number}")
    return number


def normalize_yaw(yaw: Any) -> float:
    """yaw 를 [-pi, pi) 로 정규화하고 12자리로 반올림한다.

    정규화는 반올림 전에 원래 float 로 수행한다(정확히 pi 인 yaw 가 -pi 로,
    2*pi 가 0.0 으로 떨어지도록). 12자리 격자에는 pi 가 정확히 표현되지 않으므로
    경계값은 -pi 대비 1e-12 이내로 떨어질 수 있다(두 레인 모두 같은 값).
    """
    quantize(yaw, GEOMETRY_DECIMALS)  # 유한성 검사
    number = float(yaw)
    wrapped = math.fmod(number + math.pi, 2.0 * math.pi)
    if wrapped < 0.0:
        wrapped += 2.0 * math.pi
    return normalize_zero(round(wrapped - math.pi, GEOMETRY_DECIMALS))


def validate_identifier(value: Any, label: str) -> str:
    """로봇 ID/프레임/토픽 문자열을 검증한다(개행/공백 미허용).

    공백을 제거해 통과시키면 ``"map\n"`` 같은 값이 조용히 통과하므로, 원문
    그대로 허용 문자 집합과 비교한다.
    """
    if not isinstance(value, str) or not _IDENT_RE.fullmatch(value):
        raise MapGoalError(f"{label} 값이 허용된 형식이 아닙니다: {value!r}")
    return value


def canonical_goal_message(robot_id: Any, frame_id: Any, x: Any, y: Any) -> str:
    """대시보드가 보내야 하는 표준 이동 문장을 만든다.

    좌표는 소수 6자리로 반올림해 문장에 넣고, 같은 정규 값을 컨텍스트에도 쓴다.
    스킬 지시자(``[skill]``)나 특정 스킬 호출 명령은 넣지 않는다.
    """
    robot = validate_identifier(robot_id, "robot_id")
    frame = validate_identifier(frame_id, "frame_id")
    cx = canonical_coordinate(x)
    cy = canonical_coordinate(y)
    return CANONICAL_MESSAGE_TEMPLATE.format(robot_id=robot, frame_id=frame, x=cx, y=cy)


def canonical_message_matches(context: "MapGoalContext", message: Any) -> bool:
    """표준 이동 문장이 컨텍스트의 정규 좌표/프레임과 같은지 판정한다.

    대시보드가 보낸 문장과 컨텍스트가 어긋나면 어느 쪽이 사용자의 선택인지
    확정할 수 없으므로 실행 전에 거부한다(앞뒤 공백만 허용).
    """
    try:
        expected = canonical_goal_message(context.robot_id, context.frame_id, context.x, context.y)
    except MapGoalError:
        return False
    return str(message or "").strip() == expected


@dataclass(frozen=True)
class MapGoalContext:
    """검증된 지도 목표 컨텍스트(대시보드 -> ExecuteTask.context_json)."""

    request_id: str
    robot_id: str
    x: float
    y: float
    frame_id: str
    geometry_id: str
    map_topic: str
    created_at: float
    expires_at: float

    def target(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "frame_id": self.frame_id}

    def fingerprint(self) -> str:
        """멱등성 판정에 쓰는 요청 페이로드 지문."""
        payload = json.dumps(
            [
                self.robot_id,
                self.x,
                self.y,
                self.frame_id,
                self.geometry_id,
                self.map_topic,
            ],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _looks_like_map_goal_context(raw: str) -> bool:
    return CONTEXT_TYPE in raw or "dashboard_map_goal" in raw


def parse_context_json(raw: Any) -> MapGoalContext | None:
    """context_json 을 해석한다.

    지도 목표 컨텍스트가 아니면 ``None`` 을 돌려준다(기존 텍스트 컨텍스트 유지).
    지도 목표로 보이는데 형식이 깨졌으면 ``MapGoalError`` 를 발생시킨다 —
    호출부는 이때 일반 텍스트로 폴백하지 않고 요청을 거부해야 한다.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None

    try:
        payload = json.loads(text)
    except (TypeError, ValueError) as exc:
        if _looks_like_map_goal_context(text):
            raise MapGoalError(f"지도 목표 컨텍스트를 해석할 수 없습니다: {exc}") from exc
        return None

    if not isinstance(payload, Mapping):
        if _looks_like_map_goal_context(text):
            raise MapGoalError("지도 목표 컨텍스트는 JSON 객체여야 합니다.")
        return None

    context_type = payload.get("type")
    if context_type != CONTEXT_TYPE:
        if isinstance(context_type, str) and context_type.startswith("dashboard_map_goal"):
            raise MapGoalError(f"지원하지 않는 지도 목표 컨텍스트 형식입니다: {context_type}")
        return None

    keys = set(payload.keys())
    if keys != _CONTEXT_KEYS:
        missing = sorted(_CONTEXT_KEYS - keys)
        extra = sorted(keys - _CONTEXT_KEYS)
        raise MapGoalError(
            f"지도 목표 컨텍스트 키가 계약과 다릅니다 (누락={missing}, 추가={extra})"
        )

    request_id = str(payload.get("request_id") or "").strip()
    if not _REQUEST_ID_RE.fullmatch(request_id):
        raise MapGoalError(f"request_id 형식이 올바르지 않습니다: {request_id!r}")
    geometry_id = str(payload.get("geometry_id") or "").strip()
    if not _GEOMETRY_ID_RE.fullmatch(geometry_id):
        raise MapGoalError("geometry_id 는 소문자 16진수 64자리여야 합니다.")

    created_at = quantize(payload.get("created_at"), COORD_DECIMALS)
    expires_at = quantize(payload.get("expires_at"), COORD_DECIMALS)
    if created_at <= 0.0:
        raise MapGoalError(f"created_at 이 올바르지 않습니다: {created_at}")
    if abs((expires_at - created_at) - SELECTION_WINDOW_SEC) > COORD_TOLERANCE_M:
        raise MapGoalError(
            f"expires_at 은 created_at + {SELECTION_WINDOW_SEC:.0f}초여야 합니다 "
            f"(created_at={created_at}, expires_at={expires_at})"
        )

    return MapGoalContext(
        request_id=request_id,
        robot_id=validate_identifier(payload.get("robot_id"), "robot_id"),
        x=canonical_coordinate(payload.get("x")),
        y=canonical_coordinate(payload.get("y")),
        frame_id=validate_identifier(payload.get("frame_id"), "frame_id"),
        geometry_id=geometry_id,
        map_topic=validate_identifier(payload.get("map_topic"), "map_topic"),
        created_at=created_at,
        expires_at=expires_at,
    )


def is_expired(context: MapGoalContext, now: float | None = None) -> bool:
    """선택 유효 시간이 지났는지 판정한다."""
    current = now_epoch() if now is None else float(now)
    return current > context.expires_at


@dataclass(frozen=True)
class MapGeometry:
    """지도 지오메트리(스탬프/셀은 ID 에 포함하지 않는다)."""

    map_topic: str
    frame_id: str
    width: int
    height: int
    resolution: float
    origin_x: float
    origin_y: float
    origin_yaw: float
    cells: Any = ()

    def geometry_id(self) -> str:
        """두 레인(대시보드/에이전트)이 공유하는 정규 지오메트리 ID(SHA256)."""
        payload = [
            self.map_topic,
            self.frame_id,
            int(self.width),
            int(self.height),
            quantize(self.resolution, GEOMETRY_DECIMALS),
            quantize(self.origin_x, GEOMETRY_DECIMALS),
            quantize(self.origin_y, GEOMETRY_DECIMALS),
            normalize_yaw(self.origin_yaw),
        ]
        blob = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("ascii")).hexdigest()

    def cell_at(self, x: Any, y: Any) -> int | None:
        """world 좌표를 grid 셀 값으로 변환한다(지도 밖이면 None).

        ``origin`` 회전을 역으로 적용한다. grid 경계는 half-open 이며 지도 밖이면
        값을 돌려주지 않는다.
        """
        if self.width <= 0 or self.height <= 0 or not (self.resolution > 0.0):
            return None
        try:
            world_x = float(x)
            world_y = float(y)
        except (TypeError, ValueError):
            return None
        dx = world_x - float(self.origin_x)
        dy = world_y - float(self.origin_y)
        cos_yaw = math.cos(float(self.origin_yaw))
        sin_yaw = math.sin(float(self.origin_yaw))
        local_x = cos_yaw * dx + sin_yaw * dy
        local_y = -sin_yaw * dx + cos_yaw * dy
        col = int(math.floor(local_x / float(self.resolution)))
        row = int(math.floor(local_y / float(self.resolution)))
        if not (0 <= col < self.width and 0 <= row < self.height):
            return None
        index = row * self.width + col
        cells = self.cells
        try:
            if index >= len(cells):
                return None
            return int(cells[index])
        except (TypeError, IndexError, ValueError):
            return None

    def is_confirmed_free(self, x: Any, y: Any) -> bool:
        """확인된 자유 셀(값 0)일 때만 True. unknown(255)/미확인(-1 등)은 거부."""
        return self.cell_at(x, y) == 0


def geometry_from_map_message(message: Any, map_topic: Any, frame_id: Any) -> MapGeometry | None:
    """OccupancyGrid 메시지(duck-typed)에서 ``MapGeometry`` 를 만든다."""
    info = getattr(message, "info", None)
    origin = getattr(info, "origin", None)
    position = getattr(origin, "position", None)
    if info is None or position is None:
        return None
    try:
        return MapGeometry(
            map_topic=validate_identifier(map_topic, "map_topic"),
            frame_id=validate_identifier(frame_id, "frame_id"),
            width=int(getattr(info, "width", 0)),
            height=int(getattr(info, "height", 0)),
            resolution=float(getattr(info, "resolution", 0.0)),
            origin_x=float(position.x),
            origin_y=float(position.y),
            origin_yaw=float(yaw_from_quaternion(getattr(origin, "orientation", None))),
            cells=getattr(message, "data", ()),
        )
    except (AttributeError, TypeError, ValueError, MapGoalError):
        return None
