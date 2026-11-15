#!/usr/bin/env python3
import argparse,fcntl,hashlib,json
from pathlib import Path
def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--passed-report',type=Path,required=True);a=p.parse_args();root=a.root;project=root/'project';lock=(root/'native.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);v=json.loads(a.passed_report.read_text());assert v['passed'] and v['phase']=='v1';meta=json.loads((root/'grade.json').read_text());assert not (project/'CHANGE.md').exists();assert hashlib.sha256((project/'CHECKPOINT.json').read_bytes()).hexdigest()==meta['checkpoint_sha256'];(project/'CHANGE.md').write_bytes((Path(__file__).parent/'level7-lab/CHANGE.md').read_bytes());meta['protected']['CHANGE.md']=hashlib.sha256((project/'CHANGE.md').read_bytes()).hexdigest();(root/'grade.json').write_text(json.dumps(meta,indent=2));print('{"turn_b_ready":true}')
if __name__=='__main__':main()
