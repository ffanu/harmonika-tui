"""Protected workflow coordinator bootstrap; own container lifecycle only."""
import os,signal,threading
from workflow_engine import WorkflowStore
from workflow_adapter import ChildClient
stop=threading.Event();store=WorkflowStore('/data/workflows.sqlite',ChildClient(),schema_version=int(os.environ['WORKFLOW_SCHEMA']))
signal.signal(signal.SIGTERM,lambda *_:stop.set());store.start()
try:stop.wait()
finally:store.stop()
