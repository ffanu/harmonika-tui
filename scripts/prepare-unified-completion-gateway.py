#!/usr/bin/env python3
"""Prepare an isolated gateway copy; never patches the active binary directly."""
import sys
from pathlib import Path
p=Path(sys.argv[1] if len(sys.argv)>1 else '/opt/harmonika-ai/unified-completion-stage/gateway')/'public_api_docs.go';s=p.read_text();needle='func publicAIFeatures() map[string]any {';assert needle in s
if 'completionReady := publicUnifiedCompletionReady()' not in s:s=s.replace(needle,needle+'\n completionReady := publicUnifiedCompletionReady()')
needle='"unified_orchestrator": publicUnifiedReady(),';assert needle in s
if '"unified_graphql"' not in s:s=s.replace(needle,needle+' "unified_graphql": completionReady, "unified_websocket": completionReady, "unified_knowledge": completionReady, "unified_connector_secret_store": completionReady,')
needle='{"POST", "/v1/public-ai/chat/messages",';i=s.index(needle)
entries='''{"GET", "/v1/unified/capabilities", "Capability orchestrator dan izin token", "application/json"},
 {"POST", "/v1/unified/graphql", "GraphQL read-only terautentikasi", "application/json"},
 {"POST", "/v1/unified/ticket", "Ticket WebSocket satu kali; TTL60detik", "application/json"},
 {"GET", "/v1/unified/ws", "WebSocket read-only; Bearer atau ticket", "websocket"},
 {"POST", "/v1/unified/bus/events", "Publish event privat per token", "application/json"},
 {"POST", "/v1/unified/bus/replay", "Replay event privat per token", "application/json"},
 {"POST", "/v1/unified/knowledge/query", "Query corpus operator terverifikasi", "application/json"},
 {"POST", "/v1/unified/secrets", "Connector secret privat; scope khusus; metadata saja", "application/json"},
 '''
if '"/v1/unified/graphql"' not in s:s=s[:i]+entries+s[i:]
p.write_text(s)
