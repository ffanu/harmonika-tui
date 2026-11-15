package main

// Only stable policy enums cross the internal/public boundary; never upstream prose.
func publicToolPolicyStatus(code string) (int, bool) {
 switch code {
 case "tool_secret_reference_required", "invalid_tool_history", "tool_input_complexity":
  return 400, true
 case "context_length_exceeded", "tool_input_too_large":
  return 413, true
 default:
  return 0, false
 }
}
