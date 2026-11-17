"""日历提醒与记账（0.4.6 F 组，6-5）。

日历：本机 SQLite 存日程；``.ics`` 导入导出走 icalendar 7.3.0
（BSD-2-Clause）；接外部日历走 caldav（双许可 GPL-3.0 / Apache-2.0，
**本项目按 Apache-2.0 使用**，书面记录见
docs/voice-ocr-calendar-mobile.md 与 THIRD-PARTY-NOTICES 模板）。
Google / Apple 云日历**不作默认**：接它们要把日程送上云、要 OAuth，
只作用户自己选开的可选项，本轮不接线。

记账：自研薄封装，不引记账框架。最小闭环：
发票/收据拍照 -> OCR 识字 -> 解析出金额/日期/商家 -> 记一笔 ->
按月汇总。OCR 错字可能导致金额错，记账页永远标明\"识别结果请核对\"，
金额字段建账时可改。
"""

from .calendar_store import CalendarEvent, CalendarStore  # noqa: F401
from .ledger import Expense, LedgerStore, parse_invoice_text  # noqa: F401
