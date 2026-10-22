package launcher

import (
	"bytes"
	"fmt"
	"io"
	"regexp"
	"sync"
	"time"
)

// ConsoleLogWriter는 자식 프로세스의 각 출력 줄에 콘솔 수신 시각을 붙입니다.
// ROS가 자체 저장하는 로그는 변경하지 않습니다.
type ConsoleLogWriter struct {
	mu      sync.Mutex
	out     io.Writer
	pending []byte
	now     func() time.Time
}

var rosEpoch = regexp.MustCompile(`(\[(?:DEBUG|INFO|WARN|ERROR|FATAL)\]) \[[0-9]{9,}\.[0-9]+\] `)

func NewConsoleLogWriter(out io.Writer) *ConsoleLogWriter {
	return &ConsoleLogWriter{out: out, now: time.Now}
}

func (w *ConsoleLogWriter) Write(data []byte) (int, error) {
	w.mu.Lock()
	defer w.mu.Unlock()
	w.pending = append(w.pending, data...)
	for {
		end := bytes.IndexByte(w.pending, '\n')
		if end < 0 {
			break
		}
		if err := w.emit(w.pending[:end]); err != nil {
			return 0, err
		}
		w.pending = w.pending[end+1:]
	}
	return len(data), nil
}

func (w *ConsoleLogWriter) emit(line []byte) error {
	text := rosEpoch.ReplaceAllString(string(line), "$1 ")
	_, err := fmt.Fprintf(w.out, "%s %s\n", w.now().Format("2006-01-02 15:04:05"), text)
	return err
}

// Flush는 개행 없이 종료된 마지막 출력도 보존합니다.
func (w *ConsoleLogWriter) Flush() error {
	w.mu.Lock()
	defer w.mu.Unlock()
	if len(w.pending) == 0 {
		return nil
	}
	err := w.emit(w.pending)
	w.pending = nil
	return err
}
