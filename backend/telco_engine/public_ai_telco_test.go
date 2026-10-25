package main

import (
	"fmt"
	"github.com/DATA-DOG/go-sqlmock"
	"net/http/httptest"
	"regexp"
	"testing"
)

func TestTelcoRoutesScopeAndUntrustedPaths(t *testing.T) {
	for _, p := range []string{"/v1/telco/capabilities", "/v1/telco/invoke", "/v1/telco/agent", "/v1/telco/requests/00000000-0000-0000-0000-000000000000/events"} {
		if !telcoPath.MatchString(p) {
			t.Fatal(p)
		}
	}
	for _, p := range []string{"/v1/telco/../../private", "/v1/telco/connectors", "/v1/telco/admin", "/v1/telco/requests/1/approve"} {
		if telcoPath.MatchString(p) {
			t.Fatal(p)
		}
	}
	if telcoScopeAllowed(publicAIToken{Scopes: "models"}) {
		t.Fatal("models-only key granted domain access")
	}
	if !telcoScopeAllowed(publicAIToken{Scopes: "telco"}) || !telcoScopeAllowed(publicAIToken{Scopes: "chat,models"}) {
		t.Fatal("authorized isolated domain scope unavailable")
	}
}
func TestTelcoTokenRecoveryDoesNotDoubleChargeOrTrustCallerIdentity(t *testing.T) {
	db, m, _ := sqlmock.New()
	defer db.Close()
	a := &app{db: db}
	q := "SELECT id,name,owner_chat_id,scopes,tier,request_limit_per_day,unlimited FROM public_ai_tokens WHERE token_hash=$1 AND status='active' AND revoked_at IS NULL"
	m.ExpectQuery(regexp.QuoteMeta(q)).WithArgs(hashToken("test-api-key")).WillReturnRows(sqlmock.NewRows([]string{"id", "name", "owner", "scopes", "tier", "limit", "unlimited"}).AddRow(700, "fixture", "-1", "chat", "public", 10, false))
	req := publicDocsRequest("GET", "/v1/telco/capabilities", "")
	req.Header.Set("X-Harmonika-Principal", "api-token:999")
	tok, ok := a.telcoToken(httptest.NewRecorder(), req)
	if !ok || tok.ID != 700 {
		t.Fatal("untrusted principal was used")
	}
	if e := m.ExpectationsWereMet(); e != nil {
		t.Fatal(e)
	}
}
func TestTelcoModeConflictingWorkspaceRejectedBeforeEngine(t *testing.T) {
	w := httptest.NewRecorder()
	a := &app{}
	a.publicTelcoChat(w, publicDocsRequest("POST", "/v1/chat/completions", ""), publicAIToken{Scopes: "chat"}, publicAIChatCompletionRequest{Telco: true, WorkspaceID: "00000000-0000-0000-0000-000000000000"})
	if w.Code != 400 {
		t.Fatal(w.Code)
	}
}

func TestTelcoDatabaseFailureIsNotInvalidKey(t *testing.T) {
	db, m, _ := sqlmock.New()
	defer db.Close()
	a := &app{db: db}
	m.ExpectQuery("SELECT id,name,owner_chat_id,scopes").WillReturnError(fmt.Errorf("private database failure"))
	w := httptest.NewRecorder()
	_, ok := a.telcoToken(w, publicDocsRequest("GET", "/v1/telco/capabilities", ""))
	if ok || w.Code != 503 {
		t.Fatal("database outage mislabeled", w.Code)
	}
	if e := m.ExpectationsWereMet(); e != nil {
		t.Fatal(e)
	}
}
