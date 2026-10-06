#!/usr/bin/env python3
"""활성 config 의 RAG 하이퍼파라미터(rag_score_threshold / rag_top_k)를 갱신한다.

왜 필요한가: 코드 기본값은 `0.55 / 5` 로 내렸는데(비대칭 임베딩 재측정 결과 정답 위치
점수 0.586~0.722 vs 오답 ≤0.467 사이의 분리선) 활성 config 는 여전히 `0.6 / 4` 였다.
threshold 0.6 은 정답 하단(0.586~0.6)을 잘라내서 "데이터는 있는데 검색 결과가 없습니다"
를 만든다. config 서버 값이 코드 기본값을 덮어쓰므로 여기서 맞춰줘야 한다.

`apply_skills_guide.py` 와 같은 이유로 GET → 필드 교체 → 전체 PUT 라운드트립을 쓴다
(UpdateConfig 가 GORM Save 로 레코드를 통째로 교체하므로 부분 갱신이 불가능하다.
마스킹된 비밀값 "********" 는 서버가 기존 원본값으로 복원한다).

사용 예:
  ADMIN_TOKEN=xxxx ./apply_rag_params.py --env 0047_w2_2f
  ADMIN_TOKEN=xxxx ./apply_rag_params.py --threshold 0.55 --top-k 5
  ADMIN_TOKEN=xxxx ./apply_rag_params.py --dry-run

반영 후 에이전트 컨테이너를 재기동해야 값이 적용된다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime

# 비대칭 임베딩(qwen3-embedding) 재측정 기준 권장값. agent params.py 기본값과 동일.
DEFAULT_THRESHOLD = 0.55
DEFAULT_TOP_K = 5


def _req(method: str, url: str, token: str, body: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except urllib.error.URLError as e:
        print(f"[ERROR] 연결 실패: {url} — {e}", file=sys.stderr)
        sys.exit(2)


def _config_id(cfg: dict) -> int:
    """config 레코드의 PK 를 꺼낸다.

    config 서버는 gorm.Model 을 임베드하므로 JSON 키가 ``ID``(대문자)로 나온다.
    구현/버전에 따라 ``id`` 인 경우도 있어 둘 다 받는다.
    """
    for key in ("ID", "id", "Id"):
        if key in cfg:
            return int(cfg[key])
    print(
        f"[ERROR] config 레코드에서 ID 를 찾지 못했습니다. keys={list(cfg)[:15]}",
        file=sys.stderr,
    )
    sys.exit(3)


def _pick_config(configs: list[dict], env: str | None) -> dict:
    if env:
        for c in configs:
            if c.get("environment") == env:
                return c
        print(f"[ERROR] env='{env}' 인 config 를 찾지 못했습니다.", file=sys.stderr)
        sys.exit(3)
    actives = [c for c in configs if c.get("is_active")]
    if len(actives) == 1:
        return actives[0]
    if not actives:
        print(
            "[ERROR] is_active=true 인 config 가 없습니다. --env 또는 --config-id 로 지정하세요.",
            file=sys.stderr,
        )
        sys.exit(3)
    # 실제 운영 DB 에는 is_active=true 레코드가 여럿 있다(2026-08-12 기준 former 7건).
    # 자동 선택은 위험하므로 반드시 --env 를 요구한다.
    envs = ", ".join(str(c.get("environment")) for c in actives)
    print(f"[ERROR] 활성 config 가 여러 개입니다({envs}). --env 로 지정하세요.", file=sys.stderr)
    sys.exit(3)


def main() -> int:
    ap = argparse.ArgumentParser(description="활성 config 의 RAG threshold/top_k 갱신")
    ap.add_argument(
        "--config-url", default=os.environ.get("CONFIG_URL", "http://10.159.172.74:30180")
    )
    ap.add_argument("--robot", default=os.environ.get("ROBOT", "former"))
    ap.add_argument(
        "--env",
        default=os.environ.get("ENVIRONMENT", "") or None,
        help="대상 environment (미지정 시 is_active 자동 선택)",
    )
    ap.add_argument("--config-id", type=int, default=None, help="대상 config ID 직접 지정")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    ap.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--dry-run", action="store_true", help="PUT 하지 않고 변경 미리보기만")
    args = ap.parse_args()

    if not args.token:
        print(
            "[ERROR] 관리자 토큰이 필요합니다 (ADMIN_TOKEN 환경변수 또는 --token).",
            file=sys.stderr,
        )
        return 1
    if not 0.0 <= args.threshold <= 1.0:
        print("[ERROR] --threshold 는 0.0~1.0 이어야 합니다 (Cosine 유사도).", file=sys.stderr)
        return 1
    if args.top_k < 1:
        print("[ERROR] --top-k 는 1 이상이어야 합니다.", file=sys.stderr)
        return 1

    base = args.config_url.rstrip("/")

    # 1) 대상 config ID 확정
    if args.config_id is not None:
        cfg_id = args.config_id
    else:
        status, raw = _req(
            "GET", f"{base}/api/v1/admin/configs?robot_name={args.robot}", args.token
        )
        if status != 200:
            print(
                f"[ERROR] configs 목록 조회 실패 (HTTP {status}): "
                f"{raw.decode('utf-8', 'replace')[:300]}",
                file=sys.stderr,
            )
            return 1
        configs = json.loads(raw)
        if isinstance(configs, dict):  # {"configs": [...]} 형태 방어
            configs = configs.get("configs", configs.get("data", []))
        chosen = _pick_config(configs, args.env)
        cfg_id = _config_id(chosen)
        print(
            f"[INFO] 대상 config: id={cfg_id}, env={chosen.get('environment')}, "
            f"is_active={chosen.get('is_active')}"
        )

    # 2) 현재 config 전체 GET
    status, raw = _req("GET", f"{base}/api/v1/admin/configs/{cfg_id}", args.token)
    if status != 200:
        print(
            f"[ERROR] config(id={cfg_id}) 조회 실패 (HTTP {status}): "
            f"{raw.decode('utf-8', 'replace')[:300]}",
            file=sys.stderr,
        )
        return 1
    cfg = json.loads(raw)

    old_threshold = cfg.get("rag_score_threshold")
    old_top_k = cfg.get("rag_top_k")
    print(
        f"[INFO] rag_score_threshold: {old_threshold} → {args.threshold}\n"
        f"[INFO] rag_top_k          : {old_top_k} → {args.top_k}"
    )
    if old_threshold == args.threshold and old_top_k == args.top_k:
        print("[INFO] 이미 목표값입니다. 변경 없음.")
        return 0

    # 3) 백업
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"config-{cfg_id}-{ts}.bak.json"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 백업: {backup}")

    # 4) 필드 교체
    cfg["rag_score_threshold"] = args.threshold
    cfg["rag_top_k"] = args.top_k

    if args.dry_run:
        print("[DRY-RUN] PUT 생략.")
        return 0

    # 5) 전체 PUT (마스킹된 ******** 비밀값은 서버가 원본으로 복원)
    status, raw = _req("PUT", f"{base}/api/v1/admin/configs/{cfg_id}", args.token, cfg)
    if status != 200:
        print(
            f"[ERROR] PUT 실패 (HTTP {status}): {raw.decode('utf-8', 'replace')[:500]}",
            file=sys.stderr,
        )
        print(
            f"        롤백하려면: PUT {base}/api/v1/admin/configs/{cfg_id} 에 {backup} 내용 전송",
            file=sys.stderr,
        )
        return 1
    print(f"[SUCCESS] RAG 파라미터 반영 완료 (id={cfg_id}). **에이전트 재기동 필요**.")
    print("          검증: robo_talk.py 리포트 헤더의 RAG Top-K / Score Threshold 확인")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
