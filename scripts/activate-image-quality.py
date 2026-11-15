"""Activate only the owner image adapter; private backups never enter Git."""
import argparse
import ast
import datetime
import hashlib
from pathlib import Path

parser=argparse.ArgumentParser()
parser.add_argument('--owner',type=Path,default=Path.home()/'.config/harmonika-terminal')
args=parser.parse_args()
owner=args.owner.resolve();mcp=owner/'mcp_server.py';original=mcp.read_bytes()
source=original.decode();tree=ast.parse(source)
nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='tool_generate_fluxhd']
if len(nodes)!=1:raise SystemExit('image_adapter_not_found')
node=nodes[0];segment=ast.get_source_segment(source,node)
if 'from visual_media import ensure_media_port, png_result' not in segment and 'from image_generation import generate' not in segment:
    raise SystemExit('unsupported_image_adapter_preserved')
replacement='def tool_generate_fluxhd(args):\n    from image_generation import generate\n    return generate(args, OUTPUT_DIR, VISION_TOKEN)'
lines=source.splitlines();lines[node.lineno-1:node.end_lineno]=replacement.splitlines()
candidate=('\n'.join(lines)+'\n').encode();compile(candidate,str(mcp),'exec')
candidates={mcp:candidate}
for name in ('image_generation.py','image_anatomy.py','visual_media.py','media_tunnels.py'):
    file=Path(__file__).resolve().parents[1]/'runtime/tools'/name
    data=file.read_bytes();compile(data,str(file),'exec');candidates[owner/name]=data
changes={p:data for p,data in candidates.items() if not p.exists() or p.read_bytes()!=data}
if not changes:
    print({'already_active':True,'updated_files':0});raise SystemExit(0)
snapshots={p:p.read_bytes() if p.exists() else None for p in changes}
if mcp.read_bytes()!=original:raise SystemExit('concurrent_edit')
backup=owner/'image-quality-backups'/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
backup.mkdir(parents=True,mode=0o700)
for p,old in snapshots.items():
    if old is not None:
        b=backup/p.name;b.write_bytes(old);b.chmod(0o600)
for p,data in changes.items():
    if (p.read_bytes() if p.exists() else None)!=snapshots[p]:raise SystemExit('concurrent_edit')
    temporary=p.with_suffix('.image-quality.tmp');temporary.write_bytes(data);temporary.chmod(0o600);temporary.replace(p)
print({'updated_files':len(changes),'mcp_sha256':hashlib.sha256(mcp.read_bytes()).hexdigest(),'private_backup':str(backup)})
