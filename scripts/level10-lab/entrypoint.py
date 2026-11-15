import os,runpy,crash_guard
crash_guard.start()
role=os.environ.get('LAB_ROLE');paths={'api':'server.py','worker':'worker.py','sink':'effect_service.py','coordinator':'coordinator.py','proxy':'proxy.py'}
if role not in paths:raise RuntimeError('invalid_role')
runpy.run_path('/app/'+paths[role],run_name='__main__')
