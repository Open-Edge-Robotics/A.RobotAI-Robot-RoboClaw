package cmd

import "testing"

func TestServerBaseURLUsesHTTPSByDefault(t *testing.T) {
	old := cfg
	t.Cleanup(func() { cfg = old })
	cfg.Server.Host = "config.example.com"
	cfg.Server.Port = 8443
	cfg.Server.AllowInsecureHTTP = false

	got, err := serverBaseURL()
	if err != nil {
		t.Fatal(err)
	}
	if got != "https://config.example.com:8443" {
		t.Fatalf("unexpected URL: %s", got)
	}
}

func TestServerBaseURLAllowsHTTPOnlyWhenConfigured(t *testing.T) {
	old := cfg
	t.Cleanup(func() { cfg = old })
	cfg.Server.Host = "127.0.0.1"
	cfg.Server.Port = 8080
	cfg.Server.AllowInsecureHTTP = true

	got, err := serverBaseURL()
	if err != nil {
		t.Fatal(err)
	}
	if got != "http://127.0.0.1:8080" {
		t.Fatalf("unexpected URL: %s", got)
	}
}
