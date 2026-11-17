package main

// harmonika-imagegen gateway: proxies /api/tools/imagegen/* to the RTX
// sd-turbo service via the SSH tunnel on 127.0.0.1:18284, injecting the
// internal worker token (agents authenticate with their public AI token).

import (
	"net/http"
	"net/url"
	"os"
	"strings"
)

const imageGenTokenPath = "/etc/harmonika-ai/imagegen.token"

var imageGenProxy = newSelfHostedToolsProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_IMAGEGEN_URL", "127.0.0.1:18284"),
}, "imagegen_unavailable")

func imageGenToken() string {
	b, err := os.ReadFile(imageGenTokenPath)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func (a *app) imageGenAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	token := imageGenToken()
	if token == "" {
		jsonOut(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "imagegen_not_configured"})
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/imagegen")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-imagegen",
			"model":     "sd-turbo (RTX)",
			"endpoints": []string{"/api/tools/imagegen/health", "/api/tools/imagegen/generate"},
		})
		return
	}
	r.URL.Path = target
	r.Header.Set("Authorization", "Bearer "+token)
	imageGenProxy.ServeHTTP(w, r)
}
