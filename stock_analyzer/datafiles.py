"""讀取 repo 的 data/ 檔案。

每日排程把資料 commit 到 GitHub；網站（Render）設定 DATA_REMOTE=1 時直接讀 GitHub 上的最新版，
這樣每天更新資料不必重新部署。讀不到就退回本機檔案。
"""
from __future__ import annotations

import os

from .http import get_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.environ.get("DATA_RAW_BASE", "https://raw.githubusercontent.com/WillChiou6529/stock-checkup/main/data/")


def read_text(name: str, ttl: float = 1800) -> str | None:
    if os.environ.get("DATA_REMOTE") == "1":
        try:
            return get_text(RAW + name, ttl=ttl, encoding="utf-8-sig")
        except Exception:  # noqa: BLE001
            pass
    path = os.path.join(ROOT, "data", name)
    if os.path.exists(path):
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    return None
