# RoboClaw Project Analysis Documentation

This document summarizes the static analysis results for the current `robo-claw` repository based on the `docs/docs.readme.md` guide.

Related documents:

- `docs/opencode/eng/architecture.md`: System and deployment architecture summary
- `docs/opencode/eng/flowchart.md`: Main execution flowcharts
- `docs/opencode/eng/functional-architecture.md`: Functional Architecture summary

## 1. One-Line Project Summary

RoboClaw is an AI agent runtime for robots running on ROS 2. It accepts natural-language commands, lets an LLM plan skill chains, and controls a real robot or simulator through ROS actions, services, topics, and external messenger channels.

## 2. Key Components in the Current Repository

| Package | Implementation Language | Current Role |
| --- | --- | --- |
| `robo_claw_msgs` | ROS IDL + Proto | Defines actions, services, messages, and gRPC interface specifications |
| `robo_claw_core` | C++ | Hardware interface, sensor registration, 50 Hz control loop |
| `robo_claw_agent` | Python | LLM bridge, memory, skill loading, execution orchestration |
| `robo_claw_channel` | Python | HTTP, gRPC, Discord, Slack, and Telegram channel integration |
| `robo_claw_discovery` | Python | Discovery of the ROS graph and other agents |
| `robo_claw_bringup` | Python | Launch and configuration composition for real robots and simulation |

## 3. Entry Points and Execution

The practical operational entry point of the project is `rc.sh`.

- `./rc.sh setup-deps`: Configures the `uv`-based virtual environment and Python dependencies
- `./rc.sh proto`: Generates Python gRPC code from `messenger.proto`
- `./rc.sh build`: Runs `colcon build --symlink-install`
- `./rc.sh run`: Launches `robo_claw.launch.py` for a real robot
- `./rc.sh sim`: Launches `robo_claw_sim.launch.py` for simulation
- `./rc.sh test`: Runs Python tests

The runtime flow splits into two main paths.

1. Real robot launch: `robo_claw_core` + `robo_claw_agent` + `robo_claw_discovery` + optional `robo_claw_channel`
2. Simulation launch: Gazebo/Nav2 layer + `robo_claw_agent` + `robo_claw_discovery` + optional `robo_claw_channel`

## 4. Core Behavior

### 4.1 Natural-Language Command Processing

User commands enter through one of the following paths.

- HTTP `POST /task`
- gRPC `SendCommand` or `ChatStream`
- Discord/Slack/Telegram messages
- Direct ROS action call to `/<agent>/execute_task`

The common flow after that is as follows.

1. `robo_claw_channel` forwards the command to `robo_claw_agent` as an `ExecuteTask` action.
2. `robo_claw_agent` collects recent conversation history, optional RAG knowledge, and a robot state summary.
3. The currently loaded skill list is inserted dynamically into the system prompt.
4. The LLM must return one of the following in JSON format.
   - Single skill execution
   - Multi-skill chaining
   - Final natural-language response
5. `SkillManager` executes skills by name.
6. On failure, the agent feeds the failure details back into the LLM and retries replanning for up to 5 rounds.
7. If a successful result contains `file_path`, the file is automatically sent to the user through the channel node.
8. When all work is complete, the result is returned as an action response. If RAG is enabled, the successful experience is stored as knowledge.

### 4.2 Hardware and Simulation Control

`robo_claw_core` runs as a separate C++ node.

- It subscribes to `/joint_states` and updates the internal joint state.
- It publishes base control commands through the `cmd_vel` publisher.
- `HardwareInterface::write()` publishes commands in a 50 Hz loop.
- During emergency stop, it sets velocity to 0 and raises state through the error callback.

In other words, the agent does not touch hardware directly. It controls the lower layers through skills and ROS interfaces.

## 5. Package-Level Analysis

### 5.1 `robo_claw_msgs`

This package is the interface specification set for the whole system.

- `ExecuteTask.action`: High-level task execution API based on natural-language commands
- `ExecuteSkill.srv`: API for directly executing a specific skill
- `QueryState.srv`: API for querying agent/core state
- `SendMessage.srv`: API for sending messages/files through the channel node
- `AgentStatus.msg`: Agent status code and message
- `SkillResult.msg`: Individual skill execution result
- `proto/messenger.proto`: gRPC messenger interface specification for internal networks

A key characteristic is that ROS interfaces and gRPC interfaces coexist. This allows the project to support both ROS-native control and integration with external systems.

