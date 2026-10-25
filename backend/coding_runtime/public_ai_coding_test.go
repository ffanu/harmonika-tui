package main

import (
	"github.com/DATA-DOG/go-sqlmock"
	"net/http/httptest"
	"regexp"
	"testing"
)

func TestCodingRecoveryAndStopDoNotConsumeMutationQuota(t *testing.T) {
	for _, tc := range []struct{ method, path string }{{"GET", "/v1/coding/jobs/00000000-0000-0000-0000-000000000000"}, {"POST", "/v1/coding/jobs/00000000-0000-0000-0000-000000000000/stop"}} {
		db, m, _ := sqlmock.New()
		a := &app{db: db}
		q := "SELECT id,name,owner_chat_id,scopes,tier,request_limit_per_day,unlimited FROM public_ai_tokens WHERE token_hash=$1 AND status='active' AND revoked_at IS NULL"
		m.ExpectQuery(regexp.QuoteMeta(q)).WithArgs(hashToken("test-api-key")).WillReturnRows(sqlmock.NewRows([]string{"id", "name", "owner", "scopes", "tier", "limit", "unlimited"}).AddRow(77, "test", "-1", "chat,models", "public", 1, false))
		r := publicDocsRequest(tc.method, tc.path, "")
		r.Header.Set("X-Harmonika-Principal", "api-token:999")
		tok, ok := a.codingToken(httptest.NewRecorder(), r)
		if !ok || tok.ID != 77 {
			t.Fatal("client spoofed principal or recovery blocked")
		}
		if e := m.ExpectationsWereMet(); e != nil {
			t.Fatal(e)
		}
		db.Close()
	}
}
func TestCodingRoutesRejectPrivateOrMalformedPaths(t *testing.T) {
	for _, p := range []string{"/v1/coding/../../etc/passwd", "/v1/coding/jobs/abc/stop", "/v1/coding/internal", "/v1/coding/workspaces/00000000-0000-0000-0000-000000000000/approve"} {
		if codingPath.MatchString(p) {
			t.Fatal(p)
		}
	}
}
func TestCodingChatRequiresIdempotencyAndWorkspace(t *testing.T) {
	a := &app{}
	w := httptest.NewRecorder()
	a.publicCodingChat(w, publicDocsRequest("POST", "/v1/chat/completions", ""), publicAIToken{Scopes: "chat"}, publicAIChatCompletionRequest{WorkspaceID: "00000000-0000-0000-0000-000000000000"})
	if w.Code != 400 {
		t.Fatal("missing idempotency not rejected", w.Code)
	}
}

func TestCodingScopeRestriction(t *testing.T) {
	if codingScopeAllowed(publicAIToken{Scopes: "models"}) {
		t.Fatal("models-only token accepted")
	}
	if !codingScopeAllowed(publicAIToken{Scopes: "chat,models"}) {
		t.Fatal("existing chat token broken")
	}
}

func TestCodingDebuggerPublicRoutes(t *testing.T) {
	for _, path := range []string{"/v1/coding/debug/sessions/00000000-0000-0000-0000-000000000000", "/v1/coding/debug/sessions/00000000-0000-0000-0000-000000000000/events", "/v1/coding/debug/sessions/00000000-0000-0000-0000-000000000000/actions", "/v1/coding/debug/sessions/00000000-0000-0000-0000-000000000000/stop"} {
		if !codingPath.MatchString(path) {
			t.Fatal(path)
		}
	}
	for _, path := range []string{"/v1/coding/debug/sessions/1234/stop", "/v1/coding/debug/sessions/00000000-0000-0000-0000-000000000000/evaluate", "/v1/coding/debug/pid/1", "/v1/coding/debug/sessions/00000000-0000-0000-0000-000000000000/../secret"} {
		if codingPath.MatchString(path) {
			t.Fatal(path)
		}
	}
}
func TestCodingTrustedUpstreamAllowsOnlyLoopbackStage(t *testing.T) {
	for _, value := range []string{"https://example.com", "http://169.254.169.254", "http://127.0.0.1:18188@evil.test", "http://127.0.0.1:18188/path"} {
		t.Setenv("HARMONIKA_CODING_UPSTREAM", value)
		if codingServiceBase() != "http://127.0.0.1:18187" {
			t.Fatal("untrusted coding destination")
		}
	}
	t.Setenv("HARMONIKA_CODING_UPSTREAM", "http://127.0.0.1:18188")
	if codingServiceBase() != "http://127.0.0.1:18188" {
		t.Fatal("staging override unavailable")
	}
}
