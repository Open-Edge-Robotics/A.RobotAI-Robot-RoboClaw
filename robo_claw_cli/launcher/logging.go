package launcher

import "strings"

// FormatLaunchLogArgs는 최종 override만 표시하고 진단에 필요한 값만 공개합니다.
// 반환값은 로그 전용이며 실제 실행 인자는 변경하지 않습니다.
func FormatLaunchLogArgs(args []string) string {
	safe := map[string]bool{"robot_config": true, "llm_provider": true, "llm_model": true, "use_grpc": true, "enable_grpc": true, "use_channel": true, "use_client": true, "use_vision": true, "use_gripper_vision": true, "run_core": true, "run_manipulator": true, "enable_rag": true, "http_port": true, "grpc_port": true}
	last := map[string]int{}
	for i, arg := range args {
		if key, _, ok := strings.Cut(arg, ":="); ok {
			last[key] = i
		}
	}
	result := []string{}
	for i, arg := range args {
		key, value, ok := strings.Cut(arg, ":=")
		if !ok {
			result = append(result, arg)
			continue
		}
		if last[key] != i {
			continue
		}
		if !safe[key] {
			value = "<masked>"
		}
		result = append(result, key+":="+value)
	}
	return strings.Join(result, " ")
}

func launchSummary(args []string) string {
	formatted := strings.Fields(FormatLaunchLogArgs(args))
	result := []string{}
	for _, arg := range formatted {
		if !strings.HasSuffix(arg, ":=<masked>") {
			arg = strings.ReplaceAll(arg, "enable_grpc:=", "channel_grpc=")
			arg = strings.ReplaceAll(arg, "use_grpc:=", "ros_grpc_bridge=")
			result = append(result, arg)
		}
	}
	return strings.Join(result, " ")
}
