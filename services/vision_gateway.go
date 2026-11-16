package main

// harmonika-vision gateway: proxies /api/tools/vision/* to the RTX
// ollama service (Qwen3-VL-8B) via the SSH tunnel on 127.0.0.1:18285.
// Agents authenticate with their public AI token; ollama itself is
// loopback-bound behind the tunnel (no additional token).

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

var visionProxy = httputil.NewSingleHostReverseProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_VISION_URL", "127.0.0.1:18285"),
})

func (a *app) visionToolsAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/vision")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-vision",
			"model":     "qwen3-vl:8b-instruct (RTX)",
			"endpoints": []string{"/api/tools/vision/tags", "/api/tools/vision/generate"},
		})
		return
	}
	r.URL.Path = target
	visionProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": fmt.Sprintf("vision unavailable: %v", err)})
	}
	visionProxy.ServeHTTP(w, r)
}
