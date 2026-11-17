package main

// harmonika-semantic gateway: proxies /api/tools/embedding/* to the
// self-hosted fastembed semantic service on 127.0.0.1:17897.

import (
	"net/http"
	"net/url"
	"strings"
)

var semanticEmbeddingProxy = newSelfHostedToolsProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_SEMANTIC_URL", "127.0.0.1:17897"),
}, "semantic_embedding_unavailable")

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
	semanticEmbeddingProxy.ServeHTTP(w, r)
}
