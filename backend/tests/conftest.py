import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db, seed


@pytest.fixture()
def 独立库(tmp_path, monkeypatch):
    """每个测试一个独立 SQLite 库，运行期切换路径，杜绝跨文件串库。"""
    库文件 = tmp_path / "租约台.db"
    monkeypatch.setattr(db, "库路径", 库文件)
    seed.初始化库()
    return 库文件
