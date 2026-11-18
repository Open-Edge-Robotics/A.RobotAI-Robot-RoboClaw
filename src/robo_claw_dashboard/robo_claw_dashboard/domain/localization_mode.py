"""지도 선택/위치추정 모드 전환 규칙 — CLOiD LIO `/switch_mode` 계약.

대시보드가 로봇의 SLAM 모드를 바꾸는 요청을 검증하고, LIO 상태 문자열
(`/lio_node/status`)을 화면 표시용으로 정규화한다.

주의: ROS2 / rclpy / generated message import 금지. 입력은 순수 Python 값만 받는다.
"""

import posixpath
import re
from collections.abc import Mapping, Sequence
from typing import Any

from .localization import validate_initial_pose

#: 위치추정 모드(저장된 맵으로 재위치추정).
MODE_LOCALIZATION = "localization"
#: 매핑 모드(SLAM). 맵 이름은 사용하지 않는다.
MODE_MAPPING = "mapping"
MODES = (MODE_LOCALIZATION, MODE_MAPPING)

#: 맵 이름 허용 형식. 경로 구분자/공백/선행 점(상위 경로) 을 허용하지 않는다.
MAP_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
#: 맵 이름 최대 길이.
MAX_MAP_NAME_LEN = 64

#: 확인(confirm) 없는 모드 전환을 거부하는 문구.
CONFIRM_REQUIRED = "지도 전환은 로봇의 위치추정 모드를 바꿉니다. confirm=true 로 확인해야 합니다."

#: LIO 상태 문자열 → (라벨, 단계, 모드). upstream docs/LIO_STATUS.md 계약을 따른다.
_STATUS_TABLE: dict[str, tuple[str, str, str]] = {
    "mapping": ("매핑(SLAM) 진행 중", "mapping", MODE_MAPPING),
    "saving_map": ("맵 저장 중", "saving", MODE_MAPPING),
    "loading_map": ("3D 맵 로드 중", "loading", MODE_LOCALIZATION),
    "relocalizing_pose": ("초기 위치 기반 재위치추정 중", "relocalizing", MODE_LOCALIZATION),
    "relocalizing_gkr": ("360° 회전 재위치추정 중", "relocalizing", MODE_LOCALIZATION),
    "loading_grid_map": ("2D 그리드 맵 로드 중", "loading", MODE_LOCALIZATION),
    "ready": ("위치추정 준비 완료", "ready", MODE_LOCALIZATION),
    "recovering": ("위치추정 복구 중", "recovering", MODE_LOCALIZATION),
    "failed": ("위치추정 실패", "failed", "unknown"),
}

#: 진행 중으로 표시할 단계(화면 진행 표시/중복 요청 차단에 사용).
ACTIVE_PHASES = frozenset({"mapping", "saving", "loading", "relocalizing", "recovering"})


def parse_map_names(raw: Any) -> list[str]:
    """설정값(JSON 배열 또는 쉼표 구분 문자열)을 유효한 맵 이름 목록으로 바꾼다."""
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        # JSON 배열의 숫자/불리언 항목은 맵 이름이 아니므로 버린다.
        candidates = [str(item) for item in raw if isinstance(item, str)]
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            import json

            try:
                parsed = json.loads(text)
            except (TypeError, ValueError):
                return []
            if not isinstance(parsed, list):
                return []
            candidates = [str(item) for item in parsed if isinstance(item, str)]
        else:
            candidates = text.split(",")
    else:
        return []

    names: list[str] = []
    for candidate in candidates:
        name = candidate.strip()
        if not name or name in names:
            continue
        ok, _ = validate_map_name(name)
        if ok:
            names.append(name)
    return names


def validate_map_name(name: Any) -> tuple[bool, str | None]:
    """맵 이름이 경로 이탈 없이 안전한지 검사한다."""
    text = str(name or "")
    if not text:
        return False, "맵 이름이 비어 있습니다"
    if len(text) > MAX_MAP_NAME_LEN:
        return False, f"맵 이름이 너무 깁니다(최대 {MAX_MAP_NAME_LEN}자): {text[:16]}…"
    if not MAP_NAME_PATTERN.match(text):
        return False, f"맵 이름에 허용되지 않은 문자가 있습니다: {text!r}"
    return True, None


def resolve_map_path(name: Any, map_root: str) -> str | None:
    """맵 이름을 저장소 루트 하위 절대 경로로 바꾼다(검증 실패 시 None)."""
    ok, _ = validate_map_name(name)
    if not ok:
        return None
    root = posixpath.normpath(str(map_root or "").strip() or "/")
    if not root.startswith("/"):
        return None
    resolved = posixpath.normpath(posixpath.join(root, str(name).strip()))
    if posixpath.dirname(resolved) != root:
        return None
    return resolved


