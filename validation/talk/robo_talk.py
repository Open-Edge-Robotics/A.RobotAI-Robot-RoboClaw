#!/usr/bin/env python3
"""robo_talk — 외부 호스트에서 로봇에 자연어 명령을 보내는 독립 gRPC 클라이언트.

robo_claw_cli(Go)·설정서버 없이, 파이썬 + grpcio 만으로 로봇의 RoboMessenger
(gRPC `SendCommand`, 기본 포트 50052)에 접속해:
  - 시나리오 파일의 테스트 케이스를 하나씩 자동 전송하고 응답을 판정(scenario 모드), 또는
  - 대화형으로 직접 명령을 입력(interactive 모드)
한다. 테스트 케이스 파일은 외부 호스트에 두고 재사용한다.

준비:
  pip install grpcio grpcio-tools
  ./gen_proto.sh                       # messenger_pb2*.py 생성 (최초 1회)

사용 예:
  # 시나리오 자동 실행 (로봇 이동)
  python3 robo_talk.py --host 10.159.172.69 --scenario ../testcases/first-use-scenario-auto.json --output report.md
  # 특정 스텝만
  python3 robo_talk.py --host 10.159.172.69 --scenario ... --only tc03,tc16
  # 대화형(단발 명령 반복)
  python3 robo_talk.py --host 10.159.172.69 -i
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import grpc
    import messenger_pb2 as pb
    import messenger_pb2_grpc as pb_grpc
except ImportError as e:
    sys.stderr.write(
        f"[ERROR] 의존성/생성물 누락: {e}\n"
        "  1) pip install grpcio grpcio-tools\n"
        "  2) ./gen_proto.sh  (messenger_pb2*.py 생성)\n"
    )
    sys.exit(2)


DEFAULT_PORT = 50052  # RoboMessenger(SendCommand). 50051은 RosGrpc(텔레메트리)라 사용 금지.
DEFAULT_CONFIG_URL = "http://10.159.172.74:30180"  # AI Config 서버


# 마지막 config 조회 실패 사유(디버깅용). resolve 실패 시 헤더에 노출한다.
_LAST_CONFIG_ERROR: str = ""


def _get_json(url: str, timeout: float = 5.0, token: str = ""):
    """GET JSON. token이 있으면 Authorization: Bearer 로 붙인다.

    실패 원인(HTTP 상태·본문·네트워크 예외)을 _LAST_CONFIG_ERROR 에 남겨,
    조용히 None으로 사라지지 않게 한다(원인 진단용).
    """
    global _LAST_CONFIG_ERROR
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace").strip()[:160]
        except Exception:  # noqa: BLE001
            pass
        hint = " (토큰 필요 — --config-token)" if e.code in (401, 403) else ""
        _LAST_CONFIG_ERROR = f"HTTP {e.code} {e.reason}{hint}"
        if body:
            _LAST_CONFIG_ERROR += f" — {body}"
    except (urllib.error.URLError, OSError) as e:
        _LAST_CONFIG_ERROR = f"연결 실패({type(e).__name__}: {getattr(e, 'reason', e)})"
    except ValueError as e:
        _LAST_CONFIG_ERROR = f"JSON 파싱 실패({e})"
    return None


def resolve_ai_config(
    config_url: str, robot: str = "", env: str = "", token: str = ""
) -> dict | None:
    """AI Config 서버에서 '실제 활성' 설정을 알아온다.

    - robot+env가 명시되면 정확 조회(/configs/active — device/admin 토큰).
    - env가 없으면 config 목록에서 is_active=true 인 설정을 자동 탐색(/configs — admin 토큰).
    실패하면 None(사유는 _LAST_CONFIG_ERROR).
    """
    global _LAST_CONFIG_ERROR
    _LAST_CONFIG_ERROR = ""
    base = config_url.rstrip("/")
    if robot and env:
        cfg = _get_json(
            f"{base}/api/v1/configs/active?robot_name={robot}&environment={env}", token=token
        )
        if isinstance(cfg, dict) and cfg:
            return cfg
    # 활성 설정 자동 탐색 (env를 몰라도 실제 활성 프로파일을 찾는다)
    q = f"?robot_name={robot}" if robot else ""
    items = _get_json(f"{base}/api/v1/configs{q}", token=token)
    if isinstance(items, dict):  # {"configs":[...]} 등으로 감싸진 응답 방어
        items = items.get("configs") or items.get("data") or items.get("items")
    if isinstance(items, list):
        actives = [c for c in items if isinstance(c, dict) and c.get("is_active")]
        if robot:
            matched = [c for c in actives if c.get("robot_name") == robot]
            actives = matched or actives
        if actives:
            return actives[0]
        if items and not actives:
            _LAST_CONFIG_ERROR = "조회는 됐으나 is_active=true 설정이 없음"
    return None


# LLM 하이퍼파라미터 출력 순서/라벨 → (ollama_options_json 키, 대체 키들)
# scripts/show_ai_config.py 와 동일 항목·순서를 유지한다.
_LLM_OPT_FIELDS = [
    ("num_ctx", "num_ctx", ()),
    ("temperature", "temperature", ()),
    ("Repeat Penalty", "repeat_penalty", ()),
    ("Repeat Last N", "repeat_last_n", ()),
    ("Seed", "seed", ()),
    ("Max Predict Token", "num_predict", ("max_tokens",)),
    ("Top K", "top_k", ()),
    ("Top P", "top_p", ()),
    ("Min P", "min_p", ()),
]


def _cfg_val(value) -> str:
    return "(미설정)" if value is None or value == "" else str(value)


# ── 소프트웨어 버전 + "실행 코드" 최신성 조회 ────────────────────────
#
# 왜 이미지 배너로는 안 되는가 (2026-08-12 실기에서 몇 시간을 오진한 지점):
# `robo_claw_cli launch --docker` 는 **로봇 호스트의 소스 트리**를 컨테이너의
# install 공간 위에 bind mount 한다.
#   /home/.../src/robo_claw_agent/robo_claw_agent
#     -> /ros2_ws/install/robo_claw_agent/{lib,local/lib}/python3.{10,12}/{site,dist}-packages/...
# 즉 **파이썬 코드는 이미지에서 오지 않는다**(이미지 install 산출물은 마운트에 가려짐).
# 그래서 `/etc/robo_claw_version`(이미지 배너)이 최신이어도, `--no-cache` 로 재빌드해도
# 실행 코드는 그대로일 수 있다. 실제 실행 코드의 신원 = **호스트 체크아웃의 git commit**.
#
# 이 프로브는 그 값을 읽어 "테스트하려는 commit"과 비교한다.
_BUILD_PROBE_SH = r"""
REPO="$RC_REPO"
docker exec "$RC_CONTAINER" cat /etc/robo_claw_version 2>/dev/null | sed -e 's/^/IMG /'
# install 공간이 bind mount 인가(= 라이브 코드 방식인가)
docker inspect "$RC_CONTAINER"   --format '{{range .Mounts}}{{.Type}} {{.Destination}}
{{end}}' 2>/dev/null   | grep -E 'site-packages|dist-packages' | wc -l | sed -e 's/^/MOUNTS /'
if [ -d "$REPO/.git" ]; then
  echo "HOSTCOMMIT $(git -C "$REPO" rev-parse --short HEAD 2>/dev/null)"
  echo "HOSTBRANCH $(git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  echo "HOSTDIRTY $(git -C "$REPO" status --porcelain 2>/dev/null | wc -l)"
