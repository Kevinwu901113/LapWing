import asyncio
import logging

logger = logging.getLogger("lapwing.memory.embedding_worker")


class EmbeddingWorker:
    """后台 embedding 生成器。扫描待处理的笔记并将其添加到向量存储中。"""

    def __init__(self, note_store, vector_store):
        self.note_store = note_store
        self.vector_store = vector_store

    async def process_pending(self):
        """处理所有 embedding_version=="pending" 的笔记。

        对每条笔记：
          1. await vector_store.add(note_id, content, metadata)
          2. note_store.mark_embedded(file_path, "v1")
        逐条捕获异常 — 记录警告并继续处理下一条。
        """
        notes = self.note_store.get_all_for_embedding()
        for note in notes:
            note_id = note["note_id"]
            file_path = note["file_path"]
            content = note["content"]
            metadata = note["meta"]
            try:
                await self.vector_store.add(
                    note_id=note_id,
                    content=content,
                    metadata=metadata,
                )
                self.note_store.mark_embedded(file_path, "v1")
            except Exception as e:
                logger.warning("笔记 %s embedding 失败: %s", note_id, e)

    async def run_loop(self, interval: int = 60):
        """每隔 `interval` 秒运行一次 process_pending()。无限循环。"""
        while True:
            try:
                await self.process_pending()
            except Exception as e:
                logger.warning("process_pending 运行异常: %s", e)
            await asyncio.sleep(interval)
