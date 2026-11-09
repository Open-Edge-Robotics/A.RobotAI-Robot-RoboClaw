package cmd

import "testing"

func TestValidateRuntimeManifest(t *testing.T) {
	config := map[string]interface{}{
		"llm_provider":  "ollama",
		"llm_model":     "llama3.2",
		"ros_domain_id": float64(10),
	}
	if err := validateRuntimeManifest("2.0", "revision-1", config); err != nil {
		t.Fatal(err)
	}
}

func TestValidateRuntimeManifestRejectsInvalidDomain(t *testing.T) {
	err := validateRuntimeManifest("2.0", "revision-1", map[string]interface{}{
		"llm_provider":  "ollama",
		"llm_model":     "llama3.2",
		"ros_domain_id": float64(300),
	})
	if err == nil {
		t.Fatal("expected invalid ROS domain to be rejected")
	}
}

func TestValidateManifestConfigRequiresRuntimeFields(t *testing.T) {
	if err := validateManifestConfig(map[string]interface{}{
		"llm_provider": "ollama",
		"llm_model":    "llama3.2",
	}); err == nil {
		t.Fatal("expected missing ros_domain_id to be rejected")
	}
}

func TestValidateRuntimeManifestAcceptsEnabledMaestro(t *testing.T) {
	err := validateRuntimeManifest("2.0", "revision-1", map[string]interface{}{
		"llm_provider":    "ollama",
		"llm_model":       "llama3.2",
		"ros_domain_id":   float64(10),
		"maestro_enabled": true,
		"maestro_ip":      "10.0.0.5",
		"robot_id":        "former-01",
		"use_grpc":        true,
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestValidateRuntimeManifestAcceptsDisabledMaestroWithStaleIp(t *testing.T) {
	err := validateRuntimeManifest("2.0", "revision-1", map[string]interface{}{
		"llm_provider":    "ollama",
		"llm_model":       "llama3.2",
		"ros_domain_id":   float64(10),
		"maestro_enabled": false,
		"maestro_ip":      "10.0.0.5",
	})
	if err != nil {
		t.Fatal(err)
	}
}

func TestValidateRuntimeManifestRequiresMaestroIpWhenEnabled(t *testing.T) {
	err := validateRuntimeManifest("2.0", "revision-1", map[string]interface{}{
		"llm_provider":    "ollama",
		"llm_model":       "llama3.2",
		"ros_domain_id":   float64(10),
		"maestro_enabled": true,
		"robot_id":        "former-01",
		"use_grpc":        true,
	})
	if err == nil {
		t.Fatal("expected missing maestro_ip to be rejected")
	}
}

func TestValidateRuntimeManifestRequiresRobotIdWhenMaestroEnabled(t *testing.T) {
	err := validateRuntimeManifest("2.0", "revision-1", map[string]interface{}{
		"llm_provider":    "ollama",
		"llm_model":       "llama3.2",
		"ros_domain_id":   float64(10),
		"maestro_enabled": true,
		"maestro_ip":      "10.0.0.5",
		"use_grpc":        true,
	})
	if err == nil {
		t.Fatal("expected missing robot_id to be rejected")
	}
}

func TestValidateRuntimeManifestRequiresUseGrpcWhenMaestroEnabled(t *testing.T) {
	err := validateRuntimeManifest("2.0", "revision-1", map[string]interface{}{
		"llm_provider":    "ollama",
		"llm_model":       "llama3.2",
		"ros_domain_id":   float64(10),
		"maestro_enabled": true,
		"maestro_ip":      "10.0.0.5",
		"robot_id":        "former-01",
		"use_grpc":        false,
	})
	if err == nil {
		t.Fatal("expected disabled use_grpc to be rejected")
	}
}
