#!/usr/bin/env python3
"""Pure owner MCP extension patch. Actual installation keeps private backups."""
BLOCK = '''# Harmonika curated context extension v2026.11.12.1
import project_memory_mcp as _project_memory
from tool_firewall import protect as _protect_tool
TOOLS.extend(_project_memory.TOOLS)
HANDLERS.update(_project_memory.HANDLERS)
HANDLERS = {name: _protect_tool(name, handler) for name, handler in HANDLERS.items()}

'''


def patch(source):
    if BLOCK in source:
        return source
    anchor = 'if __name__ == "__main__":'
    if source.count(anchor) != 1:
        raise ValueError('owner_mcp_baseline_changed')
    result = source.replace(anchor, BLOCK+anchor, 1)
    compile(result, '<owner-mcp-extension>', 'exec')
    return result
