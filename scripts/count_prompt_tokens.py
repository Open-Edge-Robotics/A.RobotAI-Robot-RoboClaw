#!/usr/bin/env python3
"""프롬프트 파일(들)을 Ollama에 넣어 실제 토큰 수(prompt_eval_count)를 측정한다.

왜: num_ctx 를 얼마로 잡을지 정하려면 시스템 프롬프트가 실제 몇 토큰인지 알아야 한다.
파일 여러 개(SKILLS.md, soul.md, troubleshooting.md 등)를 이어붙여 한 번에 측정한다.

측정 시에는 --num-ctx 를 충분히 크게(기본 65536) 주어 프롬프트가 잘리지 않게 한 뒤
prompt_eval_count(모델이 실제 처리한 입력 토큰 수)를 읽는다.

사용 예:
  ./count_prompt_tokens.py SKILLS.former.md soul.md troubleshooting.md limits.json
  OLLAMA_URL=http://10.159.172.75:11434 ./count_prompt_tokens.py SKILLS.former.md
  ./count_prompt_tokens.py --model gemma4:e4b --num-ctx 65536 SKILLS.former.md
  # 표준입력으로도 가능:  cat a.md b.md | ./count_prompt_tokens.py -
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    ap = argparse.ArgumentParser(description="파일 프롬프트의 토큰 수 측정 (Ollama prompt_eval_count)")
    ap.add_argument("files", nargs="+", help="이어붙일 프롬프트 파일들 ('-' 이면 표준입력)")
    ap.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", "gemma4:e4b"))
    ap.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://10.159.172.75:11434"))
    ap.add_argument("--num-ctx", type=int, default=65536,
                    help="측정용 컨텍스트 크기(프롬프트가 잘리지 않게 충분히 크게, 기본 65536)")
    ap.add_argument("--sep", default="\n\n", help="파일 사이 구분자 (기본: 빈 줄)")
    ap.add_argument("--show-prompt", action="store_true", help="이어붙인 프롬프트를 함께 출력")
    args = ap.parse_args()

    parts: list[str] = []
    for fn in args.files:
        if fn == "-":
            parts.append(sys.stdin.read())
            continue
        if not os.path.isfile(fn):
            print(f"[ERROR] 파일 없음: {fn}", file=sys.stderr)
            return 1
        with open(fn, encoding="utf-8") as f:
            parts.append(f.read())
    prompt = args.sep.join(parts)
    n_chars = len(prompt)

    payload = {
        "model": args.model,
        "prompt": prompt,
        "stream": False,
        # num_predict=0 → 토큰 생성 없이 프롬프트 평가만(빠름). 일부 버전은 무시할 수 있음.
        "options": {"num_ctx": args.num_ctx, "num_predict": 0, "temperature": 0},
    }
    url = args.ollama_url.rstrip("/") + "/api/generate"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        print(f"[ERROR] HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}", file=sys.stderr)
        return 2
    except urllib.error.URLError as e:
        print(f"[ERROR] 연결 실패: {url} — {e}", file=sys.stderr)
        return 2

    tok = body.get("prompt_eval_count")
    if args.show_prompt:
        print(prompt)
        print("=" * 60)
    print(f"파일 수        : {len(args.files)}")
    print(f"문자 수        : {n_chars}")
    if tok:
        print(f"prompt_eval_count(토큰): {tok}")
        print(f"문자/토큰 비율 : {n_chars / tok:.2f}  (num_ctx 산정용 참고)")
        # 권장 num_ctx: 시스템 프롬프트 + 이력/도구결과 여유(2배) 를 2의 거듭제곱으로 올림
        need = tok * 2
        for c in (8192, 16384, 32768, 65536, 131072):
            if c >= need:
                print(f"권장 num_ctx   : {c} 이상 (시스템 프롬프트 {tok}토큰 + 이력/도구결과 여유 반영)")
                break
    else:
        print("[WARN] prompt_eval_count 가 응답에 없습니다. Ollama 버전/모델을 확인하세요.")
        print(json.dumps(body, ensure_ascii=False)[:400])
    if tok and args.num_ctx < tok:
        print(f"[경고] --num-ctx({args.num_ctx}) < 프롬프트 토큰({tok}) → 측정값이 잘렸을 수 있음. 더 크게 다시 측정하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
