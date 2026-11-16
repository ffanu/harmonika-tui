package main

// harmonika-videogen gateway: proxies /api/tools/video/* to the RTX
// CogVideoX service via the SSH tunnel on 127.0.0.1:18272, injecting the
// internal worker token (agents authenticate with their public AI token).

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
)

const videoGenTokenPath = "/etc/harmonika-ai/video-gen.token"

var videoGenProxy = httputil.NewSingleHostReverseProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_VIDEOGEN_URL", "127.0.0.1:18272"),
})

func videoGenToken() string {
	b, err := os.ReadFile(videoGenTokenPath)
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

func (a *app) videoGenAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	token := videoGenToken()
	if token == "" {
		jsonOut(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "videogen_not_configured"})
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/video")
	// Gateway exposes /file/<id>; the RTX service serves bytes at /video/<id>.
	if strings.HasPrefix(target, "/file/") {
		target = "/video/" + strings.TrimPrefix(target, "/file/")
	}
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-videogen",
			"model":     "CogVideoX-2b (RTX)",
			"endpoints": []string{"/api/tools/video/generate", "/api/tools/video/status/<id>", "/api/tools/video/file/<id>"},
		})
		return
	}
	r.URL.Path = target
	r.Header.Set("Authorization", "Bearer "+token)
	videoGenProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": fmt.Sprintf("videogen unavailable: %v", err)})
	}
	videoGenProxy.ServeHTTP(w, r)
}
