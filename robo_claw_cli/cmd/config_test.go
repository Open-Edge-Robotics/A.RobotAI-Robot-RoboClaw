package cmd

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestFormatYAMLValue(t *testing.T) {
	cases := []struct{ in, want string }{
		{"true", "true"},
		{"FALSE", "false"},
		{"8080", "8080"},
		{"3.14", "3.14"},
		{"10.159.172.74", `"10.159.172.74"`},
		{"hello world", `"hello world"`},
		{"", `""`},
	}
	for _, c := range cases {
		if got := formatYAMLValue(c.in); got != c.want {
			t.Errorf("formatYAMLValue(%q) = %s, want %s", c.in, got, c.want)
		}
	}
}

func TestParseYAMLLine(t *testing.T) {
	// 주석
	l := parseYAMLLine("# 주석")
	if l.key != "" {
		t.Errorf("주석 줄이 entry로 파싱됨: %+v", l)
	}
	// 빈 줄
	l = parseYAMLLine("   ")
	if l.key != "" {
		t.Errorf("빈 줄이 entry로 파싱됨: %+v", l)
	}
	// 최상위 scalar
	l = parseYAMLLine("name: robo_claw_cli")
	if l.indent != 0 || l.key != "name" || !l.hasValue || l.val != "robo_claw_cli" {
		t.Errorf("최상위 scalar 파싱 실패: %+v", l)
	}
	// 중첩 섹션
	l = parseYAMLLine("  host: 10.0.0.1")
	if l.indent != 2 || l.key != "host" || l.val != "10.0.0.1" {
		t.Errorf("중첩 scalar 파싱 실패: %+v", l)
	}
	// 인용값
	l = parseYAMLLine(`  allow_insecure_http: "true"`)
	if l.val != "true" {
		t.Errorf("인용값 스트립 실패: val=%q", l.val)
	}
}

func TestPatchYAML_ReplaceTop(t *testing.T) {
	src := "name: old\nlog:\n  level: info\n"
	out, replaced, old := patchYAMLSecrets(src, []string{"name"}, "new")
	if !replaced || old != "old" {
		t.Fatalf("replace name 실패: replaced=%v old=%q", replaced, old)
	}
	if !strings.Contains(out, "name: \"new\"") {
		t.Errorf("out에 새값 없음:\n%s", out)
	}
}

func TestPatchYAML_ReplaceNested(t *testing.T) {
	src := "server:\n  host: 10.159.172.74\n  port: 30180\nlog:\n  level: debug\n"
	out, replaced, old := patchYAMLSecrets(src, []string{"server", "host"}, "10.0.0.1")
	if !replaced || old != "10.159.172.74" {
		t.Fatalf("replace server.host 실패: replaced=%v old=%q", replaced, old)
	}
	if !strings.Contains(out, "  host: \"10.0.0.1\"") {
		t.Errorf("out에 server.host 새값 없음:\n%s", out)
	}
	if !strings.Contains(out, "  port: 30180") {
		t.Errorf("형제 키가 손상됨:\n%s", out)
	}
}

func TestPatchYAML_AddNested(t *testing.T) {
	src := "name: x\nserver:\n  port: 8080\n"
	out, replaced, _ := patchYAMLSecrets(src, []string{"server", "host"}, "1.2.3.4")
	if replaced {
		t.Fatal("기존 host가 없는데 replaced=true")
	}
	if !strings.Contains(out, "  host: \"1.2.3.4\"") || !strings.Contains(out, "  port: 8080") {
		t.Errorf("섹션에 host 추가 실패:\n%s", out)
	}
}

func TestPatchYAML_AddNewSection(t *testing.T) {
	src := "name: x\n"
	out, replaced, _ := patchYAMLSecrets(src, []string{"log", "level"}, "warn")
	if replaced {
		t.Fatal("기존 log.level 없는데 replaced=true")
	}
	if !strings.Contains(out, "log:\n") || !strings.Contains(out, "  level: \"warn\"") {
		t.Errorf("log.level 섹션 신규 추가 실패:\n%s", out)
	}
}

func TestPatchYAML_BoolValue(t *testing.T) {
	src := "server:\n  allow_insecure_http: false\n"
	out, _, _ := patchYAMLSecrets(src, []string{"server", "allow_insecure_http"}, "true")
	if !strings.Contains(out, "  allow_insecure_http: true") {
		t.Errorf("bool 값 설정 실패:\n%s", out)
	}
}

func TestSetConfigValue_ReadWrite(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "config.yaml")
	if err := os.WriteFile(path, []byte("name: a\nserver:\n  host: 0.0.0.0\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	replaced, old, err := setConfigValue(path, "server.host", "1.2.3.4")
	if err != nil {
		t.Fatal(err)
	}
	if !replaced || old != "0.0.0.0" {
		t.Fatalf("setConfigValue 결과: replaced=%v old=%q", replaced, old)
	}
	b, _ := os.ReadFile(path)
	if !strings.Contains(string(b), "  host: \"1.2.3.4\"") {
		t.Errorf("파일 저장 내용 확인 실패:\n%s", b)
	}
}
