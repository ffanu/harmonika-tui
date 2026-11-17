package main

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
)

func TestVisionGatewayCanonicalAndCompatibilityPaths(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_, _ = w.Write([]byte(r.Method + " " + r.URL.RequestURI() + " " + string(body)))
	}))
	defer upstream.Close()
	proxy := newVisionProxy(upstream.URL)
	for _, prefix := range []string{"/api/tools/vlm", "/api/tools/vision"} {
		for _, endpoint := range []string{"tags", "generate", "api/tags", "api/generate"} {
			t.Run(prefix+"/"+endpoint, func(t *testing.T) {
				method, want := http.MethodGet, "/api/tags"
				if strings.HasSuffix(endpoint, "generate") {
					method, want = http.MethodPost, "/api/generate"
				}
				original := prefix + "/" + endpoint + "?probe=1"
				req := httptest.NewRequest(method, original, strings.NewReader(`{"model":"synthetic"}`))
				response := httptest.NewRecorder()
				serveVisionTools(proxy, response, req)
				if response.Code != 200 || response.Body.String() != method+" "+want+`?probe=1 {"model":"synthetic"}` {
					t.Fatalf("wrong upstream request: %d %s", response.Code, response.Body.String())
				}
				if req.URL.RequestURI() != original {
					t.Fatal("caller URL was mutated")
				}
			})
		}
	}
}

func TestVisionGatewayRejectsWrongMethodsAndUnknownEndpoints(t *testing.T) {
	var calls atomic.Int32
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer upstream.Close()
	proxy := newVisionProxy(upstream.URL)
	for _, tc := range []struct {
		method, path string
		status       int
		allow        string
	}{
		{http.MethodGet, "/api/tools/vlm/generate", 405, http.MethodPost},
		{http.MethodPost, "/api/tools/vlm/tags", 405, http.MethodGet},
		{http.MethodPost, "/api/tools/vlm/", 405, http.MethodGet},
		{http.MethodGet, "/api/tools/vlm/pull", 404, ""},
		{http.MethodGet, "/api/tools/vlm-other/tags", 404, ""},
	} {
		r := httptest.NewRecorder()
		serveVisionTools(proxy, r, httptest.NewRequest(tc.method, tc.path, nil))
		if r.Code != tc.status || r.Header().Get("Allow") != tc.allow {
			t.Fatalf("%s %s: %d", tc.method, tc.path, r.Code)
		}
	}
	if calls.Load() != 0 {
		t.Fatal("invalid request reached upstream")
	}
}

func TestVisionGatewayRewritesUpstreamHost(t *testing.T) {
	upstream := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasPrefix(r.Host, "127.0.0.1:") {
			w.WriteHeader(http.StatusForbidden)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer upstream.Close()
	r := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "http://chat.harmonika.id/api/tools/vlm/tags", nil)
	serveVisionTools(newVisionProxy(upstream.URL), r, request)
	if r.Code != http.StatusOK {
		t.Fatalf("public Host reached the private worker: %d", r.Code)
	}
	if request.Host != "chat.harmonika.id" {
		t.Fatal("original request Host was mutated")
	}
}

func TestVisionGatewayTargetConfiguration(t *testing.T) {
	for _, target := range []string{"127.0.0.1:18285", "http://127.0.0.1:18285", "https://example.invalid/", "[::1]:18285"} {
		if newVisionProxy(target) == nil {
			t.Fatalf("valid origin rejected: %s", target)
		}
	}
	for _, target := range []string{"", "http://", "ftp://example.invalid", "http://user:password@example.invalid", "http://example.invalid/api", "http://example.invalid/?key=private"} {
		if newVisionProxy(target) != nil {
			t.Fatalf("invalid origin accepted: %s", target)
		}
	}
	r := httptest.NewRecorder()
	serveVisionTools(nil, r, httptest.NewRequest(http.MethodGet, "/api/tools/vlm/tags", nil))
	if r.Code != 503 || !strings.Contains(r.Body.String(), "vision_not_configured") {
		t.Fatal("invalid target did not fail closed")
	}
}

type visionFailedTransport struct{}

func (visionFailedTransport) RoundTrip(*http.Request) (*http.Response, error) {
	return nil, errors.New("SYNTHETIC_PRIVATE_UPSTREAM_ERROR")
}

func TestVisionGatewayConcurrentFailuresAreIsolatedAndSanitized(t *testing.T) {
	proxy := newVisionProxy("127.0.0.1:18285")
	proxy.Transport = visionFailedTransport{}
	var wg sync.WaitGroup
	for i := 0; i < 24; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			r := httptest.NewRecorder()
			serveVisionTools(proxy, r, httptest.NewRequest(http.MethodGet, "/api/tools/vlm/tags", nil))
			if r.Code != 502 || strings.Contains(r.Body.String(), "SYNTHETIC_PRIVATE") || !strings.Contains(r.Body.String(), "vision_unavailable") {
				t.Error("unsafe gateway error")
			}
		}()
	}
	wg.Wait()
}

func TestVisionGatewayUnauthenticatedRequestNeverProxies(t *testing.T) {
	r := httptest.NewRecorder()
	a := &app{}
	a.visionToolsAPI(r, httptest.NewRequest(http.MethodGet, "/api/tools/vlm/tags", nil))
	if r.Code != http.StatusUnauthorized {
		t.Fatalf("authentication boundary changed: %d", r.Code)
	}
}
