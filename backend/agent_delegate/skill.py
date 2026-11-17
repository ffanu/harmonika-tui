"""Discovery descriptor; execution stays in the authenticated gateway."""
def GetSkillIdentity():
    return {
        "id": "agent.delegate", "name": "Delegasi sub-tugas dengan job persisten",
        "core_capabilities": ["delegate"], "requirements": [],
        "input_schema": {"type": "object", "properties": {
            "action": {"type": "string", "enum": ["submit", "status"]},
            "task": {"type": "string", "minLength": 1, "maxLength": 12000},
            "provider": {"type": "string", "enum": ["auto", "opencode", "codex"]},
            "timeout_seconds": {"type": "integer", "minimum": 10, "maximum": 300},
            "request_id": {"type": "string", "minLength": 16, "maxLength": 128},
            "job_id": {"type": "string", "minLength": 35, "maxLength": 35},
        }, "additionalProperties": False},
    }

def GetContextRequirements():
    return {"max_input_tokens": 16384, "max_output_tokens": 8192,
            "max_chat_messages": 0, "max_tool_calls": 0}

async def Execute(payload):
    return {"status": "error", "error": "gateway_only"}
