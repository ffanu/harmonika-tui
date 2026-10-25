"""Phase2 pure file operations inside an unpublished COW workspace generation."""
from .files import FileStore
from .phase2_foundation import plan
from .catalog import FOUNDATION_SKILLS

class EngineeringFileStore(FileStore):
    def execute(self,skill_id,args):
        if skill_id not in FOUNDATION_SKILLS:return super().execute(skill_id,args)
        proposed=plan(skill_id,args,self)
        results=[]
        for item in proposed['writes']:
            spec={'path':item['path'],'content':item['content']}
            if item['mode']=='replace':spec.update(overwrite=True,expected_sha256=item['expected_sha256'])
            results.append(super().execute('code.write',spec))
        return {**{k:v for k,v in proposed.items() if k not in ('writes','production_registered')},
                'status':'completed','files':results,'files_changed':len(results),
                'note':'Published by runtime COW transaction only after all changes succeed.'}
