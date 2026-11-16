package main

// harmonika-zread gateway: proxies /api/tools/zread/* to the repo
// reader service on 127.0.0.1:17901.

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

var zreadProxy = httputil.NewSingleHostReverseProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_ZREAD_URL", "127.0.0.1:17901"),
})

func (a *app) zreadToolsAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/zread")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-zread",
			"endpoints": []string{"/api/tools/zread/health", "/api/tools/zread/read"},
		})
		return
	}
	r.URL.Path = target
	zreadProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": fmt.Sprintf("zread unavailable: %v", err)})
	}
	zreadProxy.ServeHTTP(w, r)
}
