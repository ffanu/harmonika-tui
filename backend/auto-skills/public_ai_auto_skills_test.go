package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
)

func TestAutoSkillsDoNotInterceptClientTools(t *testing.T) {
	for _, in := range []publicAIChatCompletionRequest{
		{Messages: []publicAIChatMessage{{Role: "user", Content: "SEO audit https://example.com"}}, Tools: []publicAITool{{Type: "function"}}},
		{Messages: []publicAIChatMessage{{Role: "user", Content: "SEO"}}, ToolChoice: "none"},
		{Messages: []publicAIChatMessage{{Role: "tool", Content: "SEO", ToolCallID: "id"}}},
		{Messages: []publicAIChatMessage{{Role: "user", Content: "Halo apa kabar?"}}},
	} {
		if automaticSkillsCandidate(in) {
			t.Fatal("unexpected interception")
		}
	}
	if !automaticSkillsCandidate(publicAIChatCompletionRequest{Messages: []publicAIChatMessage{{Role: "user", Content: "Audit SEO https://example.com"}, {Role: "assistant", Content: "Target?"}, {Role: "user", Content: "ya situs itu"}}}) {
		t.Fatal("lost context")
	}
}
func TestAutoSkillBoundary(t *testing.T) {
	for _, name := range []string{"shell_exec", "file_read", "seo_visual", "seo_drift", "seo_page_audit;id"} {
		c := publicAIToolCall{}
		c.Function.Name = name
		c.Function.Arguments = "{}"
		if _, e := autoSkillExecute(context.Background(), 1, c); e == nil {
			t.Fatal("unsafe operation allowed", name)
		}
	}
	c := publicAIToolCall{}
	c.Function.Name = "seo_page_audit"
	c.Function.Arguments = "{}"
	if _, e := autoSkillExecute(context.Background(), 0, c); e == nil {
		t.Fatal("untrusted identity allowed")
	}
}
func TestAutoSkillResultRoundTrip(t *testing.T) {
	n := 0
	decide := func(ctx context.Context, in publicAIChatCompletionRequest) (publicAIChatMessage, error) {
		n++
		if n == 1 {
			c := publicAIToolCall{ID: "call_1", Type: "function"}
			c.Function.Name = "seo_page_audit"
			c.Function.Arguments = `{"url":"https://example.com"}`
			return publicAIChatMessage{Role: "assistant", ToolCalls: []publicAIToolCall{c}}, nil
		}
		if len(in.Tools) != 0 {
			t.Fatal("tool results can rearm planner")
		}
		last := in.Messages[len(in.Messages)-2]
		if last.Role != "tool" || last.ToolCallID != "call_1" {
			t.Fatal("lost tool result identity")
		}
		if last.Content != `{"status":"completed","findings":[]}` {
			t.Fatal("fabricated tool output")
		}
		return publicAIChatMessage{Role: "assistant", Content: "Ringkasan berdasarkan hasil pemeriksaan."}, nil
	}
	text, count, e := autoSkillAnswer(context.Background(), publicAIChatCompletionRequest{Messages: []publicAIChatMessage{{Role: "user", Content: "audit SEO https://example.com"}}}, nil, decide, func(context.Context, publicAIToolCall) (json.RawMessage, error) {
		return json.RawMessage(`{"status":"completed","findings":[]}`), nil
	})
	if e != nil || count != 1 || text == "" {
		t.Fatal(text, count, e)
	}
}
func TestAutoSkillFailureNoFabricatedAnswer(t *testing.T) {
	c := publicAIToolCall{ID: "a", Type: "function"}
	c.Function.Name = "seo_page_audit"
	c.Function.Arguments = "{}"
	text, count, e := autoSkillAnswer(context.Background(), publicAIChatCompletionRequest{}, nil, func(context.Context, publicAIChatCompletionRequest) (publicAIChatMessage, error) {
		return publicAIChatMessage{Role: "assistant", ToolCalls: []publicAIToolCall{c}}, nil
	}, func(context.Context, publicAIToolCall) (json.RawMessage, error) {
		return nil, errors.New("unavailable")
	})
	if e == nil || text != "" || count != 0 {
		t.Fatal("false successful audit")
	}
}
func TestAutoSkillBudget(t *testing.T) {
	calls := []publicAIToolCall{}
	for i := 0; i < 4; i++ {
		c := publicAIToolCall{ID: fmt.Sprintf("a%d", i), Type: "function"}
		c.Function.Name = "seo_page_audit"
		c.Function.Arguments = "{}"
		calls = append(calls, c)
	}
	executions := 0
	_, count, e := autoSkillAnswer(context.Background(), publicAIChatCompletionRequest{}, nil, func(context.Context, publicAIChatCompletionRequest) (publicAIChatMessage, error) {
		return publicAIChatMessage{Role: "assistant", ToolCalls: calls}, nil
	}, func(context.Context, publicAIToolCall) (json.RawMessage, error) {
		executions++
		return json.RawMessage(`{"status":"degraded"}`), nil
	})
	if e == nil || count != 0 || executions != 0 {
		t.Fatal("budget not enforced before execution", count, executions)
	}
}
func TestAutoSkillAdmission(t *testing.T) {
	if !autoSkillAcquire(900001) {
		t.Fatal("admission")
	}
	defer autoSkillRelease(900001)
	if autoSkillAcquire(900001) {
		t.Fatal("same token parallel")
	}
	if !autoSkillAcquire(900002) {
		t.Fatal("second token")
	}
	defer autoSkillRelease(900002)
	if autoSkillAcquire(900003) {
		autoSkillRelease(900003)
		t.Fatal("global bound")
	}
}
func TestAutoSkillOutputBound(t *testing.T) {
	b := &autoSkillBoundedBuffer{limit: 3}
	if _, e := b.Write([]byte("abcd")); e == nil || b.Len() != 0 {
		t.Fatal("output limit")
	}
}

