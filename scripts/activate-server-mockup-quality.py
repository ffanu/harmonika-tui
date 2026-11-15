"""Update only the scoped hot-read mockup guidance, preserving all other instructions."""
from pathlib import Path
import datetime
import hashlib

target=Path('/etc/harmonika-ai/team-system.md');original=target.read_bytes()
guide=Path('/opt/harmonika-ai/visual-quality/MOCKUP-QUALITY.md').read_text().strip()
begin='<!-- HARMONIKA_MOCKUP_QUALITY_BEGIN -->';end='<!-- HARMONIKA_MOCKUP_QUALITY_END -->'
source=original.decode()
if source.count(begin)!=source.count(end) or source.count(begin)>1:raise SystemExit('invalid_mockup_marker_preserved')
block=begin+'\n'+guide+'\n'+end
if begin in source:
    a=source.index(begin);b=source.index(end,a)+len(end);candidate=source[:a]+block+source[b:]
else:candidate=source.rstrip()+'\n\n'+block+'\n'
data=candidate.encode()
if data==original:print({'already_active':True});raise SystemExit(0)
if target.read_bytes()!=original:raise SystemExit('concurrent_edit')
backup=Path('/root/harmonika-mockup-quality-backups')/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
backup.mkdir(parents=True,mode=0o700);saved=backup/target.name;saved.write_bytes(original);saved.chmod(0o600)
tmp=target.with_suffix('.mockup-quality.tmp');tmp.write_bytes(data);tmp.chmod(target.stat().st_mode&0o777)
if target.read_bytes()!=original:tmp.unlink();raise SystemExit('concurrent_edit')
tmp.replace(target)
print({'mockup_guidance_active':True,'service_restart_required':False,'sha256':hashlib.sha256(data).hexdigest()})
