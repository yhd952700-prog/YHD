"""记账：自研薄封装。最小闭环：发票/收据拍照 -> OCR -> 记一笔 -> 按月汇总。

没有引任何记账框架：核心就是\"OCR 文字 -> 结构化（金额/日期/商家）->
本地 SQLite -> 按月汇总\"，这几步用 RapidOCR + 本模块就能做。

金额以**分**存（整数），避免浮点误差。OCR 识别可能错，解析出的金额
只是\"草稿\"：前端建账时必须能改，记账页标明\"识别结果请核对\"。
"""

from __future__ import annotations

import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Collection, Dict, List, Optional

from src.common.data_paths import resolve_store_path
from src.common.engines import data_dir

#: 默认分类关键词（用户以后可在设置里改；先给一套能用的默认值）。
CATEGORY_KEYWORDS: Dict[str, tuple] = {
    "餐饮": ("餐", "饭", "面馆", "餐厅", "食堂", "奶茶", "咖啡", "外卖", "火锅", "烧烤"),
    "交通": ("打车", "出租", "地铁", "公交", "加油", "停车", "机票", "火车票", "高速"),
    "住宿": ("住宿", "酒店", "宾馆", "民宿"),
    "办公": ("办公", "文具", "打印", "复印", "耗材"),
    "购物": ("超市", "商场", "购物", "百货", "便利店", "服装", "商城"),
    "医疗": ("药", "医院", "诊所", "体检", "医疗"),
}


def guess_category(text: str) -> str:
    haystack = text or ""
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in haystack for kw in keywords):
            return category
    return "其他"


_DATE_RE = re.compile(
    r"(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})"
)
#: 金额：¥/￥/元 前后带数字；也认 \"合计 123.45\" 这类写法。
_AMOUNT_RE = re.compile(
    r"(?:¥|￥|人民币|合计|总计|小计|金额|应付|实付)[^\d]{0,8}([\d,]+\.\d{1,2}|[\d,]+)"
)
_BARE_AMOUNT_RE = re.compile(r"([\d,]+\.\d{2})")


@dataclass(frozen=True)
class ParsedInvoice:
    """从 OCR 文字里解析出的发票字段（草稿，必须人工核对）。"""

    amount_cents: Optional[int]
    occurred_on: Optional[date]
    merchant: str
    category: str
    raw_text: str


def parse_invoice_text(text: str) -> ParsedInvoice:
    """OCR 文字 -> 发票草稿。解析不出金额时 ``amount_cents`` 为 None。"""
    raw = (text or "").strip()
    amount_cents: Optional[int] = None
    labeled = _AMOUNT_RE.findall(raw)
    if labeled:
        # \"合计/总计\"优先：明确标了合计的数最可能是应记金额。
        candidates = [float(v.replace(",", "")) for v in labeled]
        amount_cents = int(round(max(candidates) * 100))
    else:
        bare = [float(v.replace(",", "")) for v in _BARE_AMOUNT_RE.findall(raw)]
        if bare:
            amount_cents = int(round(max(bare) * 100))
    occurred: Optional[date] = None
    match = _DATE_RE.search(raw)
    if match:
        try:
            occurred = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            occurred = None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    merchant = ""
    for line in lines[:3]:
        # 商家一般在票头第一行；金额/日期行不算商家。
        if _DATE_RE.search(line) or _AMOUNT_RE.search(line):
            continue
        merchant = line[:30]
        break
    return ParsedInvoice(
        amount_cents=amount_cents,
        occurred_on=occurred,
        merchant=merchant,
        category=guess_category(raw),
        raw_text=raw,
    )


@dataclass
class Expense:
    id: str
    occurred_on: date
    amount_cents: int
    category: str
    merchant: str = ""
    note: str = ""
    source: str = "manual"  # manual | ocr
    ocr_text: str = ""
    # 归属账号（2026-11-12 权限统一补）：空串 = 分账前的旧数据，默认归根账号。
    account_id: str = ""


def _store_path() -> Path:
    return Path(
        resolve_store_path(
            "ledger.db",
            data_dir() / "ledger.db",
            env_var="LIUHAO_LEDGER_DB",
        )
    )


