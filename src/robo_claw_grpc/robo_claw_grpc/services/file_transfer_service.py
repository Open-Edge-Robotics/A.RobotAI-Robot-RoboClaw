import os
from collections.abc import AsyncIterable

import grpc
from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.robo_pb2 import (
    DownloadFileRequest,
    DownloadFileResponse,
    UploadFileRequest,
    UploadFileResponse,
)
from robo_claw_grpc.utils import LogHelper

CHUNK_SIZE = 1024 * 1024  # 1MB


class FileTransferService:
    def __init__(self, config: ConfigModel):
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._config = config
        self._transfer_dir = config.file_transfer_dir

        if not os.path.exists(self._transfer_dir):
            os.makedirs(self._transfer_dir, exist_ok=True)

    def _get_secure_file_path(self, filename: str) -> str:
        safe_filename = os.path.basename(filename)
        return os.path.join(self._transfer_dir, safe_filename)

    async def UploadFile(
        self, request_iterator: AsyncIterable[UploadFileRequest], context: ServicerContext
    ) -> UploadFileResponse:
        filename = None
        file_path = None

        try:
            async for chunk in request_iterator:
                if not filename:
                    if not chunk.filename:
                        await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Filename required.")
                    filename = chunk.filename
                    file_path = self._get_secure_file_path(filename)
                    self._logger.info(f"Upload started: {filename}")
                    with open(file_path, "wb") as f:
                        f.write(chunk.chunk_data)
                else:
                    if file_path is None:
                        await context.abort(grpc.StatusCode.INTERNAL, "File path not initialized.")
                    with open(file_path, "ab") as f:
                        f.write(chunk.chunk_data)

            self._logger.info(f"Upload complete: {filename}")
            return UploadFileResponse(success=True, message=f"{filename} 업로드 완료")

        except Exception as e:
            if isinstance(e, grpc.RpcError):
                raise e
            self._logger.error(f"Upload error: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, f"업로드 실패: {str(e)}")

    async def DownloadFile(
        self, request: DownloadFileRequest, context: ServicerContext
    ) -> AsyncIterable[DownloadFileResponse]:
        try:
            if not request.filename:
                await context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Filename required.")

            file_path = self._get_secure_file_path(request.filename)
            self._logger.info(f"Download started: {request.filename}")

            if not os.path.exists(file_path):
                await context.abort(grpc.StatusCode.NOT_FOUND, f"{request.filename} 없음")

            with open(file_path, "rb") as f:
                while True:
                    chunk_data = f.read(CHUNK_SIZE)
                    if not chunk_data:
                        break
                    yield DownloadFileResponse(chunk_data=chunk_data)

            self._logger.info(f"Download complete: {request.filename}")

        except Exception as e:
            if isinstance(e, grpc.RpcError):
                raise e
            self._logger.error(f"Download error: {e}")
            await context.abort(grpc.StatusCode.INTERNAL, f"다운로드 실패: {str(e)}")
