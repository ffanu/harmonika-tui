"""Windows Job Object containment for children created by this toolkit only."""
import ctypes
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