class LedgerStore:
    """记账本机存储：一个 SQLite 文件，无云端。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else _store_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS expenses (
                    id TEXT PRIMARY KEY, occurred_on TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL, category TEXT NOT NULL,
                    merchant TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'manual',
                    ocr_text TEXT NOT NULL DEFAULT '',
                    account_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL)"""
            )
            cols = [r[1] for r in conn.execute("PRAGMA table_info(expenses)")]
            if "account_id" not in cols:
                conn.execute("ALTER TABLE expenses ADD COLUMN account_id TEXT NOT NULL DEFAULT ''")

    def add(self, expense: Expense) -> Expense:
        if expense.amount_cents <= 0:
            raise ValueError("金额必须大于 0。")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO expenses VALUES (?,?,?,?,?,?,?,?,?,?)",
                (expense.id, expense.occurred_on.isoformat(), expense.amount_cents,
                 expense.category, expense.merchant, expense.note, expense.source,
                 expense.ocr_text, expense.account_id,
                 datetime.now(timezone.utc).isoformat()),
            )
        return expense

    def add_from_invoice(self, parsed: ParsedInvoice, *,
                         amount_cents: Optional[int] = None,
                         occurred_on: Optional[date] = None,
                         category: Optional[str] = None,
                         merchant: Optional[str] = None,
                         note: str = "", account_id: str = "") -> Expense:
        """OCR 草稿记一笔：调用方可覆盖解析出的任何字段（人工核对后）。"""
        amount = amount_cents if amount_cents is not None else parsed.amount_cents
        if amount is None:
            raise ValueError("这张票没认出金额，请手动填金额再记。")
        return self.add(Expense(
            id=uuid.uuid4().hex[:16],
            occurred_on=occurred_on or parsed.occurred_on or date.today(),
            amount_cents=int(amount),
            category=category or parsed.category,
            merchant=merchant if merchant is not None else parsed.merchant,
            note=note, source="ocr", ocr_text=parsed.raw_text,
            account_id=account_id,
        ))

    def list(self, year: Optional[int] = None, month: Optional[int] = None,
             account_ids: Optional[Collection[str]] = None) -> List[Expense]:
        sql = "SELECT * FROM expenses"
        args: List[object] = []
        where: List[str] = []
        if year is not None and month is not None:
            where.append("substr(occurred_on, 1, 7) = ?")
            args.append(f"{year:04d}-{month:02d}")
        if account_ids is not None:
            ids = list(account_ids)
            if not ids:
                return []
            where.append("account_id IN (%s)" % ",".join("?" * len(ids)))
            args.extend(ids)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY occurred_on DESC, created_at DESC"
        with self._connect() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [
            Expense(id=r["id"], occurred_on=date.fromisoformat(r["occurred_on"]),
                    amount_cents=r["amount_cents"], category=r["category"],
                    merchant=r["merchant"] or "", note=r["note"] or "",
                    source=r["source"] or "manual", ocr_text=r["ocr_text"] or "",
                    account_id=r["account_id"] if "account_id" in r.keys() else "")
            for r in rows
        ]

    def get(self, expense_id: str) -> Optional[Expense]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM expenses WHERE id = ?", (expense_id,)
            ).fetchone()
        if not row:
            return None
        return Expense(
            id=row["id"], occurred_on=date.fromisoformat(row["occurred_on"]),
            amount_cents=row["amount_cents"], category=row["category"],
            merchant=row["merchant"] or "", note=row["note"] or "",
            source=row["source"] or "manual", ocr_text=row["ocr_text"] or "",
            account_id=row["account_id"] if "account_id" in row.keys() else "",
        )

    def delete(self, expense_id: str,
               account_ids: Optional[Collection[str]] = None) -> bool:
        with self._connect() as conn:
            if account_ids is None:
                cur = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
            else:
                ids = list(account_ids)
                if not ids:
                    return False
                cur = conn.execute(
                    "DELETE FROM expenses WHERE id = ? AND account_id IN (%s)"
                    % ",".join("?" * len(ids)),
                    (expense_id, *ids),
                )
        return cur.rowcount > 0

    def monthly_summary(self, year: int, month: int,
                        account_ids: Optional[Collection[str]] = None) -> Dict[str, object]:
        """按月汇总：总支出、笔数、按分类小计（单位：分）。"""
        expenses = self.list(year=year, month=month, account_ids=account_ids)
        by_category: Dict[str, int] = {}
        for expense in expenses:
            by_category[expense.category] = (
                by_category.get(expense.category, 0) + expense.amount_cents
            )
        return {
            "year": year, "month": month,
            "total_cents": sum(e.amount_cents for e in expenses),
            "count": len(expenses),
            "by_category_cents": by_category,
        }
