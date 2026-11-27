import os
import sqlite3
from pathlib import Path

数据目录 = Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))
夹具目录 = Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))
库路径 = 数据目录 / "租约台.db"
_wal已开 = False


def 连接() -> sqlite3.Connection:
    global _wal已开
    数据目录.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(库路径, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # 两路扫描并发写入时，让后来的写连接排队等锁，而不是立刻 database is locked。
    conn.execute("PRAGMA busy_timeout = 8000")
    # WAL：读不阻塞写、写不阻塞读，双扫并写时只串行化写事务本身。库级持久属性，开一次即可。
    if not _wal已开:
        conn.execute("PRAGMA journal_mode = WAL")
        _wal已开 = True
    return conn