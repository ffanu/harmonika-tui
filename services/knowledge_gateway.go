package main

// harmonika-knowledge gateway: proxies /api/tools/knowledge/* to the
// self-hosted knowledge base service on 127.0.0.1:17898.

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

var knowledgeProxy = httputil.NewSingleHostReverseProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_KNOWLEDGE_URL", "127.0.0.1:17898"),
})

func (a *app) knowledgeToolsAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/knowledge")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-knowledge",
			"endpoints": []string{"/api/tools/knowledge/health", "/api/tools/knowledge/store", "/api/tools/knowledge/search", "/api/tools/knowledge/list"},
		})
		return
	}
	r.URL.Path = target
	knowledgeProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": fmt.Sprintf("knowledge base unavailable: %v", err)})
	}
	knowledgeProxy.ServeHTTP(w, r)
}
