"""品牌字面单一来源。

所有对外主名、中文名与品类都从 ``src/brand.json`` 读取；前端
``apps/console/console/src/brand.ts`` 引用同一份 JSON。
禁止在代码或文档里另写一套主名。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_DATA: dict[str, Any] = json.loads(
    Path(__file__).with_name("brand.json").read_text(encoding="utf-8")
)

BRAND_NAME_EN: str = str(_DATA["name_en"])
BRAND_NAME_ZH: str = str(_DATA["name_zh"])
BRAND_LOCKUP: str = f"{BRAND_NAME_EN} · {BRAND_NAME_ZH}"
BRAND_CATEGORY: str = str(_DATA["category"])
