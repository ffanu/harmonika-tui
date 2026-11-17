package main

// The translation worker is private and never receives public API credentials.
// Keep this gateway an explicit JSON API, not a general purpose reverse proxy.
import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"
)

const (
	translationRequestLimit  = 256 << 10
	translationResponseLimit = 512 << 10
	translationTimeout       = 135 * time.Second
	translationTokenFile     = "/etc/harmonika-ai/translation-engine.token"
)

type translationWorkerGateway struct {
	origin *url.URL
	secret string
	client *http.Client
}

var translationGateway = newTranslationWorkerGatewayFromFile(
	getenv("HARMONIKA_TRANSLATION_URL", "127.0.0.1:17918"), translationTokenFile,
)

func newTranslationWorkerGatewayFromFile(address, path string) *translationWorkerGateway {
	// Refuse symlinks, permissive permissions and oversized token files. Never
	// report file contents or OS errors through the public API.
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0600 || info.Size() > 4096 {
		return nil
	}
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	opened, err := f.Stat()
	if err != nil || !os.SameFile(info, opened) || opened.Mode().Perm() != 0600 {
		return nil
	}
	secret, err := io.ReadAll(io.LimitReader(f, 4097))
	if err != nil || len(secret) > 4096 {
		return nil
	}
	return newTranslationWorkerGateway(address, strings.TrimSpace(string(secret)))
}

// address and secret are operator configuration, never values from a request.
// Literal loopback addresses avoid DNS rebinding; test servers also use loopback.
func newTranslationWorkerGateway(address, secret string) *translationWorkerGateway {
	address = strings.TrimSpace(address)
	if !strings.Contains(address, "://") {
		address = "http://" + address
	}
	u, err := url.Parse(address)
	if err != nil || u.Scheme != "http" || u.User != nil || u.Opaque != "" || u.RawQuery != "" || u.ForceQuery || u.Fragment != "" || (u.Path != "" && u.Path != "/") {
		return nil
	}
	host, port, err := net.SplitHostPort(u.Host)
	if err != nil || (host != "127.0.0.1" && host != "::1") {
		return nil
	}
	portNumber, err := strconv.Atoi(port)
	if err != nil || portNumber < 1 || portNumber > 65535 || len(secret) < 16 || len(secret) > 4096 {
		return nil
	}
	for _, c := range secret {
		if c < 33 || c > 126 {
			return nil
		}
	}
	u.Path = ""
	return &translationWorkerGateway{
		origin: u, secret: secret,
		client: &http.Client{
			Timeout:       translationTimeout,
			CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse },
			Transport: &http.Transport{
				Proxy:                 nil,
				DialContext:           (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
				ResponseHeaderTimeout: translationTimeout,
				IdleConnTimeout:       30 * time.Second, MaxIdleConnsPerHost: 8,
			},
		},
	}
}

func (a *app) translationToolsAPI(w http.ResponseWriter, r *http.Request) {
	translationResponseHeaders(w)
	actor, ok := a.requirePublicAIToken(w, r)
	if !ok {
		return
	}
	serveTranslationTools(translationGateway, actor.ID, w, r)
}

func translationResponseHeaders(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
}

func translationGatewayError(w http.ResponseWriter, status int, code string) {
	translationResponseHeaders(w)
	jsonOut(w, status, map[string]any{"ok": false, "error": code})
}

func translationEndpoint(path string) (string, string) {
	if path == "/v1/translate" {
		return "/translate", http.MethodPost
	}
	for _, prefix := range []string{"/api/tools/translation", "/v1/translation"} {
		if strings.HasPrefix(path, prefix+"/") {
			switch endpoint := strings.TrimPrefix(path, prefix); endpoint {
			case "/health", "/languages":
				return endpoint, http.MethodGet
			case "/translate", "/detect", "/batch", "/invoke":
				return endpoint, http.MethodPost
			}
		}
	}
	return "", ""
}

