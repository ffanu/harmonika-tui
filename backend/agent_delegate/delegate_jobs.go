package main

// Durable mechanics only; task and generated text live in worker RAM, never disk.
import (
	"context"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"syscall"
	"time"
)

type delegationRecord struct {
	Version     int    `json:"version"`
	JobID       string `json:"job_id"`
	RequestID   string `json:"request_id"`
	Owner       string `json:"owner"`
	Fingerprint string `json:"fingerprint"`
	Status      string `json:"status"`
	Created     int64  `json:"created"`
	Deadline    int64  `json:"deadline"`
	Updated     int64  `json:"updated"`
	Error       string `json:"error,omitempty"`
	Worker      string `json:"worker"`
	Provider    string `json:"provider,omitempty"`
}
type delegationStore struct {
	mu      sync.Mutex
	root    string
	key     []byte
	worker  string
	outputs map[string]string
	active  map[string]bool
	now     func() time.Time
	execute func(context.Context, string, string) (string, string, error)
}

func delegationRandom(n int) string {
	b := make([]byte, n)
	if _, e := rand.Read(b); e != nil {
		panic("delegate_random_unavailable")
	}
	return hex.EncodeToString(b)
}
func openDelegationStore(root string, execute func(context.Context, string, string) (string, string, error)) (*delegationStore, error) {
	if e := os.MkdirAll(root, 0700); e != nil {
		return nil, e
	}
	if e := os.Chmod(root, 0700); e != nil {
		return nil, e
	}
	keypath := filepath.Join(root, ".key")
	f, e := os.OpenFile(keypath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if e == nil {
		key := make([]byte, 32)
		_, e = rand.Read(key)
		if e == nil {
			_, e = f.Write(key)
		}
		if e == nil {
			e = f.Sync()
		}
		f.Close()
		if e != nil {
			return nil, e
		}
	} else if !os.IsExist(e) {
		return nil, e
	}
	info, e := os.Lstat(keypath)
	if e != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0077 != 0 {
		return nil, errors.New("delegate_key_permissions")
	}
	key, e := os.ReadFile(keypath)
	if e != nil || len(key) != 32 {
		return nil, errors.New("delegate_key_invalid")
	}
	return &delegationStore{root: root, key: key, worker: delegationRandom(16), outputs: map[string]string{}, active: map[string]bool{}, now: time.Now, execute: execute}, nil
}
func (s *delegationStore) digest(value string) string {
	h := hmac.New(sha256.New, s.key)
	h.Write([]byte(value))
	return hex.EncodeToString(h.Sum(nil))
}
func (s *delegationStore) locked(fn func() error) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	f, e := os.OpenFile(filepath.Join(s.root, ".lock"), os.O_CREATE|os.O_RDWR, 0600)
	if e != nil {
		return e
	}
	defer f.Close()
	for attempt := 0; attempt < 100; attempt++ {
		e = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if e == nil {
			break
		}
		time.Sleep(5 * time.Millisecond)
	}
	if e != nil {
		return errors.New("delegate_store_busy")
	}
	defer syscall.Flock(int(f.Fd()), syscall.LOCK_UN)
	return fn()
}
func (s *delegationStore) readAll() ([]delegationRecord, error) {
	entries, e := os.ReadDir(s.root)
	if e != nil {
		return nil, e
	}
	rows := []delegationRecord{}
	for _, entry := range entries {
		if !strings.HasSuffix(entry.Name(), ".json") {
			continue
		}
		if len(rows) >= 1024 {
			return nil, errors.New("delegate_store_full")
		}
		p := filepath.Join(s.root, entry.Name())
		info, e := os.Lstat(p)
		if e != nil || !info.Mode().IsRegular() || info.Size() > 4096 {
			return nil, errors.New("delegate_store_invalid")
		}
		b, e := os.ReadFile(p)
		if e != nil {
			return nil, e
		}
		var envelope struct {
			Payload string `json:"payload"`
			MAC     string `json:"mac"`
		}
		if json.Unmarshal(b, &envelope) != nil || !hmac.Equal([]byte(envelope.MAC), []byte(s.digest(envelope.Payload))) {
			return nil, errors.New("delegate_store_integrity")
		}
		var v delegationRecord
		if json.Unmarshal([]byte(envelope.Payload), &v) != nil || v.Version != 1 {
			return nil, errors.New("delegate_store_invalid")
		}
		if (v.Status == "completed" || v.Status == "failed" || v.Status == "timeout") && s.now().UnixMilli()-v.Updated > int64(24*time.Hour/time.Millisecond) && !s.active[v.JobID] {
			if e := os.Remove(p); e != nil {
				return nil, e
			}
			delete(s.outputs, v.JobID)
			continue
		}
		rows = append(rows, v)
	}
	return rows, nil
}
func (s *delegationStore) save(v delegationRecord) error {
	payload, e := json.Marshal(v)
	if e != nil {
		return e
	}
	b, e := json.Marshal(map[string]string{"payload": string(payload), "mac": s.digest(string(payload))})
	if e != nil {
		return e
	}
	tmp := filepath.Join(s.root, ".tmp-"+delegationRandom(12))
	f, e := os.OpenFile(tmp, os.O_CREATE|os.O_EXCL|os.O_WRONLY, 0600)
	if e != nil {
		return e
	}
	defer os.Remove(tmp)
	_, e = f.Write(b)
	if e == nil {
		e = f.Sync()
	}
	f.Close()
	if e != nil {
		return e
	}
	if e = os.Rename(tmp, filepath.Join(s.root, v.JobID+".json")); e != nil {
		return e
	}
	d, e := os.Open(s.root)
	if e == nil {
		e = d.Sync()
		d.Close()
	}
	return e
}
func (s *delegationStore) view(v delegationRecord, requestID string) map[string]any {
	status := v.Status
	running := s.active[v.JobID]
	if (status == "queued" || status == "running") && (!running || s.now().UnixMilli() > v.Deadline) {
		status = "outcome_unknown"
	}
	out := map[string]any{"job_id": v.JobID, "status": status, "worker_running": func() any {
		if !running && (v.Status == "queued" || v.Status == "running" || v.Status == "outcome_unknown") {
			return nil
		}
		return running
	}(), "worker_attached": running, "deadline_ms": v.Deadline, "created_ms": v.Created, "provider": v.Provider, "retry_after_seconds": 2}
	if requestID != "" {
		out["request_id"] = requestID
	}
	if v.Error != "" {
		out["error"] = v.Error
	}
	if status == "completed" {
		if text, ok := s.outputs[v.JobID]; ok {
			out["output"] = text
			out["output_available"] = true
		} else {
			out["status"] = "output_unavailable"
			out["output_available"] = false
		}
	}
	return out
}
func (s *delegationStore) submit(owner, requestID, task, provider string, seconds int, reserve func() error) (map[string]any, error) {
	if len(requestID) < 16 || len(requestID) > 128 || strings.TrimSpace(task) == "" || len(task) > 12000 {
		return nil, errors.New("invalid_delegate_request")
	}
	fingerprint := s.digest(task + "\x00" + provider + "\x00" + fmt.Sprint(seconds))
	ownerID := s.digest(owner)
	requestHash := s.digest(owner + "\x00" + requestID)
	var record delegationRecord
	created := false
	err := s.locked(func() error {
		rows, e := s.readAll()
		if e != nil {
			return e
		}
		pending := 0
		for _, v := range rows {
			if v.Owner == ownerID && v.RequestID == requestHash {
				if v.Fingerprint != fingerprint {
					return errors.New("idempotency_conflict")
				}
				record = v
				return nil
			}
			if v.Status == "queued" || v.Status == "running" || v.Status == "outcome_unknown" {
				pending++
			}
		}
		if pending >= 2 {
			return errors.New("delegate_busy")
		}
		if len(rows) >= 1000 {
			return errors.New("delegate_store_full")
		}
		if reserve != nil {
			if e = reserve(); e != nil {
				return e
			}
		}
		now := s.now().UnixMilli()
		record = delegationRecord{Version: 1, JobID: "dg_" + delegationRandom(16), RequestID: requestHash, Owner: ownerID, Fingerprint: fingerprint, Status: "queued", Created: now, Deadline: now + int64(seconds)*1000, Updated: now, Worker: s.worker}
		if e = s.save(record); e != nil {
			return e
		}
		s.active[record.JobID] = true
		created = true
		return nil
	})
	if err != nil {
		return nil, err
	}
	if created {
		go s.work(record, task, provider)
	}
	var out map[string]any
	s.mu.Lock()
	out = s.view(record, requestID)
	s.mu.Unlock()
	return out, nil
}
func (s *delegationStore) status(owner, jobID, requestID string) (map[string]any, error) {
	var out map[string]any
	err := s.locked(func() error {
		rows, e := s.readAll()
		if e != nil {
			return e
		}
		ownerID := s.digest(owner)
		requestHash := s.digest(owner + "\x00" + requestID)
		for _, v := range rows {
			if v.Owner == ownerID && ((jobID != "" && v.JobID == jobID) || (jobID == "" && v.RequestID == requestHash)) {
				out = s.view(v, requestID)
				return nil
			}
		}
		return errors.New("delegate_not_found")
	})
	return out, err
}
func (s *delegationStore) work(v delegationRecord, task, provider string) {
	if e := s.locked(func() error { v.Status = "running"; v.Updated = s.now().UnixMilli(); return s.save(v) }); e != nil {
		return
	}
	ctx, cancel := context.WithDeadline(context.Background(), time.UnixMilli(v.Deadline))
	defer cancel()
	used, output, err := s.execute(ctx, provider, task)
	// Slot remains occupied until the executor actually returns, even past deadline.
	_ = s.locked(func() error {
		delete(s.active, v.JobID)
		v.Updated = s.now().UnixMilli()
		v.Provider = used
		if ctx.Err() != nil || s.now().UnixMilli() > v.Deadline {
			v.Status = "timeout"
			v.Error = "delegate_deadline"
		} else if err != nil || strings.TrimSpace(output) == "" {
			v.Status = "failed"
			v.Error = "delegate_failed"
		} else {
			v.Status = "completed"
			if len(output) > 32000 {
				v.Status = "failed"
				v.Error = "delegate_output_limit"
			} else {
				s.outputs[v.JobID] = output
			}
		}
		return s.save(v)
	})
}
