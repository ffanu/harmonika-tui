"""Private atomic MCP patch. Backups stay outside Git; no secret values are emitted."""
import ast,datetime,hashlib,os,pathlib,shutil
p=pathlib.Path.home()/'.config/harmonika-terminal/mcp_server.py'
original=p.read_bytes();s=original.decode();t=ast.parse(s);edits=[]
for n in t.body:
 if isinstance(n,ast.FunctionDef) and n.name=='tool_generate_fluxhd':
  src=ast.get_source_segment(s,n)
  if 'from visual_media import ensure_media_port, png_result' in src:
   continue
  if '"model": "FLUX.1-dev"' not in src:
   raise SystemExit('unsupported_image_adapter_preserved')
  src=src.replace('if not port_open(18200):\n        ensure_tunnel()', 'from visual_media import ensure_media_port, png_result\n    if not ensure_media_port(18200):\n        return {"ok": False, "error": "image_gateway_offline"}')
  src=src.replace('"model": "FLUX.1-dev", "width": int.from_bytes(raw[16:20], "big"), "height": int.from_bytes(raw[20:24], "big")','**png_result(raw, j["result"], jid)')
  node=ast.parse(src).body[0]
  class Publication(ast.NodeTransformer):
   def visit_With(self, node):
    for item in node.items:
     call=item.context_expr
     if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='open' and len(call.args)>=2 and isinstance(call.args[0],ast.Name) and call.args[0].id=='path' and isinstance(call.args[1],ast.Constant) and call.args[1].value=='wb':
      return [ast.parse("metadata = png_result(raw, j['result'], jid)").body[0],node]
    return self.generic_visit(node)
   def visit_Return(self,node):
    if isinstance(node.value,ast.Dict):
     node.value.values=[ast.Name(id='metadata',ctx=ast.Load()) if isinstance(v,ast.Call) and isinstance(v.func,ast.Name) and v.func.id=='png_result' else v for v in node.value.values]
    return node
  node=Publication().visit(node)
  node.body=[ast.Try(body=node.body,handlers=[ast.ExceptHandler(type=ast.Name(id='Exception',ctx=ast.Load()),name=None,body=ast.parse('return {"ok": False, "error": "image_result_unavailable", "job_id": locals().get("jid"), "uncertain": True}').body)],orelse=[],finalbody=[])]
  edits.append((n.lineno-1,n.end_lineno,ast.unparse(ast.fix_missing_locations(node))))
 if isinstance(n,ast.FunctionDef) and n.name=='tool_analyze_visual_image':
  if 'from visual_media import analyze' in ast.get_source_segment(s,n):
   continue
  edits.append((n.lineno-1,n.end_lineno,'def tool_analyze_visual_image(args):\n    from visual_media import analyze\n    return analyze(args, VISION_TOKEN)'))
if not edits:
 print({'already_active':True});raise SystemExit(0)
lines=s.splitlines()
for a,b,new in sorted(edits,reverse=True):lines[a:b]=new.splitlines()
new='\n'.join(lines)+'\n'
# Update only descriptive string literals, never private constants.
t=ast.parse(new); replacements=[]
for n in ast.walk(t):
 if isinstance(n,ast.Constant) and isinstance(n.value,str) and 'FLUX.1-dev' in n.value and len(n.value)>45:
  val = ('Generate an actual PNG with the RTX high profile. Defaults 2048x2048; delivery minimum 2 MiB. Read the actual model and engine from the result. Bytes alone do not measure visual quality. Other engines require explicit selection and readiness.' if n.value.startswith('Generate an actual PNG') else 'Generate high-profile PNG through the RTX gateway. Defaults 2048x2048; delivery minimum 2 MiB. Model and engine come from the actual result; the profile name does not identify a model or parameter count.')
  replacements.append((n.lineno-1,n.col_offset,n.end_lineno-1,n.end_col_offset,repr(val)))
lines=new.splitlines()
for a,c,b,d,v in sorted(replacements,reverse=True):
 if a==b:lines[a]=lines[a][:c]+v+lines[a][d:]
new='\n'.join(lines)+'\n'
compile(new,str(p),'exec')
backup=p.parent/'visual-quality-backups'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S');backup.mkdir(parents=True,mode=0o700)
b=backup/'mcp_server.py';b.write_bytes(original);b.chmod(0o600)
for name in ('visual_media.py','media_tunnels.py'):
 helper=p.parent/name
 if helper.exists():
  (backup/name).write_bytes(helper.read_bytes());(backup/name).chmod(0o600)
 source=pathlib.Path(__file__).resolve().parents[1]/'runtime/tools'/name
 tmphelper=helper.with_suffix('.visual.tmp');tmphelper.write_bytes(source.read_bytes());tmphelper.chmod(0o600);tmphelper.replace(helper)
if p.read_bytes()!=original:raise SystemExit('concurrent_edit')
tmp=p.with_suffix('.visual.tmp');tmp.write_text(new);tmp.chmod(0o600);tmp.replace(p)
print({'patched_functions':len(edits),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'private_backup':str(backup)})