func TestAutoSkillRejectsToolIntroducedURL(t *testing.T) {
	in := publicAIChatCompletionRequest{Messages: []publicAIChatMessage{{Role: "user", Content: "audit SEO https://example.com"}, {Role: "assistant", Content: "https://evil.example"}}}
	c := publicAIToolCall{}
	c.Function.Arguments = `{"url":"https://evil.example"}`
	if autoSkillReferencesAllowed(in, c) {
		t.Fatal("assistant reference became authority")
	}
	c.Function.Arguments = `{"url":"https://example.com/"}`
	if !autoSkillReferencesAllowed(in, c) {
		t.Fatal("user target rejected")
	}
}

func TestAutoSkillOutputHasNoInjectedImageOrLink(t *testing.T) {
	in := publicAIChatCompletionRequest{Messages: []publicAIChatMessage{{Role: "user", Content: "SEO https://example.com/"}}}
	result := autoSkillSafeAnswer(in, "Temuan ![image](https://evil.example/x) https://evil.example/?data=foo https://example.com/")
	if strings.Contains(result, "evil.example") || strings.Contains(result, "![") || !strings.Contains(result, "https://example.com/") {
		t.Fatal("untrusted outbound URL", result)
	}
}

func TestAutoEngineeringImplicitRoutingAndClientPrecedence(t *testing.T) {
	for _, text := range []string{"Review kode ini sebelum merge", "Review keamanan kode Python ini", "Debug error ini", "Buatkan test plan untuk endpoint pembayaran", "Buat README dari kode berikut"} {
		in := publicAIChatCompletionRequest{Messages: []publicAIChatMessage{{Role: "user", Content: text}}}
		if !automaticSkillsCandidate(in) {
			t.Fatal("missing automatic engineering route", text)
		}
		in.ToolChoice = "none"
		if automaticSkillsCandidate(in) {
			t.Fatal("client override ignored")
		}
	}
}
func TestAutoEngineeringBindsOnlyOriginalEvidence(t *testing.T) {
	c := publicAIToolCall{ID: "a", Type: "function"}
	c.Function.Name = "engineering_code_review"
	c.Function.Arguments = `{"files":[{"path":"invented.py","content":"evil"}]}`
	original := json.RawMessage(`{"request":"Review","files":[{"path":"snippet-1.py","content":"actual evidence"}]}`)
	bound, e := autoSkillBindEngineeringEvidence(c, original)
	if e != nil || strings.Contains(bound.Function.Arguments, "invented") || !strings.Contains(bound.Function.Arguments, "actual evidence") {
		t.Fatal("model fabricated source survived")
	}
	if _, e := autoSkillBindEngineeringEvidence(c, nil); e == nil {
		t.Fatal("missing evidence accepted")
	}
}
