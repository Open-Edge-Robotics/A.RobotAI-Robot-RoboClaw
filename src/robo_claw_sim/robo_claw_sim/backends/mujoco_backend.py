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
from .scene import (
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

    def __init__(self, world: World, scan_config: ScanConfig | None = None) -> None:
        self.world = world
        self.scan_config = scan_config or ScanConfig()
        self._model = mujoco.MjModel.from_xml_string(build_scene_xml(world))
        self._data = mujoco.MjData(self._model)
        mujoco.mj_forward(self._model, self._data)
        self._timestep = float(self._model.opt.timestep)
        self._base_body = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_BODY, ROBOT_BODY_NAME)
        self._base_geom = mujoco.mj_name2id(self._model, mujoco.mjtObj.mjOBJ_GEOM, ROBOT_GEOM_NAME)
        self._obstacle_geoms = self._collect_obstacle_geoms()
        self._point = np.zeros(3, dtype=np.float64)
        self._vector = np.zeros(3, dtype=np.float64)
        self._geomid = np.zeros(1, dtype=np.int32)

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
            mujoco.mj_step(self._model, self._data)
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
        self._data.qpos[:] = (pose.x, pose.y, ROBOT_Z, 1.0, 0.0, 0.0, 0.0)
        self._data.qvel[:] = 0.0
        self._set_orientation(pose.yaw)
        mujoco.mj_forward(self._model, self._data)
        return True

    def close(self) -> None:
        """MuJoCo 핸들은 참조가 끊기면 해제된다. 명시적으로 참조만 지운다."""
        self._data = None  # type: ignore[assignment]
        self._model = None  # type: ignore[assignment]

    # ---------------------------------------------------------------- 내부
    def _apply_velocity(self, command: Twist2D) -> None:
        yaw = self._read_pose().yaw
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

    def _in_contact_with_obstacle(self) -> bool:
        data = self._data
        for index in range(data.ncon):
            contact = data.contact[index]
            if contact.geom1 == self._base_geom or contact.geom2 == self._base_geom:
                other = contact.geom2 if contact.geom1 == self._base_geom else contact.geom1
                if other in self._obstacle_geoms:
                    return True
        return False
