package main

// harmonika-safety gateway: proxies /api/tools/safety/* to the heuristic
// content safety service on 127.0.0.1:17903.

import (
	"net/http"
	"net/url"
	"strings"
)

var safetyProxy = newSelfHostedToolsProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_SAFETY_URL", "127.0.0.1:17903"),
}, "safety_unavailable")

func (a *app) safetyToolsAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/safety")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-safety",
			"endpoints": []string{"/api/tools/safety/health", "/api/tools/safety/check"},
		})
		return
	}
	r.URL.Path = target
	safetyProxy.ServeHTTP(w, r)
}
