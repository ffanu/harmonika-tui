package main

import (
	"net/http"
	"net/http/httputil"
	"net/url"
)

// Shared proxies are immutable after construction. In particular, setting an
// ErrorHandler inside an HTTP handler races with other in-flight requests.
func newSelfHostedToolsProxy(target *url.URL, errorCode string) *httputil.ReverseProxy {
	proxy := httputil.NewSingleHostReverseProxy(target)
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": errorCode})
	}
	return proxy
}
