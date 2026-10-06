import logging
import math
from typing import Any

import numpy as np
from sensor_msgs.msg import CameraInfo, Image, LaserScan

from . import depth3d, globals

_TARGET_ALIASES = {
    # 음료/용기
    "컵": ("cup",),
    "잔": ("cup",),
    "물컵": ("cup",),
    "머그": ("cup",),
    "병": ("bottle",),
    "물병": ("bottle",),
    "투명병": ("bottle",),
    "페트병": ("bottle",),
    "플라스틱병": ("bottle",),
    "와인잔": ("wine glass",),
    "그릇": ("bowl",),
    "접시": ("bowl",),
    "볼": ("bowl",),
    "숟가락": ("spoon",),
    "포크": ("fork",),
    "칼": ("knife",),
    "나이프": ("knife",),
    # 가구/집기
    "의자": ("chair",),
    "소파": ("couch",),
    "침대": ("bed",),
    "식탁": ("dining table",),
    "테이블": ("dining table",),
    "책상": ("dining table",),
    "화장실": ("toilet",),
    "변기": ("toilet",),
    "화분": ("potted plant",),
    "식물": ("potted plant",),
    # 가방류
    "가방": ("backpack", "handbag", "suitcase"),
    "배낭": ("backpack",),
    "핸드백": ("handbag",),
    "캐리어": ("suitcase",),
    "여행가방": ("suitcase",),
    "우산": ("umbrella",),
    # 전자/가전
    "티비": ("tv",),
    "tv": ("tv",),
    "텔레비전": ("tv",),
    "노트북": ("laptop",),
    "컴퓨터": ("laptop",),
    "마우스": ("mouse",),
    "리모컨": ("remote",),
    "키보드": ("keyboard",),
    "휴대폰": ("cell phone",),
    "스마트폰": ("cell phone",),
    "핸드폰": ("cell phone",),
    "전자레인지": ("microwave",),
    "오븐": ("oven",),
    "토스터": ("toaster",),
    "싱크대": ("sink",),
    "냉장고": ("refrigerator",),
    # 사무/생활
    "책": ("book",),
    "시계": ("clock",),
    "벽시계": ("clock",),
    "꽃병": ("vase",),
    "가위": ("scissors",),
    "칫솔": ("toothbrush",),
    "테디베어": ("teddy bear",),
    "곰인형": ("teddy bear",),
    "헤어드라이어": ("hair drier",),
    "드라이기": ("hair drier",),
    # 스포츠/교통
    "자전거": ("bicycle",),
    "자동차": ("car",),
    "차": ("car",),
    "오토바이": ("motorcycle",),
    "비행기": ("airplane",),
    "버스": ("bus",),
    "기차": ("train",),
    "열차": ("train",),
    "트럭": ("truck",),
    "보트": ("boat",),
    "배": ("boat",),
    "신호등": ("traffic light",),
    "소화전": ("fire hydrant",),
    "벤치": ("bench",),
    "스포츠공": ("sports ball",),
    "공": ("sports ball",),
    "연": ("kite",),
    "스키": ("skis",),
    "스노보드": ("snowboard",),
    "서핑보드": ("surfboard",),
    "스케이트보드": ("skateboard",),
    "야구방망이": ("baseball bat",),
    "야구글러브": ("baseball glove",),
    "테니스라켓": ("tennis racket",),
    # 사람/동물
    "사람": ("person",),
    "인간": ("person",),
    "고양이": ("cat",),
    "개": ("dog",),
    "새": ("bird",),
    "말": ("horse",),
    "양": ("sheep",),
    "소": ("cow",),
    "코끼리": ("elephant",),
    "곰": ("bear",),
    "얼룩말": ("zebra",),
    "기린": ("giraffe",),
    # 음식
    "바나나": ("banana",),
    "사과": ("apple",),
    "샌드위치": ("sandwich",),
    "오렌지": ("orange",),
    "브로콜리": ("broccoli",),
    "당근": ("carrot",),
    "핫도그": ("hot dog",),
    "피자": ("pizza",),
    "도넛": ("donut",),
    "케이크": ("cake",),
}


