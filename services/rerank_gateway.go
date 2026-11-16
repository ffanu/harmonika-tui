package main

// harmonika-rerank gateway: proxies /api/tools/rerank/* to the
// self-hosted CrossEncoder rerank service on 127.0.0.1:17899.

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

var rerankProxy = httputil.NewSingleHostReverseProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_RERANK_URL", "127.0.0.1:17899"),
})

func (a *app) rerankToolsAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/rerank")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-rerank",
			"endpoints": []string{"/api/tools/rerank/health", "/api/tools/rerank/rerank"},
		})
		return
	}
	r.URL.Path = target
	rerankProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": fmt.Sprintf("rerank unavailable: %v", err)})
	}
	rerankProxy.ServeHTTP(w, r)
}
