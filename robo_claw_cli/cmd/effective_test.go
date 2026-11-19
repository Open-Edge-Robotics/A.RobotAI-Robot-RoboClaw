package cmd

import "testing"

func TestDisplaySecretMasksConfiguredSecret(t *testing.T) {
	if got := displaySecret("OPENAI_API_KEY", "secret"); got != "********" {
		t.Fatalf("secret was not masked: %s", got)
	}
}

func TestDisplaySecretKeepsNonSecretValue(t *testing.T) {
	if got := displaySecret("RC_LLM_MODEL", "gpt-4.1"); got != "gpt-4.1" {
		t.Fatalf("non-secret value was changed: %s", got)
	}
}

func TestDisplaySecretMasksLangfuseKeys(t *testing.T) {
	for _, key := range []string{"LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"} {
		if got := displaySecret(key, "pk-lf-secret"); got != "********" {
			t.Fatalf("%s was not masked: %s", key, got)
		}
	}
}