def resolve_target_variants(target_object: str) -> tuple[str, ...]:
    """사용자 표현을 COCO 클래스 후보로 변환한다.

    수식어가 붙어도 별칭을 부분 매칭한다(예: "작은 투명병" → ("bottle",)).
    **별칭이 하나도 매칭되지 않으면 빈 튜플**을 반환한다 — 이는 이 대상이
    COCO 80개 클래스 어휘에 없다는 신호로, 호출부는 오픈어휘(VLM) 로컬라이즈
    경로로 라우팅해야 한다.
    """
    target = target_object.strip().lower()
    variants: list[str] = []
    for alias, classes in _TARGET_ALIASES.items():
        if alias in target:
            variants.extend(classes)
    return tuple(dict.fromkeys(variants))


def target_class_variants(target_object: str) -> tuple[str, ...]:
    """수식어가 붙은 사용자 표현을 COCO 클래스 후보로 변환한다 (하위 호환).

    별칭 미매칭 시 기존 동작대로 원문(소문자) 그대로를 반환한다. 매핑 여부를
    확인하려면 ``target_is_coco``를 쓴다.
    """
    variants = resolve_target_variants(target_object)
    if variants:
        return variants
    return (target_object.strip().lower(),)


def target_is_coco(target_object: str) -> bool:
    """대상이 COCO 80개 클래스 어휘에 매핑되는지 여부 (오픈어휘 라우팅 신호)."""
    return bool(resolve_target_variants(target_object))

logger = logging.getLogger(__name__)


def _resolve_class_name(class_id_str: str) -> str:
    """class_id가 숫자 문자열이면 COCO 이름으로 변환, 아니면 그대로 반환."""
    try:
        idx = int(class_id_str)
        if 0 <= idx < len(globals._COCO_CLASSES):
            return globals._COCO_CLASSES[idx]
    except (ValueError, TypeError):
        pass
    return class_id_str


def _select_candidate(
    candidates: list[dict[str, Any]],
    *,
    selection: str,
) -> dict[str, Any]:
    """동일 클래스로 매칭된 다중 후보 중 하나를 selection 전략에 따라 선택한다.

    candidates: 각 원소는 최소 "score"(float)와 "distance_m"(float | None)을 포함하는 dict.
    빈 리스트를 넘기지 않는 것은 호출부 책임이다.

    selection == "nearest": distance_m이 있는 후보 중 최솟값(가장 가까움)을 선택한다.
    거리 정보가 전혀 없으면(모든 후보 distance_m=None) score 최댓값으로 폴백한다.
    그 외(기본값 "score" 포함, 알 수 없는 값도 안전하게 폴백): score 최댓값을 선택한다.
    동점 시 candidates에 먼저 나온 항목을 선택해 기존 동작(첫 매칭 우선)과 호환된다.
    """
    if selection == "nearest":
        with_distance = [c for c in candidates if c.get("distance_m") is not None]
        if with_distance:
            return min(with_distance, key=lambda c: c["distance_m"])
        logger.debug(
            "[_select_candidate] selection=nearest but none of the candidates have "
            "distance info; falling back to score."
        )
    return max(candidates, key=lambda c: c["score"])


def _distance_at_bearing(scan: LaserScan, bearing_rad: float) -> float | None:
    """LaserScan에서 지정 방향(로봇 프레임, rad)의 최소 거리를 반환. 유효 샘플이 없으면 None을 반환한다."""
    threshold = math.radians(15.0)
    ranges = np.array(scan.ranges)
    angles = np.linspace(scan.angle_min, scan.angle_max, len(ranges))
    diffs = (angles - bearing_rad + np.pi) % (2 * np.pi) - np.pi
    mask = (
        (np.abs(diffs) < threshold)
        & (ranges > scan.range_min)
        & (ranges < scan.range_max)
    )
    valid = ranges[mask]
    if len(valid) == 0:
        return None
    valid_finite = valid[np.isfinite(valid)]  # NaN/Inf 제거
    if len(valid_finite) == 0:
        return None
    return float(np.min(valid_finite))


