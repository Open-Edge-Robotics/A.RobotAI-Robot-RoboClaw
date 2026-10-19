import sys

from style import DARK, RED, arrow, box, canvas, label, poly, save

OUT = sys.argv[1]
fig, ax = canvas(8.6, 6.67, 16, 12.4)

# ---- Row 1: ingress -> router ----
box(ax, 0.3, 10.7, 1.7, 1.1, "User", "light", fs=8)
box(ax, 2.5, 10.7, 3.1, 1.1, "Channel Gateway\nHTTP · Messenger · gRPC", "head", fs=6.3)
box(ax, 6.1, 10.7, 3.1, 1.1, "ExecuteTask Action\n+ Task Queue", "head", fs=7)
box(ax, 9.7, 10.7, 3.6, 1.1, "System 1 Router\n(Rule / Fast Router)", "dark", fs=7.5)
box(ax, 13.8, 10.7, 1.9, 1.1, "Direct\nReply", "item", fs=7)
arrow(ax, (2.0, 11.25), (2.5, 11.25))
arrow(ax, (5.6, 11.25), (6.1, 11.25))
arrow(ax, (9.2, 11.25), (9.7, 11.25))
arrow(ax, (13.3, 11.25), (13.8, 11.25))
label(ax, 13.55, 11.95, "simple_reply", fs=6)

# ---- System 2 planner panel ----
box(ax, 0.3, 3.7, 11.3, 6.4, "", "panel")
box(ax, 0.3, 9.45, 11.3, 0.65, "System 2 Planner (LLM-based)", "head", fs=8)
arrow(ax, (10.5, 10.7), (10.5, 10.1))
label(ax, 10.85, 10.4, "llm", fs=6, ha="left")

box(ax, 0.6, 7.6, 3.4, 1.5, "Context Builder\nRAG · Robot State · Health\nSkill Lessons · History", "item", fs=6.5)
box(ax, 4.4, 7.6, 3.4, 1.5, "Task Decomposer\ncompound instruction\n→ Step 1 … N", "item", fs=6.5)
box(ax, 8.2, 7.6, 3.1, 1.5, "Step Context\nfresh robot state\n+ previous results", "item", fs=6.5)
arrow(ax, (4.0, 8.35), (4.4, 8.35))
arrow(ax, (7.8, 8.35), (8.2, 8.35))

# ReAct loop
box(ax, 0.6, 4.75, 1.9, 1.4, "LLM", "dark", fs=9)
box(ax, 2.9, 4.75, 2.1, 1.4, "Parse\n(JSON Plan)", "item", fs=7)
box(ax, 5.4, 4.75, 2.6, 1.4, "Schema / Policy\nValidation", "item", fs=7)
box(ax, 8.4, 4.75, 2.9, 1.4, "Navigation\nSafety Prelude", "item", fs=7)
arrow(ax, (2.5, 5.45), (2.9, 5.45))
arrow(ax, (5.0, 5.45), (5.4, 5.45))
arrow(ax, (8.0, 5.45), (8.4, 5.45))
poly(ax, [(9.75, 7.6), (9.75, 6.9), (1.55, 6.9), (1.55, 6.15)])
label(ax, 5.6, 7.15, "ReAct planning loop (bounded rounds / format retries)", fs=6.3, color=DARK)
# validation failure -> replan
poly(ax, [(6.7, 6.15), (6.7, 6.55), (2.1, 6.55), (2.1, 6.15)], color=RED, ls="--", lw=1.1)
label(ax, 4.4, 6.35, "invalid → error feedback", fs=5.8, color=RED, bg="#DEEBF7")

# ---- Skill Manager ----
box(ax, 12.1, 3.7, 3.6, 6.4, "", "panel")
box(ax, 12.1, 9.45, 3.6, 0.65, "Skill Manager", "head", fs=8)
items = ["Allow / Block Policy", "Preconditions", "Input Schema / Params",
         "Timeout · Cancel · Retry", "Recover / Postcondition"]
for i, t in enumerate(items):
    box(ax, 12.35, 8.45 - i * 1.0, 3.1, 0.75, t, "item", fs=6.5)
for i in range(len(items) - 1):
    arrow(ax, (13.9, 8.45 - i * 1.0), (13.9, 8.2 - i * 1.0), head=5, lw=1.0)
poly(ax, [(12.9, 10.7), (12.9, 10.1)])
label(ax, 12.75, 10.4, "direct_skill", fs=6, ha="right")
arrow(ax, (11.3, 5.45), (12.1, 5.45))
label(ax, 11.7, 6.1, "skill\nchain", fs=5.8)

# execution result feedback -> replan
poly(ax, [(12.1, 4.05), (1.0, 4.05), (1.0, 4.75)], color=RED, ls="--", lw=1.1)
label(ax, 6.6, 4.3, "SkillResult / failure context (error type, robot state) → replan", fs=5.8, color=RED, bg="#DEEBF7")

# ---- Bottom row ----
box(ax, 0.3, 0.6, 5.0, 2.3, "", "panel")
box(ax, 0.3, 2.25, 5.0, 0.65, "Task Result", "head", fs=7.5)
box(ax, 0.55, 0.8, 4.5, 1.25, "final answer + SkillResult[ ]\npartial execution report (step i / N)", "item", fs=6.3)
arrow(ax, (2.8, 3.7), (2.8, 2.9))

box(ax, 5.8, 0.6, 9.9, 2.3, "", "panel")
box(ax, 5.8, 2.25, 9.9, 0.65, "Robot Skills (BaseSkill plugins)", "head", fs=7.5)
sk = ["Navigation", "Manipulation", "Vision", "Map", "System/HRI", "Cooperate"]
w = 1.48
for i, t in enumerate(sk):
    box(ax, 6.0 + i * (w + 0.13), 0.95, w, 1.0, t, "item", fs=6)
arrow(ax, (13.9, 3.7), (13.9, 2.9))

label(ax, 8.0, 12.25, "", fs=1)
save(fig, "fig2_task_planning", OUT)
