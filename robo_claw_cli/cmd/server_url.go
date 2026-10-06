package cmd

import (
	"fmt"
	"net/url"
)

// serverBaseURL은 device token이 전송되는 config server의 기본 URL을 만든다.
// 평문 HTTP는 로컬 개발 시에만 명시적으로 허용한다.
func serverBaseURL() (string, error) {
	scheme := "https"
	if cfg.Server.AllowInsecureHTTP {
		scheme = "http"
	}
	serverURL := url.URL{Scheme: scheme, Host: fmt.Sprintf("%s:%d", cfg.Server.Host, cfg.Server.Port)}
	return serverURL.String(), nil
}
