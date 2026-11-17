package main

import "context"

// The operator creates translation_message_receipts explicitly during
// activation. A missing/unavailable table fails closed; this file never runs
// DDL. Receipts contain message and owner IDs only, never translation text.
const translationReceiptInsert = `INSERT INTO translation_message_receipts (message_id,user_id)
SELECT m.id,c.user_id FROM chat_messages m
JOIN chat_conversations c ON c.id=m.conversation_id
WHERE m.id=$1 AND c.user_id=$2 AND m.role='assistant'
ON CONFLICT (message_id) DO NOTHING`

const translationReceiptExists = `SELECT EXISTS (
SELECT 1 FROM translation_message_receipts WHERE user_id=$1 AND message_id=$2)`

// requested comes from the explicit intent parser; upstreamTranslationHeader
// must be the trusted core HTTP RESPONSE header, never a client header or SSE
// body field. Call only after the assistant message has actually been saved.
func (a *app) saveTranslationMessageReceipt(ctx context.Context, userID, messageID int64, requested bool, upstreamTranslationHeader string) bool {
	if a == nil || a.db == nil || userID <= 0 || messageID <= 0 || !requested || upstreamTranslationHeader != "v1" {
		return false
	}
	result, err := a.db.ExecContext(ctx, translationReceiptInsert, messageID, userID)
	if err != nil {
		return false
	}
	count, err := result.RowsAffected()
	if err != nil {
		return false
	}
	if count == 1 {
		return true
	}
	// Duplicate acknowledgement is idempotent, and must never reassign a
	// receipt owned by someone else or treat an unowned message as trusted.
	return count == 0 && a.hasTranslationMessageReceipt(ctx, userID, messageID)
}

func (a *app) hasTranslationMessageReceipt(ctx context.Context, userID, messageID int64) bool {
	if a == nil || a.db == nil || userID <= 0 || messageID <= 0 {
		return false
	}
	var exists bool
	if err := a.db.QueryRowContext(ctx, translationReceiptExists, userID, messageID).Scan(&exists); err != nil {
		return false
	}
	return exists
}
