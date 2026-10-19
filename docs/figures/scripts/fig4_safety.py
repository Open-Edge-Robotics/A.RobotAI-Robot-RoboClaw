import sys

from style import DARK, GRAY, RED, arrow, box, canvas, label, save

OUT = sys.argv[1]
fig, ax = canvas(8.6, 6.67, 16, 12.4)

X0, HW, IW = 1.9, 2.8, 5.7  # layer header x / header width / detail width

box(ax, X0, 11.2, HW + IW, 0.9, "Request  (User · Peer Robot · LLM Plan)", "light", fs=7.5)

layers = [
    ("Access Control", "HTTP: CIDR allowlist · role token · rate limit\ngRPC peer: token · expiry · dedup"),
    ("Planner Guard", "skill-chain validation · ROBOT_LIMITS in prompt\naction skills blocked in conversational query"),
    ("Skill Manager", "allow / block policy · preconditions · schema\ntimeout + cancel · bounded retry · recover"),
    ("Hard Limits", "Navigation Safety Gate: forbidden zone ·\ndistance / rotation limit · joint target limit"),
    ("Sensor Health", "LiDAR · IMU · camera health · stale-message\nrejection · frame check · battery guard"),
    ("Core Control Loop\n(C++, 50 Hz)", "stale joint_states · command watchdog\njoint velocity clamp · E-stop latch"),
]
H, GAP, TOP = 1.15, 0.38, 9.65
ys = [TOP - i * (H + GAP) for i in range(len(layers))]
for (name, detail), y in zip(layers, ys):
    box(ax, X0, y, HW, H, name, "head" if y != ys[-1] else "dark", fs=7)
    box(ax, X0 + HW + 0.1, y, IW - 0.1, H, detail, "item", fs=6)

cx = X0 + (HW + IW) / 2
arrow(ax, (cx, 11.2), (cx, ys[0] + H))
for y in ys[:-1]:
    arrow(ax, (cx, y), (cx, y - GAP), head=6)

box(ax, X0, 0.5, HW + IW, 0.85, "Robot Hardware  (base · arm · sensors)", "light", fs=7.5)
arrow(ax, (cx, ys[-1]), (cx, 1.35))

# ---- Emergency stop: highest-priority bypass path ----
box(ax, 0.3, 0.5, 1.2, 11.6, "", "redd")
ax.text(0.9, 6.3, "Emergency Stop  (highest priority)\nzero cmd_vel · cancel Nav2 goal · latch",
        rotation=90, ha="center", va="center", fontsize=7, color="white", weight="bold", zorder=4,
        linespacing=1.4)
for y in (ys[3] + H / 2, ys[5] + H / 2, 0.92):
    arrow(ax, (1.5, y), (X0, y), color=RED, head=7)
label(ax, 1.7, ys[0] + H + 0.2, "", fs=1)

# ---- Observability ----
OX = 11.0
box(ax, OX, 0.5, 4.7, 11.6, "", "panel")
box(ax, OX, 11.45, 4.7, 0.65, "Observability", "head", fs=8)
obs = [
    ("Tracing (LangSmith)", "task · planning loop · skill\npolicy / schema checks"),
    ("Runtime Metrics", "count · success / failure\nlatency · last error"),
    ("Event & History Log", "task events · skill episodes\nrequest / response history"),
    ("Status Streaming", "AgentStatus · task feedback\n(step, progress)"),
    ("Dashboard / QueryState", "health · battery · peers"),
]
oh, og = 1.6, 0.4
for i, (t, d) in enumerate(obs):
    y = 9.45 - i * (oh + og)
    box(ax, OX + 0.25, y, 4.2, oh, "", "item")
    ax.text(OX + 2.35, y + oh - 0.35, t, ha="center", va="center", fontsize=7, color=DARK,
            weight="bold", zorder=4)
    ax.text(OX + 2.35, y + 0.6, d, ha="center", va="center", fontsize=6, color=DARK, zorder=4,
            linespacing=1.2)

for y in ys:
    arrow(ax, (X0 + HW + IW, y + H / 2), (OX, y + H / 2), color=GRAY, ls="--", lw=1.0, head=6)

save(fig, "fig4_safety_observability", OUT)
