"""Private atomic owner MCP upgrade; no provider/credential values are printed."""
import argparse,ast,datetime,hashlib,json
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('--owner',type=Path,default=Path.home()/'.config/harmonika-terminal');args=p.parse_args()
owner=args.owner.resolve();file=owner/'mcp_server.py';original=file.read_bytes();source=original.decode();tree=ast.parse(source)
functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='tool_qa_matrix']
if len(functions)!=1:raise SystemExit('qa_adapter_not_found')
node=functions[0];body=ast.get_source_segment(source,node)
if 'qa_matrix.cjs' not in body and 'from mockup_quality import invoke' not in body:raise SystemExit('unsupported_qa_adapter_preserved')
replacement='''def tool_qa_matrix(args):
    from mockup_quality import invoke
    node = os.environ.get("HARMONIKA_NODE") or "/Users/id/.local/node/bin/node"
    if not os.path.exists(node):
        node = "node"
    return invoke(args, node, HERE, os.path.expanduser("~/Downloads/harmonika-ai/document/qa-matrix-%d" % int(time.time())))'''
edits=[(node.lineno-1,node.end_lineno,replacement)]
tools=[]
for n in ast.walk(tree):
    if not isinstance(n,ast.Dict):continue
    try:value=ast.literal_eval(n)
    except (ValueError,TypeError):continue
    if isinstance(value,dict) and value.get('name')=='qa_matrix':tools.append((n,value))
if len(tools)!=1:raise SystemExit('qa_schema_not_found')
n,spec=tools[0]
spec['description']='Responsive QA over six emulated profiles including tablet 768px, or exact PNG frames at scale 1. Reports geometry, clipping of required selectors, runtime errors and a11y signals. ok means the audit ran; verdict=needs-work is not a design pass. Use authorized URLs only.'
spec['inputSchema']['properties']['frames']={'type':'array','maxItems':8,'minItems':1,'description':'Optional exact-size PNG contracts; omit for the standard six-profile matrix. fit=true requires the whole document in the frame.',
    'items':{'type':'object','properties':{'name':{'type':'string'},'width':{'type':'integer'},'height':{'type':'integer'},'fit':{'type':'boolean'},'requiredSelectors':{'type':'array','items':{'type':'string'},'maxItems':30}},'required':['name','width','height','requiredSelectors']}}
lines=source.splitlines()
# Schema dictionaries in the supported owner adapter occupy complete source lines.
prefix=lines[n.lineno-1][:n.col_offset];suffix=lines[n.end_lineno-1][n.end_col_offset:]
if prefix.strip() or suffix.strip() not in ['',',']:raise SystemExit('unsupported_qa_schema_layout')
edits.append((n.lineno-1,n.end_lineno,prefix+repr(spec)+suffix))
for a,b,new in sorted(edits,reverse=True):lines[a:b]=new.splitlines()
candidate=('\n'.join(lines)+'\n').encode();compile(candidate,str(file),'exec')
repo=Path(__file__).resolve().parents[1]
candidates={file:candidate,owner/'mockup_quality.py':(repo/'runtime/tools/mockup_quality.py').read_bytes(),owner/'qa_matrix.cjs':(repo/'skills/harmonika-browser-qa/scripts/qa_matrix.cjs').read_bytes()}
compile(candidates[owner/'mockup_quality.py'],'mockup_quality.py','exec')
changes={p:data for p,data in candidates.items() if not p.exists() or p.read_bytes()!=data}
if not changes:print({'already_active':True,'updated_files':0});raise SystemExit(0)
snapshots={p:p.read_bytes() if p.exists() else None for p in changes}
if file.read_bytes()!=original:raise SystemExit('concurrent_edit')
backup=owner/'mockup-quality-backups'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f');backup.mkdir(parents=True,mode=0o700)
for p,old in snapshots.items():
    if old is not None:
        b=backup/p.name;b.write_bytes(old);b.chmod(0o600)
for p,data in changes.items():
    if (p.read_bytes() if p.exists() else None)!=snapshots[p]:raise SystemExit('concurrent_edit')
    tmp=p.with_suffix('.mockup-quality.tmp');tmp.write_bytes(data);tmp.chmod(0o600);tmp.replace(p)
print({'updated_files':len(changes),'mcp_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'private_backup':str(backup)})
