package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func TestMediaStrictRoutes(t *testing.T) {
	for _, x := range []struct{ m, p string }{{"GET", "/v1/media/capabilities"}, {"POST", "/v1/media/jobs"}, {"GET", "/v1/media/jobs/job_abc"}, {"POST", "/v1/media/jobs/job_abc/cancel"}, {"POST", "/v1/media/jobs/job_abc/recover"}, {"GET", "/v1/media/files/file_abc"}, {"POST", "/v1/media/files"}} {
		if _, ok := mediaTarget(x.m, x.p); !ok {
			t.Fatal(x)
		}
	}
	for _, x := range []struct{ m, p string }{{"POST", "/v1/media/jobs/job_abc"}, {"GET", "/v1/media/jobs/job_abc/cancel"}, {"GET", "/v1/media/files/../../etc/passwd"}, {"GET", "/v1/media/jobs/job_abc/unknown"}, {"GET", "/v1/media/jobs/job_abc%2fcancel"}} {
		if _, ok := mediaTarget(x.m, x.p); ok {
			t.Fatal(x)
		}
	}
	if mediaBillable("POST", "/v1/media/jobs/job_abc/cancel") || mediaBillable("GET", "/v1/media/capabilities") || !mediaBillable("POST", "/v1/media/jobs") {
		t.Fatal("control_plane_quota")
	}
}
func TestMediaMetadataNoWorkerName(t *testing.T) {
	v := mediaSafeJSON(map[string]any{"error": map[string]any{"code": "rtx_offline"}})
	b, _ := json.Marshal(v)
	if strings.Contains(string(b), "rtx_") {
		t.Fatal("internal_worker_label_exposed")
	}
}
