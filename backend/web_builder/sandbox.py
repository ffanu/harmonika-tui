"""Reuse audited container/volume lifecycle with explicit web-browser resource bounds."""
try:
 from coding_runtime.sandbox import DockerSandbox
except ModuleNotFoundError as e:
 if e.name!='coding_runtime':raise
 from ..coding_runtime.sandbox import DockerSandbox
class WebSandbox(DockerSandbox):
 def __init__(self):super().__init__(image='harmonika-web-builder:20261024',phase2_runtime=True,max_active=2)
 def _run_argv(self,*args,**kwargs):
  argv=super()._run_argv(*args,**kwargs)
  replace={'--pids-limit=64':'--pids-limit=256','--memory=512m':'--memory=1g','--memory-swap=512m':'--memory-swap=1g','nofile=128':'nofile=512','--tmpfs=/tmp:rw,noexec,nosuid,size=16m':'--tmpfs=/tmp:rw,noexec,nosuid,size=128m'}
  return [replace.get(x,x) for x in argv]
 def _volume_create_argv(self,volume):
  argv=super()._volume_create_argv(volume)
  return [x.replace('o=size=32m,','o=size=256m,') for x in argv]
 def limits(self):
  out=super().limits();out['volume']['opts']=out['volume']['opts'].replace('size=32m,','size=256m,');out['container']={**out['container'],'pids_limit':256,'memory':'1g','memory_swap':'1g','ulimits':{'nofile':512,'core':0}};return out
