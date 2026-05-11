"""Kernel persistence stores: InterruptStore, EventLog.

Both are sync (sqlite3, not aiosqlite) per blueprint §8.2 / §9.2 —
low-volume access patterns (interrupts trigger only on owner-attention
events; events log every action but each write is fast).

ActionExecutor's InterruptStoreProtocol / EventLogProtocol expose sync
methods; these classes implement them.
"""
