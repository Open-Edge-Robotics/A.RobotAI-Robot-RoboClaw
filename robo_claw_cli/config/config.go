package config

// Config 애플리케이션 설정 구조체
type Config struct {
	AppName     string `wcli:"name" default:"robo_claw_cli"`
	DeviceToken string `wcli:"device_token" default:""`
	Server      struct {
		Host string `wcli:"host" default:"0.0.0.0"`
		Port int    `wcli:"port" default:"8080"`
		// 개발용 평문 HTTP는 명시적으로만 허용한다.
		AllowInsecureHTTP bool `wcli:"allow_insecure_http" default:"false"`
	} `wcli:"server"`
	Log struct {
		Level string `wcli:"level" default:"info"`
	} `wcli:"log"`
	Contract struct {
		Project string `wcli:"project" default:"wkqco33/rcf-config-contract"`
		BaseURL string `wcli:"base_url" default:"https://gitlab.com/api/v4"`
	} `wcli:"contract"`
}
