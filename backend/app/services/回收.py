from __future__ import annotations

import sqlite3
import uuid

from app.db import 连接
from app.services import 租约, 校验, 说明

折叠标记 = "重入已折叠"


def _留痕(conn, 操作, 租约号="", 详情=""):
    conn.execute(
        "INSERT INTO 操作留痕(时刻,操作,租约号,详情) VALUES (?,?,?,?)",
        (校验.此刻(), 操作, 租约号, 详情),
    )


def _写连接() -> sqlite3.Connection:
    """autocommit 模式下显式开 IMMEDIATE 事务，两路扫描在此排队串行。"""
    conn = 连接()
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _匣行(conn, 回收号: str) -> dict:
    return dict(conn.execute("SELECT * FROM 回收匣 WHERE 回收号=?", (回收号,)).fetchone())


def _折叠重入(conn, 原行, 原因: str) -> None:
    """重入不新增匣行：回刷原行计数与标记，并往底账轨写一笔拒回刷指纹。"""
    回收号 = 原行["回收号"]
    conn.execute(
        """UPDATE 回收匣 SET 重入次数=重入次数+1, 重入标记=?, 原因=?
           WHERE 回收号=?""",
        (折叠标记, 原因, 回收号),
    )
    conn.execute(
        """INSERT INTO 匣重入账(指纹号,回收号,租约号,池键,租户,原因,时刻,处置)
           VALUES (?,?,?,?,?,?,?,?)""",
        (
            f"fp-{uuid.uuid4().hex[:10]}",
            回收号,
            原行["租约号"],
            原行["池键"],
            原行["租户"],
            原因,
            校验.此刻(),
            "拒回刷",
        ),
    )
    _留痕(conn, "重入已折叠", 原行["租约号"], f"折入 {回收号}")


def 入匣(body: dict) -> dict:
    租约号 = 校验.租约号(body.get("租约号"))
    原因 = str(body.get("原因") or "到期未续")
    row = 说明.租约一条(租约号)
    if not row:
        raise ValueError("无此租约")
    回收号 = str(body.get("回收号") or f"rc-{uuid.uuid4().hex[:8]}")
    conn = _写连接()
    try:
        已有 = conn.execute(
            "SELECT * FROM 回收匣 WHERE 租约号=? AND 状态='待回收'", (租约号,)
        ).fetchone()
        if 已有:
            # 常见路径：同会话重复扫描，直接折进原行。
            _折叠重入(conn, 已有, 原因)
            conn.commit()
            return _匣行(conn, 已有["回收号"])
        try:
            conn.execute(
                """INSERT INTO 回收匣(回收号,租约号,池键,租户,原因,状态,入匣时,处置时,重入次数,重入标记)
                   VALUES (?,?,?,?,?,?,?,?,0,'')""",
                (
                    回收号,
                    租约号,
                    row["池键"],
                    row["租户"],
                    原因,
                    "待回收",
                    校验.此刻(),
                    "",
                ),
            )
        except sqlite3.IntegrityError:
            # 争用路径：另一路扫描已抢先插入待回收行（部分唯一索引兜底）。
            原行 = conn.execute(
                "SELECT * FROM 回收匣 WHERE 租约号=? AND 状态='待回收'", (租约号,)
            ).fetchone()
            if 原行 is None:
                raise RuntimeError("唯一约束冲突但查无待回收匣行")
            _折叠重入(conn, 原行, 原因)
            conn.commit()
            return _匣行(conn, 原行["回收号"])
        _留痕(conn, "租约入回收匣", 租约号, 原因)
        conn.commit()
        return _匣行(conn, 回收号)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 确认回收(回收号: str) -> dict:
    回收号 = 校验.非空("回收号", 回收号)
    conn = _写连接()
    try:
        row = conn.execute("SELECT * FROM 回收匣 WHERE 回收号=?", (回收号,)).fetchone()
        if not row:
            raise ValueError("无此回收记录")
        if row["状态"] != "待回收":
            raise ValueError("仅待回收可确认")
        conn.execute(
            "UPDATE 回收匣 SET 状态='已回收', 处置时=? WHERE 回收号=?",
            (校验.此刻(), 回收号),
        )
        租约.标记回收(row["租约号"], conn=conn)
        _留痕(conn, "确认回收", row["租约号"], 回收号)
        conn.commit()
        return _匣行(conn, 回收号)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 作废(回收号: str, 原因: str = "误回收") -> dict:
    回收号 = 校验.非空("回收号", 回收号)
    conn = _写连接()
    try:
        row = conn.execute("SELECT * FROM 回收匣 WHERE 回收号=?", (回收号,)).fetchone()
        if not row:
            raise ValueError("无此回收记录")
        if row["状态"] != "待回收":
            raise ValueError("仅待回收可作废")
        conn.execute(
            "UPDATE 回收匣 SET 状态='已作废', 处置时=?, 原因=? WHERE 回收号=?",
            (校验.此刻(), 原因, 回收号),
        )
        _留痕(conn, "回收作废", row["租约号"], 原因)
        conn.commit()
        return _匣行(conn, 回收号)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def 扫描并入匣() -> list:
    return 租约.扫描逾期()
