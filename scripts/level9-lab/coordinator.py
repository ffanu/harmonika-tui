"""Protected role identity/bootstrap; no model-supplied address or process PID."""
import inspect,os,signal,threading
from workflow_engine import WorkflowStore
from workflow_adapter import ChildClient
identity=os.environ['COORDINATOR_ID'];stop=threading.Event()
extra={'coordinator_id':identity,'lease_seconds':2.0} if 'coordinator_id' in inspect.signature(WorkflowStore).parameters else {}
store=WorkflowStore('/data/workflows.sqlite',ChildClient(coordinator_id=identity),schema_version=int(os.environ['WORKFLOW_SCHEMA']),**extra)
signal.signal(signal.SIGTERM,lambda *_:stop.set());store.start()
try:stop.wait()
finally:store.stop()
