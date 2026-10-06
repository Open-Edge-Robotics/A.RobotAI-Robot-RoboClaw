# RoboClaw Architecture

## System Architecture

```mermaid
graph TD
    U[User] --> HTTP[HTTP REST]
    U --> MSG[Discord / Slack / Telegram]
    U --> GRPC[gRPC Messenger]
    HTTP --> CH[robo_claw_channel]
    MSG --> CH
    GRPC --> CH
    CH -->|ExecuteTask action| AG[robo_claw_agent]
    CH -->|ExecuteSkill service| AG
    CH -->|QueryState service| AG
    AG --> LLM[LLM Bridge]
    AG --> MEM[MemoryManager]
    AG --> SK[SkillManager]
    SK --> NAV[Navigation Skills]
    SK --> VIS[Vision / Map Skills]
    SK --> SYS[System / HRI Skills]
    SK --> COOP[Cooperate / File Skills]
    NAV --> ROS[ROS 2 Graph / Nav2 / Topics]
    VIS --> ROS
    SYS --> ROS
    COOP --> ROS
    AG -->|SendMessage service| CH
    ROS --> CORE[robo_claw_core]
    CORE --> HW[Robot HW / Simulator]
    DISC[robo_claw_discovery] --> ROS
    DISC --> CAP[Capability JSON]
```

## Deployment Architecture

```mermaid
graph LR
    subgraph Runtime
        BR[robo_claw_bringup]
        CH[robo_claw_channel]
        AG[robo_claw_agent]
        DISC[robo_claw_discovery]
    end

    subgraph Real Robot Path
        CORE[robo_claw_core]
        HW[Stretch 3 or other robot]
    end

    subgraph Simulation Path
        GZ[Gazebo]
        NAV2[Nav2 / AMCL / SLAM]
    end

    BR --> CH
    BR --> AG
    BR --> DISC
    BR --> CORE
    BR --> GZ
    GZ --> NAV2
    CORE --> HW
    AG --> NAV2
    AG --> GZ
```

## Layer Responsibilities

| Layer | Components | Role |
| --- | --- | --- |
| External Interface | HTTP, gRPC, Discord, Slack, Telegram | Collect user commands and deliver results |
| Orchestration | `robo_claw_agent` | Collect context, plan, replan, control execution |
| Execution Tools | `SkillManager` + `skills/*` | Invoke actual robot capabilities |
| Memory/Knowledge | `MemoryManager` | Store conversation memory, RAG data, semantic locations |
| ROS Integration | Nav2, topics, services, actions | Robot control and sensor queries |
| Hardware Layer | `robo_claw_core` | Control loop, sensor registration, safety stop |
| Deployment Assembly | `robo_claw_bringup`, `rc.sh` | Environment, build, and launch composition |

## Interpretation Points

1. `robo_claw_channel` is not just an API server. It is a hub that integrates all external channels.
2. `robo_claw_agent` is the central brain. It combines skills and ROS interfaces rather than directly controlling devices.
3. `robo_claw_core` handles the hardware-friendly loop, while `robo_claw_agent` handles inference and planning.
4. gRPC and ROS interfaces coexist, supporting both closed-network and external-network operation.
