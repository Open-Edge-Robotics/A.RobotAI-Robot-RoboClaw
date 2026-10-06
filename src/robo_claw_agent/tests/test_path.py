import sys
import logging

logger = logging.getLogger(__name__)

def test_print_sys_path():
    print("\n[SYS_PATH_DEBUG]")
    for path in sys.path:
        print(f"  {path}")
    print("[SYS_PATH_DEBUG_END]")
    
    try:
        import robo_claw_agent
        print(f"robo_claw_agent path: {robo_claw_agent.__file__}")
        import robo_claw_agent.agent_node
        print("agent_node imported successfully")
        import robo_claw_agent.agent_node.execution
        print("execution imported successfully")
    except Exception as exc:
        print(f"IMPORT FAILED: {exc}")
        import traceback
        traceback.print_exc()
        assert False, f"Import failed: {exc}"
