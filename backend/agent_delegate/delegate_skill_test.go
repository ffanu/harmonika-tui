package main

import (
	"context"
	"errors"
	"strings"
	"testing"
)

func TestDelegateProviderFallbackKeepsOriginalBudget(t *testing.T) {
	calls := []string{}
	answer := func(ctx context.Context, provider, task string) (string, error) {
		calls = append(calls, provider)
		if provider == "opencode" {
			return "", errors.New("synthetic rate limit")
		}
		return "323", nil
	}
	used, text, e := executeDelegationWith(context.Background(), "auto", "17*19", answer)
	if e != nil || used != "codex" || text != "323" || strings.Join(calls, ",") != "opencode,codex" {
		t.Fatal("fallback contract", used, e, calls)
	}
	calls = nil
	ctx, cancel := context.WithCancel(context.Background())
	answer = func(context.Context, string, string) (string, error) {
		calls = append(calls, "opencode")
		cancel()
		return "", ctx.Err()
	}
	_, _, e = executeDelegationWith(ctx, "auto", "task", answer)
	if e == nil || len(calls) != 1 {
		t.Fatal("fallback after expired original deadline", calls)
	}
}
func TestDelegateForcedProviderDoesNotTryOtherAgent(t *testing.T) {
	calls := []string{}
	answer := func(ctx context.Context, provider, task string) (string, error) {
		calls = append(calls, provider)
		return "4", nil
	}
	provider, _, e := executeDelegationWith(context.Background(), "codex", "2+2", answer)
	if e != nil || provider != "codex" || strings.Join(calls, ",") != "codex" {
		t.Fatal("forced provider lost")
	}
}
func TestDelegateOutputBufferRemainsBounded(t *testing.T) {
	var b delegateBuffer
	p := []byte(strings.Repeat("x", 256*1024))
	n, e := b.Write(p)
	if e != nil || n != len(p) || b.Len() != 128*1024 || !b.exceeded {
		t.Fatal("unbounded provider output")
	}
}
