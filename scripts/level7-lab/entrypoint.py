"""Immutable role selection; no shell/process orchestration inside runtime."""
import os,runpy
role=os.environ.get('LAB_ROLE')
if role not in ['api','worker','sink']:raise RuntimeError('invalid_role')
runpy.run_path('/app/'+{'api':'server.py','worker':'worker.py','sink':'effect_service.py'}[role],run_name='__main__')
