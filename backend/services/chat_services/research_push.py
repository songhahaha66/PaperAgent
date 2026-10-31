"""Coalesce committed state notifications into the same snapshot as the REST API."""
import asyncio
import logging

logger=logging.getLogger(__name__)


class ResearchPush:
    def __init__(self, snapshot, publish, *, delay=.1):
        self.snapshot,self.publish,self.delay=snapshot,publish,delay
        self.task=None;self.dirty=False;self.closed=False

    def schedule(self):
        if self.closed:return
        self.dirty=True
        if self.task is None or self.task.done():self.task=asyncio.create_task(self.run())

    async def run(self):
        while self.dirty and not self.closed:
            await asyncio.sleep(self.delay)
            self.dirty=False
            try:
                value=await asyncio.to_thread(self.snapshot)
                if not self.closed:await self.publish('research_snapshot',value.model_dump())
            except Exception:
                logger.warning('研究快照推送未完成，将由重连或下次通知校准',exc_info=True)
                if not self.closed:await self.publish('research_state',{})

    async def close(self):
        self.closed=True
        if self.task and not self.task.done():
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
