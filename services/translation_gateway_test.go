package main

import (
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

const translationTestSecret = "synthetic-worker-secret-123456789"

func translationTestWorker(t *testing.T, handler http.HandlerFunc) *translationWorkerGateway {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	gateway := newTranslationWorkerGateway(server.URL, translationTestSecret)
	if gateway == nil {
		t.Fatal("loopback test worker rejected")
	}
	t.Cleanup(gateway.client.CloseIdleConnections)
	return gateway
}

func TestTranslationGatewayAllowedRoutesAndTrustedHeaders(t *testing.T) {
	var calls atomic.Int32
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
		calls.Add(1)
		if r.Header.Get("X-Harmonika-Translation-Actor") != "token:42" || r.Header.Get("X-Harmonika-Translation-Secret") != translationTestSecret {
			t.Error("verified actor or worker secret missing")
		}
		for _, name := range []string{"Authorization", "Cookie", "X-Forwarded-For", "X-Caller-Private", "Idempotency-Key"} {
			if r.Header.Get(name) != "" {
				t.Errorf("caller header forwarded: %s", name)
			}
		}
		if !strings.HasPrefix(r.Host, "127.0.0.1:") || r.Header.Get("Accept") != "application/json" {
			t.Error("worker host or response type changed")
		}
		if r.Method == http.MethodPost {
			body, _ := io.ReadAll(r.Body)
			if string(body) != `{"text":"Halo 世界 👋"}` || r.Header.Get("Content-Type") != "application/json" {
				t.Error("Unicode payload changed")
			}
		}
		w.Header().Set("Content-Type", "application/json")
		w.Header().Set("Set-Cookie", "internal=private")
		w.Header().Set("X-Worker-Private", "private")
		_, _ = io.WriteString(w, `{"ok":true,"result":"世界"}`)
	})
	for _, prefix := range []string{"/api/tools/translation", "/v1/translation"} {
		for _, endpoint := range []string{"health", "languages", "translate", "detect", "batch", "invoke"} {
			t.Run(prefix+"/"+endpoint, func(t *testing.T) {
				method, body := http.MethodPost, `{"text":"Halo 世界 👋"}`
				if endpoint == "health" || endpoint == "languages" {
					method, body = http.MethodGet, ""
				}
				r := httptest.NewRequest(method, prefix+"/"+endpoint, strings.NewReader(body))
				r.Header.Set("Authorization", "Bearer synthetic-public-token")
				r.Header.Set("Cookie", "session=synthetic")
				r.Header.Set("X-Forwarded-For", "192.0.2.1")
				r.Header.Set("X-Caller-Private", "private")
				r.Header.Set("Idempotency-Key", "synthetic")
				r.Header.Set("X-Harmonika-Translation-Actor", "token:999")
				r.Header.Set("X-Harmonika-Translation-Secret", "caller-secret")
				w := httptest.NewRecorder()
				serveTranslationTools(gateway, 42, w, r)
				if w.Code != 200 || w.Body.String() != `{"ok":true,"result":"世界"}` {
					t.Fatalf("response changed: %d %s", w.Code, w.Body.String())
				}
				if w.Header().Get("Cache-Control") != "no-store" || !strings.HasPrefix(w.Header().Get("Content-Type"), "application/json") || w.Header().Get("Set-Cookie") != "" || w.Header().Get("X-Worker-Private") != "" {
					t.Fatal("response headers are unsafe")
				}
				if r.Header.Get("X-Harmonika-Translation-Actor") != "token:999" {
					t.Fatal("caller request was mutated")
				}
			})
		}
	}
	w := httptest.NewRecorder()
	serveTranslationTools(gateway, 42, w, httptest.NewRequest(http.MethodPost, "/v1/translate", strings.NewReader(`{"text":"Halo 世界 👋"}`)))
	if w.Code != 200 || calls.Load() != 13 {
		t.Fatalf("alias failed or route duplicated: status=%d calls=%d", w.Code, calls.Load())
	}
}

