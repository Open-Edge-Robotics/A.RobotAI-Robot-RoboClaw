#!/usr/bin/env python3
"""활성 config의 skills_content 를 마크다운 파일 내용으로 안전하게 교체한다.

왜 이런 방식인가: config 서버의 UpdateConfig(PUT /api/v1/admin/configs/:id)는 GORM Save로
레코드를 "통째로" 교체한다(부분 갱신이 아님). 따라서 skills_content 하나만 바꾸려면
현재 config를 그대로 GET → skills_content 만 교체 → 전체를 다시 PUT 해야 한다.
(마스킹된 비밀값 "********" 는 서버가 기존 원본값으로 복원하므로 라운드트립이 안전하다.)

사용 예:
  ADMIN_TOKEN=xxxx ./apply_skills_guide.py \
      --skills-file ../../../roboclaw_config_server/database/seeds/SKILLS.former.md
  # 특정 env/ID 지정
  ADMIN_TOKEN=xxxx ./apply_skills_guide.py --env 0047_w2_2f --skills-file SKILLS.former.md
  ADMIN_TOKEN=xxxx ./apply_skills_guide.py --config-id 7 --skills-file SKILLS.former.md
  # 실제 변경 없이 미리보기
  ADMIN_TOKEN=xxxx ./apply_skills_guide.py --skills-file SKILLS.former.md --dry-run

토큰: 관리자 API 이므로 ADMIN_TOKEN(환경변수) 또는 --token 이 필요하다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime


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
        print("[ERROR] is_active=true 인 config 가 없습니다. --env 또는 --config-id 로 지정하세요.", file=sys.stderr)
        sys.exit(3)
    envs = ", ".join(str(c.get("environment")) for c in actives)
    print(f"[ERROR] 활성 config 가 여러 개입니다({envs}). --env 로 지정하세요.", file=sys.stderr)
    sys.exit(3)


def main() -> int:
    ap = argparse.ArgumentParser(description="활성 config 의 skills_content 교체")
    ap.add_argument("--config-url", default=os.environ.get("CONFIG_URL", "http://10.159.172.74:30180"))
    ap.add_argument("--robot", default=os.environ.get("ROBOT", "former"))
    ap.add_argument("--env", default=os.environ.get("ENVIRONMENT", "") or None,
                    help="대상 environment (미지정 시 is_active 자동 선택)")
    ap.add_argument("--config-id", type=int, default=None, help="대상 config ID 직접 지정")
    ap.add_argument("--skills-file", required=True, help="skills_content 로 넣을 마크다운 파일")
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN", ""))
    ap.add_argument("--dry-run", action="store_true", help="PUT 하지 않고 변경 미리보기만")
    args = ap.parse_args()

    if not args.token:
        print("[ERROR] 관리자 토큰이 필요합니다 (ADMIN_TOKEN 환경변수 또는 --token).", file=sys.stderr)
        return 1
    if not os.path.isfile(args.skills_file):
        print(f"[ERROR] skills 파일이 없습니다: {args.skills_file}", file=sys.stderr)
        return 1
    with open(args.skills_file, encoding="utf-8") as f:
        new_skills = f.read()

    base = args.config_url.rstrip("/")

    # 1) 대상 config ID 확정
    if args.config_id is not None:
        cfg_id = args.config_id
    else:
        status, raw = _req("GET", f"{base}/api/v1/admin/configs?robot_name={args.robot}", args.token)
        if status != 200:
            print(f"[ERROR] configs 목록 조회 실패 (HTTP {status}): {raw.decode('utf-8', 'replace')[:300]}", file=sys.stderr)
            return 1
        configs = json.loads(raw)
        if isinstance(configs, dict):  # {"configs": [...]} 형태 방어
            configs = configs.get("configs", configs.get("data", []))
        chosen = _pick_config(configs, args.env)
        cfg_id = _config_id(chosen)
        print(f"[INFO] 대상 config: id={cfg_id}, env={chosen.get('environment')}, is_active={chosen.get('is_active')}")

    # 2) 현재 config 전체 GET
    status, raw = _req("GET", f"{base}/api/v1/admin/configs/{cfg_id}", args.token)
    if status != 200:
        print(f"[ERROR] config(id={cfg_id}) 조회 실패 (HTTP {status}): {raw.decode('utf-8', 'replace')[:300]}", file=sys.stderr)
        return 1
    cfg = json.loads(raw)

    # 3) 백업
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = f"config-{cfg_id}-{ts}.bak.json"
    with open(backup, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    old = cfg.get("skills_content", "")
    print(f"[INFO] 현재 skills_content 길이={len(old)}자 → 새 내용 길이={len(new_skills)}자 (백업: {backup})")

    # 4) skills_content 교체
    cfg["skills_content"] = new_skills

    if args.dry_run:
        print("[DRY-RUN] PUT 생략. 새 skills_content 미리보기:\n" + "-" * 60)
        print(new_skills)
        print("-" * 60)
        return 0

    # 5) 전체 PUT (마스킹된 ******** 비밀값은 서버가 원본으로 복원)
    status, raw = _req("PUT", f"{base}/api/v1/admin/configs/{cfg_id}", args.token, cfg)
    if status != 200:
        print(f"[ERROR] PUT 실패 (HTTP {status}): {raw.decode('utf-8', 'replace')[:500]}", file=sys.stderr)
        print(f"        롤백하려면: PUT {base}/api/v1/admin/configs/{cfg_id} 에 {backup} 내용 전송", file=sys.stderr)
        return 1
    print(f"[SUCCESS] skills_content 반영 완료 (id={cfg_id}). 에이전트 재기동 후 SKILLS.md 재생성됩니다.")
    print(f"          검증: GET {base}/api/v1/configs/active/files/SKILLS.md (디바이스 토큰)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
