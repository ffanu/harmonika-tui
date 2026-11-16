package main

// harmonika-asr gateway: proxies /api/tools/asr/* to the RTX whisper
// service via the SSH tunnel on 127.0.0.1:18282, injecting the internal
// worker token (agents authenticate with their public AI token).

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
)

const asrTokenPath = "/etc/harmonika-ai/asr.token"

var asrProxy = httputil.NewSingleHostReverseProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_ASR_URL", "127.0.0.1:18282"),
})

func asrToken() string {
	b, err := os.ReadFile(asrTokenPath)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func (a *app) asrToolsAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	token := asrToken()
	if token == "" {
		jsonOut(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "asr_not_configured"})
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/asr")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-asr",
			"model":     "faster-whisper-large-v3 (RTX)",
			"endpoints": []string{"/api/tools/asr/health", "/api/tools/asr/transcribe"},
		})
		return
	}
	r.URL.Path = target
	r.Header.Set("Authorization", "Bearer "+token)
	asrProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": fmt.Sprintf("asr unavailable: %v", err)})
	}
	asrProxy.ServeHTTP(w, r)
}