else
  echo "HOSTCOMMIT unavailable"
fi
"""

_LAST_BUILD_ERROR = ""

_DEFAULT_ROBOT_REPO = "/home/former/workspace/robo-claw/robo-claw"


def _local_head() -> str:
    """테스트 기준 commit(이 저장소의 HEAD)."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        out = subprocess.run(
            ["git", "-C", here, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def probe_build_info(
    ssh_target: str, container: str, repo: str = _DEFAULT_ROBOT_REPO, timeout: float = 20.0
) -> dict | None:
    """로봇에 ssh 로 붙어 이미지 배너 + 실행 코드(호스트 체크아웃) 신원을 조회한다.

    실패는 치명적이지 않다(시나리오는 그대로 진행). 사유는 `_LAST_BUILD_ERROR`.
    """
    global _LAST_BUILD_ERROR
    if not shutil.which("ssh"):
        _LAST_BUILD_ERROR = "ssh 실행 파일을 찾을 수 없습니다"
        return None
    b64 = base64.b64encode(_BUILD_PROBE_SH.encode()).decode()
    # 원격 명령은 base64(영숫자/+/=)와 변수 대입만 → 따옴표 중첩 이슈 없음.
    remote = (
        f"RC_REPO={repo} RC_CONTAINER={container}; export RC_REPO RC_CONTAINER; "
        f"echo {b64} | base64 -d | bash"
    )
    try:
        proc = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
             "-o", "StrictHostKeyChecking=accept-new", ssh_target, remote],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _LAST_BUILD_ERROR = f"ssh 타임아웃({timeout:.0f}s)"
        return None
    except Exception as exc:  # noqa: BLE001
        _LAST_BUILD_ERROR = f"{type(exc).__name__}: {exc}"
        return None
    if proc.returncode != 0:
        _LAST_BUILD_ERROR = (proc.stderr or proc.stdout or "").strip()[:200] or f"exit {proc.returncode}"
        return None

    info: dict = {"image": {}, "mounts": 0, "host": {}, "expected": _local_head()}
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if line.startswith("IMG ") and "=" in line[4:]:
            k, _, v = line[4:].partition("=")
            info["image"][k.strip()] = v.strip()
        elif line.startswith("MOUNTS "):
            try:
                info["mounts"] = int(line.split()[1])
            except (IndexError, ValueError):
                pass
        elif line.startswith("HOSTCOMMIT "):
            info["host"]["commit"] = line.split(maxsplit=1)[1]
        elif line.startswith("HOSTBRANCH "):
            info["host"]["branch"] = line.split(maxsplit=1)[1]
        elif line.startswith("HOSTDIRTY "):
            try:
                info["host"]["dirty"] = int(line.split()[1])
            except (IndexError, ValueError):
                pass
    if not info["image"] and not info["host"]:
        _LAST_BUILD_ERROR = "출력 파싱 실패(컨테이너 미기동 또는 repo 경로 상이 — --robot-repo)"
        return None
    return info


