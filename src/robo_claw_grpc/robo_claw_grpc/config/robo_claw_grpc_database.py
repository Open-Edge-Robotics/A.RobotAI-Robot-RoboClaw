"""robo_claw_grpc SQLite 데이터베이스 엔진"""


from sqlalchemy import Engine
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlmodel import SQLModel, create_engine, text

from robo_claw_grpc.constants import DATABASE_NAME
from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.utils import LogHelper


class RoboClawGrpcDatabase:
    def __init__(self, config: ConfigModel):
        self._logger = LogHelper().get_logger(__name__)
        self._engine = self._create_database()

    async def init_database(self):
        if self._engine is not None:
            async with self._engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)
                await conn.execute(text("PRAGMA journal_mode=WAL;"))

    def _create_database(self) -> AsyncEngine | None:
        try:
            self._logger.info("Creating database engine...")
            return create_async_engine(f"sqlite+aiosqlite:///{DATABASE_NAME}")
        except Exception as e:
            self._logger.error(f"Failed to create database engine: {e}")
            return None
        finally:
            self._logger.info(f"Database: {DATABASE_NAME}")

    def get_engine(self) -> AsyncEngine | None:
        return self._engine

    def get_sync_engine(self) -> Engine | None:
        try:
            return create_engine(f"sqlite:///{DATABASE_NAME}")
        except Exception as e:
            self._logger.error(f"Failed to create synchronous engine: {e}")
            return None
