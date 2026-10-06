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
