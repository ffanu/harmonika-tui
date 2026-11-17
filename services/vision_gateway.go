package main

// harmonika-vision gateway: proxies /api/tools/vlm/* to the RTX Ollama
// service through the private tunnel. Public API authentication stays here;
// the worker uses Ollama's /api/tags and /api/generate endpoints.

import (
	"net/http"
	"net/http/httputil"
	"net/url"
	"strings"
)

var visionProxy = newVisionProxy(getenv("HARMONIKA_VISION_URL", "127.0.0.1:18285"))

func newVisionProxy(address string) *httputil.ReverseProxy {
	address = strings.TrimSpace(address)
	if !strings.Contains(address, "://") {
		address = "http://" + address
	}
	target, err := url.Parse(address)
	if err != nil || target.Host == "" || (target.Scheme != "http" && target.Scheme != "https") || target.User != nil || target.RawQuery != "" || target.Fragment != "" || (target.Path != "" && target.Path != "/") {
		return nil
	}
	target.Path = ""
	proxy := httputil.NewSingleHostReverseProxy(target)
	director := proxy.Director
	proxy.Director = func(r *http.Request) {
		director(r)
		// Ollama accepts the private upstream host, not the public gateway host.
		r.Host = target.Host
	}
	// Configure once: per-request assignment races when requests overlap.
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		jsonOut(w, http.StatusBadGateway, map[string]any{"ok": false, "error": "vision_unavailable"})
	}
	return proxy
}

func (a *app) visionToolsAPI(w http.ResponseWriter, r *http.Request) {
	if _, ok := a.requirePublicAIToken(w, r); !ok {
		return
	}
	serveVisionTools(visionProxy, w, r)
}

func serveVisionTools(proxy *httputil.ReverseProxy, w http.ResponseWriter, r *http.Request) {
	target := ""
	matched := false
	for _, prefix := range []string{"/api/tools/vlm", "/api/tools/vision"} {
		if r.URL.Path == prefix || strings.HasPrefix(r.URL.Path, prefix+"/") {
			target = strings.TrimPrefix(r.URL.Path, prefix)
			matched = true
			break
		}
	}
	if !matched {
		jsonOut(w, http.StatusNotFound, map[string]any{"ok": false, "error": "vision_endpoint_not_found"})
		return
	}
	if target == "" || target == "/" {
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			jsonOut(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "method_not_allowed"})
			return
		}
		jsonOut(w, http.StatusOK, map[string]any{
			"ok": true, "service": "harmonika-vision", "model": "qwen3-vl:8b-instruct (RTX)",
			"endpoints": []string{"/api/tools/vlm/tags", "/api/tools/vlm/generate"},
		})
		return
	}
	method := ""
	switch target {
	case "/tags", "/api/tags":
		target, method = "/api/tags", http.MethodGet
	case "/generate", "/api/generate":
		target, method = "/api/generate", http.MethodPost
	default:
		jsonOut(w, http.StatusNotFound, map[string]any{"ok": false, "error": "vision_endpoint_not_found"})
		return
	}
	if r.Method != method {
		w.Header().Set("Allow", method)
		jsonOut(w, http.StatusMethodNotAllowed, map[string]any{"ok": false, "error": "method_not_allowed"})
		return
	}
	if proxy == nil {
		jsonOut(w, http.StatusServiceUnavailable, map[string]any{"ok": false, "error": "vision_not_configured"})
		return
	}
	forward := r.Clone(r.Context())
	forward.URL.Path = target
	forward.URL.RawPath = ""
	proxy.ServeHTTP(w, forward)
}