def format_build_line(info: dict | None) -> str:
    """리포트 최상단에 넣을 **한 줄** 요약: 실행 코드가 테스트 기준과 같은지."""
    if info is None:
        return f"- 소프트웨어: 조회 실패 — 사유: {_LAST_BUILD_ERROR or '알 수 없음'}"

    img = info.get("image", {})
    host = info.get("host", {})
    live = info.get("mounts", 0) > 0
    expected = info.get("expected", "")

    # 라이브 마운트면 실행 코드 = 호스트 체크아웃, 아니면 = 이미지.
    if live:
        running = host.get("commit", "?")
        origin = f"호스트 체크아웃({host.get('branch', '?')})"
    else:
        running = img.get("git_commit", "?")
        origin = "이미지"

    dirty = host.get("dirty")
    dirty_note = f", 미커밋 {dirty}개" if live and dirty else ""

    if not expected or running in ("?", "unavailable"):
        verdict = "**확인 불가**"
    elif running == expected:
        verdict = "**일치 ✅**"
    else:
        verdict = (
            f"⚠️ **테스트 기준 `{expected}` 과 다릅니다 → "
            f"로봇에서 git pull + 컨테이너 재기동 필요"
            + (" (이미지 재빌드로는 반영되지 않습니다)" if live else "")
            + "**"
        )
    return (
        f"- 소프트웨어: 실행코드 `{running}` ({origin}{dirty_note}) · "
        f"이미지 `{img.get('git_commit', '?')}`/`{img.get('image_tag', '?')}` "
        f"build `{img.get('build_date', '?')}` · {verdict}"
    )


