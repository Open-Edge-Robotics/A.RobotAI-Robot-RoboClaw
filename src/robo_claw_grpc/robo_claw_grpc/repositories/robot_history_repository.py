"""robo_claw_grpc 로봇 이력 레포지토리"""

import asyncio
import os
from collections.abc import Sequence
from contextlib import asynccontextmanager

from sqlmodel import col, delete, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from robo_claw_grpc.config import RoboClawGrpcDatabase
from robo_claw_grpc.constants import DATABASE_NAME
from robo_claw_grpc.exceptions import DatabaseError
from robo_claw_grpc.models import ConfigModel, RobotCameraImage, RobotHistory
from robo_claw_grpc.utils import LogHelper


class RobotHistoryRepository:
    """로봇 이력 데이터 레포지토리"""

    def __init__(self, db: RoboClawGrpcDatabase, config: ConfigModel):
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._engine = db.get_engine()
        self._config = config
        self._cleanup_lock = asyncio.Lock()

    @asynccontextmanager
    async def _session_scope(self):
        if self._engine is None:
            raise DatabaseError("Database engine is not initialized")
        try:
            async with AsyncSession(self._engine) as session:
                yield session
        except Exception as e:
            self._logger.exception(f"DB operation error: {e}")
            raise DatabaseError(original_exception=e) from e

    async def get_history_list(self) -> Sequence[RobotHistory]:
        async with self._session_scope() as session:
            result = await session.exec(select(RobotHistory))
            return result.all()

    async def get_latest_camera_image(self, robot_id: str, fmt: str) -> RobotCameraImage | None:
        async with self._session_scope() as session:
            statement = (
                select(RobotCameraImage)
                .where(
                    RobotCameraImage.robot_id == robot_id,
                    RobotCameraImage.format == fmt,
                )
                .order_by(RobotCameraImage.timestamp.desc())  # type: ignore
            )
            result = await session.exec(statement)
            return result.first()

    async def get_latest_history(self, robot_id: str) -> RobotHistory | None:
        async with self._session_scope() as session:
            statement = (
                select(RobotHistory)
                .where(RobotHistory.robot_id == robot_id)
                .order_by(RobotHistory.last_seen.desc())  # type: ignore
            )
            result = await session.exec(statement)
            return result.first()

    async def add_history(self, entry: RobotHistory) -> RobotHistory:
        async with self._session_scope() as session:
            session.add(entry)
            await session.commit()
            await session.refresh(entry)

        asyncio.create_task(self._cleanup_history())
        return entry

    async def _cleanup_history(self):
        if self._cleanup_lock.locked():
            return

        async with self._cleanup_lock:
            try:
                loop = asyncio.get_running_loop()
                db_exists = await loop.run_in_executor(None, os.path.exists, DATABASE_NAME)
                if db_exists:
                    db_size_bytes = await loop.run_in_executor(None, os.path.getsize, DATABASE_NAME)
                    db_size_mb = db_size_bytes / (1024 * 1024)

                    if db_size_mb > self._config.max_db_size_mb:
                        self._logger.warning(f"DB size exceeded ({db_size_mb:.2f}MB), cleaning up...")
                        async with self._session_scope() as session:
                            subquery = select(RobotHistory.id).order_by(col(RobotHistory.id)).limit(1000)
                            await session.exec(delete(RobotHistory).where(col(RobotHistory.id).in_(subquery)))
                            await session.commit()

                async with self._session_scope() as session:
                    count_result = await session.exec(select(func.count(RobotHistory.id)))  # type: ignore
                    count = count_result.one() or 0

                    if count > self._config.max_history_count:
                        delete_count = count - self._config.max_history_count
                        self._logger.info(f"Deleting {delete_count} history records...")
                        subquery = select(RobotHistory.id).order_by(col(RobotHistory.id)).limit(delete_count)  # type: ignore
                        await session.exec(delete(RobotHistory).where(col(RobotHistory.id).in_(subquery)))  # type: ignore
                        await session.commit()

            except Exception as e:
                self._logger.exception(f"History cleanup error: {e}")
