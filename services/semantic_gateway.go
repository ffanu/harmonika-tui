package main

// harmonika-semantic gateway: proxies /api/tools/embedding/* to the
// self-hosted fastembed semantic service on 127.0.0.1:17897.

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

var semanticEmbeddingProxy = httputil.NewSingleHostReverseProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_SEMANTIC_URL", "127.0.0.1:17897"),
})

func (a *app) semanticEmbeddingAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/embedding")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-semantic-embedding",
			"endpoints": []string{"/api/tools/embedding/health", "/api/tools/embedding/embed", "/api/tools/embedding/similarity"},
		})
		return
	}
	r.URL.Path = target
	semanticEmbeddingProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": fmt.Sprintf("semantic embedding unavailable: %v", err)})
	}
	semanticEmbeddingProxy.ServeHTTP(w, r)
}
