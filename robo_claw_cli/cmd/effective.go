package cmd

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/logging"
	"robo_claw_cli/launcher"
)

var effectiveKeys = map[string]string{
	"llm_provider":  "RC_LLM_PROVIDER",
	"llm_model":     "RC_LLM_MODEL",
	"agent_id":      "RC_AGENT_ID",
	"ros_domain_id": "ROS_DOMAIN_ID",
	"enable_rag":    "RC_ENABLE_RAG",
	"http_port":     "RC_HTTP_PORT",
	"use_vision":    "RC_USE_VISION",
	"grpc_port":                    "ROBO_CLAW_GRPC_PORT",
	"system1_router":                "SYSTEM1_ROUTER",
	"system1_shadow":                "SYSTEM1_SHADOW",
	"system1_shadow_log":            "SYSTEM1_SHADOW_LOG",
	"system1_scope":                 "SYSTEM1_SCOPE",
	"system1_endpoint":              "SYSTEM1_ENDPOINT",
	"system1_provider":              "SYSTEM1_PROVIDER",
	"system1_timeout_ms":            "SYSTEM1_TIMEOUT_MS",
	"system1_conf_thresholds_json":  "SYSTEM1_CONF_THRESHOLDS_JSON",
	"system1_skills":                "SYSTEM1_SKILLS",
	"system1_max_options":           "SYSTEM1_MAX_OPTIONS",
	"system1_api_key":               "SYSTEM1_API_KEY",
}

var secretKeys = map[string]bool{
	"AZURE_OPENAI_API_KEY": true,
	"OPENAI_API_KEY":       true,
	"ANTHROPIC_API_KEY":    true,
	"QDRANT_API_KEY":       true,
	"LANGSMITH_API_KEY":    true,
	"GRPC_PEER_TOKEN":      true,
	"SYSTEM1_API_KEY":       true,
}

// EffectiveCmd displays the values that the cached launcher will consume.
func EffectiveCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "config-effective [robot_name] [environment]",
		Short: "캐시된 설정의 최종값과 출처를 표시합니다.",
		Run: func(ctx *wcli.Context) error {
			if len(ctx.Args) < 2 {
				return fmt.Errorf("로봇 이름과 환경명을 입력해야 합니다. 예: robo_claw_cli config-effective butler office")
			}
			config, env, err := loadCachedConfig(ctx.Args[0], ctx.Args[1])
			if err != nil {
				return err
			}
			keys := make([]string, 0, len(effectiveKeys))
			for key := range effectiveKeys {
				keys = append(keys, key)
			}
			sort.Strings(keys)
			for _, key := range keys {
				envKey := effectiveKeys[key]
				if value, ok := env[envKey]; ok {
					logging.Info("%s=%s (source=.env:%s)", key, displaySecret(envKey, value), envKey)
					continue
				}
				if value, ok := config[key]; ok {
					logging.Info("%s=%s (source=config.json)", key, displayJSONValue(key, value))
					continue
				}
				logging.Warn("%s is missing", key)
			}
			return nil
		},
	}
}

func loadCachedConfig(robotName, environment string) (map[string]interface{}, map[string]string, error) {
	home, err := os.UserHomeDir()
	if err != nil {
		return nil, nil, fmt.Errorf("홈 디렉토리를 확인할 수 없습니다: %w", err)
	}
	dir := filepath.Join(home, ".robo_claw", "config_cache", robotName+"_"+environment)
	configBytes, err := os.ReadFile(filepath.Join(dir, "config.json"))
	if err != nil {
		return nil, nil, fmt.Errorf("config.json cache를 읽을 수 없습니다: %w", err)
	}
	var config map[string]interface{}
	if err := json.Unmarshal(configBytes, &config); err != nil {
		return nil, nil, fmt.Errorf("config.json cache 형식이 올바르지 않습니다: %w", err)
	}
	_, env, err := launcher.ParseEnvFile(filepath.Join(dir, ".env"))
	if err != nil {
		return nil, nil, fmt.Errorf(".env cache를 읽을 수 없습니다: %w", err)
	}
	return config, env, nil
}

func displaySecret(key, value string) string {
	if secretKeys[key] && value != "" {
		return "********"
	}
	return value
}

func displayJSONValue(key string, value interface{}) string {
	if strings.Contains(strings.ToLower(key), "token") || strings.Contains(strings.ToLower(key), "api_key") {
		return "********"
	}
	bytes, err := json.Marshal(value)
	if err != nil {
		return "<invalid>"
	}
	return string(bytes)
}
