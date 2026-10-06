import asyncio
import os
import shlex
from collections.abc import AsyncGenerator
from typing import Any

from grpc.aio import ServicerContext

from robo_claw_grpc.models import ConfigModel
from robo_claw_grpc.robo_pb2 import CommandRequest, CommandResponse
from robo_claw_grpc.utils import LogHelper, handle_grpc_errors, log_to_history

# 파이프라인 하위 세그먼트에 허용되는 필터 명령.
# 임의 프로세스를 띄울 수 있는 도구(awk, sed, xargs, find, tee, sort -o 등)는
# 의도적으로 제외한다. 여기 있는 명령들은 stdin을 읽어 stdout으로만 출력한다.
_ALLOWED_PIPE_FILTERS = frozenset({
    "grep", "egrep", "fgrep", "head", "tail", "wc", "cut", "tr", "nl", "jq",
})

# 쉘을 거치지 않으므로 아래 연산자는 해석되지 않는다. 사용자가 동작할 것이라
# 오해하지 않도록 명시적으로 거부한다.
_FORBIDDEN_TOKENS = frozenset({";", "&", "&&", "||", "<", ">", ">>", "<<", "|&", "|;"})

_SECURITY_ERROR = "보안 위반: 'ros2' 명령만 허용됩니다."


def _parse_ros_command(command: str) -> tuple[list[list[str]], str]:
    """``ros2 ... | grep ...`` 형태를 검증된 파이프라인 세그먼트로 분해한다.

    쉘을 거치지 않고 각 세그먼트를 직접 exec 하기 위한 파싱이다. 쉘을 쓰지
    않으므로 명령 치환/체이닝으로 임의 명령이 실행될 여지가 없다.

    Returns:
        (세그먼트 목록, 오류 메시지). 오류가 없으면 오류 메시지는 빈 문자열.
    """
    if not command:
        return [], _SECURITY_ERROR

    # 쉘이 없으므로 치환은 일어나지 않지만, 오해를 막기 위해 선제 거부한다.
    if "`" in command or "$(" in command:
        return [], "보안 위반: 명령 치환(`...`, $(...))은 허용되지 않습니다."

    try:
        # punctuation_chars=True 로 두어야 따옴표 안의 '|' 는 보존하면서
        # 'list|grep' 처럼 붙어 있는 파이프도 별도 토큰으로 분리된다.
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError as e:
        return [], f"명령 파싱 실패: {e}"

    if not tokens:
        return [], _SECURITY_ERROR

    segments: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in _FORBIDDEN_TOKENS:
            return [], f"보안 위반: 쉘 연산자 '{token}'는 허용되지 않습니다."
        if token == "|":
            if not current:
                return [], "명령 파싱 실패: 파이프 앞에 명령이 없습니다."
            segments.append(current)
            current = []
            continue
        current.append(token)

    if not current:
        return [], "명령 파싱 실패: 파이프 뒤에 명령이 없습니다."
    segments.append(current)

    if segments[0][0] != "ros2":
        return [], _SECURITY_ERROR

    for segment in segments[1:]:
        if segment[0] not in _ALLOWED_PIPE_FILTERS:
            allowed = ", ".join(sorted(_ALLOWED_PIPE_FILTERS))
            return [], (
                f"보안 위반: 파이프 대상 '{segment[0]}'는 허용되지 않습니다. "
                f"허용 필터: {allowed}"
            )

    return segments, ""


async def _spawn_pipeline(segments: list[list[str]]) -> list[asyncio.subprocess.Process]:
    """검증된 세그먼트들을 os.pipe로 연결해 쉘 없이 실행한다.

    마지막 프로세스의 stdout만 PIPE로 노출하고, 모든 프로세스의 stderr는
    개별적으로 수집할 수 있도록 PIPE로 연결한다.
    """
    procs: list[asyncio.subprocess.Process] = []
    prev_read_fd: int | None = None
    try:
        for index, args in enumerate(segments):
            is_last = index == len(segments) - 1
            write_fd: int | None = None
            read_fd: int | None = None
            if is_last:
                stdout_target: Any = asyncio.subprocess.PIPE
            else:
                read_fd, write_fd = os.pipe()
                stdout_target = write_fd

            proc = await asyncio.create_subprocess_exec(
                args[0],
                *args[1:],
                stdin=prev_read_fd if prev_read_fd is not None else asyncio.subprocess.DEVNULL,
                stdout=stdout_target,
                stderr=asyncio.subprocess.PIPE,
            )
            procs.append(proc)

            # 부모 프로세스에 남은 파이프 끝단은 즉시 닫아야 EOF가 전달된다.
            if write_fd is not None:
                os.close(write_fd)
            if prev_read_fd is not None:
                os.close(prev_read_fd)
            prev_read_fd = read_fd
    except BaseException:
        if prev_read_fd is not None:
            os.close(prev_read_fd)
        for proc in procs:
            if proc.returncode is None:
                proc.kill()
        raise

    return procs


