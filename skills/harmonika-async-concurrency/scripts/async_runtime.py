"""Reference async controller; no network, arbitrary commands or user files."""
import asyncio
import json
import sys

class Controller:
    def __init__(self,capacity=16):
        if not isinstance(capacity,int) or not 1<=capacity<=4096:raise ValueError('invalid_capacity')
        self.events=asyncio.Queue(maxsize=capacity)
        self.generation=0;self.current=None;self.owned=set();self.closed=False

    def start(self,source,timeout=90):
        if self.closed:raise RuntimeError('controller_closed')
        if timeout<=0:raise ValueError('invalid_timeout')
        self.cancel();generation=self.generation
        task=asyncio.create_task(self._run(source,generation,timeout))
        self.current=task;self.owned.add(task);task.add_done_callback(self.owned.discard)
        return generation

    def cancel(self):
        self.generation+=1
        if self.current and not self.current.done():self.current.cancel()
        self.current=None
        while not self.events.empty():
            try:self.events.get_nowait()
            except asyncio.QueueEmpty:break

    async def _run(self,source,generation,timeout):
        async def emit(kind,text=None):
            if generation==self.generation:
                event={'generation':generation,'type':kind}
                if text is not None:event['text']=text
                await self.events.put(event)
        async def consume():
            await emit('started')
            async for chunk in source:
                if generation!=self.generation:return
                if not isinstance(chunk,str):raise ValueError('invalid_delta')
                # Bound each event as well as the number of queued events.
                for offset in range(0,len(chunk),4096):await emit('delta',chunk[offset:offset+4096])
            await emit('final')
        try:await asyncio.wait_for(consume(),timeout)
        except asyncio.CancelledError:raise
        except asyncio.TimeoutError:await emit('error','response_timeout')
        except Exception:await emit('error','response_unavailable')
        finally:
            closer=getattr(source,'aclose',None)
            if closer:
                try:await closer()
                except Exception:pass

    def poll(self):
        while True:
            try:event=self.events.get_nowait()
            except asyncio.QueueEmpty:return None
            if event['generation']==self.generation:return event

    async def close(self):
        self.closed=True;self.cancel()
        if self.owned:await asyncio.gather(*tuple(self.owned),return_exceptions=True)

async def self_test():
    controller=Controller(4);release=asyncio.Event();entered=asyncio.Event()
    async def held():
        entered.set();await release.wait();yield 'late'
    old=controller.start(held());await entered.wait()
    for _ in range(100):controller.poll()
    controller.cancel();controller.cancel()
    async def ready():yield 'Halo';yield ' Harmonika'
    new=controller.start(ready());output=[];terminal=False
    for _ in range(100):
        event=controller.poll()
        if event:
            assert event['generation']==new and event['generation']!=old
            if event['type']=='delta':output.append(event['text'])
            if event['type']=='final':terminal=True;break
        await asyncio.sleep(0)
    assert terminal and ''.join(output)=='Halo Harmonika'
    await controller.close();assert not controller.owned
    return {'ok':True,'provider':'Harmonika Ai','scope':'reference_controller','checks':['nonblocking_poll','stale_generation','repeated_stop','stream_final','cleanup']}

if __name__=='__main__':
    if sys.argv[1:]!=['self-test']:raise SystemExit('Usage: async_runtime.py self-test')
    print(json.dumps(asyncio.run(self_test())))
