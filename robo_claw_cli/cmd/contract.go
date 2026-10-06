package cmd

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/wkqco33/wcli"
	"github.com/wkqco33/wcli/rich"
)

// ContractCmd 는 컨트랙트 관리 통합 부모 커맨드를 생성합니다.
func ContractCmd() *wcli.Command {
	cmd := &wcli.Command{
		Use:   "contract",
		Short: "런타임 설정 컨트랙트 검증 및 업데이트 도구",
	}

	cmd.AddCommand(
		contractCheckCmd(),
		contractUpdateSubCmd(),
	)

	return cmd
}

func contractCheckCmd() *wcli.Command {
	return &wcli.Command{
		Use:   "check",
		Short: "벤더링된 컨트랙트 체크섬, 환경변수 문서화 및 생성 코드 일치 여부(drift) 검증",
		Run: func(ctx *wcli.Context) error {
			root, err := findRoboRoot()
			if err != nil {
				return err
			}

			// 1. Lock 파일 및 체크섬 검증
			if err := verifyContractLock(root); err != nil {
				rich.Println("[red]✘[/red] 컨트랙트 잠금(lock) 검증 실패: %v", err)
				return err
			}
			rich.Println("[green]✔[/green] 벤더링된 런타임 컨트랙트 잠금(lock)이 유효합니다.")

			// 2. .env.example 환경변수 문서화 누락 검증
			doc, err := LoadContractDoc(filepath.Join(root, "contracts"))
			if err != nil {
				rich.Println("[red]✘[/red] 컨트랙트 문서 로드 실패: %v", err)
				return err
			}
			if err := verifyEnvCoverage(root, doc); err != nil {
				rich.Println("[red]✘[/red] 환경변수 커버리지 검증 실패: %v", err)
				return err
			}
			rich.Println("[green]✔[/green] 모든 레지스트리 환경변수가 .env.example에 문서화되어 있습니다.")

			// 3. 아티팩트 코드 drift 검증
			changes, err := GenerateArtifacts(root, true)
			if err != nil {
				rich.Println("[red]✘[/red] 아티팩트 검증 중 오류: %v", err)
				return err
			}
			if len(changes) > 0 {
				rich.Println("[red]✘[/red] 생성 대상 파일이 최신 컨트랙트와 다릅니다 (drift 발생):")
				for _, p := range changes {
					rich.Println("  - %s", p)
				}
				rich.Println("조치: 'robo_claw_cli contract update'를 실행하여 아티팩트를 동기화하세요.")
				return fmt.Errorf("컨트랙트 아티팩트 불일치 (%d개 파일)", len(changes))
			}

			rich.Println("[green]✔[/green] 생성된 모든 아티팩트가 최신 상태입니다.")
			rich.Println("[bold green]✔ 모든 설정 컨트랙트 검증을 통과했습니다.[/bold green]")
			return nil
		},
	}
}

func contractUpdateSubCmd() *wcli.Command {
	var version string
	var source string

	cmd := &wcli.Command{
		Use:   "update",
		Short: "공개 GitLab contract release 다운로드 또는 로컬 번들 반영 및 코드 자동 생성",
		Run: func(ctx *wcli.Context) error {
			return executeContractUpdate(version, source)
		},
	}
	cmd.Flags().StringVar(&version, "version", "", "", "GitLab Release version")
	cmd.Flags().StringVar(&source, "from", "", "", "로컬 release bundle 경로")
	return cmd
}

// ContractUpdateCmd 는 하위 호환성을 위한 루트 레벨 alias 커맨드입니다.
func ContractUpdateCmd() *wcli.Command {
	var version string
	var source string
	cmd := &wcli.Command{
		Use:   "contract-update",
		Short: "공개 GitLab contract release 갱신 (deprecated: 'contract update' 권장)",
		Run: func(ctx *wcli.Context) error {
			rich.Println("[yellow]ℹ[/yellow] 'contract-update'는 향후 지원 중단될 수 있습니다. 'robo_claw_cli contract update'를 사용하세요.")
			return executeContractUpdate(version, source)
		},
	}
	cmd.Flags().StringVar(&version, "version", "", "", "GitLab Release version")
	cmd.Flags().StringVar(&source, "from", "", "", "로컬 release bundle 경로")
	return cmd
}

func executeContractUpdate(version, source string) error {
	root, err := findRoboRoot()
	if err != nil {
		return err
	}
	contractsDir := filepath.Join(root, "contracts")

	if source != "" {
		rich.Println("[cyan]▶[/cyan] 로컬 번들에서 컨트랙트 복사 중: %s", source)
		if err := copyContractBundle(source, contractsDir); err != nil {
			return err
		}
	} else if version != "" {
		rich.Println("[cyan]▶[/cyan] GitLab Release(%s)에서 컨트랙트 다운로드 중...", version)
		if err := downloadContractRelease(version, contractsDir); err != nil {
			return err
		}
	} else {
		return fmt.Errorf("--version 또는 --from을 지정하세요")
	}

	rich.Println("[green]✔[/green] 컨트랙트 및 lock 파일 갱신 완료.")

	// 다운로드/복사 직후 소비자 코드 및 스키마 자동 동기화 생성
	rich.Println("[cyan]▶[/cyan] 런타임 소비자 코드 및 아티팩트 자동 생성 중...")
	changes, err := GenerateArtifacts(root, false)
	if err != nil {
		return fmt.Errorf("아티팩트 자동 생성 실패: %w", err)
	}

	if len(changes) > 0 {
		rich.Println("[green]✔[/green] 업데이트된 아티팩트 파일 목록:")
		for _, p := range changes {
			rich.Println("  - %s", p)
		}
	} else {
		rich.Println("[green]✔[/green] 모든 아티팩트가 최신 상태입니다.")
	}

	rich.Println("[bold green]✔ 컨트랙트 업데이트 및 코드 동기화가 성공적으로 완료되었습니다.[/bold green]")
	return nil
}