func TestTranslationGatewayRejectsInvalidRequestsBeforeWorker(t *testing.T) {
	var calls atomic.Int32
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1) })
	for _, tc := range []struct {
		name, method, path, body string
		status                   int
	}{
		{"unknown", "POST", "/api/tools/translation/admin", `{}`, 404},
		{"prefix", "POST", "/api/tools/translation-other/translate", `{}`, 404},
		{"traversal", "POST", "/api/tools/translation/../translate", `{}`, 404},
		{"encoded", "POST", "/api/tools/translation/%74ranslate", `{}`, 404},
		{"get-translate", "GET", "/v1/translate", "", 405},
		{"post-health", "POST", "/v1/translation/health", `{}`, 405},
		{"query", "POST", "/v1/translate?url=http://192.0.2.1", `{}`, 400},
		{"empty-query", "POST", "/v1/translate?", `{}`, 400},
		{"invalid-json", "POST", "/v1/translate", `{"text":`, 400},
		{"invalid-utf8", "POST", "/v1/translate", "{\"text\":\"\xff\"}", 400},
		{"empty-body", "POST", "/v1/translate", "", 400},
		{"get-body", "GET", "/v1/translation/health", `{}`, 400},
		{"oversized", "POST", "/v1/translate", strings.Repeat("x", translationRequestLimit+1), 413},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := httptest.NewRequest(tc.method, tc.path, strings.NewReader(tc.body))
			// Exercise streaming limit without relying on Content-Length.
			if tc.name == "oversized" {
				r.ContentLength = -1
			}
			w := httptest.NewRecorder()
			serveTranslationTools(gateway, 42, w, r)
			if w.Code != tc.status {
				t.Fatalf("status=%d want=%d", w.Code, tc.status)
			}
			if tc.status == 405 && w.Header().Get("Allow") == "" {
				t.Fatal("missing Allow")
			}
		})
	}
	for _, header := range []string{"Content-Type", "Content-Encoding"} {
		r := httptest.NewRequest("POST", "/v1/translate", strings.NewReader(`{}`))
		r.Header.Set(header, "application/octet-stream")
		w := httptest.NewRecorder()
		serveTranslationTools(gateway, 42, w, r)
		if w.Code != 415 {
			t.Fatalf("unsafe content accepted: %s", header)
		}
	}
	if calls.Load() != 0 {
		t.Fatal("invalid request reached worker")
	}
}

