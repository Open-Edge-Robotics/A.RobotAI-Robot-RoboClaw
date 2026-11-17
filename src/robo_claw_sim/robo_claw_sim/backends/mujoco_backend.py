"""MuJoCo 물리 백엔드(선택 의존성 ``mujoco``).

설계 요점
- 세계는 ``World``(시나리오 YAML)에서 MJCF 로 매번 생성한다. 별도 자산 없음.
- 로봇은 free joint + 원기둥. 명령은 매 phys step 마다 자유 관절 속도로 주입하므로
  바퀴 마찰·서스펜션 파라미터 튜닝 없이도 재현성이 유지된다(운동학 백엔드와 같은
  속도 모델). 접촉은 벽·장애물에서만 발생한다.
- 충돌 시 운동학 백엔드와 같은 규칙을 쓴다: 충돌 카운트를 올리고 그 tick 의 남은
  이동을 버린다(관통 금지).
- 센서는 MuJoCo ray casting(``mj_ray``)으로 합성한다. 로봇 body 는 광선에서 제외해
  자기 몸체를 장애물로 보지 않는다.

Phase 0 범위: 베이스 주행 + LaserScan. 카메라 렌더·관절/파지 동역학은 후속 단계다.
"""

from __future__ import annotations

import math

import mujoco
import numpy as np

from ..raycast import ScanConfig
from ..world import Pose2D, Twist2D, World

# 관절 목표 접근 속도(스텝당 m). 0.005s 스텝 기준 4 m/s 로 충분히 빠르다.
JOINT_MAX_DELTA = 0.02

from .manipulation import (
    GRASP_REACH_MARGIN,
    GRASP_WIDTH_MARGIN,
    GraspState,
    ManipulationConfig,
)
from .scene import (
    HEAD_CAMERA_NAME,
    OBSTACLE_PREFIX,
    ROBOT_BODY_NAME,
    ROBOT_GEOM_NAME,
    ROBOT_Z,
    WALL_PREFIX,
    build_scene_xml,
)


