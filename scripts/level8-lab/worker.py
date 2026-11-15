"""Immutable worker bootstrap, one Queue per independent container."""
import os,signal,threading
from engine import Queue
from effect_connector import EffectConnector
worker=os.environ['WORKER_ID'];stop=threading.Event();q=Queue('/data/jobs.sqlite',EffectConnector('/data',worker),worker_id=worker,lease_seconds=3.0)
signal.signal(signal.SIGTERM,lambda *_:stop.set());q.start()
try:stop.wait()
finally:q.stop()