func TestTranslationGatewayConfigurationAndPrivateToken(t *testing.T) {
	for _, address := range []string{"127.0.0.1:17918", "http://127.0.0.1:17918/", "http://[::1]:17918"} {
		if newTranslationWorkerGateway(address, translationTestSecret) == nil {
			t.Fatalf("loopback rejected: %s", address)
		}
	}
	for _, address := range []string{"", "http://localhost:17918", "https://127.0.0.1:17918", "http://192.0.2.1:17918", "http://127.0.0.1.evil:17918", "http://user:password@127.0.0.1:17918", "http://127.0.0.1:17918/api", "http://127.0.0.1:17918?", "http://127.0.0.1:17918?secret=private", "http://127.0.0.1:0", "http://127.0.0.1:65536"} {
		if newTranslationWorkerGateway(address, translationTestSecret) != nil {
			t.Fatalf("unsafe target accepted: %s", address)
		}
	}
	for _, secret := range []string{"", "short", translationTestSecret + "\nheader: injected"} {
		if newTranslationWorkerGateway("127.0.0.1:17918", secret) != nil {
			t.Fatal("invalid secret accepted")
		}
	}
	path := filepath.Join(t.TempDir(), "worker.token")
	if err := os.WriteFile(path, []byte(translationTestSecret+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if newTranslationWorkerGatewayFromFile("127.0.0.1:17918", path) == nil {
		t.Fatal("private token rejected")
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if newTranslationWorkerGatewayFromFile("127.0.0.1:17918", path) != nil {
		t.Fatal("public token file accepted")
	}
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	link := filepath.Join(t.TempDir(), "worker.link")
	if err := os.Symlink(path, link); err != nil {
		t.Fatal(err)
	}
	if newTranslationWorkerGatewayFromFile("127.0.0.1:17918", link) != nil {
		t.Fatal("symlink token accepted")
	}
	w := httptest.NewRecorder()
	serveTranslationTools(nil, 42, w, httptest.NewRequest("GET", "/v1/translation/health", nil))
	if w.Code != 503 {
		t.Fatal("missing configuration did not fail closed")
	}
}

func TestTranslationGatewayWorkerResponses(t *testing.T) {
	for _, tc := range []struct {
		name              string
		status            int
		contentType, body string
		want              int
		contains          string
	}{
		{"structured-4xx", 422, "application/json", `{"ok":false,"error":"unsupported_language","field":"target"}`, 422, "unsupported_language"},
		{"raw-4xx", 400, "text/plain", "SYNTHETIC_PRIVATE_DETAILS", 502, "translation_invalid_response"},
		{"missing-error", 400, "application/json", `{"private":"SYNTHETIC_PRIVATE_DETAILS"}`, 502, "translation_invalid_response"},
		{"bad-json", 200, "application/json", `{"private":"SYNTHETIC_PRIVATE_DETAILS"`, 502, "translation_invalid_response"},
		{"non-object", 200, "application/json", `[]`, 502, "translation_invalid_response"},
		{"html", 200, "text/html", "SYNTHETIC_PRIVATE_DETAILS", 502, "translation_invalid_response"},
		{"oversized", 200, "application/json", `{"text":"` + strings.Repeat("x", translationResponseLimit) + `"}`, 502, "translation_invalid_response"},
		{"raw-500", 500, "application/json", `{"error":"SYNTHETIC_PRIVATE_DETAILS"}`, 502, "translation_unavailable"},
		{"safe-503", 503, "application/json", `{"error":"translation_busy","detail":"SYNTHETIC_PRIVATE_DETAILS"}`, 503, "translation_busy"},
		{"timeout", 504, "text/plain", "SYNTHETIC_PRIVATE_DETAILS", 504, "translation_timeout"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
				w.Header().Set("Content-Type", tc.contentType)
				w.WriteHeader(tc.status)
				_, _ = io.WriteString(w, tc.body)
			})
			w := httptest.NewRecorder()
			serveTranslationTools(gateway, 42, w, httptest.NewRequest("GET", "/v1/translation/health", nil))
			if w.Code != tc.want || !strings.Contains(w.Body.String(), tc.contains) || strings.Contains(w.Body.String(), "SYNTHETIC_PRIVATE") {
				t.Fatalf("unexpected/unsafe response: %d %s", w.Code, w.Body.String())
			}
			if tc.name == "structured-4xx" && w.Body.String() != tc.body {
				t.Fatal("structured validation error changed")
			}
		})
	}
}

func TestTranslationGatewayDoesNotFollowRedirects(t *testing.T) {
	var calls atomic.Int32
	sink := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { calls.Add(1) }))
	defer sink.Close()
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, sink.URL, http.StatusTemporaryRedirect)
	})
	w := httptest.NewRecorder()
	serveTranslationTools(gateway, 42, w, httptest.NewRequest("POST", "/v1/translate", strings.NewReader(`{}`)))
	if w.Code != 502 || calls.Load() != 0 || strings.Contains(w.Body.String(), sink.URL) {
		t.Fatal("redirect followed or target leaked")
	}
}

type translationFailedTransport struct{}

func (translationFailedTransport) RoundTrip(*http.Request) (*http.Response, error) {
	return nil, errors.New("SYNTHETIC_PRIVATE_TRANSPORT_DETAILS")
}

