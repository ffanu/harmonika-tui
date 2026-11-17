package main

// harmonika-knowledge gateway: proxies /api/tools/knowledge/* to the
// self-hosted knowledge base service on 127.0.0.1:17898.

import (
	"fmt"
	"net/http"
	"net/url"
	"strings"
)

var knowledgeProxy = newSelfHostedToolsProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_KNOWLEDGE_URL", "127.0.0.1:17898"),
}, "knowledge_unavailable")

func (a *app) knowledgeToolsAPI(w http.ResponseWriter, r *http.Request) {
	owner, ok := a.requirePublicAIToken(w, r)
	if !ok {
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
	forward := bindKnowledgeOwner(r, owner.ID)
	forward.URL.Path = target
	forward.URL.RawPath = ""
	knowledgeProxy.ServeHTTP(w, forward)
}

// Only the gateway's authenticated token identity selects the private namespace.
func bindKnowledgeOwner(r *http.Request, id int64) *http.Request {
	forward := r.Clone(r.Context())
	forward.Header.Set("X-Harmonika-KB-Owner", "token:"+fmt.Sprint(id))
	return forward
}