class MujocoBackend:
    """MuJoCo 로 세계를 스텝하고 ray casting 으로 LaserScan 을 만든다."""

    name = "mujoco"

    def __init__(
        self,
        world: World,
        scan_config: ScanConfig | None = None,
        manipulation: ManipulationConfig | None = None,
        viewer: bool = False,
    ) -> None:
        self.world = world
        self.scan_config = scan_config or ScanConfig()
        self.manipulation = manipulation
        self._model = mujoco.MjModel.from_xml_string(build_scene_xml(world, manipulation))
        self._data = mujoco.MjData(self._model)
        mujoco.mj_forward(self._model, self._data)
        self._timestep = float(self._model.opt.timestep)
        self._base_body = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, ROBOT_BODY_NAME)
        self._base_geom = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_GEOM, ROBOT_GEOM_NAME)
        self._obstacle_geoms = self._collect_obstacle_geoms()
        self._point = np.zeros(3, dtype=np.float64)
        self._vector = np.zeros(3, dtype=np.float64)
        self._geomid = np.zeros(1, dtype=np.int32)
        self._renderer = None
        self._setup_manipulation()
        # 대화형 뷰어(디스플레이 필요). 시각화 전용이며 판정에는 영향을 주지 않는다.
        self._viewer = None
        if viewer:
            # mujoco.viewer 는 하위 모듈이라 명시적으로 import 해야 한다(패키지 루트에
            # 자동 노출되지 않는다). glfw 기반이므로 DISPLAY/X11 이 필요하다.
            try:
                from mujoco import viewer as mujoco_viewer
            except ImportError as exc:  # pragma: no cover - 매우 오래된 패키지
                raise RuntimeError(
                    "mujoco.viewer 를 import 할 수 없습니다. `pip install -U mujoco` 로 갱신하세요."
                ) from exc
            try:
                self._viewer = mujoco_viewer.launch_passive(self._model, self._data)
            except Exception as exc:
                raise RuntimeError(
                    f"MuJoCo 뷰어를 열 수 없습니다: {exc}. "
                    "DISPLAY/X11(또는 GUI 세션)에서 실행하세요."
                ) from exc

    # ------------------------------------------------------------------ 계약
    def step(self, twist: Twist2D, dt: float) -> None:
        if dt <= 0.0:
            return

        command = self.world.clamp_twist(twist)
        self.world.last_command = command
        self.world.elapsed_sec += dt
        start = self.world.robot

        steps = max(1, int(round(dt / self._timestep)))
        for _ in range(steps):
            self._apply_velocity(command)
            self._apply_joint_targets()
            mujoco.mj_step(self._model, self._data)
            self._update_grasp()
            if self._viewer is not None and self._viewer.is_running():
                self._viewer.sync()
            if self._in_contact_with_obstacle():
                # 운동학 백엔드와 같은 규칙: 충돌하면 남은 이동을 버린다.
                self.world.collisions += 1
                break

        pose = self._read_pose()
        self.world.robot = pose
        self.world.traveled_m += math.hypot(pose.x - start.x, pose.y - start.y)

    def scan(self, config: ScanConfig | None = None) -> list[float]:
        cfg = config or self.scan_config
        mujoco.mj_forward(self._model, self._data)
        pose = self._read_pose()
        ranges: list[float] = []
        self._point[:] = (pose.x, pose.y, ROBOT_Z)
        for bearing in cfg.bearings():
            angle = pose.yaw + bearing
            self._vector[:] = (math.cos(angle), math.sin(angle), 0.0)
            distance = float(
                mujoco.mj_ray(
                    self._model,
                    self._data,
                    self._point,
                    self._vector,
                    None,
                    True,
                    self._base_body,
                    self._geomid,
                )
            )
            if distance < 0.0 or distance > cfg.range_max:
                ranges.append(math.inf)
            else:
                ranges.append(max(distance, cfg.range_min))
        return ranges

    def teleport(self, pose: Pose2D) -> bool:
        if not self.world.is_free(pose.x, pose.y, self.world.robot_radius):
            return False
        self.world.robot = pose
        self._data.qpos[0:7] = (pose.x, pose.y, ROBOT_Z, 1.0, 0.0, 0.0, 0.0)
        self._data.qvel[:] = 0.0
        self._set_orientation(pose.yaw)
        mujoco.mj_forward(self._model, self._data)
        return True

    # ------------------------------------------------------------- 카메라
    def render_camera(self, width: int = 640, height: int = 480) -> tuple[np.ndarray, np.ndarray]:
        """헤드 카메라 RGB(uint8 HxWx3)와 depth(float32 HxW, m)를 렌더한다.

        오프스크린 렌더링이므로 ``MUJOCO_GL=egl``(GPU) 또는 ``osmesa``(CPU)가
        필요하다. 설정이 없으면 렌더러 생성이 실패하므로 안내와 함께 예외를 올린다.
        """
        renderer = self._ensure_renderer(width, height)
        renderer.update_scene(self._data, camera=HEAD_CAMERA_NAME)
        rgb = renderer.render().copy()
        renderer.enable_depth_rendering()
        try:
            depth = renderer.render().copy()
        finally:
            renderer.disable_depth_rendering()
        return rgb.astype(np.uint8), depth.astype(np.float32)

    def _ensure_renderer(self, width: int, height: int):  # noqa: ANN202
        if self._viewer is not None:
            self._viewer.close()
            self._viewer = None
        if self._renderer is not None:
            return self._renderer
        try:
            self._renderer = mujoco.Renderer(self._model, height=height, width=width)
        except Exception as exc:
            raise RuntimeError(
                "MuJoCo 오프스크린 렌더러를 만들 수 없습니다. "
                "MUJOCO_GL=egl(GPU) 또는 MUJOCO_GL=osmesa(CPU)를 설정한 뒤 다시 실행하세요."
            ) from exc
        return self._renderer

    def close(self) -> None:
        """MuJoCo 핸들은 참조가 끊기면 해제된다. 명시적으로 참조만 지운다."""
        if self._renderer is not None:
            self._renderer.close()
            self._renderer = None
        self._data = None  # type: ignore[assignment]
        self._model = None  # type: ignore[assignment]

    # ---------------------------------------------------------------- 내부
    def _apply_velocity(self, command: Twist2D) -> None:
        yaw = self._read_pose().yaw
        # 평면 주행 모델: 높이를 고정한다(중력 자유낙하로 조작 기준 높이가 흔들리지 않게).
        self._data.qpos[2] = ROBOT_Z
        self._data.qvel[0] = command.vx * math.cos(yaw)
        self._data.qvel[1] = command.vx * math.sin(yaw)
        self._data.qvel[2] = 0.0
        self._data.qvel[3] = 0.0
        self._data.qvel[4] = 0.0
        self._data.qvel[5] = command.vyaw

    def _set_orientation(self, yaw: float) -> None:
        # qpos[3:7] 은 (w, x, y, z).
        self._data.qpos[3] = math.cos(yaw / 2.0)
        self._data.qpos[4] = 0.0
        self._data.qpos[5] = 0.0
        self._data.qpos[6] = math.sin(yaw / 2.0)

    def _read_pose(self) -> Pose2D:
        qpos = self._data.qpos
        w, x, y, z = float(qpos[3]), float(qpos[4]), float(qpos[5]), float(qpos[6])
        yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
        return Pose2D(x=float(qpos[0]), y=float(qpos[1]), yaw=yaw)

    def _collect_obstacle_geoms(self) -> set[int]:
        geoms: set[int] = set()
        for geom_id in range(self._model.ngeom):
            name = mujoco.mj_id2name(self._model, mujoco.mjtObj.mjOBJ_GEOM, geom_id) or ""
            if name.startswith(WALL_PREFIX) or name.startswith(OBSTACLE_PREFIX):
                geoms.add(geom_id)
        return geoms

    # ------------------------------------------------------ 조작(매니퓰레이션)
    def set_joint_targets(self, targets: dict[str, float]) -> None:
        """관절 목표값을 설정한다. 그리퍼 폐합/개방도 같은 경로로 처리한다."""
        if self.manipulation is None:
            return
        for name, value in targets.items():
            if name not in self._joint_qpos:
                continue
            self._joint_targets[name] = self.manipulation.clamp(name, float(value))

    def joint_positions(self) -> dict[str, float]:
        """현재 관절값(그리퍼 포함)."""
        positions: dict[str, float] = {}
        for name, address in self._joint_qpos.items():
            positions[name] = float(self._data.qpos[address])
        return positions

    def grasp_state(self) -> GraspState:
        """마지막 스텝 기준 파지 상태."""
        return self._grasp

    def object_pose(self, name: str) -> Pose2D | None:
        """장면에 넣은 파지 대상 물체의 (x, y, yaw)."""
        body = self._object_bodies.get(name)
        if body is None:
            return None
        address = self._model.body_jntadr[body]
        if address < 0:
            return None
        qpos_adr = self._model.jnt_qposadr[address]
        qpos = self._data.qpos
        w, x, y, z = (
            float(qpos[qpos_adr + 3]),
            float(qpos[qpos_adr + 4]),
            float(qpos[qpos_adr + 5]),
            float(qpos[qpos_adr + 6]),
        )
        yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
        return Pose2D(x=float(qpos[qpos_adr]), y=float(qpos[qpos_adr + 1]), yaw=yaw)

    def _setup_manipulation(self) -> None:
        self._joint_qpos: dict[str, int] = {}
        self._joint_targets: dict[str, float] = {}
        self._object_bodies: dict[str, int] = {}
        self._contact_counts: dict[str, int] = {}
        self._grasp = GraspState()
        config = self.manipulation
        if config is None:
            return
        for name in config.joint_names():
            joint_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_JOINT, name)
            if joint_id < 0:
                continue
            address = int(self._model.jnt_qposadr[joint_id])
            self._joint_qpos[name] = address
            # 초기 자세는 home 값에서 시작한다(장면 qpos0 는 0).
            home = config.home.get(name, 0.0)
            self._joint_targets[name] = home
            self._data.qpos[address] = home
        for obj in self.world.objects:
            body_id = mujoco.mj_name2id(
                self._model, mujoco.mjtObj.mjOBJ_BODY, config.object_body(obj)
            )
            if body_id >= 0:
                self._object_bodies[obj.name] = body_id

    def _apply_joint_targets(self) -> None:
        """관절 목표값을 위치 서보 없이 직접 반영한다(결정론 유지).

        실제 매니퓰레이터는 위치 제어기를 쓰지만, 시뮬레이터 판정에 필요한 것은
        목표 자세 도달과 그리퍼 폐합 시 접촉 발생이므로 직접 대입으로 충분하다.
        """
        if self.manipulation is None:
            return
        for name, address in self._joint_qpos.items():
            current = float(self._data.qpos[address])
            target = self._joint_targets[name]
            # 실제 위치 서보처럼 목표로 점진 이동한다(즉시 대입하면 접촉이 순간적으로
            # 발생·소멸해 파지 판정과 물체 반응이 비현실적으로 튄다).
            delta = max(-JOINT_MAX_DELTA, min(JOINT_MAX_DELTA, target - current))
            self._data.qpos[address] = current + delta

    def _update_grasp(self) -> None:
        config = self.manipulation
        if config is None:
            return

        jaw_ids = {
            mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_GEOM, name)
            for name in config.jaw_geoms
        }
        jaw_ids.discard(-1)

        # 조와 물체 사이 접촉 수를 센다.
        counts: dict[str, int] = {name: 0 for name in self._object_bodies}
        data = self._data
        for index in range(data.ncon):
            contact = data.contact[index]
            geoms = (contact.geom1, contact.geom2)
            if not (geoms[0] in jaw_ids or geoms[1] in jaw_ids):
                continue
            for name, body in self._object_bodies.items():
                object_geom = self._model.body_geomadr[body]
                if object_geom in geoms:
                    counts[name] += 1

        gripper_value = self.joint_positions().get(config.gripper_joint, 0.0)
        gap = config.object_gap(gripper_value)

        # 히스테리시스: 한 번 성립한 파지는 조가 열릴 때까지 유지한다. 접촉 해석은
        # 스텝마다 접촉 수가 흔들려(미세 침투/분리) 재검증만 하면 파지가 끊긴다.
        held = self._grasp.grasped_object
        if held is not None:
            if gap <= 2.0 * config.object_radius + GRASP_WIDTH_MARGIN:
                self._grasp = GraspState(
                    grasped_object=held,
                    contact_jaws=int(sum(counts.values())),
                    gripper_value=gripper_value,
                    gap_m=gap,
                )
                self._carry_object(held)
                return

        grasped: str | None = None
        for name, count in counts.items():
            if count < 2:  # 양쪽 조가 동시에 닿아야 파지로 본다.
                continue
            object_pose = self.object_pose(name)
            ee_pose = self._ee_pose()
            if object_pose is None or ee_pose is None:
                continue
            radius = config.object_radius
            if gap > 2.0 * radius + GRASP_WIDTH_MARGIN:
                continue
            if abs(object_pose.x - ee_pose.x) > radius + GRASP_REACH_MARGIN:
                continue
            if abs(object_pose.y - ee_pose.y) > radius + GRASP_REACH_MARGIN:
                continue
            grasped = name
            break

        self._grasp = GraspState(
            grasped_object=grasped,
            contact_jaws=int(sum(counts.values())),
            gripper_value=gripper_value,
            gap_m=gap,
        )
        if grasped is not None:
            self._carry_object(grasped)

    def _ee_pose(self) -> Pose2D | None:
        config = self.manipulation
        if config is None:
            return None
        body_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, config.ee_body)
        if body_id < 0:
            return None
        xpos = self._data.xpos[body_id]
        return Pose2D(x=float(xpos[0]), y=float(xpos[1]), yaw=0.0)

    def _carry_object(self, name: str) -> None:
        """파지 성공한 물체를 말단에 붙여 운반한다.

        MuJoCo 는 런타임에 등가 제약을 추가할 수 없으므로, 파지 상태에서 물체
        자세를 말단 자세로 맞추는 부착 근사(kinematic attach)를 쓴다. 마찰 기반
        파지 물리 튜닝은 실기 상관 검증 단계에서 다룬다.
        """
        body = self._object_bodies.get(name)
        if body is None:
            return
        config = self.manipulation
        ee = self._ee_pose()
        if config is None or ee is None:
            return
        joint_adr = self._model.body_jntadr[body]
        if joint_adr < 0:
            return
        qpos_adr = self._model.jnt_qposadr[joint_adr]
        qvel_adr = self._model.jnt_dofadr[joint_adr]
        body_id = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, config.ee_body)
        z = float(self._data.xpos[body_id][2]) if body_id >= 0 else ROBOT_Z
        # 물체 중심은 조 중심(= 말단 + 전방 오프셋)에 둔다.
        forward_x = config.jaw_forward_x * math.cos(ee.yaw)
        forward_y = config.jaw_forward_x * math.sin(ee.yaw)
        self._data.qpos[qpos_adr : qpos_adr + 7] = (
            ee.x + forward_x,
            ee.y + forward_y,
            z,
            1.0,
            0.0,
            0.0,
            0.0,
        )
        self._data.qvel[qvel_adr : qvel_adr + 6] = 0.0

    def _in_contact_with_obstacle(self) -> bool:
        data = self._data
        for index in range(data.ncon):
            contact = data.contact[index]
            if contact.geom1 == self._base_geom or contact.geom2 == self._base_geom:
                other = contact.geom2 if contact.geom1 == self._base_geom else contact.geom1
                if other in self._obstacle_geoms:
                    return True
        return False
