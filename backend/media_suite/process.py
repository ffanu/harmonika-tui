"""Bounded child execution. Windows Job Object contains venv launcher descendants."""
import ctypes,json,os,subprocess,tempfile,time
from pathlib import Path
from contracts import Failure
LIMIT=36*1024*1024
class WindowsJob:
 def __init__(self,process):
  from ctypes import wintypes as w
  class IO(ctypes.Structure):_fields_=[(n,ctypes.c_ulonglong) for n in ('a','b','c','d','e','f')]
  class Basic(ctypes.Structure):
   _fields_=[('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),('min',ctypes.c_size_t),('max',ctypes.c_size_t),('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('schedule',w.DWORD)]
  class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
  self.k=ctypes.WinDLL('kernel32',use_last_error=True);self.k.CreateJobObjectW.restype=w.HANDLE;self.k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
  self.k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];self.k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE];self.k.CloseHandle.argtypes=[w.HANDLE]
  self.handle=self.k.CreateJobObjectW(None,None)
  if not self.handle:raise OSError('job_create_failed')
  info=Extended();info.basic.flags=0x2000
  if not self.k.SetInformationJobObject(self.handle,9,ctypes.byref(info),ctypes.sizeof(info)) or not self.k.AssignProcessToJobObject(self.handle,w.HANDLE(int(process._handle))):
   self.close();raise OSError('job_assign_failed')
  nt=ctypes.WinDLL('ntdll');nt.NtResumeProcess.argtypes=[w.HANDLE];nt.NtResumeProcess.restype=ctypes.c_long
  if nt.NtResumeProcess(w.HANDLE(int(process._handle)))!=0:self.close();raise OSError('process_resume_failed')
 def close(self):
  if self.handle:self.k.CloseHandle(self.handle);self.handle=None

def execute(python,script,payload,timeout,cwd):
 env={k:v for k,v in os.environ.items() if k not in ('PYTHONHOME','PYTHONPATH','PYTHONSTARTUP','OPENAI_API_KEY','ANTHROPIC_API_KEY')}
 env.update(PYTHONNOUSERSITE='1',OMP_NUM_THREADS='4',MKL_NUM_THREADS='4',HF_HUB_OFFLINE='1',HF_HUB_DISABLE_TELEMETRY='1',ANONYMIZED_TELEMETRY='false')
 raw=json.dumps(payload).encode();job=None;proc=None
 with tempfile.TemporaryFile() as source,tempfile.TemporaryFile() as output:
  source.write(raw);source.seek(0)
  try:
   proc=subprocess.Popen([str(python),'-I',str(script)],stdin=source,stdout=output,stderr=subprocess.DEVNULL,cwd=cwd,env=env,creationflags=0x4 if os.name=='nt' else 0,start_new_session=os.name!='nt')
   if os.name=='nt':job=WindowsJob(proc)
   start=time.monotonic()
   while proc.poll() is None:
    if output.tell()>LIMIT:raise Failure('output_limit')
    if time.monotonic()-start>timeout:raise Failure('engine_timeout')
    time.sleep(.05)
   if output.tell()>LIMIT:raise Failure('output_limit')
   output.seek(0)
   try:result=json.load(output)
   except (ValueError,UnicodeError):raise Failure('invalid_engine_payload') from None
   if not isinstance(result,dict) or result.get('status') not in ('completed','failed'):raise Failure('invalid_engine_payload')
   if proc.returncode and result.get('status')=='completed':raise Failure('engine_failed')
   return result
  finally:
   if job:job.close() # kills only this invocation and its descendants, including venv launcher.
   if proc is not None and proc.poll() is None:
    if os.name!='nt':
     import signal
     os.killpg(proc.pid,signal.SIGKILL)
    else:proc.kill()
    proc.wait(timeout=10)
