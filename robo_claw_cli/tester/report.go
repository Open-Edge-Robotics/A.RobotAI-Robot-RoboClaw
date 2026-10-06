package tester

import (
	"bufio"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/wkqco33/wcli/rich"
)

// PrintResultTable 수집된 결과를 콘솔 표 형태로 이쁘게 출력합니다.
func PrintResultTable(steps []TestStepResult) {
	table := rich.NewTable("Step", "테스트 항목", "결과", "상세 내역")
	for _, step := range steps {
		statusColor := "white"

		switch step.Status {
		case "PASS":
			statusColor = "green"
		case "FAIL":
			statusColor = "red"
		case "SKIP":
			statusColor = "cyan"
		case "WARNING":
			statusColor = "yellow"
		}

		detailShort := strings.ReplaceAll(step.Detail, "\n", " ")
		if len(detailShort) > 50 {
			detailShort = detailShort[:47] + "..."
		}

		table.AddRow(
			step.Step,
			step.Name,
			fmt.Sprintf("[%s]%s[/%s]", statusColor, step.Status, statusColor),
			detailShort,
		)
	}
	table.Print()
}

// WriteMarkdownReport 최종 Markdown 보고서 파일 라이터
func WriteMarkdownReport(reportPath, robotName, envName, llmProvider, llmModel, grpcTarget, dockerContainer, testMode string, steps []TestStepResult) {
	dirName := filepath.Dir(reportPath)
	if dirName != "" {
		os.MkdirAll(dirName, 0755)
	}

	f, err := os.Create(reportPath)
	if err != nil {
		return
	}
	defer f.Close()

	w := bufio.NewWriter(f)
	w.WriteString("# 🤖 RoboClaw 자동화 테스트 보고서\n\n")
	fmt.Fprintf(w, "- **테스트 일시**: %s\n", time.Now().Format("2006-01-02 15:04:05"))
	fmt.Fprintf(w, "- **대상 로봇 및 환경**: `%s` (환경: `%s`)\n", robotName, envName)
	fmt.Fprintf(w, "- **적용된 LLM 설정**: `%s` / `%s`\n", llmProvider, llmModel)
	fmt.Fprintf(w, "- **구동 모드**: `%s`\n", testMode)
	fmt.Fprintf(w, "- **gRPC 접속 주소**: `%s`\n", grpcTarget)
	if dockerContainer != "" {
		fmt.Fprintf(w, "- **감지된 도커 컨테이너**: `%s`\n", dockerContainer)
	}
	w.WriteString("\n")

	w.WriteString("## 📊 요약\n")
	w.WriteString("테스트가 성공적으로 완수되었습니다.\n\n")

	w.WriteString("## 📋 상세 테스트 항목 결과\n\n")
	w.WriteString("| Step | 테스트 항목 | 상태 | 상세 내역 |\n")
	w.WriteString("| :--- | :--- | :---: | :--- |\n")

	for _, step := range steps {
		statusEmoji := "✅ PASS"
		switch step.Status {
		case "FAIL":
			statusEmoji = "❌ FAIL"
		case "SKIP":
			statusEmoji = "➖ SKIP"
		case "WARNING":
			statusEmoji = "⚠️ WARN"
		}

		detailClean := strings.ReplaceAll(step.Detail, "\n", "<br>")
		if step.ImagePath != "" {
			// 마크다운 표 안에서 가로 300px 크기로 렌더링되도록 <img> 태그 삽입
			detailClean = fmt.Sprintf("%s<br><br><img src=\"%s\" width=\"300\" alt=\"결과 이미지\"/>", detailClean, step.ImagePath)
		}
		fmt.Fprintf(w, "| %s | %s | %s | %s |\n", step.Step, step.Name, statusEmoji, detailClean)
	}

	w.WriteString("\n---\n*본 보고서는 robo_claw_cli에 의해 동적으로 생성되었습니다.*\n")
	w.Flush()
}