class DepthFrame:
    """한 번 구독한 depth 이미지 + CameraInfo를 여러 bbox 계산에 재사용하기 위한 컨테이너.

    LiDAR의 `scan` 메시지를 한 번 구독해 여러 검출 결과에 재사용하는 기존 패턴과
    동일하게, depth/CameraInfo도 회전 스텝(또는 한 번의 detect 호출)당 한 번만
    구독하고 bbox별 픽셀 샘플링/역투영/TF 변환만 반복한다.
    """

    def __init__(self, depth_img: Any, encoding: str, frame_id: str, stamp: Any, camera_model: Any):
        self.depth_img = depth_img
        self.encoding = encoding
        self.frame_id = frame_id
        self.stamp = stamp
        self.camera_model = camera_model


def _fetch_depth_frame(skill: Any, params: dict[str, Any]) -> DepthFrame | None:
    """depth_topic/camera_info_topic을 구독해 DepthFrame을 만든다.

    skill은 get_string_param/wait_for_message 속성을 가진 BaseSkill 인스턴스.
    depth/CameraInfo 미구독이거나 처리에 실패하면 None을 반환해 호출부가 기존
    LiDAR 기반 방식으로 폴백하게 한다.
    """
    if not bool(params.get("use_depth", True)):
        return None

    camera_source = str(
        params.get("camera") or params.get("camera_source") or ""
    ).strip().lower()
    use_gripper_camera = camera_source in {
        "gripper",
        "wrist",
        "hand",
        "end_effector",
        "eoa",
    }
    depth_param = "gripper_depth_topic" if use_gripper_camera else "depth_topic"
    info_param = (
        "gripper_camera_info_topic" if use_gripper_camera else "camera_info_topic"
    )
    depth_topic = str(params.get("depth_topic") or "").strip()
    camera_info_topic = str(params.get("camera_info_topic") or "").strip()
    if not depth_topic:
        depth_topic = skill.get_string_param(params, depth_param, "")
    if not camera_info_topic:
        camera_info_topic = skill.get_string_param(params, info_param, "")
    if not depth_topic or not camera_info_topic:
        return None

    camera_info_msg = skill.wait_for_message(
        CameraInfo, camera_info_topic, timeout_sec=1.0, max_age_sec=1.0
    )
    camera_model = depth3d.build_camera_model(camera_info_msg) if camera_info_msg else None
    if camera_model is None:
        return None

    depth_msg = skill.wait_for_message(
        Image, depth_topic, timeout_sec=1.0, max_age_sec=1.0
    )
    if depth_msg is None:
        return None

    try:
        from cv_bridge import CvBridge

        depth_img = CvBridge().imgmsg_to_cv2(depth_msg, desired_encoding="passthrough")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[depth3d] Failed to process depth image: %s", exc)
        return None

    return DepthFrame(
        depth_img=depth_img,
        encoding=depth_msg.encoding,
        frame_id=depth_msg.header.frame_id,
        stamp=depth_msg.header.stamp,
        camera_model=camera_model,
    )


def _is_gripper_camera_params(params: dict[str, Any]) -> bool:
    """params dict에서 카메라 출처가 그리퍼 카메라인지 판별.

    detect.py의 _is_gripper_camera와 동일 기준. core.py가 detect.py를 역으로
    임포트하지 않도록 로컬 복제.
    """
    camera_source = str(
        params.get("camera") or params.get("camera_source") or ""
    ).strip().lower()
    return camera_source in {"gripper", "wrist", "hand", "end_effector", "eoa"}


def _head_rotate_deg(skill: Any, params: dict[str, Any]) -> float:
    """head_image_rotate_deg(도)를 실행 params → 노드 파라미터 순으로 읽는다.

    헤드 카메라 upright 회전 보정(option 2) 시, depth/camera_info는 raw 좌표계를
    쓰므로 upright bbox 픽셀을 raw로 역회전하기 위한 각도. 기본 0.0 = 보정 없음.
    """
    val = params.get("head_image_rotate_deg")
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    node = getattr(skill, "node", None)
    if node is not None:
        try:
            if node.has_parameter("head_image_rotate_deg"):
                pv = node.get_parameter("head_image_rotate_deg").get_parameter_value()
                if hasattr(pv, "double_value"):
                    return float(pv.double_value)
        except Exception:  # noqa: BLE001
            pass
    return 0.0


