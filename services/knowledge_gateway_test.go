package main

import (
	"net/http/httptest"
	"testing"
)

func TestKnowledgeVerifiedOwnerOverridesCaller(t *testing.T) {
	r := httptest.NewRequest("POST", "/api/tools/knowledge/search", nil)
	r.Header.Add("X-Harmonika-KB-Owner", "token:999")
	r.Header.Add("X-Harmonika-KB-Owner", "token:888")
	forward := bindKnowledgeOwner(r, 42)
	if forward.Header.Get("X-Harmonika-KB-Owner") != "token:42" || len(forward.Header.Values("X-Harmonika-KB-Owner")) != 1 {
		t.Fatal("caller selected another token namespace")
	}
	if r.Header.Get("X-Harmonika-KB-Owner") != "token:999" {
		t.Fatal("binding mutated the original request")
	}
}