### 5.2 `robo_claw_core`

This is the real-time control and hardware abstraction layer.

- `core_node.cpp`: Core node creation, sensor/hardware initialization, 50 Hz loop management
- `hardware_interface.*`: Joint state, velocity limits, emergency stop, and `cmd_vel` publication
- `sensor_manager.*`: Registered sensor list and state JSON generation

Current implementation characteristics:

- It is closer to a ROS topic bridge than a complete direct hardware driver implementation.
- It is configured mainly through YAML parameters such as `joint_names`, `sensor_names`, `sensor_topics.*`, and `cmd_vel_topic`.
- The default Stretch 3 configuration is in `robo_claw_bringup/config/stretch3_config.yaml`.

### 5.3 `robo_claw_agent`

This is the core intelligence layer of the project.

- `agent_node.py`: Overall task orchestration
- `llm_bridge.py`: Common wrapper for Ollama, OpenAI, Azure OpenAI, and Anthropic
- `skill_manager.py`: Plugin-style skill loader and executor
- `memory_manager.py`: Short-term and long-term memory, RAG knowledge, semantic map management
- `skills/*.py`: Collection of domain-specific executable tools

Important design points:

- The actual loaded skill list is inserted into the prompt.
- The LLM output format is strongly constrained to JSON.
- Skill results are fed back into the LLM context to continue multi-step execution.
- Skills that return `file_path` have an automatic delivery path.

### 5.4 `robo_claw_channel`

This is the aggregation layer for external interfaces.

- The HTTP server is implemented directly with the standard-library `HTTPServer`.
- The gRPC server runs based on `messenger.proto`.
- Telegram, Slack, and Discord are unified behind the same abstract interface, `BaseMessengerChannel`.
- It also provides a `SendMessage` service server so the agent can send reverse messages to users.

This node is not just a simple input gateway. It is an I/O hub responsible for user responses and result file delivery.

### 5.5 `robo_claw_discovery`

This package periodically collects topics, services, actions, and node lists through the ROS CLI and publishes them as JSON.

Current roles:

- Collect ROS graph metadata
- Identify nodes matching the `robo_claw_agent_node` pattern as other agents
- Publish JSON to the `~/robot_capabilities` topic

In the current repository, the implementation that generates discovery results exists, but there is no visible code connection where the agent actively subscribes to and uses those results for decision-making.

### 5.6 `robo_claw_bringup`

This is the deployment assembly layer.

- It separates launch paths for real robots and simulation.
- It automatically infers camera topics from the robot configuration file.
- It branches by ROS distribution: Jazzy uses Gazebo Harmonic/TurtleBot4, while Humble uses Gazebo Classic/TurtleBot3.

This layer absorbs differences in robot type and simulation engine at the launch-file level.

## 6. Skill System Analysis

The default loaded skill modules are 9 modules based on `agent.yaml`.

- `navigation_skill`
- `manipulation_skill`
- `perception_skill`
- `system_skill`
- `vision_skill`
- `map_skill`
- `hri_skill`
- `cooperate_skill`
- `file_skill`

Representative skill categories are listed below.

| Category | Main Skills | Current State |
| --- | --- | --- |
| Navigation | `navigate_to`, `rotate`, `stop` | Implemented up to direct Nav2 action calls |
| Perception | `detect_object`, `get_distance` | Distance measurement is integrated; object detection is minimal |
| Vision | `analyze_scene`, `annotate_image` | VLM-based scene analysis and image annotation |
| Map | `analyze_map`, `get_map_visual`, `annotate_map` | Map image generation, VLM analysis, coordinate annotation |
| System | `get_status`, `ros_command`, `emergency_stop` | State summary, restricted ROS CLI, safety stop |
| HRI | `say`, `listen`, `send_message` | Voice/messenger interfaces, with some stubs |
| Cooperation | `delegate_task` | Task delegation to other agents |
| File | `analyze_stored_file`, `list_files`, `delete_file` | Stored file analysis and management |
| Manipulation | `grasp`, `place`, `arm_pose` | Interface-oriented; actual manipulation is still TODO-level |

## 7. Distinctive Ideas and Technical Points

### 7.1 Automatic File Delivery for Skill Results

This project does not treat skills returning `file_path` as plain result values.

- `analyze_scene`
- `annotate_image`
- `get_map_visual`
- `annotate_map`

