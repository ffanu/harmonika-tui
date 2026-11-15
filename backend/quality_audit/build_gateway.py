"""Build at the physical Go source path and prove instrumentation was linked."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess


def build(source,overlay,output):
    physical=source.resolve(strict=True);environment=dict(os.environ);environment['PWD']=str(physical)
    subprocess.run(['go','build','-overlay',str(overlay.resolve()),'-o',str(output.resolve()),'.'],
                   cwd=physical,env=environment,check=True,capture_output=True,text=True)
    symbols=subprocess.run(['go','tool','nm',str(output.resolve())],check=True,capture_output=True,text=True).stdout
    if 'main.qualityFromEnvironment' not in symbols:
        raise ValueError('quality_instrumentation_not_linked')
    output.chmod(0o700)
    result={'build':'passed','physical_source_path':str(physical),
            'instrumentation_link_verified':True,'binary_sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
    (output.parent/'quality-build-proof.json').write_text(json.dumps(result,indent=2))
    (output.parent/'quality-build-proof.json').chmod(0o600)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--overlay',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    print(json.dumps(build(a.source,a.overlay,a.output)))