func TestTranslationGatewayConcurrentErrorsAreRedacted(t *testing.T) {
	gateway := newTranslationWorkerGateway("127.0.0.1:17918", translationTestSecret)
	gateway.client.Transport = translationFailedTransport{}
	var group sync.WaitGroup
	for i := 0; i < 12; i++ {
		group.Add(1)
		go func() {
			defer group.Done()
			w := httptest.NewRecorder()
			serveTranslationTools(gateway, 42, w, httptest.NewRequest("GET", "/v1/translation/health", nil))
			if w.Code != 502 || strings.Contains(w.Body.String(), "SYNTHETIC_PRIVATE") {
				t.Error("transport error leaked")
			}
		}()
	}
	group.Wait()
}

func TestTranslationGatewayTimeout(t *testing.T) {
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(time.Second):
		}
	})
	gateway.client.Timeout = 10 * time.Millisecond
	w := httptest.NewRecorder()
	serveTranslationTools(gateway, 42, w, httptest.NewRequest("GET", "/v1/translation/health", nil))
	if w.Code != 504 || !strings.Contains(w.Body.String(), "translation_timeout") {
		t.Fatalf("timeout contract changed: %d", w.Code)
	}
}

func TestTranslationGatewayTimeoutWhileReadingResponse(t *testing.T) {
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"ok":`)
		w.(http.Flusher).Flush()
		select {
		case <-r.Context().Done():
		case <-time.After(time.Second):
		}
	})
	gateway.client.Timeout = 10 * time.Millisecond
	w := httptest.NewRecorder()
	serveTranslationTools(gateway, 42, w, httptest.NewRequest("GET", "/v1/translation/health", nil))
	if w.Code != 504 || !strings.Contains(w.Body.String(), "translation_timeout") {
		t.Fatalf("body deadline contract changed: %d", w.Code)
	}
}

func TestTranslationGatewayExactRoutesAndBodyBoundary(t *testing.T) {
	for _, prefix := range []string{"/api/tools/translation", "/v1/translation"} {
		for _, endpoint := range []string{"/health", "/languages", "/translate", "/detect", "/batch", "/invoke"} {
			got, method := translationEndpoint(prefix + endpoint)
			if got != endpoint || method == "" {
				t.Fatalf("wrong route mapping for %s", prefix+endpoint)
			}
		}
	}
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/translate" || r.URL.RawQuery != "" {
			t.Error("wrong worker endpoint")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = io.WriteString(w, `{"ok":true}`)
	})
	body := `{"text":"` + strings.Repeat("x", translationRequestLimit-len(`{"text":""}`)) + `"}`
	for _, extra := range []string{"", " "} {
		w := httptest.NewRecorder()
		serveTranslationTools(gateway, 42, w, httptest.NewRequest("POST", "/v1/translate", strings.NewReader(body+extra)))
		want := 200
		if extra != "" {
			want = 413
		}
		if w.Code != want {
			t.Fatalf("byte boundary status=%d want=%d", w.Code, want)
		}
	}
}

func TestTranslationGatewayUnauthorizedBeforeUpstream(t *testing.T) {
	var calls atomic.Int32
	gateway := translationTestWorker(t, func(w http.ResponseWriter, r *http.Request) { calls.Add(1) })
	previous := translationGateway
	translationGateway = gateway
	defer func() { translationGateway = previous }()
	w := httptest.NewRecorder()
	(&app{}).translationToolsAPI(w, httptest.NewRequest("GET", "/v1/translation/health", nil))
	if w.Code != 401 || calls.Load() != 0 {
		t.Fatal("unauthenticated request reached worker")
	}
	w = httptest.NewRecorder()
	serveTranslationTools(gateway, 0, w, httptest.NewRequest("GET", "/v1/translation/health", nil))
	if w.Code != 401 || calls.Load() != 0 {
		t.Fatal("invalid token identity reached worker")
	}
}
