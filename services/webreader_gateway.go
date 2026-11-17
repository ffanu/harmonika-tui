package main

// harmonika-webreader gateway: proxies /api/tools/webreader/* to the
// self-hosted web reader service on 127.0.0.1:17896.
// Auth: Bearer public AI token (same as /v1/chat/completions agent path).

import (
	"net/http"
	"net/url"
	"strings"
)

var webReaderProxy = newSelfHostedToolsProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_WEBREADER_URL", "127.0.0.1:17896"),
}, "webreader_unavailable")

func (a *app) webReaderAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/webreader")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-webreader",
			"endpoints": []string{"/api/tools/webreader/health", "/api/tools/webreader/read?url=<target>"},
		})
		return
	}
	r.URL.Path = target
	webReaderProxy.ServeHTTP(w, r)
}
