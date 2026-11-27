"""测匣重入乙：互证读侧必须把重入影子与台面待回收区分开。

失败测乙（互证服务侧，读引用不同）：旧读法把同一租约的重入痕迹
误当成两笔待回收，互证报告的待回收数与逾期实况不符。
本测不与回收服务的写校验共用一处判断——只读两套独立读集合互核。
"""

from datetime import datetime, timedelta, timezone

from app.db import 连接
from app.services import 互证, 回收, 租约, 说明


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


def test_互证不把重入影子误标成两笔待回收(独立库):
    # 基线台面：种子库自带一行待回收（rc-9001）。
    基线台面 = [r for r in 说明.回收列表() if r["状态"] == "待回收"]
    基线待回收 = len(基线台面)

    for lid in ("ls-b-reentry-01", "ls-b-reentry-02"):
        _造逾期租约(lid)

    # 占用基线在申领之后、入匣之前取：申领预占的两格属正常，
    # 我们要核的是之后的「重入回刷」不再动池占用。
    conn = 连接()
    try:
        基线池已占 = conn.execute("SELECT COALESCE(SUM(已占),0) FROM 资源池").fetchone()[0]
        基线未回收 = conn.execute(
            "SELECT COUNT(*) FROM 租约 WHERE 状态 IN ('生效中','已续约','待生效')"
        ).fetchone()[0]
    finally:
        conn.close()

    # 租约一重入三次（1 行台面 + 2 笔底账指纹），租约二只入匣一次。
    for _ in range(3):
        回收.入匣({"租约号": "ls-b-reentry-01", "原因": "到期未续"})
    回收.入匣({"租约号": "ls-b-reentry-02", "原因": "到期未续"})

    # 台面轨：按租约号去重后的待回收，每个租约恰好一行。
    台面待回收 = [r for r in 说明.回收列表() if r["状态"] == "待回收"]
    重入组 = [r for r in 台面待回收 if r["租约号"] == "ls-b-reentry-01"]
    assert len(重入组) == 1, "重入影子在台面长出了第二行待回收"
    assert len({r["租约号"] for r in 台面待回收}) == len(台面待回收), "台面待回收未按租约号去重"
    # 四次入匣调用只应净增两行台面（每租约一行）。
    assert len(台面待回收) == 基线待回收 + 2

    # 底账轨独立读：指纹存在，但绝不能被待回收计数引用。
    指纹 = 说明.重入账列表()
    assert len(指纹) == 2
    assert all(r["处置"] == "拒回刷" for r in 指纹)

    报告 = 互证.跑一次()
    # 互证只引用台面轨：待回收数等于去重租约数，而不是入匣调用次数。
    assert 报告["待回收数"] == 基线待回收 + 2
    assert 报告["待回收数"] == len({r["租约号"] for r in 台面待回收})

    # 概览卡片同核：回收匣待处置与台面行集合同核。
    概览 = 说明.概览()
    assert 概览["回收匣待处置"] == len(台面待回收)

    conn = 连接()
    try:
        # 占用轨独立读，不与匣行并查：入匣本身不触碰池占用，与基线完全一致。
        池已占 = conn.execute("SELECT COALESCE(SUM(已占),0) FROM 资源池").fetchone()[0]
        未回收租约 = conn.execute(
            "SELECT COUNT(*) FROM 租约 WHERE 状态 IN ('生效中','已续约','待生效')"
        ).fetchone()[0]
    finally:
        conn.close()
    assert 池已占 == 基线池已占, "重入回刷误动了池占用"
    assert 未回收租约 == 基线未回收
    # 重入前后两轨各自的差值都为零：匣行轨与占用轨不并查、互不串数。
    assert (池已占 - 未回收租约) == (基线池已占 - 基线未回收)
