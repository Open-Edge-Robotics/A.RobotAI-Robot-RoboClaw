package cmd

import (
	"os"
	"strconv"
	"strings"

	wcliconfig "github.com/wkqco33/wcli/config"
)

// writeConfigYAML target 경로에 설정을 YAML로 저장한다.
// wcli WriteDefault 를 사용해 구조체 default 태그 기반의 기본 템플릿을 기록한다.
func writeConfigYAML(path string, cfg interface{}) error {
	return wcliconfig.WriteDefault(cfg, path)
}

// setConfigValue config.yaml 파일에서 점 표기 키(key: "a" 또는 "a.b")의 값을 새 값으로 변경하고 저장한다.
// 반환: (기존 키를 덮어썼는지, 이전 값(없으면 ""), 에러).
func setConfigValue(path, key, value string) (bool, string, error) {
	content, err := os.ReadFile(path)
	if err != nil {
		return false, "", err
	}

	segments := strings.Split(strings.ToLower(key), ".")
	updated, replaced, oldVal := patchYAMLSecrets(string(content), segments, value)
	if err := os.WriteFile(path, []byte(updated), 0o600); err != nil {
		return false, "", err
	}
	return replaced, oldVal, nil
}

// yamlLine 은 한 줄의 파싱 결과다.
type yamlLine struct {
	raw      string
	indent   int
	key      string // map entry key (colon 앞) ; 비어있으면 entry 가 아님
	hasValue bool
	val      string // colon 뒤의 값
}

// parseYAMLLine 한 줄을 파싱한다. 주석/빈 줄은 key="" 로 반환된다.
func parseYAMLLine(line string) yamlLine {
	trimmed := strings.TrimLeft(line, " \t")
	indent := len(line) - len(trimmed)
	if trimmed == "" || strings.HasPrefix(trimmed, "#") {
		return yamlLine{raw: line, indent: indent}
	}
	idx := strings.Index(trimmed, ":")
	if idx < 0 {
		return yamlLine{raw: line, indent: indent}
	}
	key := strings.TrimSpace(trimmed[:idx])
	rest := strings.TrimSpace(trimmed[idx+1:])
	yl := yamlLine{raw: line, indent: indent, key: key}
	if rest != "" {
		yl.hasValue = true
		yl.val = stripQuotes(rest)
	}
	return yl
}

// patchYAMLSecrets 주어진 YAML 문자열에서 segments 경로의 값을 value 로 교체한다.
// 두 단계까지(섹션.하위키) 지원한다.
func patchYAMLSecrets(src string, segments []string, value string) (string, bool, string) {
	rawLines := strings.Split(src, "\n")
	lines := make([]yamlLine, len(rawLines))
	for i, l := range rawLines {
		lines[i] = parseYAMLLine(l)
	}

	fmtVal := formatYAMLValue(value)

	if len(segments) == 1 {
		lines, replaced, old := replaceLeafTop(lines, segments[0], fmtVal)
		return join(lines), replaced, old
	}

	// two segments: section.child
	section := segments[0]
	child := segments[1]
	sectionIdx := -1
	for i, l := range lines {
		if l.key == section && l.indent == 0 {
			sectionIdx = i
			break
		}
	}

	if sectionIdx < 0 {
		// 섹션 전체 추가 (파일 맨 끝)
		lines = append(lines,
			yamlLine{raw: section + ":"},
			yamlLine{raw: "  " + child + ": " + fmtVal, indent: 2, key: child, hasValue: true, val: value},
		)
		return join(lines), false, ""
	}

	// 섹션이 있는 경우, 섹션 블록(다음 최상위 항목 전까지)에서 child 탐색
	sectionEnd := endOfSection(lines, sectionIdx)
	childIdx, childOld := -1, ""
	for i := sectionIdx + 1; i < sectionEnd; i++ {
		if lines[i].key == child {
			childIdx = i
			childOld = lines[i].val
			break
		}
	}

	if childIdx >= 0 {
		lines[childIdx].raw = strings.Repeat(" ", lines[childIdx].indent) + child + ": " + fmtVal
		lines[childIdx].val = value
		lines[childIdx].hasValue = true
		return join(lines), true, childOld
	}

	// child 가 없으면 섹션 라인 바로 다음에 삽입 (기존 자식·주석은 그대로 보존)
	insertLine := yamlLine{raw: "  " + child + ": " + fmtVal, indent: 2, key: child, hasValue: true, val: value}
	insertAt := sectionIdx + 1
	newLines := make([]yamlLine, 0, len(lines)+1)
	newLines = append(newLines, lines[:insertAt]...)
	newLines = append(newLines, insertLine)
	newLines = append(newLines, lines[insertAt:]...)
	return join(newLines), false, ""
}

// endOfSection 주어진 top-level 섹션 라인 이후의 블록 끝 인덱스(다음 최상위 entry 또는 파일 끝)를 구한다.
func endOfSection(lines []yamlLine, start int) int {
	for i := start + 1; i < len(lines); i++ {
		if isRealEntry(lines[i]) && lines[i].indent == 0 {
			return i
		}
	}
	return len(lines)
}

// replaceLeafTop 최상위 레벨의 key 값을 교체한다. 없으면 마지막에 추가한다.
// 반환: (갱신된 줄, 있었는지, 이전 값).
func replaceLeafTop(lines []yamlLine, key, fmtVal string) ([]yamlLine, bool, string) {
	for i := range lines {
		l := &lines[i]
		if l.key == key && l.indent == 0 {
			old := l.val
			l.raw = key + ": " + fmtVal
			l.val = fmtVal
			l.hasValue = true
			return lines, true, old
		}
	}
	lines = append(lines, yamlLine{raw: key + ": " + fmtVal, key: key, hasValue: true, val: fmtVal})
	return lines, false, ""
}

func isRealEntry(l yamlLine) bool {
	return strings.TrimSpace(l.raw) != "" && !strings.HasPrefix(strings.TrimSpace(l.raw), "#")
}

// join 파싱된 줄들을 다시 문자열로 합친다.
func join(lines []yamlLine) string {
	var b strings.Builder
	for _, l := range lines {
		b.WriteString(l.raw)
		b.WriteString("\n")
	}
	return b.String()
}

// formatYAMLValue 값을 YAML 리터럴로 포맷한다 (bool/숫자는 비인용, 그 외는 인용).
func formatYAMLValue(s string) string {
	s = strings.TrimSpace(s)
	switch strings.ToLower(s) {
	case "true", "false":
		return strings.ToLower(s)
	}
	if _, err := strconv.ParseInt(s, 10, 64); err == nil {
		return s
	}
	if _, err := strconv.ParseFloat(s, 64); err == nil {
		return s
	}
	return strconv.Quote(s)
}

// stripQuotes 값이 따옴표로 감싸져 있으면 제거한다 (표시용).
func stripQuotes(s string) string {
	s = strings.TrimSpace(s)
	if len(s) >= 2 {
		if (s[0] == '"' && s[len(s)-1] == '"') || (s[0] == '\'' && s[len(s)-1] == '\'') {
			return s[1 : len(s)-1]
		}
	}
	return s
}
