package main

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func testStore(t *testing.T, run func(context.Context, string, string) (string, string, error)) *delegationStore {
	t.Helper()
	s, e := openDelegationStore(t.TempDir(), run)
	if e != nil {
		t.Fatal(e)
	}
	return s
}
func awaitDelegate(t *testing.T, s *delegationStore, owner, id string) map[string]any {
	t.Helper()
	for i := 0; i < 300; i++ {
		v, e := s.status(owner, id, "")
		if e != nil {
			t.Fatal(e)
		}
		if v["status"] != "queued" && v["status"] != "running" {
			return v
		}
		time.Sleep(time.Millisecond * 5)
	}
	t.Fatal("worker did not finish")
	return nil
}
func TestDelegateLostAckDeduplicatesBeforeQuota(t *testing.T) {
	var calls, quota atomic.Int32
	release := make(chan struct{})
	s := testStore(t, func(context.Context, string, string) (string, string, error) {
		calls.Add(1)
		<-release
		return "opencode", "323", nil
	})
	reserve := func() error { quota.Add(1); return nil }
	a, e := s.submit("owner-one", "request-fixed-12345", "17*19", "auto", 120, reserve)
	if e != nil {
		t.Fatal(e)
	}
	b, e := s.submit("owner-one", "request-fixed-12345", "17*19", "auto", 120, reserve)
	if e != nil || a["job_id"] != b["job_id"] {
		t.Fatal("lost ACK made another job", e)
	}
	if quota.Load() != 1 {
		t.Fatal("duplicate charged quota")
	}
	lookup, e := s.status("owner-one", "", "request-fixed-12345")
	if e != nil || lookup["job_id"] != a["job_id"] {
		t.Fatal("request lookup lost", e)
	}
	close(release)
	v := awaitDelegate(t, s, "owner-one", a["job_id"].(string))
	if v["output"] != "323" || calls.Load() != 1 {
		t.Fatal("effects duplicated or output lost", v)
	}
}
func TestDelegateOwnershipAndConflict(t *testing.T) {
	s := testStore(t, func(context.Context, string, string) (string, string, error) { return "codex", "ok", nil })
	v, e := s.submit("owner-one", "request-fixed-12345", "task one", "auto", 120, nil)
	if e != nil {
		t.Fatal(e)
	}
	if _, e = s.status("owner-two", v["job_id"].(string), ""); e == nil {
		t.Fatal("cross-owner read")
	}
	if _, e = s.submit("owner-one", "request-fixed-12345", "other task", "auto", 120, nil); e == nil || e.Error() != "idempotency_conflict" {
		t.Fatal("request reuse not bound to task")
	}
	awaitDelegate(t, s, "owner-one", v["job_id"].(string))
}
func TestDelegateOnlyTwoPhysicalWorkersAndTimeoutDoesNotRelease(t *testing.T) {
	release := make(chan struct{})
	s := testStore(t, func(context.Context, string, string) (string, string, error) {
		<-release
		return "opencode", "late", nil
	})
	var now atomic.Int64
	now.Store(time.Now().UnixMilli())
	s.now = func() time.Time { return time.UnixMilli(now.Load()) }
	one, e := s.submit("one", "request-one-123456", "first", "auto", 1, nil)
	if e != nil {
		t.Fatal(e)
	}
	two, e := s.submit("two", "request-two-123456", "second", "auto", 1, nil)
	if e != nil {
		t.Fatal(e)
	}
	now.Add(2000)
	v, e := s.status("one", one["job_id"].(string), "")
	if e != nil || v["status"] != "outcome_unknown" || v["worker_running"] != true {
		t.Fatal("deadline pretended worker stopped", v, e)
	}
	if _, e = s.submit("three", "request-third-12345", "third", "auto", 120, nil); e == nil || e.Error() != "delegate_busy" {
		t.Fatal("timed out worker slot reused", e)
	}
	close(release)
	for _, pair := range []struct{ owner, id string }{{"one", one["job_id"].(string)}, {"two", two["job_id"].(string)}} {
		for i := 0; i < 300; i++ {
			v, _ = s.status(pair.owner, pair.id, "")
			if v["worker_running"] == false {
				break
			}
			time.Sleep(time.Millisecond * 5)
		}
		if v["status"] != "timeout" || v["output"] != nil {
			t.Fatal("late output accepted", v)
		}
	}
}
func TestDelegateRestartNeverReexecutesAndOutputIsNotPersisted(t *testing.T) {
	var calls atomic.Int32
	run := func(context.Context, string, string) (string, string, error) {
		calls.Add(1)
		return "opencode", "SYNTHETIC_PRIVATE_OUTPUT", nil
	}
	s := testStore(t, run)
	v, e := s.submit("one", "request-restart-12345", "SYNTHETIC_PRIVATE_TASK", "auto", 120, nil)
	if e != nil {
		t.Fatal(e)
	}
	awaitDelegate(t, s, "one", v["job_id"].(string))
	other, e := openDelegationStore(s.root, run)
	if e != nil {
		t.Fatal(e)
	}
	same, e := other.submit("one", "request-restart-12345", "SYNTHETIC_PRIVATE_TASK", "auto", 120, nil)
	if e != nil || same["job_id"] != v["job_id"] || same["status"] != "output_unavailable" || calls.Load() != 1 {
		t.Fatal("restart replayed or fabricated output", same, e)
	}
	entries, _ := os.ReadDir(s.root)
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		b, _ := os.ReadFile(filepath.Join(s.root, entry.Name()))
		if strings.Contains(string(b), "SYNTHETIC_PRIVATE") || strings.Contains(string(b), "request-restart") {
			t.Fatal("private input/output logged")
		}
		info, _ := entry.Info()
		if info.Mode().Perm() != 0600 {
			t.Fatal("ledger permissions")
		}
	}
}
func TestDelegateRestartPendingRemainsUnknown(t *testing.T) {
	release := make(chan struct{})
	run := func(context.Context, string, string) (string, string, error) { <-release; return "opencode", "ok", nil }
	s := testStore(t, run)
	v, e := s.submit("one", "request-pending-12345", "task", "auto", 120, nil)
	if e != nil {
		t.Fatal(e)
	}
	other, e := openDelegationStore(s.root, run)
	if e != nil {
		t.Fatal(e)
	}
	seen, e := other.status("one", v["job_id"].(string), "")
	if e != nil || seen["status"] != "outcome_unknown" {
		t.Fatal("missing worker treated as stopped/success", seen, e)
	}
	close(release)
	awaitDelegate(t, s, "one", v["job_id"].(string))
}

