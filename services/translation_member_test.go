package main

import "testing"

func TestMemberTranslationRequestedOnlyForExplicitTextWithoutAttachments(t *testing.T) {
	for _, tc := range []struct {
		message     string
		attachments int
		want        bool
	}{
		{"Terjemahkan ke bahasa Jawa: Saya dibuat oleh OpenAI.", 0, true},
		{"translate to Japanese: I was created by Google.", 0, true},
		{`terjemahkan "Saya Codex" ke bahasa Jawa`, 0, true},
		{"Terjemahkan ke bahasa Jawa: Halo", 1, false},
		{"Terjemahkan ke bahasa Jawa: Halo", -1, false},
		{"Bisakah translate to Japanese: Hello", 0, false},
		{"Apa itu terjemahan?", 0, false},
		{"Terjemahkan ke bahasa Jawa:", 0, false},
	} {
		if got := memberTranslationRequested(tc.message, tc.attachments); got != tc.want {
			t.Errorf("requested=%v want=%v, attachments=%d", got, tc.want, tc.attachments)
		}
	}
}

func TestMemberTranslationPreservesNamesOnlyWithTrustedAcknowledgement(t *testing.T) {
	raw := "I was created by OpenAI.\nSaya Codex.\n![reference](https://example.invalid/reference.png)\n"
	if got := memberTranslationAnswer(raw, true, "v1"); got != raw {
		t.Fatal("trusted translation was rewritten")
	}
	for _, tc := range []struct {
		requested bool
		header    string
	}{
		{false, "v1"}, {false, ""}, {true, ""}, {true, "V1"}, {true, "v1 "}, {true, "v1, forged"},
	} {
		got := memberTranslationAnswer(raw, tc.requested, tc.header)
		if got != sanitizeMemberAIAnswer(raw) || got == raw {
			t.Fatal("untrusted response bypassed member sanitization")
		}
	}
}
