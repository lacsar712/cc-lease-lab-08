from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 用户 (
  账号 TEXT PRIMARY KEY,
  密码哈希 TEXT NOT NULL,
  角色 TEXT NOT NULL DEFAULT '管理员'
);
CREATE TABLE IF NOT EXISTS 租账规 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 资源池 (
  池键 TEXT PRIMARY KEY,
  名称 TEXT NOT NULL,
  容量 INTEGER NOT NULL DEFAULT 100,
  已占 INTEGER NOT NULL DEFAULT 0,
  启用 INTEGER NOT NULL DEFAULT 1,
  说明 TEXT NOT NULL DEFAULT '',
  更新时 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 租约 (
  租约号 TEXT PRIMARY KEY,
  池键 TEXT NOT NULL,
  租户 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '待生效',
  申领时 TEXT NOT NULL,
  生效时 TEXT NOT NULL DEFAULT '',
  到期时刻 TEXT NOT NULL,
  续约次数 INTEGER NOT NULL DEFAULT 0,
  更新时 TEXT NOT NULL,
  FOREIGN KEY (池键) REFERENCES 资源池(池键)
);
CREATE TABLE IF NOT EXISTS 心跳 (
  心跳号 TEXT PRIMARY KEY,
  租约号 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  结果 TEXT NOT NULL DEFAULT '成功',
  延迟毫秒 INTEGER NOT NULL DEFAULT 0,
  说明 TEXT NOT NULL DEFAULT '',
  FOREIGN KEY (租约号) REFERENCES 租约(租约号)
);
CREATE TABLE IF NOT EXISTS 回收匣 (
  回收号 TEXT PRIMARY KEY,
  租约号 TEXT NOT NULL,
  池键 TEXT NOT NULL,
  租户 TEXT NOT NULL,
  原因 TEXT NOT NULL DEFAULT '到期未续',
  状态 TEXT NOT NULL DEFAULT '待回收',
  入匣时 TEXT NOT NULL,
  处置时 TEXT NOT NULL DEFAULT '',
  重入次数 INTEGER NOT NULL DEFAULT 0,
  重入标记 TEXT NOT NULL DEFAULT ''
);
-- 唯一索引由 _迁移旧库 在折叠完历史重复行后再建，避免旧库建索引即冲突。
-- 底账轨：重入指纹只读归档，不参与台面列表与待回收计数。
CREATE TABLE IF NOT EXISTS 匣重入账 (
  指纹号 TEXT PRIMARY KEY,
  回收号 TEXT NOT NULL,
  租约号 TEXT NOT NULL,
  池键 TEXT NOT NULL,
  租户 TEXT NOT NULL,
  原因 TEXT NOT NULL DEFAULT '',
  时刻 TEXT NOT NULL,
  处置 TEXT NOT NULL DEFAULT '拒回刷',
  FOREIGN KEY (租约号) REFERENCES 租约(租约号)
);
CREATE TABLE IF NOT EXISTS 互证报告 (
  报告号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  结论 TEXT NOT NULL,
  差异摘要 TEXT NOT NULL DEFAULT '',
  租约总数 INTEGER NOT NULL,
  生效中数 INTEGER NOT NULL,
  待回收数 INTEGER NOT NULL,
  池已占合计 INTEGER NOT NULL,
  是否对齐 INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS 操作留痕 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  时刻 TEXT NOT NULL,
  操作 TEXT NOT NULL,
  租约号 TEXT NOT NULL DEFAULT '',
  池键 TEXT NOT NULL DEFAULT '',
  详情 TEXT NOT NULL DEFAULT ''
);
"""


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _留痕(conn, 操作: str, 租约号: str = "", 池键: str = "", 详情: str = "") -> None:
    conn.execute(
        "INSERT INTO 操作留痕(时刻,操作,租约号,池键,详情) VALUES (?,?,?,?,?)",
        (此刻(), 操作, 租约号, 池键, 详情),
    )


def 初始化库() -> None:
    conn = 连接()
    try:
        conn.executescript(建表)
        n = conn.execute("SELECT COUNT(*) FROM 用户").fetchone()[0]
        if n == 0:
            conn.execute(
                "INSERT INTO 用户(账号,密码哈希,角色) VALUES (?,?,?)",
                ("admin", "123456", "管理员"),
            )
            规 = [
                ("默认租期分钟", "120"),
                ("心跳宽限分钟", "15"),
                ("回收策略", "到期进匣待处置"),
                ("允许超额申领", "0"),
            ]
            for 键, 值 in 规:
                conn.execute("INSERT INTO 租账规(键,值) VALUES (?,?)", (键, 值))
            t0 = datetime.now(timezone.utc)
            def iso(dt):
                return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            池们 = [
                ("pool-compute", "计算槽池", 64, 12, 1, "GPU 切片共享池"),
                ("pool-storage", "对象存储池", 200, 45, 1, "温存桶配额"),
                ("pool-net", "出口带宽池", 80, 30, 1, "跨区带宽租约"),
                ("pool-db", "只读连接池", 32, 8, 1, "分析库连接"),
                ("pool-msg", "消息主题池", 120, 0, 0, "维护中暂禁申领"),
            ]
            for 池键, 名称, 容量, 已占, 启用, 说明 in 池们:
                conn.execute(
                    """INSERT INTO 资源池(池键,名称,容量,已占,启用,说明,更新时)
                       VALUES (?,?,?,?,?,?,?)""",
                    (池键, 名称, 容量, 已占, 启用, 说明, iso(t0)),
                )

            租约样 = [
                (
                    "ls-active-01",
                    "pool-compute",
                    "team-ml",
                    "生效中",
                    iso(t0 - timedelta(hours=2)),
                    iso(t0 - timedelta(hours=2)),
                    iso(t0 + timedelta(hours=1)),
                    2,
                ),
                (
                    "ls-wait-02",
                    "pool-storage",
                    "team-etl",
                    "待生效",
                    iso(t0 - timedelta(minutes=30)),
                    "",
                    iso(t0 + timedelta(hours=3)),
                    0,
                ),
                (
                    "ls-renew-03",
                    "pool-net",
                    "svc-gateway",
                    "已续约",
                    iso(t0 - timedelta(days=1)),
                    iso(t0 - timedelta(days=1)),
                    iso(t0 + timedelta(minutes=40)),
                    5,
                ),
                (
                    "ls-over-04",
                    "pool-db",
                    "biz-report",
                    "生效中",
                    iso(t0 - timedelta(hours=5)),
                    iso(t0 - timedelta(hours=5)),
                    iso(t0 - timedelta(minutes=20)),
                    1,
                ),
            ]
            for row in 租约样:
                conn.execute(
                    """INSERT INTO 租约(租约号,池键,租户,状态,申领时,生效时,到期时刻,续约次数,更新时)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (*row, iso(t0)),
                )

            conn.execute(
                """INSERT INTO 心跳(心跳号,租约号,时刻,结果,延迟毫秒,说明) VALUES (?,?,?,?,?,?)""",
                (
                    "hb-0001",
                    "ls-active-01",
                    iso(t0 - timedelta(minutes=5)),
                    "成功",
                    42,
                    "定时续约",
                ),
            )
            conn.execute(
                """INSERT INTO 心跳(心跳号,租约号,时刻,结果,延迟毫秒,说明) VALUES (?,?,?,?,?,?)""",
                (
                    "hb-0002",
                    "ls-renew-03",
                    iso(t0 - timedelta(minutes=2)),
                    "成功",
                    38,
                    "宽限内续命",
                ),
            )
            conn.execute(
                """INSERT INTO 租约(租约号,池键,租户,状态,申领时,生效时,到期时刻,续约次数,更新时)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    "ls-old-99",
                    "pool-compute",
                    "team-legacy",
                    "生效中",
                    iso(t0 - timedelta(days=2)),
                    iso(t0 - timedelta(days=2)),
                    iso(t0 - timedelta(hours=8)),
                    0,
                    iso(t0 - timedelta(hours=6)),
                ),
            )
            conn.execute(
                """INSERT INTO 回收匣(回收号,租约号,池键,租户,原因,状态,入匣时,处置时) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    "rc-9001",
                    "ls-old-99",
                    "pool-compute",
                    "team-legacy",
                    "心跳超时",
                    "待回收",
                    iso(t0 - timedelta(hours=6)),
                    "",
                ),
            )
            conn.execute(
                """INSERT INTO 互证报告(时刻,结论,差异摘要,租约总数,生效中数,待回收数,池已占合计,是否对齐) VALUES (?,?,?,?,?,?,?,?)""",
                (
                    iso(t0 - timedelta(hours=1)),
                    "池占用与生效租约基本一致",
                    "消息主题池禁用但无生效租约",
                    4,
                    2,
                    1,
                    95,
                    1,
                ),
            )
            _留痕(conn, "系统播种", 详情="初始资源池与样例租约")
        _迁移旧库(conn)
        conn.commit()
    finally:
        conn.close()


