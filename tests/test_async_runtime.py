import asyncio
import importlib.util
from pathlib import Path
import unittest

path=Path(__file__).resolve().parent.parent/'skills/harmonika-async-concurrency/scripts/async_runtime.py'
spec=importlib.util.spec_from_file_location('async_runtime',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Runtime(unittest.IsolatedAsyncioTestCase):
    async def test_reference_nonblocking_stale_generation_cleanup(self):self.assertTrue((await m.self_test())['ok'])
    async def test_full_queue_is_bounded_and_close_unblocks_worker(self):
        controller=m.Controller(2)
        async def source():
            for _ in range(1000):yield 'chunk'
        controller.start(source())
        for _ in range(10):await asyncio.sleep(0)
        self.assertLessEqual(controller.events.qsize(),2)
        await asyncio.wait_for(controller.close(),1);self.assertFalse(controller.owned)
    async def test_exception_redacted(self):
        controller=m.Controller()
        async def source():
            raise RuntimeError('sensitive-provider-details');yield 'never'
        controller.start(source());events=[]
        for _ in range(30):
            event=controller.poll()
            if event:events.append(event)
            await asyncio.sleep(0)
        self.assertTrue(any(e.get('text')=='response_unavailable' for e in events));self.assertNotIn('sensitive',str(events))
        await controller.close()
    async def test_close_forbids_new_request(self):
        controller=m.Controller();await controller.close()
        async def source():yield 'x'
        stream=source()
        with self.assertRaisesRegex(RuntimeError,'closed'):controller.start(stream)
        await stream.aclose()
    async def test_total_timeout_emits_error_without_claiming_final(self):
        controller=m.Controller();wait=asyncio.Event()
        async def source():await wait.wait();yield 'never'
        controller.start(source(),timeout=.01);await asyncio.sleep(.03)
        events=[]
        while True:
            event=controller.poll()
            if event is None:break
            events.append(event)
        self.assertTrue(any(e.get('text')=='response_timeout' for e in events));self.assertFalse(any(e['type']=='final' for e in events))
        await controller.close()

if __name__=='__main__':unittest.main()