def format_config_header(cfg: dict | None, robot: str, env: str) -> list[str]:
    """리포트/콘솔용 AI Config 상세 라인들을 만든다.

    scripts/show_ai_config.py 와 동일한 LLM·임베딩 하이퍼파라미터를 리포트 맨 위에 출력한다.
    """
    if not cfg:
        q = f"robot={robot or '(any)'}, env={env or '(자동탐색)'}"
        why = f"; 사유: {_LAST_CONFIG_ERROR}" if _LAST_CONFIG_ERROR else ""
        return [f"- AI Config: (조회 실패 — {q}{why})"]

    opts: dict = {}
    raw = cfg.get("ollama_options_json") or ""
    if raw:
        try:
            opts = json.loads(raw)
        except (ValueError, TypeError):
            opts = {}
    model_url = cfg.get("ollama_base_url") or cfg.get("azure_openai_endpoint")

    lines = [
        f"- 프로파일: **{cfg.get('name') or (robot + '/' + env)}**  "
        f"(robot={cfg.get('robot_name', robot)}, env={cfg.get('environment', env)}, "
        f"active={cfg.get('is_active', '?')})",
        "",
        "**LLM**",
        f"- Model: `{_cfg_val(cfg.get('llm_model'))}`",
        f"- Model URL: {_cfg_val(model_url)}",
    ]
    for label, key, alts in _LLM_OPT_FIELDS:
        val = opts.get(key)
        if val is None:
            for a in alts:
                if a in opts:
                    val = opts[a]
                    break
        lines.append(f"- {label}: {_cfg_val(val)}")

    lines += [
        "",
        "**Embedding Model**",
        f"- Model: `{_cfg_val(cfg.get('llm_embedding_model'))}`",
        f"- Qdrant URL: {_cfg_val(cfg.get('qdrant_url'))}",
        f"- Qdrant Collection: {_cfg_val(cfg.get('qdrant_collection'))}",
        f"- Qdrant Timeout: {_cfg_val(cfg.get('qdrant_timeout_sec'))}",
        f"- RAG Top-K: {_cfg_val(cfg.get('rag_top_k'))}",
        f"- RAG Score Threshold: {_cfg_val(cfg.get('rag_score_threshold'))}",
    ]
    return lines


