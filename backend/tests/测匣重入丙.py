"""测匣重入丙：两路扫描/两个会话同时写入同一逾期租约的争用测试。

争用失败测（并发侧）：旧实现无锁 check-then-insert，两路同时写会各留
生效痕迹——回收匣双行、池占用被双释。本测用真线程各持独立连接复现。
"""

import threading
from datetime import datetime, timedelta, timezone

from app.db import 连接
from app.services import 回收, 租约, 资源, 说明


def _逾期时刻() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _备赛场(独立库) -> None:
    资源.登记({"池键": "pool-reentry", "名称": "重入观察池", "容量": 20})
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = 连接()
    try:
        for lid in ("ls-race-a", "ls-race-b", "ls-race-c"):
            conn.execute(
                """INSERT INTO 租约(租约号,池键,租户,状态,申领时,生效时,到期时刻,续约次数,更新时)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (lid, "pool-reentry", "race-team", "生效中", now, now, _逾期时刻(), 0, now),
            )
        conn.commit()
    finally:
        conn.close()


def test_两路并写入匣只留一行生效痕迹(独立库):
    _备赛场(独立库)
    租约号 = "ls-race-a"
    并发 = 8
    barrier = threading.Barrier(并发)
    errors: list[Exception] = []

    def worker():
        try:
            barrier.wait(timeout=10)
            回收.入匣({"租约号": 租约号, "原因": "到期未续"})
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(并发)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == [], f"并发入匣出现异常: {errors!r}"

    conn = 连接()
    try:
        待回收 = conn.execute(
            "SELECT * FROM 回收匣 WHERE 租约号=? AND 状态='待回收'", (租约号,)
        ).fetchall()
        全量 = conn.execute("SELECT * FROM 回收匣 WHERE 租约号=?", (租约号,)).fetchall()
    finally:
        conn.close()

    assert len(待回收) == 1, "两路并写在匣里留了多行待回收"
    assert len(全量) == 1, "争用产生了重复匣行"
    assert dict(待回收[0])["重入次数"] == 并发 - 1

    指纹 = [r for r in 说明.重入账列表() if r["租约号"] == 租约号]
    assert len(指纹) == 并发 - 1, "落败一路必须折进底账指纹，不得各留生效痕迹"


def test_双会话并扫池占用只释一次(独立库):
    _备赛场(独立库)
    # 预置 10 格占用；a/b/c 三条均逾期，每个租约只应释放一格（共 3 格），扫后为 7。
    conn = 连接()
    try:
        conn.execute("UPDATE 资源池 SET 已占=10 WHERE 池键='pool-reentry'")
        conn.commit()
    finally:
        conn.close()

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def scan():
        try:
            barrier.wait(timeout=10)
            租约.扫描逾期()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=scan)
    t2 = threading.Thread(target=scan)
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert errors == [], f"并发扫描出现异常: {errors!r}"

    assert 资源.一条("pool-reentry")["已占"] == 7, "双扫双释，池占用与逾期实况不符"

    conn = 连接()
    try:
        for lid in ("ls-race-a", "ls-race-b", "ls-race-c"):
            匣行 = conn.execute(
                "SELECT * FROM 回收匣 WHERE 租约号=? AND 状态='待回收'", (lid,)
            ).fetchall()
            状态 = conn.execute(
                "SELECT 状态 FROM 租约 WHERE 租约号=?", (lid,)
            ).fetchone()[0]
            assert len(匣行) == 1, f"{lid} 并发扫描后出现重复匣行"
            assert 状态 == "已回收"
    finally:
        conn.close()
