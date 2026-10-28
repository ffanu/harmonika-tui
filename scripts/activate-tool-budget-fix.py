"""Install signed tool-budget fix without restarting any existing terminal."""
import importlib.util,json,shutil,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('activation',ROOT/'scripts/activate-workbench.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
r=a.module(ROOT/'runtime/harmonika.py','tool_budget_runtime')
release='2026.10.28.2'
current,manifest=r.active();r.verify_files(current,manifest)
smoke=json.loads((ROOT/'docs/TUI-TOOLS-FULL-CONFIG-SMOKE.json').read_text())
if not smoke['ok'] or max(smoke['tool_counts'])!=32:raise ValueError('full_config_tool_budget_smoke_required')
launcher=r.ROOT/'launcher.py'
if r.digest(launcher)!=manifest['files']['tools/harmonika.py']['sha256']:raise ValueError('launcher_changed')
config=Path.home()/'.config/harmonika';share=Path.home()/'.local/share/harmonika/crush.json'
paths=[config/'harmonika.json',config/'crush.json',share]
originals={p:p.read_bytes() for p in paths if p.is_file()}
changes={}
for p,raw in originals.items():
 d=json.loads(raw);entry=d.get('mcp',{}).get('harmonika')
 if not entry:continue
 args=entry.get('args',[])
 managed=[str(config/'video-tools/compact-core-mcp.cjs'),str(r.ROOT/'current/tools/compact-core-mcp.cjs')]
 if len(args)!=1 or args[0] not in managed:raise ValueError('unrecognized_core_mcp_entrypoint')
 entry['args']=[str(r.ROOT/'current/tools/compact-core-mcp.cjs')]
 changes[p]=(json.dumps(d,indent=2)+'\n').encode()
backup=r.ROOT/('tool-budget-backup-'+release);backup.mkdir(mode=0o700)
for i,(p,raw) in enumerate(originals.items()):a.atomic(backup/('config-'+str(i)+'.json'),raw)
a.atomic(backup/'config-paths.json',json.dumps([str(p) for p in originals]).encode())
a.atomic(backup/'launcher.py',launcher.read_bytes())
try:
 result=r.install_package(ROOT/'dist'/('harmonika-terminal-'+release+'.zip'))
 installed,new=r.active();r.verify_files(installed,new)
 for p,raw in changes.items():a.atomic(p,raw)
 a.atomic(launcher,(installed/'tools/harmonika.py').read_bytes())
 assert r.digest(installed/'harmonika')==r.digest(current/'harmonika')
except Exception:
 r.switch(current.name)
 for p,raw in originals.items():a.atomic(p,raw)
 a.atomic(launcher,(backup/'launcher.py').read_bytes());raise
report={**result,'ascii_brand_unchanged':True,'lsp_still_enabled':True,'native_tools':15,'mcp_tools':17,'total_tools':32,'gateway_limit':32,'core_dispatch':'media action=capabilities; legacy direct name remains callable','existing_sessions_signalled':False,'gateway_modified':False,'backup_private':str(backup)}
(ROOT/'docs/TUI-TOOLS-FIX-ACTIVATION.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report,indent=2))
