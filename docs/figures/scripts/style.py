import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

plt.rcParams["font.family"] = ["DejaVu Sans", "Noto Sans CJK KR"]

DARK = "#1F3864"
HEAD = "#2F5597"
LINE = "#4472C4"
PANEL = "#DEEBF7"
LIGHT = "#BDD7EE"
RED = "#C00000"
GRAY = "#595959"

STYLES = {
    "dark": dict(fc=DARK, ec=DARK, color="white", weight="bold"),
    "head": dict(fc=HEAD, ec=HEAD, color="white", weight="bold"),
    "panel": dict(fc=PANEL, ec="#9DC3E6", color=DARK, weight="bold"),
    "item": dict(fc="white", ec=HEAD, color=DARK, weight="normal"),
    "light": dict(fc=LIGHT, ec=HEAD, color=DARK, weight="bold"),
    "red": dict(fc="#FBE5E5", ec=RED, color=RED, weight="bold"),
    "redd": dict(fc=RED, ec=RED, color="white", weight="bold"),
}


def canvas(w, h, W, H):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")
    ax.set_aspect("equal")
    return fig, ax


def box(ax, x, y, w, h, text="", style="item", fs=8, va="center", lw=1.0, pad=0.0, dashed=False, z=2):
    s = STYLES[style]
    p = FancyBboxPatch(
        (x, y), w, h,
        boxstyle=f"round,pad={pad},rounding_size=0.12",
        fc=s["fc"], ec=s["ec"], lw=lw, zorder=z,
        ls="--" if dashed else "-",
    )
    ax.add_patch(p)
    if text:
        ty = y + h / 2 if va == "center" else y + h - 0.12
        ax.text(x + w / 2, ty, text, ha="center", va=va, fontsize=fs,
                color=s["color"], weight=s["weight"], zorder=z + 1, linespacing=1.15)
    return (x, y, w, h)


def arrow(ax, p1, p2, color=LINE, lw=1.4, both=False, rad=0.0, ls="-", z=5, head=8):
    a = FancyArrowPatch(
        p1, p2,
        arrowstyle=("<|-|>" if both else "-|>"),
        mutation_scale=head, color=color, lw=lw, ls=ls,
        connectionstyle=f"arc3,rad={rad}", zorder=z, shrinkA=0, shrinkB=0,
    )
    ax.add_patch(a)


def poly(ax, pts, color=LINE, lw=1.4, ls="-", z=5, head=8):
    """Orthogonal polyline with an arrowhead on the last segment."""
    for (x1, y1), (x2, y2) in zip(pts[:-2], pts[1:-1]):
        ax.plot([x1, x2], [y1, y2], color=color, lw=lw, ls=ls, zorder=z, solid_capstyle="butt")
    arrow(ax, pts[-2], pts[-1], color=color, lw=lw, ls=ls, z=z, head=head)


def label(ax, x, y, text, fs=7, color=GRAY, ha="center", style="italic", weight="normal", z=6, bg=None):
    kw = {}
    if bg:
        kw["bbox"] = dict(fc=bg, ec="none", pad=0.6)
    ax.text(x, y, text, ha=ha, va="center", fontsize=fs, color=color,
            style=style, weight=weight, zorder=z, **kw)


def save(fig, name, outdir):
    for ext in ("png", "pdf", "svg"):
        fig.savefig(f"{outdir}/{name}.{ext}", dpi=300, bbox_inches="tight", pad_inches=0.05)
    plt.close(fig)