func verifyEnvCoverage(root string, doc *ContractDoc) error {
	examplePath := filepath.Join(root, ".env.example")
	data, err := os.ReadFile(examplePath)
	if err != nil {
		return nil
	}
	content := string(data)

	var missing []string
	for _, f := range doc.Fields {
		if f.Env == "" {
			continue
		}
		re := regexp.MustCompile(`(?m)^\s*#?\s*` + regexp.QuoteMeta(f.Env) + `\s*=`)
		if !re.MatchString(content) {
			missing = append(missing, f.Env)
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf(".env.example에 누락된 환경변수: %s", strings.Join(missing, ", "))
	}
	return nil
}

func verifyContractLock(root string) error {
	contractsDir := filepath.Join(root, "contracts")
	lockPath := filepath.Join(contractsDir, "contract.lock.json")
	data, err := os.ReadFile(lockPath)
	if err != nil {
		return fmt.Errorf("contracts/contract.lock.json 누락 또는 읽기 실패: %w", err)
	}

	var lock struct {
		RuntimeContractSha256 string `json:"runtime_contract_sha256"`
		SchemaSha256          string `json:"schema_sha256"`
	}
	if err := json.Unmarshal(data, &lock); err != nil {
		return fmt.Errorf("contract.lock.json 파싱 실패: %w", err)
	}

	for _, item := range []struct {
		filename string
		expected string
	}{
		{"runtime-contract.json", lock.RuntimeContractSha256},
		{"runtime-config.schema.json", lock.SchemaSha256},
	} {
		p := filepath.Join(contractsDir, item.filename)
		content, err := os.ReadFile(p)
		if err != nil {
			return fmt.Errorf("contracts/%s 파일이 누락되었습니다", item.filename)
		}
		actual := digest(content)
		if actual != item.expected {
			return fmt.Errorf("contracts/%s 체크섬 불일치 (기대값: %s, 실제값: %s)", item.filename, item.expected, actual)
		}
	}

	return nil
}

func findRoboRoot() (string, error) {
	wd, err := os.Getwd()
	if err != nil {
		return "", err
	}
	for dir := wd; ; dir = filepath.Dir(dir) {
		if _, err := os.Stat(filepath.Join(dir, "contracts", "contract.lock.json")); err == nil {
			return dir, nil
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			break
		}
	}
	return wd, fmt.Errorf("robo_claw root를 찾을 수 없습니다")
}

type releaseInfo struct {
	Assets struct {
		Links []struct {
			Name string `json:"name"`
			URL  string `json:"url"`
		} `json:"links"`
	} `json:"assets"`
}

func downloadContractRelease(version, target string) error {
	project := url.PathEscape(cfg.Contract.Project)
	api := strings.TrimRight(cfg.Contract.BaseURL, "/") + "/projects/" + project + "/releases/" + url.PathEscape(version)
	var info releaseInfo
	if err := getJSON(api, &info); err != nil {
		return err
	}
	links := map[string]string{}
	for _, link := range info.Assets.Links {
		links[link.Name] = link.URL
	}
	bundle := make(map[string][]byte)
	for _, name := range []string{"runtime-contract.json", "runtime-config.schema.json", "contract-manifest.json"} {
		if !strings.HasPrefix(links[name], "https://") {
			return fmt.Errorf("release asset %s must use HTTPS", name)
		}
		body, err := getBytes(links[name])
		if err != nil {
			return err
		}
		bundle[name] = body
	}
	return installContractBundle(target, bundle)
}

func copyContractBundle(source, target string) error {
	bundle := map[string][]byte{}
	for _, name := range []string{"runtime-contract.json", "runtime-config.schema.json", "contract-manifest.json"} {
		data, err := os.ReadFile(filepath.Join(source, name))
		if err != nil {
			return err
		}
		bundle[name] = data
	}
	return installContractBundle(target, bundle)
}

func installContractBundle(target string, bundle map[string][]byte) error {
	if err := os.MkdirAll(target, 0755); err != nil {
		return err
	}
	for name, data := range bundle {
		if err := os.WriteFile(filepath.Join(target, name), data, 0644); err != nil {
			return err
		}
	}
	lock := fmt.Sprintf("{\n  \"name\": \"rcf-runtime-config\",\n  \"version\": \"%s\",\n  \"source\": \"gitlab-release://rcf-config-contract/%s\",\n  \"runtime_contract_sha256\": \"%s\",\n  \"schema_sha256\": \"%s\",\n  \"supported_range\": \">=2.0.0,<3.0.0\"\n}\n", bundleVersion(bundle["contract-manifest.json"]), bundleVersion(bundle["contract-manifest.json"]), digest(bundle["runtime-contract.json"]), digest(bundle["runtime-config.schema.json"]))
	return os.WriteFile(filepath.Join(target, "contract.lock.json"), []byte(lock), 0644)
}

func bundleVersion(data []byte) string {
	var v struct {
		ContractVersion string `json:"contract_version"`
	}
	_ = json.Unmarshal(data, &v)
	return v.ContractVersion
}

func digest(data []byte) string {
	h := sha256.Sum256(data)
	return hex.EncodeToString(h[:])
}

func getBytes(raw string) ([]byte, error) {
	resp, err := http.Get(raw)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return nil, fmt.Errorf("contract download failed: HTTP %d", resp.StatusCode)
	}
	return io.ReadAll(resp.Body)
}

func getJSON(raw string, target any) error {
	data, err := getBytes(raw)
	if err != nil {
		return err
	}
	return json.Unmarshal(data, target)
}
