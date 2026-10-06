package cmd

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestDoGetRequestAuthorization(t *testing.T) {
	expectedToken := "test-device-token-1234"
	cfg.DeviceToken = expectedToken

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		if authHeader != "Bearer "+expectedToken {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	client := &http.Client{Timeout: 5 * time.Second}
	ctx := context.Background()

	resp, err := doGetRequest(ctx, client, server.URL)
	if err != nil {
		t.Fatalf("doGetRequest 실패: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		t.Errorf("예상 상태 코드 200 OK, 실제: %d", resp.StatusCode)
	}
}

func TestDoGetRequestUnauthorized(t *testing.T) {
	cfg.DeviceToken = ""

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		authHeader := r.Header.Get("Authorization")
		if authHeader == "" {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	client := &http.Client{Timeout: 5 * time.Second}
	ctx := context.Background()

	resp, err := doGetRequest(ctx, client, server.URL)
	if err != nil {
		t.Fatalf("doGetRequest 실패: %v", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("예상 상태 코드 401 Unauthorized, 실제: %d", resp.StatusCode)
	}
}
