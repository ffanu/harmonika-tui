package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"regexp"
	"strings"
	"time"
)

var mediaJobPath = regexp.MustCompile(`^/v1/media/jobs/(job_[A-Za-z0-9_-]{1,128})(/(cancel|recover))?$`)
var mediaFilePath = regexp.MustCompile(`^/v1/media/files/file_[A-Za-z0-9_-]{1,128}$`)

func mediaTarget(method, path string) (string, bool) {
	if method == "GET" && path == "/v1/media/capabilities" {
		return "/health", true
	}
	if method == "POST" && (path == "/v1/media/jobs" || path == "/v1/media/files") {
		return strings.Replace(path, "/v1/media", "/v1/rtx", 1), true
	}
	if m := mediaJobPath.FindStringSubmatch(path); m != nil && ((method == "GET" && m[2] == "") || (method == "POST" && (m[3] == "cancel" || m[3] == "recover"))) {
		return strings.Replace(path, "/v1/media", "/v1/rtx", 1), true
	}
	if method == "GET" && mediaFilePath.MatchString(path) {
		return strings.Replace(path, "/v1/media", "/v1/rtx", 1), true
	}
	return "", false
}
func mediaBillable(method, path string) bool {
	return method == "POST" && (path == "/v1/media/jobs" || path == "/v1/media/files")
}
func mediaSafeJSON(v any) any {
	switch x := v.(type) {
	case map[string]any:
		for k, item := range x {
			if k == "code" {
				if s, ok := item.(string); ok {
					x[k] = strings.ReplaceAll(s, "rtx_", "image_worker_")
					continue
				}
			}
			x[k] = mediaSafeJSON(item)
		}
	case []any:
		for i, item := range x {
			x[i] = mediaSafeJSON(item)
		}
	}
	return v
}
func (a *app) publicMediaAPI(w http.ResponseWriter, r *http.Request) {
	target, valid := mediaTarget(r.Method, r.URL.Path)
	if !valid || r.URL.RawQuery != "" {
		jsonOut(w, 404, map[string]any{"error": map[string]any{"code": "not_found"}})
		return
	}
	t, ok := a.telcoToken(w, r)
	if !ok {
		return
	}
	scope := false
	for _, s := range strings.Split(t.Scopes, ",") {
		if s == "chat" || s == "skills" || s == "media" {
			scope = true
		}
	}
	if !scope {
		jsonOut(w, 403, map[string]any{"error": map[string]any{"code": "media_scope_required"}})
		return
	}
	secret, e := os.ReadFile("/etc/harmonika-ai/rtx-public-bridge.token")
	if e != nil || len(bytes.TrimSpace(secret)) < 32 {
		jsonOut(w, 503, map[string]any{"error": map[string]any{"code": "media_bridge_unavailable"}})
		return
	}
	limit := int64(65536)
	if r.URL.Path == "/v1/media/files" {
		limit = 10*1024*1024 + 65536
	}
	if r.Method == "POST" && r.URL.Path == "/v1/media/jobs" {
		if !regexp.MustCompile(`^[A-Za-z0-9_-]{8,128}$`).MatchString(r.Header.Get("Idempotency-Key")) {
			jsonOut(w, 400, map[string]any{"error": map[string]any{"code": "idempotency_required"}})
			return
		}
	}
	raw, e := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
	if e != nil {
		jsonOut(w, 413, map[string]any{"error": map[string]any{"code": "input_too_large"}})
		return
	}
	if mediaBillable(r.Method, r.URL.Path) {
		if status, code := a.reservePublicAIQuota(r.Context(), &t, r); status != 0 {
			jsonOut(w, status, map[string]any{"error": map[string]any{"code": code}})
			return
		}
	}
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()
	req, e := http.NewRequestWithContext(ctx, r.Method, "http://127.0.0.1:18186"+target, bytes.NewReader(raw))
	if e != nil {
		jsonOut(w, 503, map[string]any{"error": map[string]any{"code": "media_unavailable"}})
		return
	}
	req.Header.Set("Authorization", "Bearer "+string(bytes.TrimSpace(secret)))
	req.Header.Set("X-Harmonika-Media-Principal", fmt.Sprintf("api_%d", t.ID))
	req.Header.Set("Content-Type", r.Header.Get("Content-Type"))
	req.Header.Set("Idempotency-Key", r.Header.Get("Idempotency-Key"))
	res, e := (&http.Client{CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}).Do(req)
	if e != nil {
		jsonOut(w, 503, map[string]any{"error": map[string]any{"code": "media_unavailable"}})
		return
	}
	defer res.Body.Close()
	w.Header().Set("Cache-Control", "private, no-store")
	w.Header().Set("X-Content-Type-Options", "nosniff")
	if r.Method == "GET" && mediaFilePath.MatchString(r.URL.Path) && res.StatusCode == 200 {
		mime := strings.Split(res.Header.Get("Content-Type"), ";")[0]
		if mime != "image/png" && mime != "image/jpeg" && mime != "image/webp" {
			jsonOut(w, 502, map[string]any{"error": map[string]any{"code": "invalid_media_result"}})
			return
		}
		if res.ContentLength > 16*1024*1024 {
			jsonOut(w, 502, map[string]any{"error": map[string]any{"code": "media_output_limit"}})
			return
		}
		w.Header().Set("Content-Type", mime)
		w.WriteHeader(200)
		io.Copy(w, io.LimitReader(res.Body, 16*1024*1024+1))
		return
	}
	payload, e := io.ReadAll(io.LimitReader(res.Body, 1048577))
	var body map[string]any
	if e != nil || len(payload) > 1048576 || json.Unmarshal(payload, &body) != nil {
		jsonOut(w, 502, map[string]any{"error": map[string]any{"code": "invalid_media_result"}})
		return
	}
	if target == "/health" && res.StatusCode == 200 {
		worker, _ := body["worker"].(map[string]any)
		body = map[string]any{"ok": true, "provider": "Harmonika Ai", "features": map[string]any{"image_generation": worker["ready"] == true && worker["generate"] == true, "image_analysis": worker["ready"] == true && worker["analyze"] == true, "image_references": worker["references"] == true}, "queue_ready": body["queue_ready"], "blocked_by_uncertain_job": body["blocked_by_uncertain_job"], "limits": map[string]any{"input_image_bytes": 10485760, "result_image_bytes": 16777216, "max_references": 3, "remote_concurrency": 1}}
	}
	jsonOut(w, res.StatusCode, mediaSafeJSON(body))
}
