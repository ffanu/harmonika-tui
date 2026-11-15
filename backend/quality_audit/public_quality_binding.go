package main

import "net/http"

// The gateway has already decoded this input. Read only the latest user turn,
// never its system prompt, prior history, attachment bytes or tool arguments.
func qualityChatInput(r *http.Request, in publicAIChatCompletionRequest) {
	last := -1
	for i := range in.Messages {
		if in.Messages[i].Role == "user" {
			last = i
		}
	}
	question := ""
	if last >= 0 {
		question = publicAIContentText(in.Messages[last].Content)
	}
	qualityQuestion(r, in.SessionID, question)
	s, ok := r.Context().Value(qualityContextKey{}).(*qualityState)
	if !ok {
		return
	}
	calls := map[string]string{}
	for i := last + 1; i < len(in.Messages); i++ {
		message := in.Messages[i]
		for _, call := range message.ToolCalls {
			calls[call.ID] = call.Function.Name
		}
		if message.Role == "tool" {
			skill := message.Name
			if skill == "" {
				skill = calls[message.ToolCallID]
			}
			s.mu.Lock()
			if len(s.steps) < 128 {
				s.steps = append(s.steps, qualityStep{"tool.client_reported", qualityBound(skill, 128), "client_reported", "unknown", 0})
			}
			s.mu.Unlock()
		}
	}
}
