import sys

from style import DARK, RED, arrow, box, canvas, label, save

OUT = sys.argv[1]
fig, ax = canvas(8.6, 5.86, 16, 10.9)

# ---------------- Robot A (requester) ----------------
box(ax, 0.3, 1.0, 6.0, 9.6, "", "panel")
box(ax, 0.3, 9.95, 6.0, 0.65, "Robot A (Requester)", "head", fs=8)
box(ax, 0.5, 8.6, 5.6, 1.0, "Core (LLM Planner)", "dark", fs=8.5)

box(ax, 0.5, 4.2, 5.6, 4.0, "", "light")
box(ax, 0.5, 7.6, 5.6, 0.6, "Cooperate Skills", "head", fs=7.5)
rows = [
    "list_peer_robots · query_peer_status / capabilities",
    "delegate_task  (ROS 2 action)",
    "call_peer_robot · broadcast_to_peers  (gRPC)",
    "coordinate_peer_task  (LLM step split + retry)",
]
for i, t in enumerate(rows):
    box(ax, 0.7, 6.8 - i * 0.8, 5.2, 0.65, t, "item", fs=6)
arrow(ax, (3.3, 8.6), (3.3, 8.2))

box(ax, 0.5, 1.3, 2.7, 2.6, "Discovery Node\n\nROS graph scan\ntopics · services\nactions · nodes\n→ capabilities", "item", fs=6)
box(ax, 3.4, 1.3, 2.7, 2.6, "Channel Client\n\nPeer Registry\n(connection state)\nListPeers\nCallPeerRobot", "item", fs=6)
arrow(ax, (1.85, 3.9), (1.85, 4.2), both=True, head=6, lw=1.1)
arrow(ax, (4.75, 3.9), (4.75, 4.2), both=True, head=6, lw=1.1)

# ---------------- Transport ----------------
box(ax, 6.75, 5.65, 2.5, 1.5, "ROS 2 DDS\nExecuteTask Action\n(same domain)", "head", fs=6.3)
box(ax, 6.75, 1.8, 2.5, 1.6, "gRPC\nRoboMessenger\n(peer token)", "head", fs=6.3)
label(ax, 8.0, 8.0, "Transport", fs=7.5, color=DARK, style="normal", weight="bold")

# A -> transport
arrow(ax, (5.9, 6.38), (6.75, 6.38), both=True)
arrow(ax, (6.1, 2.6), (6.75, 2.6), both=True)
# transport -> B
arrow(ax, (9.25, 6.38), (9.9, 6.38), both=True)
arrow(ax, (9.25, 2.6), (9.9, 2.6), both=True)
label(ax, 8.0, 7.45, "goal / feedback / result", fs=5.8)
label(ax, 8.0, 3.7, "command / response", fs=5.8)
label(ax, 8.0, 4.5, "cross-network\nfallback", fs=5.8, color=DARK)

# ---------------- Robot B (peer) ----------------
box(ax, 9.7, 1.0, 6.0, 9.6, "", "panel")
box(ax, 9.7, 9.95, 6.0, 0.65, "Robot B (Peer)", "head", fs=8)
box(ax, 9.9, 8.3, 5.6, 1.2, "Skill Manager → Robot Skills", "item", fs=7)
box(ax, 9.9, 6.0, 5.6, 1.6, "Core (LLM Planner)", "dark", fs=8.5)
arrow(ax, (12.7, 7.6), (12.7, 8.3))

box(ax, 12.9, 4.4, 2.6, 1.2, "Remote Exec. Policy\nallowlist · risk level", "red", fs=6)
box(ax, 9.9, 1.3, 2.7, 2.6, "Channel Node\n(gRPC Server)\n\nauth token\nexpiry · dedup", "item", fs=6)
box(ax, 12.9, 1.3, 2.6, 2.6, "Peer Message\nInbox\n\n(cooperation\nmessages)", "item", fs=6)
arrow(ax, (12.6, 2.6), (12.9, 2.6), head=6, lw=1.1)
arrow(ax, (14.2, 3.9), (14.2, 4.4), head=6, lw=1.1)
arrow(ax, (14.2, 5.6), (14.2, 6.0), head=6, lw=1.1)
arrow(ax, (11.25, 3.9), (11.25, 6.0))
label(ax, 11.35, 4.95, "ExecuteTask", fs=5.8, ha="left")

# ---------------- footnote ----------------
label(ax, 8.0, 0.45,
      "Bounded timeouts on every delegation path · on timeout the remote side effect is reported as \"unknown\"",
      fs=6, color=RED)

save(fig, "fig3_discovery_delegation", OUT)
