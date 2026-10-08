#!/usr/bin/env python3
"""시스템 프롬프트의 '과도한 강제어' 사용을 점검하는 린트 스크립트.

프롬프트가 LLM의 판단 유연성을 해치는 '반드시/절대/무조건'류 강제 표현을
얼마나 남용하는지 정량적으로 측정하고, 초과 시 경고를 반환한다.

기준(기본값, --strict 미사용 시):
- '절대'는 '절대 좌표'/'절대 방향'처럼 강제가 아닌 용어를 제외하고 계수한다.
- 강제어(강제 의미의 절대/반드시/절대로/무조건/금지)가 SAFE_THRESHOLD 이하 권장.

사용법:
    python scripts/check_prompt_tone.py [--strict] [파일 ...]
기본 파일: prompts.py + config/SKILLS.*.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

# 강제 의미를 갖는 표현 (안전 규칙을 위한 강제는 별도로 구분하지 않고 전체를 측정)
FORCE_WORDS = ["반드시", "무조건", "절대로"]
# '절대'는 '절대 좌표/방향/값' 등 비-강제 용어가 흔하므로 단독으로 계수하지 않고,
# '절대 + (하지|사용하지|넣지|추측|상상)' 같은 금지 패턴만 잡는다.
ABSOLUTE_BAN_PATTERN = re.compile(r"절대[^ \n]{0,4}(하지|말고|금지|넣지|추측|상상|출력)")

# 허용 한도 (안전 규칙 등 정당한 강제를 감안해 여유를 둔다)
SAFE_THRESHOLD = 10
# 안전 정책 블록(강제가 정당한 영역)은 이만큼까지는 항상 허용
SAFE_BLOCK_MARKERS = ["주행 안전", "안전 정책", "emergency_stop"]


def count_forces(text: str) -> dict[str, int]:
    counts = {w: text.count(w) for w in FORCE_WORDS}
    counts["절대(금지문맥)"] = len(ABSOLUTE_BAN_PATTERN.findall(text))
    counts["_total"] = sum(counts.values())
    return counts


def is_safe_block_line(line: str) -> bool:
    return any(m in line for m in SAFE_BLOCK_MARKERS)


def main() -> int:
    strict = "--strict" in sys.argv
    threshold = 5 if strict else SAFE_THRESHOLD
    files = [a for a in sys.argv[1:] if not a.startswith("--")]

    root = Path(__file__).resolve().parent.parent
    if not files:
        candidates = [
            root / "src/robo_claw_agent/robo_claw_agent/agent_node/prompts.py",
            root / "src/robo_claw_bringup/config",
        ]
        files = []
        for c in candidates:
            if c.is_dir():
                files += sorted(c.glob("SKILLS.*.md"))
                # 개체별 소울 프로필(ROBOT.<robot>.<개체>.md)도 프롬프트에 주입되므로 함께 점검한다.
                # ROBOT.example.md 는 주석 처리된 배포용 템플릿이므로 제외한다.
                files += sorted(p for p in c.glob("ROBOT.*.md") if p.name != "ROBOT.example.md")
                files += [
                    c / "ROBOT.md",
                    c / "TROUBLESHOOTING.md",
                ]
            elif c.exists():
                files.append(str(c))

    total = 0
    for f in files:
        p = Path(f)
        if not p.exists():
            print(f"[skip] {p} not found")
            continue
        text = p.read_text(encoding="utf-8")
        # 안전 정책 블록 안의 강제는 제외하고 측정 (정당한 강제 영역)
        lines = text.splitlines()
        safe = [l for l in lines if is_safe_block_line(l)]
        non_safe_text = "\n".join(l for l in lines if not is_safe_block_line(l))
        counts = count_forces(non_safe_text)
        safe_counts = count_forces("\n".join(safe))
        total += counts["_total"]
        flag = "OK " if counts["_total"] <= threshold else "WARN"
        print(
            f"[{flag}] {p}  강제어={counts['_total']} "
            f"(반드시:{counts['반드시']} 무조건:{counts['무조건']} 절대로:{counts['절대로']} "
            f"절대금지:{counts['절대(금지문맥)']})  / 안전블록 내 강제={safe_counts['_total']}"
        )

    print(f"\n합계(비안전 블록 강제어): {total} / 한도: {threshold}")
    return 1 if total > threshold else 0


if __name__ == "__main__":
    raise SystemExit(main())
