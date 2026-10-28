"""Prepare central Node integration; preserves provider and tool-call contracts."""
from pathlib import Path
import sys,hashlib,json,subprocess,tempfile,os
source=Path(sys.argv[1]);target=Path(sys.argv[2]);s=source.read_text()
assert 'proConversationBridge' not in s,'already_integrated'
assert s.count("const http = require('http');")==1,'require_marker_changed'
assert s.count("await runGoogleMode(latestUserPrompt, { suppressDebugCapture: privateCs })")==2,'google_call_sites_changed'
s=s.replace("const http = require('http');", "const http = require('http');\nconst proConversationBridge = require('./pro-cs-engine-bridge.cjs').createBridge({log: event => console.log(JSON.stringify(event))});",1)
marker="  if (String(req.headers['x-harmonika-client']||'') === 'internal-agent') {"
assert marker in s;s=s.replace(marker,"  const proCSState = await proConversationBridge.preflight(req, data);\n  res.setHeader('X-Harmonika-Pro-CS', proCSState.status);\n"+marker,1)
marker="  const id = `chatcmpl-${Date.now().toString(36)}`;"
assert marker in s;s=s.replace(marker,"  teamPrompt = proConversationBridge.attach(teamPrompt, proCSState);\n"+marker,1)
s=s.replace("await runGoogleMode(latestUserPrompt, { suppressDebugCapture: privateCs })", "await runGoogleMode(proConversationBridge.googlePrompt(latestUserPrompt, proCSState), { suppressDebugCapture: privateCs })")
assert s.count('proConversationBridge.googlePrompt')==2
# Private website classification inputs and internal-agent bodies remain unmodified.
assert s.count("require('./pro-cs-engine-bridge.cjs')")==1
with tempfile.NamedTemporaryFile(mode='w',suffix='.js',dir=target.parent,delete=False) as f:
 f.write(s);temporary=Path(f.name)
try:
 subprocess.run(['node','--check',str(temporary)],check=True,capture_output=True);os.replace(temporary,target)
finally:temporary.unlink(missing_ok=True)
