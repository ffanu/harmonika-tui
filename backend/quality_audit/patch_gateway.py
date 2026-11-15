"""Prepare a Go overlay; never overwrite or restart the active gateway."""
import argparse
import hashlib
import json
from pathlib import Path


def prepare(source,output):
    source=source.resolve();output.mkdir(parents=True,exist_ok=True,mode=0o700);output.chmod(0o700)
    baseline={};replacements={}
    def edit(name,changes):
        path=source/name;original=path.read_text();value=original
        baseline[str(path)]=hashlib.sha256(path.read_bytes()).hexdigest()
        for old,new in changes:
            if value.count(old)!=1:raise ValueError('gateway_anchor_mismatch_'+name)
            value=value.replace(old,new,1)
        target=output/name;target.write_text(value);target.chmod(0o600);replacements[str(path)]=str(target)
    edit('main.go',[
        ('Handler:           secureHeaders(mux),','Handler:           secureHeaders(qualityFromEnvironment(mux)),'),
        ('\tif !a.verifyAndBindDevice(w, r, out) {','\tqualityBind(r, out.ID)\n\tif !a.verifyAndBindDevice(w, r, out) {'),
        ('\tif status, code := publicAIChatInputError(input); status != 0 {','\tqualityChatInput(r, input)\n\tif status, code := publicAIChatInputError(input); status != 0 {'),
        ('\tvar input publicAINativeChatRequest\n','\tvar input publicAINativeChatRequest\n\tdefer func() { qualityQuestion(r, input.SessionID, input.Message) }()\n'),
    ])
    edit('public_ai_auto_skills.go',[
        ('\t\t\tresult, err := autoSkillExecute(ctx, tok.ID, bound)',
         '\t\t\tqualityStarted := time.Now()\n\t\t\tresult, err := autoSkillExecute(ctx, tok.ID, bound)\n\t\t\tqualityRecordStep(ctx, c.Function.Name, qualityStarted, err != nil)'),
    ])
    for name in ('public_quality_audit.go','public_quality_binding.go'):
        if (source/name).exists():raise ValueError('gateway_module_already_exists')
        target=output/name;target.write_bytes((Path(__file__).parent/name).read_bytes());target.chmod(0o600)
        replacements[str(source/name)]=str(target)
    (output/'overlay.json').write_text(json.dumps({'Replace':replacements}));(output/'overlay.json').chmod(0o600)
    manifest={'baseline':baseline,'candidate':{str(source/name):hashlib.sha256((output/name).read_bytes()).hexdigest() for name in ('main.go','public_ai_auto_skills.go','public_quality_audit.go','public_quality_binding.go')},
              'active_files_modified':False,'active_service_restarted':False}
    (output/'manifest.json').write_text(json.dumps(manifest,indent=2));(output/'manifest.json').chmod(0o600)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    prepare(a.source,a.output);print(json.dumps({'overlay_ready':True,'active_files_modified':False,'active_service_restarted':False}))
