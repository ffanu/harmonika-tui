"""Keep generation stable; independently route vision to a private local worker."""
class WorkerRouter:
    def __init__(self,primary,vision):self.primary=primary;self.vision=vision
    @property
    def connection(self):return self.primary.connection
    async def start(self):
        await self.primary.start()
        try:await self.vision.start()
        except BaseException:await self.primary.close();raise
    async def close(self):
        try:await self.vision.close()
        finally:await self.primary.close()
    async def health(self):
        import asyncio
        a,b=await asyncio.gather(self.primary.health(),self.vision.health())
        return {'ready':a['ready'] or b['ready'],'generate':a['ready'] and a['generate'],'analyze':b['ready'] and b['analyze'],'references':a['ready'] and a['references'],'vision_local_only':True}
    async def run(self,job,store):return await (self.vision if job['kind']=='analyze' else self.primary).run(job,store)
    async def recover(self,job):return await (self.vision if job['kind']=='analyze' else self.primary).recover(job)
