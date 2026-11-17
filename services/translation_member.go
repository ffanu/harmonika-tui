package main

// Use only after the member message and attachment IDs have been validated.
// The caller forces Harmonika mode after Google mode auto-selection, and omits
// roadmap enrichment, only when this returns true.
func memberTranslationRequested(message string, attachmentCount int) bool {
	if attachmentCount != 0 {
		return false
	}
	_, _, requested := translationChatIntent(message)
	return requested
}

// upstreamTranslationHeader must come from the trusted core HTTP RESPONSE,
// never the member's request headers or a model-authored SSE field. Both the
// explicit intent and the core acknowledgement are necessary to preserve text.
// This does not grant a bypass when rendering stored history: that requires
// separately persisted server provenance for the assistant message.
func memberTranslationAnswer(raw string, requested bool, upstreamTranslationHeader string) string {
	if requested && upstreamTranslationHeader == "v1" {
		return raw
	}
	return sanitizeMemberAIAnswer(raw)
}
