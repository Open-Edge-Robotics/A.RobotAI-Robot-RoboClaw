package launcher

import (
	"fmt"
	"os"
	"path/filepath"
	"testing"
)

func hasVolume(args []string, source, target, mode string) bool {
	want := fmt.Sprintf("%s:%s:%s", source, target, mode)
	for i := 0; i < len(args)-1; i++ {
		if args[i] == "-v" && args[i+1] == want {
			return true
		}
	}
	return false
}

func countVolumeTarget(args []string, target string) int {
	count := 0
	suffix := fmt.Sprintf(":%s:ro", target)
	for i := 0; i < len(args)-1; i++ {
		if args[i] == "-v" && len(args[i+1]) >= len(suffix) && args[i+1][len(args[i+1])-len(suffix):] == suffix {
			count++
		}
	}
	return count
}

func TestBuildDockerVolumesMountsModelsForDefaultAndHostPath(t *testing.T) {
	root := t.TempDir()
	modelsDir := filepath.Join(root, "models")
	if err := os.MkdirAll(modelsDir, 0755); err != nil {
		t.Fatalf("models 디렉토리 생성 실패: %v", err)
	}

	args := buildDockerVolumes(&LaunchContext{
		ProjectRoot: root,
		CacheDir:    t.TempDir(),
		EnvMap:      map[string]string{},
	})

	if !hasVolume(args, modelsDir, "/ros2_ws/models", "ro") {
		t.Fatalf("기본 컨테이너 모델 경로 마운트 누락: %v", args)
	}
	if !hasVolume(args, modelsDir, modelsDir, "ro") {
		t.Fatalf("호스트 절대 모델 경로 마운트 누락: %v", args)
	}
}

func TestBuildDockerVolumesMountsCustomVisionModelDir(t *testing.T) {
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "models"), 0755); err != nil {
		t.Fatalf("models 디렉토리 생성 실패: %v", err)
	}
	customModelDir := filepath.Join(t.TempDir(), "vision")
	if err := os.MkdirAll(customModelDir, 0755); err != nil {
		t.Fatalf("커스텀 모델 디렉토리 생성 실패: %v", err)
	}

	args := buildDockerVolumes(&LaunchContext{
		ProjectRoot: root,
		CacheDir:    t.TempDir(),
		EnvMap: map[string]string{
			"RC_VISION_MODEL_PATH": filepath.Join(customModelDir, "yolov8n.onnx"),
		},
	})

	if !hasVolume(args, customModelDir, customModelDir, "ro") {
		t.Fatalf("커스텀 비전 모델 디렉토리 마운트 누락: %v", args)
	}
	if got := countVolumeTarget(args, customModelDir); got != 1 {
		t.Fatalf("커스텀 비전 모델 디렉토리 중복 마운트: got=%d args=%v", got, args)
	}
}

func TestBuildDockerVolumesDoesNotMaskChannelPackageWithoutGeneratedProtos(t *testing.T) {
	root := t.TempDir()
	channelSrcDir := filepath.Join(root, "src", "robo_claw_channel", "robo_claw_channel")
	if err := os.MkdirAll(channelSrcDir, 0755); err != nil {
		t.Fatalf("채널 소스 디렉토리 생성 실패: %v", err)
	}

	args := buildDockerVolumes(&LaunchContext{
		ProjectRoot: root,
		CacheDir:    t.TempDir(),
		EnvMap:      map[string]string{},
	})

	target := "/ros2_ws/install/robo_claw_channel/local/lib/python3.10/dist-packages/robo_claw_channel"
	if got := countVolumeTarget(args, target); got != 0 {
		t.Fatalf("proto 생성물 없는 채널 소스가 설치 패키지를 가림: got=%d args=%v", got, args)
	}
}

func TestBuildDockerVolumesMountsChannelPackageWithGeneratedProtos(t *testing.T) {
	root := t.TempDir()
	channelSrcDir := filepath.Join(root, "src", "robo_claw_channel", "robo_claw_channel")
	if err := os.MkdirAll(channelSrcDir, 0755); err != nil {
		t.Fatalf("채널 소스 디렉토리 생성 실패: %v", err)
	}
	for _, name := range []string{"messenger_pb2.py", "messenger_pb2_grpc.py"} {
		if err := os.WriteFile(filepath.Join(channelSrcDir, name), []byte("# generated\n"), 0644); err != nil {
			t.Fatalf("proto 생성물 생성 실패: %v", err)
		}
	}

	args := buildDockerVolumes(&LaunchContext{
		ProjectRoot: root,
		CacheDir:    t.TempDir(),
		EnvMap:      map[string]string{},
	})

	target := "/ros2_ws/install/robo_claw_channel/local/lib/python3.10/dist-packages/robo_claw_channel"
	if got := countVolumeTarget(args, target); got != 1 {
		t.Fatalf("proto 생성물이 있는 채널 소스 마운트 누락: got=%d args=%v", got, args)
	}
}

// 런치 파일 수정을 이미지 재빌드 없이 반영하려면 bringup launch 디렉토리를 마운트해야 한다.
func TestBuildDockerVolumesMountsBringupLaunchDir(t *testing.T) {
	root := t.TempDir()
	launchDir := filepath.Join(root, "src", "robo_claw_bringup", "launch")
	if err := os.MkdirAll(launchDir, 0755); err != nil {
		t.Fatalf("launch 디렉토리 생성 실패: %v", err)
	}

	args := buildDockerVolumes(&LaunchContext{
		ProjectRoot: root,
		CacheDir:    t.TempDir(),
		EnvMap:      map[string]string{},
	})

	target := "/ros2_ws/install/robo_claw_bringup/share/robo_claw_bringup/launch"
	if !hasVolume(args, launchDir, target, "ro") {
		t.Fatalf("bringup launch 디렉토리 마운트 누락: %v", args)
	}
}

func TestBuildDockerVolumesSkipsMissingBringupLaunchDir(t *testing.T) {
	args := buildDockerVolumes(&LaunchContext{
		ProjectRoot: t.TempDir(),
		CacheDir:    t.TempDir(),
		EnvMap:      map[string]string{},
	})

	target := "/ros2_ws/install/robo_claw_bringup/share/robo_claw_bringup/launch"
	if got := countVolumeTarget(args, target); got != 0 {
		t.Fatalf("launch 디렉토리 부재 시 마운트하면 안 됨: got=%d args=%v", got, args)
	}
}