def normalize_map_listing(names: Sequence[Any], map_root: str) -> list[dict[str, str]]:
    """맵 이름 목록을 ``{name, path}`` 항목으로 바꾼다(순서 유지, 부적합 항목 제외)."""
    items: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw in names or []:
        name = str(raw).strip()
        if not name or name in seen:
            continue
        path = resolve_map_path(name, map_root)
        if path is None:
            continue
        seen.add(name)
        items.append({"name": name, "path": path})
    return items


def normalize_lio_status(raw: Any, *, stamp_ns: int | None = None) -> dict[str, Any]:
    """`/lio_node/status` 문자열을 화면/판정용 dict 로 정규화한다."""
    text = "" if raw is None else str(raw).strip()
    if not text:
        return unavailable_lio_status("상태 문자열이 비어 있습니다")

    label, phase, mode = _STATUS_TABLE.get(text, (text, "unknown", "unknown"))
    return {
        "available": True,
        "raw": text,
        "label": label,
        "phase": phase,
        "mode": mode,
        "active": phase in ACTIVE_PHASES,
        "failed": phase == "failed",
        "stamp_ns": stamp_ns,
    }


def unavailable_lio_status(reason: str) -> dict[str, Any]:
    """상태를 수신하지 못한 경우의 표시용 상태를 만든다."""
    label = str(reason or "").strip() or "상태 미수신"
    return {
        "available": False,
        "reason": label,
        "raw": "",
        "label": label,
        "phase": "unknown",
        "mode": "unknown",
        "active": False,
        "failed": False,
        "stamp_ns": None,
    }


def _is_confirmed(payload: Mapping[str, Any]) -> bool:
    value = payload.get("confirm")
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    return str(value or "").strip().lower() in ("true", "yes", "on", "1")


def validate_switch_request(
    payload: Any,
    *,
    maps: Sequence[Any],
    map_root: str,
    map_meta: Mapping[str, Any] | None = None,
) -> tuple[bool, str | None, dict[str, Any] | None]:
    """`/api/localization/switch` 요청을 검증하고 실행 계획을 만든다.

    반환값은 (ok, error, plan). plan 은 ``mode``/``map``/``map_path``/
    ``set_initial_pose``/``pose`` 를 담는다. ``map_meta`` 는 현재 표시 중인 지도이며,
    지도를 바꾸는 중에는 경계 검사에 쓰지 않는다(대상 지도가 아직 없음).
    """
    del map_meta  # 현재 지도 경계는 맵 전환 검증에 쓰지 않는다(위 docstring 참고)

    if not isinstance(payload, Mapping):
        return False, "지도 전환 요청이 JSON 객체가 아닙니다", None

    mode = str(payload.get("mode") or MODE_LOCALIZATION).strip().lower()
    if mode not in MODES:
        return False, f"mode 는 {' 또는 '.join(MODES)} 여야 합니다: {mode!r}", None

    if mode == MODE_MAPPING:
        if not _is_confirmed(payload):
            return False, CONFIRM_REQUIRED, None
        return (
            True,
            None,
            {
                "mode": MODE_MAPPING,
                "map": None,
                "map_path": "",
                "set_initial_pose": False,
                "pose": None,
            },
        )

    raw_map = payload.get("map")
    name = str(raw_map or "").strip()
    if not name:
        return False, "지도 이름(map)이 필요합니다", None
    name_ok, name_error = validate_map_name(name)
    if not name_ok:
        return False, name_error, None

    allowed = [str(item).strip() for item in (maps or []) if str(item).strip()]
    if not allowed:
        return (
            False,
            "지도 목록이 설정되지 않았습니다(localization_map_names_json 또는 localization_map_dir)",
            None,
        )
    if name not in allowed:
        return False, f"허용된 지도 목록에 없습니다: {name}", None

    map_path = resolve_map_path(name, map_root)
    if map_path is None:
        return False, f"맵 경로를 만들 수 없습니다: {name!r}", None

    seed = _is_confirmed({"confirm": payload.get("set_initial_pose")})
    pose: dict[str, Any] | None = None
    if seed:
        pose_ok, pose_error, pose = validate_initial_pose(payload, map_meta=None)
        if not pose_ok:
            return False, pose_error, None

    if not _is_confirmed(payload):
        return False, CONFIRM_REQUIRED, None

    return (
        True,
        None,
        {
            "mode": MODE_LOCALIZATION,
            "map": name,
            "map_path": map_path,
            "set_initial_pose": seed,
            "pose": pose,
        },
    )