def _find_referenced(obj) -> list:
    """중첩 구조(result_json 파싱 결과)에서 'referenced' 리스트를 찾는다."""
    if isinstance(obj, dict):
        if isinstance(obj.get("referenced"), list):
            return obj["referenced"]
        for v in obj.values():
            found = _find_referenced(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_referenced(v)
            if found:
                return found
    return []


def _format_referenced(result_json: str) -> list[str]:
    """result_json 에서 RAG가 참조한 Qdrant 항목(referenced)을 찾아 리포트용 라인으로 만든다.

    요구사항: RAG로 검색해 행동할 때 collection의 어떤 데이터를 참조했는지 테스트 결과에 남긴다.
    (에이전트 rag_search 결과의 referenced 필드가 result_json에 실려 오면 여기서 강조 출력)
    """
    if not result_json:
        return []
    try:
        data = json.loads(result_json)
    except (ValueError, TypeError):
        return []
    refs = _find_referenced(data)
    if not refs:
        return []
    out = ["- 참조 Qdrant 항목:"]
    for x in refs[:10]:
        if not isinstance(x, dict):
            continue
        parts = []
        if x.get("id") is not None:
            parts.append(f"id={x['id']}")
        if x.get("score") is not None:
            parts.append(f"score={x['score']}")
        if x.get("type"):
            parts.append(f"type={x['type']}")
        if x.get("source"):
            parts.append(f"근거={x['source']}")
        if x.get("location_name"):
            parts.append(f"name={x['location_name']}")
        if x.get("x") is not None and x.get("y") is not None:
            parts.append(f"({x['x']}, {x['y']})")
        if x.get("text"):
            parts.append(f"'{str(x['text'])[:60]}'")
        out.append(f"  - {' '.join(parts)}")
    return out


class RoboTalk:
    def __init__(self, host: str, port: int, sender_id: str = "robo_talk"):
        self.target = f"{host}:{port}"
        self.sender_id = sender_id
        self._channel = grpc.insecure_channel(self.target)
        self._stub = pb_grpc.RoboMessengerStub(self._channel)

    def wait_ready(self, timeout_sec: float = 5.0) -> bool:
        try:
            grpc.channel_ready_future(self._channel).result(timeout=timeout_sec)
            return True
        except grpc.FutureTimeoutError:
            return False

    def send(self, text: str, timeout_sec: float = 120.0):
        """명령 1건 전송 → (success, message, result_json)."""
        msg = pb.ChatMessage(
            sender_id=self.sender_id,
            content=text,
            timestamp=int(time.time()),
        )
        resp = self._stub.SendCommand(msg, timeout=timeout_sec)
        return resp.success, resp.message, resp.result_json

    def close(self):
        self._channel.close()


def _as_list(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v]
    return [str(v)]


def evaluate(message: str, success: bool, params: dict) -> list[str]:
    """fail 사유 목록 반환(빈 리스트면 PASS)."""
    reasons: list[str] = []
    if "expect_success" in params and isinstance(params["expect_success"], bool):
        if success != params["expect_success"]:
            reasons.append(f"성공 플래그 불일치(기대 {params['expect_success']}, 실제 {success})")
    for kw in _as_list(params.get("expected_keywords")):
        if kw not in message:
            reasons.append(f"필수 키워드 누락: '{kw}'")
    for kw in _as_list(params.get("fail_keywords")):
        if kw in message:
            reasons.append(f"금지 키워드 포함: '{kw}'")
    return reasons


def run_scenario(talk: RoboTalk, scenario_path: str, output: str | None,
                 only: set[str] | None, config_lines: list[str] | None = None,
                 build_line: str | None = None):
    with open(scenario_path, encoding="utf-8") as f:
        scenario = json.load(f)

    cases = scenario.get("test_cases", [])
    records: list[dict] = []
    n_pass = n_fail = n_skip = 0

    start_dt = datetime.now()
    t_start = time.monotonic()

    print(f"\n시나리오: {scenario.get('name', scenario_path)}")
    if config_lines:
        print("[AI Config]")
        for ln in config_lines:
            print("  " + ln.lstrip("- ").replace("**", "").replace("`", ""))
    print(f"대상: {talk.target}")
    print(f"시작: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    for tc in cases:
        tid = str(tc.get("id", ""))
        name = tc.get("name", tid)
        step = tc.get("step", "")
        params = tc.get("params", {}) or {}
        prompt = params.get("prompt") or params.get("command")

        if only and not any(k in tid for k in only):
            continue
        rec = {
            "id": tid, "name": name, "step": step, "prompt": prompt or "",
            "success": None, "message": "", "result_json": "", "latency_ms": 0,
            "reasons": [], "error": "",
        }
        if not tc.get("enabled", True):
            rec["status"] = "SKIP"; rec["detail"] = "비활성화"
            records.append(rec); n_skip += 1
            continue
        if not prompt:
            rec["status"] = "SKIP"; rec["detail"] = f"prompt 없음(type={tc.get('type')})"
            records.append(rec); n_skip += 1
            continue

        timeout_sec = float(tc.get("timeout_ms", 120000)) / 1000.0
        print(f"💬 [{name}] '{prompt}' 송신...")
        t0 = time.monotonic()
        try:
            success, message, result_json = talk.send(prompt, timeout_sec=timeout_sec)
        except grpc.RpcError as e:
            rec["latency_ms"] = int((time.monotonic() - t0) * 1000)
            code = e.code().name if e.code() else "UNKNOWN"
            detail = e.details() or ""
            secs = rec["latency_ms"] / 1000
            rec["status"] = "FAIL"; rec["error"] = f"gRPC {code}: {detail}"
            rec["detail"] = f"gRPC 오류 {code}: {detail} (소요 {secs:.1f}s)"
            print(f"❌ {name}: FAIL (gRPC {code}: {detail}, 소요 {secs:.1f}s)")
            records.append(rec); n_fail += 1
            continue

        rec["latency_ms"] = int((time.monotonic() - t0) * 1000)
        rec["success"] = success
        rec["message"] = message or ""
        rec["result_json"] = result_json or ""
        reasons = evaluate(message, success, params)
        rec["reasons"] = reasons
        secs = rec["latency_ms"] / 1000
        if reasons:
            rec["status"] = "FAIL"
            rec["detail"] = f"응답: {message[:120]} | " + "; ".join(reasons)
            print(f"❌ {name}: FAIL (소요 {secs:.1f}s) — {'; '.join(reasons)}\n   응답: {message[:300]}")
            n_fail += 1
        else:
            rec["status"] = "PASS"
            rec["detail"] = f"응답: {message[:120]}"
            print(f"✅ {name}: PASS (소요 {secs:.1f}s)\n   응답: {message[:300]}")
            n_pass += 1
        records.append(rec)

    end_dt = datetime.now()
    total_s = time.monotonic() - t_start
    print("=" * 60)
    print(f"결과: PASS={n_pass}  FAIL={n_fail}  SKIP={n_skip}")
    print(
        f"시작: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}  "
        f"종료: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}  (총 소요 {total_s:.1f}s)"
    )

    if output:
        _write_report(output, scenario, talk.target, records, n_pass, n_fail, n_skip,
                      config_lines=config_lines, start_dt=start_dt, end_dt=end_dt,
                      total_s=total_s, build_line=build_line)
        print(f"리포트: {output}")

    return n_fail == 0


def _write_report(path, scenario, target, records, n_pass, n_fail, n_skip,
                  config_lines=None, start_dt=None, end_dt=None, total_s=None,
                  build_line=None):
    icon = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "SKIP": "➖ SKIP"}

    def esc(s):
        return str(s).replace("|", "\\|").replace("\n", " ")

    lines = [
        "# robo_talk 시나리오 테스트 리포트",
        "",
    ]
    # 최상단: 소프트웨어 버전 + 빌드 최신성. 결과 해석의 전제이므로 가장 먼저 둔다.
    if build_line:
        lines.append(build_line)
    lines += [
        f"- 대상(RoboMessenger): `{target}`",
        f"- 시나리오: {scenario.get('name', '')}",
    ]
    # 실행 시각(초 포함) + 총 소요
    if start_dt:
        lines.append(f"- 시작: {start_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    if end_dt:
        lines.append(f"- 종료: {end_dt.strftime('%Y-%m-%d %H:%M:%S')}")
    if total_s is not None:
        lines.append(f"- 총 소요: {total_s:.1f}s")
    lines.append(f"- 결과: **PASS {n_pass} / FAIL {n_fail} / SKIP {n_skip}**")
    # AI Config 정보 (프로파일·LLM·임베딩·RAG)
    lines += ["", "## AI Config", ""]
    lines += (config_lines or ["- (AI Config 정보 없음)"])
    lines += [
        "",
        "## 요약",
        "",
        "| Step | 테스트 항목 | 결과 | 소요 | 상세 |",
        "| :--- | :--- | :---: | ---: | :--- |",
    ]
    for r in records:
        lat = f"{r.get('latency_ms', 0) / 1000:.1f}s" if r.get("status") != "SKIP" else "-"
        lines.append(
            f"| {r.get('step','')} | {r.get('name','')} | {icon.get(r.get('status'), r.get('status'))} "
            f"| {lat} | {esc(r.get('detail',''))} |"
        )

    # 상세 로그 부록: 응답 전문 + result_json + 판정 사유 (진단용)
    lines += ["", "## 상세 로그", ""]
    for r in records:
        if r.get("status") == "SKIP":
            continue
        lines.append(f"### [{r.get('status')}] {r.get('name','')}  (소요 {r.get('latency_ms',0) / 1000:.1f}s)")
        lines.append(f"- 프롬프트: `{esc(r.get('prompt',''))}`")
        if r.get("error"):
            lines.append(f"- 오류: {esc(r['error'])}")
        else:
            lines.append(f"- success 플래그: `{r.get('success')}`")
            lines.append("- 응답 전문:")
            lines.append("```")
            lines.append(str(r.get("message", "")))
            lines.append("```")
            rj = str(r.get("result_json", "") or "")
            # RAG가 참조한 Qdrant 항목(있으면) 강조 출력 — 요구사항: 참조 데이터 identify 로깅.
            for ref_line in _format_referenced(rj):
                lines.append(ref_line)
            if rj:
                lines.append("- result_json:")
                lines.append("```json")
                lines.append(rj[:4000])
                lines.append("```")
        if r.get("reasons"):
            lines.append(f"- 판정 실패 사유: {esc('; '.join(r['reasons']))}")
        lines.append("")

    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def run_interactive(talk: RoboTalk):
    print(f"대화형 모드 — 대상 {talk.target}. 명령을 입력하세요('exit'/Ctrl-D 종료).\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text in ("exit", "quit", ":q"):
            break
        try:
            success, message, _ = talk.send(text)
            print(f"robot[{'ok' if success else 'fail'}]> {message}\n")
        except grpc.RpcError as e:
            print(f"[gRPC 오류] {e.code().name}: {e.details()}\n")


def main():
    ap = argparse.ArgumentParser(description="robo_talk — 외부 호스트용 로봇 명령 gRPC 클라이언트")
    ap.add_argument("--host", required=True, help="로봇 IP (예: 10.159.172.69). 로봇에서 실행 시 localhost")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"RoboMessenger 포트 (기본 {DEFAULT_PORT})")
    ap.add_argument("--sender-id", default="robo_talk", help="발신자 ID")
    ap.add_argument("--scenario", help="테스트 케이스 JSON 경로 (first-use-scenario-auto.json 형식)")
    ap.add_argument("--output", help="마크다운 리포트 저장 경로")
    ap.add_argument("--only", help="특정 케이스만 실행(쉼표 구분, id 부분일치). 예: tc03,tc16")
    ap.add_argument("-i", "--interactive", action="store_true", help="대화형 모드")
    # AI Config 정보(프로파일/LLM/임베딩/RAG)를 리포트 헤더에 출력하기 위한 조회 옵션
    ap.add_argument("--config-url", default=DEFAULT_CONFIG_URL, help=f"AI Config 서버 URL (기본 {DEFAULT_CONFIG_URL})")
    ap.add_argument("--robot", default="former", help="AI Config 조회용 로봇명 (기본 former, 비우면 필터 없음)")
    ap.add_argument("--env", default="", help="AI Config 조회용 환경명(비우면 활성 설정 자동 탐색 — env를 추측하지 않음)")
    ap.add_argument(
        "--config-token",
        default=os.environ.get("ROBOCLAW_CONFIG_TOKEN")
        or os.environ.get("ADMIN_TOKEN")
        or os.environ.get("DEVICE_TOKEN")
        or "",
        help="AI Config 서버 인증 토큰(Bearer). 서버에 토큰이 설정된 경우 필요. "
        "환경변수 ROBOCLAW_CONFIG_TOKEN/ADMIN_TOKEN/DEVICE_TOKEN 로도 지정 가능.",
    )
    ap.add_argument("--no-config", action="store_true", help="AI Config 조회 생략")
    # 소프트웨어 버전 + 빌드 최신성(install↔src) — 리포트 최상단 한 줄.
    ap.add_argument(
        "--robot-ssh", default="",
        help="빌드 정보 조회용 ssh 대상(기본 former@<--host>). 예: former@10.159.172.69",
    )
    ap.add_argument("--container", default="robo_claw_container", help="에이전트 컨테이너 이름")
    ap.add_argument(
        "--robot-repo", default=_DEFAULT_ROBOT_REPO,
        help=f"로봇 호스트의 저장소 경로(실행 코드 출처). 기본 {_DEFAULT_ROBOT_REPO}",
    )
    ap.add_argument(
        "--no-build-info", action="store_true",
        help="소프트웨어 버전/빌드 최신성 조회 생략(ssh 불가 환경)",
    )
    args = ap.parse_args()

    # AI Config 조회 → 헤더용 요약 라인 (env 미지정 시 활성 설정 자동 탐색)
    config_lines = None
    if not args.no_config:
        cfg = resolve_ai_config(args.config_url, args.robot, args.env, args.config_token)
        config_lines = format_config_header(cfg, args.robot, args.env)
        if cfg is None and _LAST_CONFIG_ERROR:
            sys.stderr.write(f"[WARN] AI Config 조회 실패 — {_LAST_CONFIG_ERROR}\n")

    # 소프트웨어 버전 + install↔src 일치 여부. 결과 해석의 전제이므로 실행 전에 먼저 찍는다
    # (5분짜리 시나리오를 낡은 빌드로 돌리고 나서 알게 되는 낭비를 막는다).
    build_line = None
    if not args.no_build_info:
        ssh_target = args.robot_ssh or f"former@{args.host}"
        build_line = format_build_line(
            probe_build_info(ssh_target, args.container, args.robot_repo)
        )
        print(build_line.lstrip("- ").replace("**", ""))
        if _LAST_BUILD_ERROR:
            sys.stderr.write(f"[WARN] 빌드 정보 조회 실패 — {_LAST_BUILD_ERROR}\n")

    talk = RoboTalk(args.host, args.port, sender_id=args.sender_id)
    if not talk.wait_ready(5.0):
        sys.stderr.write(
            f"[ERROR] {talk.target} 연결 실패. 확인: 로봇 에이전트 기동 여부, "
            f"포트는 RoboMessenger(기본 {DEFAULT_PORT})인지(50051은 텔레메트리).\n"
        )
        sys.exit(1)

    try:
        if args.interactive or not args.scenario:
            if config_lines:
                print("[AI Config]")
                for ln in config_lines:
                    print("  " + ln.lstrip("- ").replace("**", "").replace("`", ""))
            run_interactive(talk)
            ok = True
        else:
            ok = run_scenario(
                talk, args.scenario, args.output,
                set(args.only.split(",")) if args.only else None,
                config_lines=config_lines,
                build_line=build_line,
            )
    finally:
        talk.close()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