class RobotCommandService:
    def __init__(self, config: ConfigModel):
        self._logger = LogHelper(level="DEBUG" if config.debug else "INFO").get_logger(__name__)
        self._config = config

    @handle_grpc_errors
    @log_to_history(include_request=True, include_response=True)
    async def ExecuteROSCommand(self, request: CommandRequest, context: ServicerContext) -> CommandResponse:
        command = request.command.strip()
        self._logger.info(f"ExecuteROSCommand: {command}")

        segments, parse_error = _parse_ros_command(command)
        if parse_error:
            self._logger.warning(f"ExecuteROSCommand rejected: {parse_error} (command={command})")
            return CommandResponse(success=False, output="", error=parse_error)

        try:
            # 쉘을 사용하지 않고 파이프라인을 직접 구성한다. 명령/인자가 그대로
            # execve 로 전달되므로 명령 주입이 성립하지 않는다.
            procs = await _spawn_pipeline(segments)
        except FileNotFoundError as e:
            return CommandResponse(success=False, output="", error=f"명령을 찾을 수 없습니다: {e}")
        except Exception as e:
            self._logger.exception(f"ExecuteROSCommand spawn error: {e}")
            return CommandResponse(success=False, output="", error=f"내부 실행 오류: {str(e)}")

        try:
            stdout, last_stderr = await procs[-1].communicate()

            # 중간 세그먼트의 stderr도 수집해 진단 정보를 잃지 않도록 한다.
            errors: list[str] = []
            for proc in procs[:-1]:
                if proc.stderr is not None:
                    errors.append((await proc.stderr.read()).decode(errors="replace").strip())
                await proc.wait()
            errors.append(last_stderr.decode(errors="replace").strip())

            return CommandResponse(
                success=all(proc.returncode == 0 for proc in procs),
                output=stdout.decode(errors="replace").strip(),
                error="\n".join(part for part in errors if part),
            )
        except Exception as e:
            self._logger.exception(f"ExecuteROSCommand execution error: {e}")
            for proc in procs:
                if proc.returncode is None:
                    proc.kill()
            return CommandResponse(success=False, output="", error=f"내부 실행 오류: {str(e)}")

    async def ExecuteROSCommandStream(
        self, request: CommandRequest, context: ServicerContext
    ) -> AsyncGenerator[CommandResponse, None]:
        command = request.command.strip()
        self._logger.info(f"ExecuteROSCommandStream: {command}")

        segments, parse_error = _parse_ros_command(command)
        if parse_error:
            self._logger.warning(
                f"ExecuteROSCommandStream rejected: {parse_error} (command={command})"
            )
            yield CommandResponse(success=False, output="", error=parse_error)
            return

        procs: list[asyncio.subprocess.Process] = []
        try:
            procs = await _spawn_pipeline(segments)

            output_queue: asyncio.Queue = asyncio.Queue()

            async def stream_reader(stream, is_error: bool):
                try:
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        decoded = line.decode(errors="replace").strip()
                        if decoded:
                            await output_queue.put((is_error, decoded))
                except Exception as e:
                    self._logger.error(f"Stream read error: {e}")

            # 마지막 세그먼트의 stdout + 모든 세그먼트의 stderr를 함께 스트리밍한다.
            reader_tasks = [asyncio.create_task(stream_reader(procs[-1].stdout, False))]
            for proc in procs:
                if proc.stderr is not None:
                    reader_tasks.append(asyncio.create_task(stream_reader(proc.stderr, True)))

            try:
                while not context.done():
                    if all(task.done() for task in reader_tasks) and output_queue.empty():
                        break
                    try:
                        is_error, line = await asyncio.wait_for(output_queue.get(), timeout=0.1)
                        if is_error:
                            yield CommandResponse(success=True, output="", error=line)
                        else:
                            yield CommandResponse(success=True, output=line, error="")
                    except asyncio.TimeoutError:
                        continue
            except asyncio.CancelledError:
                for proc in procs:
                    if proc.returncode is None:
                        proc.terminate()
                raise

            return_codes = [await proc.wait() for proc in procs]
            if any(code != 0 for code in return_codes):
                yield CommandResponse(
                    success=False, output="", error=f"종료 코드: {return_codes[-1]}"
                )

        except asyncio.CancelledError:
            for proc in procs:
                if proc.returncode is None:
                    proc.terminate()
            raise
        except Exception as e:
            self._logger.exception(f"ExecuteROSCommandStream execution error: {e}")
            for proc in procs:
                if proc.returncode is None:
                    proc.kill()
            yield CommandResponse(success=False, output="", error=f"내부 실행 오류: {str(e)}")
        finally:
            self._logger.info(f"ExecuteROSCommandStream finished: {command}")
