#!/usr/bin/env python3
"""Native isolated session runner; no agent process signals or arbitrary resume."""
import argparse,fcntl,hashlib,json,os,re,subprocess,time
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--resume',action='store_true');p.add_argument('--label',required=True);p.add_argument('--prompt',type=Path);a=p.parse_args()
 if not re.fullmatch(r'[a-z0-9-]{1,48}',a.label):raise RuntimeError('invalid_label')
 root=a.root;project=root/'project';meta=json.loads((root/'grade.json').read_text());out=root/(a.label+'-native.jsonl')
 lock=(root/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 if out.exists():raise RuntimeError('existing_run')
 if a.resume:
  if not meta.get('native_session_id'):raise RuntimeError('missing_native_checkpoint_session')
  if 'checkpoint_sha256' in meta and (not (project/'CHECKPOINT.json').is_file() or hashlib.sha256((project/'CHECKPOINT.json').read_bytes()).hexdigest()!=meta['checkpoint_sha256']):raise RuntimeError('checkpoint_changed_before_resume')
 if not all((project/n).is_file() and hashlib.sha256((project/n).read_bytes()).hexdigest()==h for n,h in meta['protected'].items()):raise RuntimeError('protected_changed')
 prompt='Kerjakan TURN A README.md Level8 seluruhnya. Implementasikan workflow_engine.WorkflowStore: DAG persisten, validasi/tenant/idempotency/admission atomic, checkpoint dispatch sebelum network, recovery child key yang sama dan validasi artifact. Queue/adapter/transports dari Level7 dilindungi; jangan edit. Tambah regresi, jalankan tes lokal, perbarui Dockerfile memakai BUILD-FILES.json, build/deploy workflow-v1 sekali via tools/ops.py. Stop coordinator lalu jalankan workflow-burst, start dan verifikasi semua accepted task/job/effect/artifact. Buat checkpoint-workflow sesuai README dan simpan CHECKPOINT.json/HASIL.md. Jangan TURN B/fault lanjutan, jangan produksi/SSH/Docker langsung atau mencari penilai independen.'
 if a.resume:prompt='Lanjutkan TURN B CHANGE.md Level8 pada sesi yang SAMA. Migrasi schema2 atomic dengan failure hook, priority/pause/resume persisten dan kompatibilitas checkpoint. Tambah regresi, tes, build/deploy workflow-v2 sekali. Jalankan seluruh drill aktual via tools/ops.py: child ACK hilang+lookup503, coordinator crash42 setelah child diterima sebelum checkpoint, bad artifact, sink offline, pause/restart/resume, queued/running cancellation, migration-drill pada snapshot v1 dan priority ordering; ulangi workflow-burst pada image final setelah semua pekerjaan settle. Verifikasi source/image, child identity, artifact dan audit mekanis; jangan reset fault/replay mutasi. Rollback kode settled v1 lalu restore v2 tanpa rollback database. Simpan HASIL.md/RESULT.json sesuai scope dan batas; kalau bug nyata koreksi revisi immutable, pertahankan kegagalan awal. Tidak ada perubahan produksi/RTX atau klaim tes independen.'
 if a.prompt:prompt=a.prompt.read_text()
 cmd=['harmonika','run','--standalone','--auto','--format','json','--model','harmonika/harmonika-ai']
 if a.resume:cmd+=['--session',meta['native_session_id']]
 env=os.environ.copy()
 for k in ['XDG_DATA_HOME','XDG_STATE_HOME','XDG_CACHE_HOME']:
  d=root/k.lower();d.mkdir(mode=0o700,exist_ok=True);env[k]=str(d)
 started=time.monotonic()
 with out.open('w') as f:
  out.chmod(0o600);proc=subprocess.Popen(cmd+[prompt],cwd=project,env=env,stdout=f,stderr=subprocess.STDOUT);print(json.dumps({'label':a.label,'native_pid':proc.pid}),flush=True);code=proc.wait()
 ev=[]
 for line in out.read_text().splitlines():
  try:ev.append(json.loads(line))
  except ValueError:pass
 sid=next((x.get('sessionID') for x in ev if x.get('sessionID')),None)
 if not a.resume:meta['native_session_id']=sid
 if (project/'CHECKPOINT.json').exists() and 'checkpoint_sha256' not in meta:meta['checkpoint_sha256']=hashlib.sha256((project/'CHECKPOINT.json').read_bytes()).hexdigest()
 (root/'grade.json').write_text(json.dumps(meta,indent=2))
 result={'label':a.label,'exit_code':code,'same_session':bool(sid) and sid==meta.get('native_session_id'),'native_errors':sum(x.get('type')=='error' for x in ev),'tool_count':sum(x.get('type')=='tool_use' for x in ev),'duration_seconds':round(time.monotonic()-started,1)}
 (root/(a.label+'-run.json')).write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
if __name__=='__main__':main()
