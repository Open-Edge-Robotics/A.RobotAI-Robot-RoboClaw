#!/usr/bin/env python3
"""AI Config 서버에서 활성 설정의 LLM·임베딩 하이퍼파라미터를 읽어 보기 좋게 출력한다.

로봇이 실제로 사용하는 값(모델·num_ctx·temperature·RAG threshold 등)을 한눈에 확인해
튜닝/디버깅에 쓴다. LLM 세부 파라미터는 config 의 ollama_options_json 에서 파싱한다.

인증: 서버에 토큰이 설정돼 있으면 Bearer 토큰이 필요하다(--token 또는 환경변수
ROBOCLAW_CONFIG_TOKEN/ADMIN_TOKEN/DEVICE_TOKEN). env 미지정 시 is_active 자동 탐색.

사용 예:
  ADMIN_TOKEN=xxxx ./show_ai_config.py --env 0047_w2_2f
  ./show_ai_config.py --robot former --env 0047_w2_2f --token xxxx
  ./show_ai_config.py --config-id 7 --token xxxx --json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_CONFIG_URL = "http://10.159.172.74:30180"

# 출력 순서/라벨 → (ollama_options_json 키, 대체 키들)
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


def _req(url: str, token: str, timeout: float = 10.0):
    req = urllib.request.Request(url)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), ""
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace").strip()[:160]
        except Exception:  # noqa: BLE001
            pass
        hint = " (토큰 필요/불일치 — --token)" if e.code in (401, 403) else ""
        return None, f"HTTP {e.code} {e.reason}{hint} {body}".strip()
    except (urllib.error.URLError, OSError) as e:
        return None, f"연결 실패({type(e).__name__}: {getattr(e, 'reason', e)})"
    except ValueError as e:
        return None, f"JSON 파싱 실패({e})"


def _resolve_config(base: str, robot: str, env: str, cfg_id, token: str):
    """대상 config 를 가져온다. id > (robot+env) > is_active 자동탐색 순."""
    if cfg_id is not None:
        data, err = _req(f"{base}/api/v1/configs/{cfg_id}", token)
        return data, err
    if robot and env:
        data, err = _req(
            f"{base}/api/v1/configs/active?robot_name={robot}&environment={env}", token
        )
        if isinstance(data, dict) and data:
            return data, ""
        # active 조회 실패 시 목록 자동탐색으로 폴백
    q = f"?robot_name={robot}" if robot else ""
    items, err = _req(f"{base}/api/v1/configs{q}", token)
    if isinstance(items, dict):
        items = items.get("configs") or items.get("data") or items.get("items")
    if isinstance(items, list):
        cands = [c for c in items if isinstance(c, dict)]
        if env:
            cands = [c for c in cands if c.get("environment") == env] or cands
        actives = [c for c in cands if c.get("is_active")]
        if actives:
            return actives[0], ""
        if cands:
            return None, "is_active=true 설정이 없음 (--env/--config-id 로 지정)"
    return None, err or "조회 결과 없음"


def _fmt(value) -> str:
    if value is None or value == "":
        return "(미설정)"
    return str(value)


def main() -> int:
    ap = argparse.ArgumentParser(description="AI Config 서버의 LLM·임베딩 하이퍼파라미터 조회")
    ap.add_argument("--config-url", default=os.environ.get("CONFIG_URL", DEFAULT_CONFIG_URL))
    ap.add_argument("--robot", default="former", help="로봇명 (기본 former)")
    ap.add_argument("--env", default="", help="환경명 (미지정 시 is_active 자동 탐색)")
    ap.add_argument("--config-id", type=int, default=None, help="config ID 직접 지정")
    ap.add_argument(
        "--token",
        default=os.environ.get("ROBOCLAW_CONFIG_TOKEN")
        or os.environ.get("ADMIN_TOKEN")
        or os.environ.get("DEVICE_TOKEN")
        or "",
        help="Bearer 토큰(환경변수 ROBOCLAW_CONFIG_TOKEN/ADMIN_TOKEN/DEVICE_TOKEN 로도 지정)",
    )
    ap.add_argument("--json", action="store_true", help="원본 config JSON 그대로 출력")
    args = ap.parse_args()

    base = args.config_url.rstrip("/")
    cfg, err = _resolve_config(base, args.robot, args.env, args.config_id, args.token)
    if cfg is None:
        sys.stderr.write(f"[ERROR] AI Config 조회 실패 — {err}\n")
        return 1

    if args.json:
        print(json.dumps(cfg, ensure_ascii=False, indent=2))
        return 0

    # LLM 세부 파라미터(ollama_options_json 파싱)
    opts = {}
    raw = cfg.get("ollama_options_json") or ""
    if raw:
        try:
            opts = json.loads(raw)
        except (ValueError, TypeError):
            sys.stderr.write("[WARN] ollama_options_json 파싱 실패 — 원문 표시\n")

    print("=" * 60)
    print(
        f"프로파일: {cfg.get('name', '?')}  "
        f"(robot={cfg.get('robot_name', '?')}, env={cfg.get('environment', '?')}, "
        f"id={cfg.get('id', cfg.get('ID', '?'))}, active={cfg.get('is_active', '?')})"
    )
    print("=" * 60)

    model_url = cfg.get("ollama_base_url") or cfg.get("azure_openai_endpoint")
    print("\nLLM :")
    print(f"  - {'Model':<18}: {_fmt(cfg.get('llm_model'))}")
    print(f"  - {'Model URL':<18}: {_fmt(model_url)}")
    for label, key, alts in _LLM_OPT_FIELDS:
        val = opts.get(key)
        if val is None:
            for a in alts:
                if a in opts:
                    val = opts[a]
                    break
        print(f"  - {label:<18}: {_fmt(val)}")

    print("\nEmbedding Model :")
    print(f"  - {'Model':<20}: {_fmt(cfg.get('llm_embedding_model'))}")
    print(f"  - {'Qdrant URL':<20}: {_fmt(cfg.get('qdrant_url'))}")
    print(f"  - {'Qdrant Collection':<20}: {_fmt(cfg.get('qdrant_collection'))}")
    print(f"  - {'Qdrant Timeout':<20}: {_fmt(cfg.get('qdrant_timeout_sec'))}")
    print(f"  - {'RAG Top-K':<20}: {_fmt(cfg.get('rag_top_k'))}")
    print(f"  - {'RAG Score Threshold':<20}: {_fmt(cfg.get('rag_score_threshold'))}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