def _depth_point_for_pixel(
    skill: Any,
    depth_frame: DepthFrame | None,
    params: dict[str, Any],
    cx: float,
    cy: float,
) -> tuple[tuple[float, float, float] | None, float | None]:
    """DepthFrame과 bbox 중심 (cx,cy)로부터 map 프레임 3D 포인트를 계산한다.

    map은 정적 프레임이므로, 로봇이 감지 이후 이동하더라도(예: navigate_to 후 grasp)
    저장된 좌표가 base_link 스냅샷처럼 stale해지지 않는다. base_link 등 로봇 기준
    프레임이 필요한 소비 시점(조작 등)에는 그 시점의 최신 TF로 다시 변환해야 한다
    (manipulation_skill/core.py의 _transform_pose_goal 참조).

    depth 값 무효, TF 실패 등 어느 단계든 실패하면 (None, None)을 반환한다.
    """
    if depth_frame is None:
        return None, None

    # 헤드 카메라 upright 보정(option 2): bbox(cx,cy)는 upright 픽셀 좌표이나
    # depth/camera_info는 raw 그대로 → depth 샘플링/역투영 전 raw 픽셀로 역회전.
    # 그리퍼 카메라는 세로 마운트가 아니므로 보정 제외. rotate_deg=0 이면 no-op.
    rotate_deg = _head_rotate_deg(skill, params)
    if rotate_deg != 0.0 and not _is_gripper_camera_params(params):
        raw_h, raw_w = depth_frame.depth_img.shape[:2]
        up_w = int(params.get("upright_image_width", 0) or 0)
        up_h = int(params.get("upright_image_height", 0) or 0)
        cx, cy = depth3d.unrotate_pixel(cx, cy, rotate_deg, raw_w, raw_h, up_w, up_h)

    depth_window = int(params.get("depth_window_px", 5))
    depth_m = depth3d.sample_depth_at_bbox(
        depth_frame.depth_img, depth_frame.encoding, cx, cy, window=depth_window
    )
    if depth_m is None:
        return None, None

    cam_xyz = depth3d.deproject_pixel(depth_frame.camera_model, cx, cy, depth_m)
    tf_buffer = getattr(skill.node, "_tf_buffer", None)
    map_frame = getattr(skill.node, "_map_frame", "map")
    depth_point_map = depth3d.transform_point_to_frame(
        tf_buffer,
        cam_xyz,
        depth_frame.frame_id,
        map_frame,
        stamp=depth_frame.stamp,
    )
    return depth_point_map, depth_m


def _compute_depth_point_base(
    skill: Any,
    params: dict[str, Any],
    cx: float,
    cy: float,
) -> tuple[tuple[float, float, float] | None, float | None]:
    """단발성 호출용: depth/CameraInfo 구독부터 3D 포인트 계산까지 한 번에 수행한다.

    여러 bbox에 대해 반복 호출할 때는 `_fetch_depth_frame` + `_depth_point_for_pixel`을
    직접 사용해 depth/CameraInfo 구독을 한 번만 하도록 하는 것이 더 효율적이다.
    """
    depth_frame = _fetch_depth_frame(skill, params)
    return _depth_point_for_pixel(skill, depth_frame, params, cx, cy)


def _target_pose_metadata(skill: Any, depth_point_map: tuple[float, float, float]) -> dict[str, Any]:
    """depth3d로 계산한 map 프레임 3D 포인트를 물체 메모리 metadata["target_pose"] 형태로 변환."""
    map_frame = getattr(skill.node, "_map_frame", "map")
    return {
        "frame_id": map_frame,
        "position": {
            "x": depth_point_map[0],
            "y": depth_point_map[1],
            "z": depth_point_map[2],
        },
        "orientation": {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
    }