func TestDelegateRetentionRemovesOnlyOldTerminalRecords(t *testing.T) {
	s := testStore(t, func(context.Context, string, string) (string, string, error) {
		return "codex", "ok", nil
	})
	v, e := s.submit("one", "request-retention-12345", "task", "codex", 120, nil)
	if e != nil {
		t.Fatal(e)
	}
	awaitDelegate(t, s, "one", v["job_id"].(string))
	future := time.Now().Add(25 * time.Hour)
	s.now = func() time.Time { return future }
	unknown := delegationRecord{Version: 1, JobID: "dg_uncertain", Owner: s.digest("one"), Status: "outcome_unknown", Created: future.Add(-48 * time.Hour).UnixMilli(), Updated: future.Add(-48 * time.Hour).UnixMilli()}
	if e := s.save(unknown); e != nil {
		t.Fatal(e)
	}
	if _, e := s.status("one", v["job_id"].(string), ""); e == nil || e.Error() != "delegate_not_found" {
		t.Fatal("expired result was retained", e)
	}
	if result, e := s.status("one", unknown.JobID, ""); e != nil || result["status"] != "outcome_unknown" {
		t.Fatal("uncertain effect was evicted", result, e)
	}
}
func TestDelegateTamperingAndQuotaFailureFailClosed(t *testing.T) {
	s := testStore(t, func(context.Context, string, string) (string, string, error) { return "opencode", "ok", nil })
	if _, e := s.submit("one", "request-denied-12345", "task", "auto", 120, func() error { return errors.New("quota_denied") }); e == nil {
		t.Fatal("quota ignored")
	}
	rows, _ := s.readAll()
	if len(rows) != 0 {
		t.Fatal("job admitted without quota")
	}
	v, _ := s.submit("one", "request-tamper-12345", "task", "auto", 120, nil)
	awaitDelegate(t, s, "one", v["job_id"].(string))
	p := filepath.Join(s.root, v["job_id"].(string)+".json")
	b, _ := os.ReadFile(p)
	os.WriteFile(p, []byte(strings.Replace(string(b), "completed", "failed", 1)), 0600)
	if _, e := s.status("one", v["job_id"].(string), ""); e == nil {
		t.Fatal("tampered ledger trusted")
	}
}
