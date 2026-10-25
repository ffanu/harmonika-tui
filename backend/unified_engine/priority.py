"""Bounded non-preemptive priority slots, FIFO per priority, cancellation-safe."""
import asyncio,heapq,itertools,time
from contextlib import asynccontextmanager

class PrioritySlots:
 def __init__(self,limit=2):self.limit=limit;self.active=0;self.waiters=[];self.granted=set();self.sequence=itertools.count()
 def pump(self):
  while self.active<self.limit and self.waiters:
   # Aging prevents a constant stream of higher-priority jobs from starving old jobs.
   aged=[(item[1],i) for i,item in enumerate(self.waiters) if time.monotonic()-item[2]>30 and not item[3].done()]
   if aged:
    _,i=min(aged);item=self.waiters[i];self.waiters[i]=self.waiters[-1];self.waiters.pop();heapq.heapify(self.waiters)
   else:item=heapq.heappop(self.waiters)
   future=item[3]
   if future.done():continue
   self.active+=1;self.granted.add(future);future.set_result(None)
 @asynccontextmanager
 async def slot(self,priority=3):
  future=asyncio.get_running_loop().create_future();heapq.heappush(self.waiters,(priority,next(self.sequence),time.monotonic(),future));self.pump()
  try:
   await future;yield
  finally:
   if future in self.granted:self.granted.remove(future);self.active-=1
   else:future.cancel()
   self.pump()