def _迁移旧库(conn) -> None:
    """旧版库补台面折叠列与底账表；历史重复待回收先按入匣时折叠为一行，最后才建唯一索引。"""
    列 = {r["name"] for r in conn.execute("PRAGMA table_info(回收匣)").fetchall()}
    if "重入次数" not in 列:
        conn.execute("ALTER TABLE 回收匣 ADD COLUMN 重入次数 INTEGER NOT NULL DEFAULT 0")
    if "重入标记" not in 列:
        conn.execute("ALTER TABLE 回收匣 ADD COLUMN 重入标记 TEXT NOT NULL DEFAULT ''")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS 匣重入账 (
          指纹号 TEXT PRIMARY KEY,
          回收号 TEXT NOT NULL,
          租约号 TEXT NOT NULL,
          池键 TEXT NOT NULL,
          租户 TEXT NOT NULL,
          原因 TEXT NOT NULL DEFAULT '',
          时刻 TEXT NOT NULL,
          处置 TEXT NOT NULL DEFAULT '拒回刷'
        )"""
    )
    # 旧库若已存在同租约多行待回收（缺陷现场），建唯一索引前先折叠。
    重复 = conn.execute(
        """SELECT 租约号, COUNT(*) AS c FROM 回收匣
           WHERE 状态='待回收' GROUP BY 租约号 HAVING c > 1"""
    ).fetchall()
    for r in 重复:
        rows = conn.execute(
            """SELECT 回收号 FROM 回收匣 WHERE 租约号=? AND 状态='待回收'
               ORDER BY 入匣时 ASC""",
            (r["租约号"],),
        ).fetchall()
        留用 = rows[0]["回收号"]
        for dup in rows[1:]:
            conn.execute("UPDATE 回收匣 SET 状态='已作废', 处置时=入匣时 WHERE 回收号=?",
                         (dup["回收号"],))
            conn.execute(
                "UPDATE 回收匣 SET 重入次数=重入次数+1, 重入标记='重入已折叠' WHERE 回收号=?",
                (留用,),
            )
    # 折叠完成、保证每租约至多一行待回收后，再建部分唯一索引兜底并发。
    conn.execute(
        """CREATE UNIQUE INDEX IF NOT EXISTS 匣_租约待回收唯一
           ON 回收匣(租约号) WHERE 状态='待回收'"""
    )