Skills like these include `file_path` in their result, and the agent detects it and automatically sends the file through the channel node. In other words, "analysis -> file generation -> user delivery" is bundled as a runtime convention.

### 7.2 Returning Map and Vision Results to Semantic Memory

`MemoryManager` does more than store simple conversation history.

- Knowledge vector storage for RAG
- Semantic coordinate storage based on object/place names
- Alias storage, enabling re-reference through names such as `1`, `Place 1`, and `Location 1`

This design allows locations recognized once to be reused later in natural-language commands.

### 7.3 JSON-Enforced LLM Orchestration

The agent prioritizes JSON plans that are easy for machines to parse again over free-form conversational responses. This approach is especially important in robot control.

- Skill names are explicit
- Parameter structures are separated
- Failure details can be fed directly back into the replanning loop

### 7.4 Dual Interface with Closed-Network Operation in Mind

The project has a separate gRPC messenger in addition to external messengers. This clearly indicates an intent to maintain internal-network-based control even in environments without internet access.

## 8. Change List

The main change directions tracked from code comments, phase markers, and implementation differences are as follows.

1. Multimodal image analysis was added to the LLM bridge.
2. Long-term memory and RAG-based experience reuse were integrated into the agent.
3. Semantic map functionality added destination lookup by place/object name.
4. VLM-based visual tools, including map image analysis and image annotation, were strengthened.
5. The `file_path`-based automatic result delivery convention was added.
6. A forbidden-keyword-based security filter was added to `ros_command`.
7. Safety guardrails were strengthened with the addition of the `emergency_stop` skill.
8. The Discovery node was extended to identify other RoboClaw agents.
9. The dual Jazzy/Humble simulation paths were organized at the launch level.

## 9. Interface Specification Summary

### 9.1 `ExecuteTask.action`

This is the high-level task execution API.

- Input: natural-language command, additional context JSON, timeout
- Output: success flag, final message, per-skill result list
- Feedback: current stage, progress, agent state

### 9.2 `ExecuteSkill.srv`

This API directly invokes a specific skill. It is useful for tests or external automation.

### 9.3 `QueryState.srv`

This is a state query API that returns the agent or core node state as a JSON string.

### 9.4 `SendMessage.srv`

This is a reverse-delivery API where the agent asks the channel node to "send this text/file to the user."

### 9.5 `messenger.proto`

`src/robo_claw_msgs/proto/messenger.proto` is the internal messenger interface specification.

- `ChatStream`: Bidirectional streaming conversation
- `SendCommand`: One-shot command request/response
- `UploadFile`: File upload
- `DownloadFile`: File download

It acts as a separate standard channel for external clients outside ROS to control RoboClaw.

## 10. Strengths Visible in the Current State

1. The ROS 2 native structure and LLM orchestration are separated.
2. Launch configurations for both real robots and simulation are prepared.
3. External channels, internal-network channels, and ROS APIs all exist.
4. The UX for automatically delivering map/image-based visual results to users is strong.
5. Memory is not just a log; it connects to RAG and semantic navigation.

## 11. Implementation Gaps Identified in the Current State

Based on static analysis, the following items are worth documenting as the current state.

1. `robo_claw_discovery` publishes capabilities, but no direct connection was found where `robo_claw_agent` consumes them.
2. Some paths in `manipulation_skill`, `listen`, and `say` are closer to stubs or TODOs than full hardware/engine integrations.
3. `SensorManager` registers sensors but does not update the `active` state through actual topic subscriptions.
4. Semantic coordinate storage in `AnalyzeSceneSkill` currently uses a stub value for the robot position.
5. Some tests appear inconsistent with the current `MemoryManager` implementation. For example, they expect `get_recent`, `clear_all`, and SQLite-related methods, which are not immediately visible in the current implementation.

These items do not undermine the direction of the project, but they are important for distinguishing "what is already complete" from "what still needs connection."

## 12. Conclusion

The current RoboClaw can be defined as "an agent runtime where an LLM converts natural language into skill plans, then controls a real robot or simulator through ROS 2 and multiple channels."

The core values are the following three points.

1. Natural-language-based robot orchestration
2. A user interface that automatically delivers visual/map outputs
3. An accumulating agent structure that combines experience memory and semantic location memory

Conversely, the current repository is closer to an integrated prototype with a strong direction and extensible foundation than a fully finished product. Manipulation, discovery utilization, and memory test consistency appear to be the next cleanup points.
