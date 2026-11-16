package main

// harmonika-fileparser gateway: proxies /api/tools/fileparser/* to the
// self-hosted document parser on 127.0.0.1:17900.

import (
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

var fileParserProxy = httputil.NewSingleHostReverseProxy(&url.URL{
	Scheme: "http",
	Host:   getenv("HARMONIKA_FILEPARSER_URL", "127.0.0.1:17900"),
})

func (a *app) fileParserAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	target := strings.TrimPrefix(r.URL.Path, "/api/tools/fileparser")
	if target == "" || target == "/" {
		jsonOut(w, http.StatusOK, map[string]any{
			"ok":        true,
			"service":   "harmonika-fileparser",
			"endpoints": []string{"/api/tools/fileparser/health", "/api/tools/fileparser/parse"},
		})
		return
	}
	r.URL.Path = target
	fileParserProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": fmt.Sprintf("fileparser unavailable: %v", err)})
	}
	fileParserProxy.ServeHTTP(w, r)
}
