package cmd

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestContractCommandHierarchy(t *testing.T) {
	cmd := ContractCmd()
	if cmd == nil {
		t.Fatal("ContractCmd() returned nil")
	}
	if cmd.Use != "contract" {
		t.Errorf("expected use 'contract', got %q", cmd.Use)
	}

	// update 서브커맨드 실행 시 플래그 누락 에러 확인
	err := cmd.Execute([]string{"update"})
	if err == nil || !strings.Contains(err.Error(), "--version 또는 --from") {
		t.Errorf("expected '--version 또는 --from' error, got %v", err)
	}
}

func TestVerifyContractLock(t *testing.T) {
	tempDir := t.TempDir()
	contractsDir := filepath.Join(tempDir, "contracts")
	if err := os.MkdirAll(contractsDir, 0755); err != nil {
		t.Fatal(err)
	}

	// 1. Missing lock file
	err := verifyContractLock(tempDir)
	if err == nil {
		t.Error("expected error when contract.lock.json is missing, got nil")
	}

	// 2. Prepare valid dummy bundle
	contractData := []byte(`{"fields": []}`)
	schemaData := []byte(`{"type": "object"}`)
	contractSha := digest(contractData)
	schemaSha := digest(schemaData)

	if err := os.WriteFile(filepath.Join(contractsDir, "runtime-contract.json"), contractData, 0644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(contractsDir, "runtime-config.schema.json"), schemaData, 0644); err != nil {
		t.Fatal(err)
	}

	lockContent := `{"runtime_contract_sha256": "` + contractSha + `", "schema_sha256": "` + schemaSha + `"}`
	if err := os.WriteFile(filepath.Join(contractsDir, "contract.lock.json"), []byte(lockContent), 0644); err != nil {
		t.Fatal(err)
	}

	// Should pass
	if err := verifyContractLock(tempDir); err != nil {
		t.Errorf("expected verifyContractLock to pass, got: %v", err)
	}

	// 3. Mismatch checksum
	tamperedData := []byte(`{"fields": ["tampered"]}`)
	if err := os.WriteFile(filepath.Join(contractsDir, "runtime-contract.json"), tamperedData, 0644); err != nil {
		t.Fatal(err)
	}
	if err := verifyContractLock(tempDir); err == nil {
		t.Error("expected checksum mismatch error, got nil")
	}
}

func TestGenerateArtifacts_CurrentRepo(t *testing.T) {
	root, err := findRoboRoot()
	if err != nil {
		t.Skip("Skipping test: robo_claw root not found")
	}

	doc, err := LoadContractDoc(filepath.Join(root, "contracts"))
	if err != nil {
		t.Fatal(err)
	}

	// Compare pyCode
	pyCode := RenderPythonLaunchContract(doc)
	existingPy, _ := os.ReadFile(filepath.Join(root, "src", "robo_claw_bringup", "launch", "_generated_runtime_config.py"))
	if string(pyCode) != string(existingPy) {
		t.Logf("pyCode diff:\n--- Generated (len %d):\n%s\n--- Existing (len %d):\n%s", len(pyCode), pyCode[:min(300, len(pyCode))], len(existingPy), existingPy[:min(300, len(existingPy))])
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}
