package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"os"
	"os/exec"
	"testing"
	"time"
)

func TestMediaBridgeLive(t *testing.T) {
	if os.Getenv("HARMONIKA_MEDIA_LIVE") != "1" {
		t.Skip("explicit isolated GPU acceptance")
	}
	db, e := sql.Open("pgx", os.Getenv("DATABASE_URL"))
	if e != nil {
		t.Fatal("db_unavailable")
	}
	defer db.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 4*time.Minute)
	defer cancel()
	var ids []int64
	var keys []string
	defer func() {
		for _, id := range ids {
			if _, e = db.Exec("DELETE FROM public_ai_requests WHERE token_id=$1", id); e != nil {
				t.Error("fixture_quota_cleanup")
			}
			if _, e = db.Exec("DELETE FROM public_ai_tokens WHERE id=$1", id); e != nil {
				t.Error("fixture_token_cleanup")
			}
		}
	}()
	for i := 0; i < 2; i++ {
		b := make([]byte, 32)
		if _, e = rand.Read(b); e != nil {
			t.Fatal("random_failed")
		}
		key := "hai_live_" + hex.EncodeToString(b)
		var id int64
		e = db.QueryRowContext(ctx, `INSERT INTO public_ai_tokens(token_hash,name,owner_chat_id,scopes,tier,request_limit_per_day) VALUES($1,'isolated-media-bridge-smoke','isolated-media-bridge-smoke','chat,models','public',80) RETURNING id`, hashToken(key)).Scan(&id)
		if e != nil {
			t.Fatal("fixture_token_creation")
		}
		ids = append(ids, id)
		keys = append(keys, key)
	}
	raw, _ := json.Marshal(map[string]any{"tokens": keys, "ids": ids})
	cmd := exec.CommandContext(ctx, "/usr/bin/python3", "/opt/harmonika-ai/smoke-media-bridge.py")
	cmd.Stdin = bytes.NewReader(raw)
	out, e := cmd.CombinedOutput()
	t.Log(string(out))
	if e != nil {
		t.Fatal("media_acceptance_failed")
	}
}
func TestMediaBridgeTokenCleanup(t *testing.T) {
	if os.Getenv("HARMONIKA_MEDIA_LIVE") != "1" {
		t.Skip("explicit fixture cleanup check")
	}
	db, e := sql.Open("pgx", os.Getenv("DATABASE_URL"))
	if e != nil {
		t.Fatal("db_unavailable")
	}
	defer db.Close()
	var n int
	if e = db.QueryRow("SELECT count(*) FROM public_ai_tokens WHERE owner_chat_id='isolated-media-bridge-smoke'").Scan(&n); e != nil || n != 0 {
		t.Fatal("fixture_tokens_remain")
	}
}