func serveTranslationTools(gateway *translationWorkerGateway, actorID int64, w http.ResponseWriter, r *http.Request) {
	translationResponseHeaders(w)
	if actorID <= 0 {
		translationGatewayError(w, http.StatusUnauthorized, "unauthorized")
		return
	}
	endpoint, method := translationEndpoint(r.URL.Path)
	if endpoint == "" || r.URL.RawPath != "" {
		translationGatewayError(w, http.StatusNotFound, "translation_endpoint_not_found")
		return
	}
	if r.Method != method {
		w.Header().Set("Allow", method)
		translationGatewayError(w, http.StatusMethodNotAllowed, "method_not_allowed")
		return
	}
	if r.URL.RawQuery != "" || r.URL.ForceQuery {
		translationGatewayError(w, http.StatusBadRequest, "query_not_allowed")
		return
	}
	if r.ContentLength > translationRequestLimit {
		translationGatewayError(w, http.StatusRequestEntityTooLarge, "translation_request_too_large")
		return
	}
	if r.Header.Get("Content-Encoding") != "" {
		translationGatewayError(w, http.StatusUnsupportedMediaType, "unsupported_content_encoding")
		return
	}
	if contentType := r.Header.Get("Content-Type"); method == http.MethodPost && contentType != "" {
		mediaType, _, err := mime.ParseMediaType(contentType)
		if err != nil || mediaType != "application/json" {
			translationGatewayError(w, http.StatusUnsupportedMediaType, "json_required")
			return
		}
	}
	var body []byte
	if r.Body != nil {
		var err error
		body, err = io.ReadAll(io.LimitReader(r.Body, translationRequestLimit+1))
		if len(body) > translationRequestLimit {
			translationGatewayError(w, http.StatusRequestEntityTooLarge, "translation_request_too_large")
			return
		}
		if err != nil {
			translationGatewayError(w, http.StatusBadRequest, "invalid_request_body")
			return
		}
	}
	if (method == http.MethodPost && (!utf8.Valid(body) || !json.Valid(body))) || (method == http.MethodGet && len(body) != 0) {
		translationGatewayError(w, http.StatusBadRequest, "invalid_request_body")
		return
	}
	if gateway == nil {
		translationGatewayError(w, http.StatusServiceUnavailable, "translation_not_configured")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), translationTimeout)
	defer cancel()
	target := *gateway.origin
	target.Path = endpoint
	forward, err := http.NewRequestWithContext(ctx, method, target.String(), bytes.NewReader(body))
	if err != nil {
		translationGatewayError(w, http.StatusBadGateway, "translation_unavailable")
		return
	}
	forward.Header.Set("Accept", "application/json")
	if method == http.MethodPost {
		forward.Header.Set("Content-Type", "application/json")
	}
	forward.Header.Set("X-Harmonika-Translation-Secret", gateway.secret)
	forward.Header.Set("X-Harmonika-Translation-Actor", "token:"+strconv.FormatInt(actorID, 10))
	response, err := gateway.client.Do(forward)
	if err != nil {
		status, code := http.StatusBadGateway, "translation_unavailable"
		var timeout net.Error
		if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &timeout) && timeout.Timeout()) {
			status, code = http.StatusGatewayTimeout, "translation_timeout"
		}
		translationGatewayError(w, status, code)
		return
	}
	defer response.Body.Close()
	// Internal failures may contain exception details; never relay their body.
	if response.StatusCode >= 500 || response.StatusCode < 200 || response.StatusCode >= 300 && response.StatusCode < 400 {
		status, code := http.StatusBadGateway, "translation_unavailable"
		if response.StatusCode == 503 {
			status = http.StatusServiceUnavailable
		} else if response.StatusCode == 504 {
			status, code = http.StatusGatewayTimeout, "translation_timeout"
		}
		if response.StatusCode >= 500 {
			// Only approved machine codes survive; discard every other field.
			output, readErr := io.ReadAll(io.LimitReader(response.Body, translationResponseLimit+1))
			var failure struct {
				Error string `json:"error"`
			}
			if readErr == nil && len(output) <= translationResponseLimit && utf8.Valid(output) && json.Unmarshal(output, &failure) == nil {
				switch failure.Error {
				case "translation_timeout", "translation_busy", "inference_unavailable", "provider_invalid_response",
					"invalid_inference_response", "invalid_detected_language", "inference_output_limit", "translation_output_limit",
					"empty_translation", "placeholder_mismatch", "url_mismatch", "number_mismatch", "name_mismatch":
					code = failure.Error
				}
			}
		}
		translationGatewayError(w, status, code)
		return
	}
	output, err := io.ReadAll(io.LimitReader(response.Body, translationResponseLimit+1))
	var timeout net.Error
	if errors.Is(err, context.DeadlineExceeded) || (errors.As(err, &timeout) && timeout.Timeout()) {
		translationGatewayError(w, http.StatusGatewayTimeout, "translation_timeout")
		return
	}
	mediaType, _, typeErr := mime.ParseMediaType(response.Header.Get("Content-Type"))
	var object map[string]json.RawMessage
	if err != nil || len(output) > translationResponseLimit || !utf8.Valid(output) || typeErr != nil || mediaType != "application/json" || json.Unmarshal(output, &object) != nil || object == nil {
		translationGatewayError(w, http.StatusBadGateway, "translation_invalid_response")
		return
	}
	if response.StatusCode >= 400 {
		var code string
		if json.Unmarshal(object["error"], &code) != nil || code == "" {
			translationGatewayError(w, http.StatusBadGateway, "translation_invalid_response")
			return
		}
	}
	w.WriteHeader(response.StatusCode)
	_, _ = w.Write(output)
}
