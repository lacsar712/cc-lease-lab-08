"""测匣重入甲：同一逾期租约重复扫描入匣，第二次只能折进原行并标明重入已折叠。

失败测甲（回收服务侧）：旧实现 check-then-insert 无唯一约束，
重复入匣会在回收匣长出第二行待回收记录。
"""

from datetime import datetime, timedelta, timezone

from app.db import 连接
from app.services import 回收, 租约, 说明


def _造逾期租约(租约号: str) -> None:
    租约.申领(
        {"池键": "pool-compute", "租户": "reentry-probe", "租期分钟": 5,
         "立即生效": True, "租约号": 租约号}
    )
    过期 = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = 连接()
    try:
        conn.execute("UPDATE 租约 SET 到期时刻=? WHERE 租约号=?", (过期, 租约号))
        conn.commit()
    finally:
        conn.close()


def test_重复扫描同一逾期只长一行且标明重入已折叠(独立库):
    租约号 = "ls-reentry-01"
    _造逾期租约(租约号)

    第一次 = 回收.入匣({"租约号": 租约号, "原因": "到期未续"})
    第二次 = 回收.入匣({"租约号": 租约号, "原因": "到期未续"})
    第三次 = 回收.入匣({"租约号": 租约号, "原因": "到期未续"})

    # 骨签：同一逾期只能一行匣——三次入匣返回同一个回收号。
    assert 第二次["回收号"] == 第一次["回收号"]
    assert 第三次["回收号"] == 第一次["回收号"]

    conn = 连接()
    try:
        待回收行 = conn.execute(
            "SELECT * FROM 回收匣 WHERE 租约号=? AND 状态='待回收'", (租约号,)
        ).fetchall()
        全量行 = conn.execute(
            "SELECT * FROM 回收匣 WHERE 租约号=?", (租约号,)
        ).fetchall()
    finally:
        conn.close()

    assert len(待回收行) == 1, "同一逾期在匣里落出了两行待回收记录"
    assert len(全量行) == 1, "重入不得新增任何匣行（含历史行）"

    行 = dict(待回收行[0])
    assert 行["重入标记"] == "重入已折叠"
    assert 行["重入次数"] == 2

    # 台面轨只见一行；折叠标记随可见行带出。
    台面 = [r for r in 说明.回收列表() if r["租约号"] == 租约号]
    assert len(台面) == 1
    assert 台面[0]["重入标记"] == "重入已折叠"
    assert 台面[0]["重入次数"] == 2

    # 底账轨：两次重入各留一笔拒回刷指纹。
    底账 = 说明.重入账列表()
    本租约指纹 = [r for r in 底账 if r["租约号"] == 租约号]
    assert len(本租约指纹) == 2
    assert all(r["处置"] == "拒回刷" for r in 本租约指纹)
    assert all(r["回收号"] == 第一次["回收号"] for r in 本租约指纹)
    assert all("指纹号" in r for r in 本租约指纹)

    # 两套读集合严禁并轨：台面读不到任何指纹号，底账不返回台面匣行字段集。
    台面指纹 = [r for r in 说明.回收列表() if "指纹号" in r]
    assert 台面指纹 == []
    assert all("入匣时" not in r for r in 底账)
