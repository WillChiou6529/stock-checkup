"""內部人與公司公告（證交所／櫃買中心 OpenAPI，免費）。

官方 API 只提供「當天」的申報與重大訊息，所以每日排程（GitHub Actions）會把它們累積到 repo 的 data/*.csv，
網站讀取「累積的歷史 + 今天的即時資料」。
"""
from __future__ import annotations

import csv
import datetime as dt
import os
import re

import io

from .data import _pick, _roc_to_date, num
from .datafiles import read_text
from .http import get_json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
INSIDER_CSV = os.path.join(DATA, "insider.csv")
ANN_CSV = os.path.join(DATA, "announcements.csv")
INSIDER_COLS = ["date", "code", "name", "role", "who", "method", "shares", "holding", "period"]
ANN_COLS = ["date", "time", "code", "name", "subject"]

SRC = {
    "insider": ("https://openapi.twse.com.tw/v1/opendata/t187ap12_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap12_O"),
    "ann": ("https://openapi.twse.com.tw/v1/opendata/t187ap04_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O"),
    "pledge": ("https://openapi.twse.com.tw/v1/opendata/t187ap09_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap09_O"),
    "short": ("https://openapi.twse.com.tw/v1/opendata/t187ap08_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap08_O"),
    "major": ("https://openapi.twse.com.tw/v1/opendata/t187ap02_L", "https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap02_O"),
}


def _code(r):
    return str(r.get("公司代號") or r.get("SecuritiesCompanyCode") or "").strip()


def _rows(kind, ttl=3600):
    out = []
    for url in SRC[kind]:
        try:
            out += get_json(url, ttl=ttl) or []
        except Exception:  # noqa: BLE001
            pass
    return out


def today_insider():
    out = []
    for r in _rows("insider"):
        d = _roc_to_date(r.get("出表日期") or r.get("Date") or "")
        out.append({"date": d.isoformat() if d else "", "code": _code(r), "name": str(r.get("公司名稱") or r.get("CompanyName") or "").strip(),
                    "role": (r.get("申報人身分") or r.get("申請人身分") or "").strip(), "who": (r.get("姓名") or "").strip(),
                    "method": (r.get("預定轉讓方式及股數-轉讓方式") or "").strip(),
                    "shares": num(_pick(r, "預定轉讓總股數-自有持股")) or 0,
                    "holding": num(_pick(r, "目前持有股數-自有持股")) or 0,
                    "period": (r.get("有效轉讓期間") or "").strip()})
    return out


def today_announcements():
    out = []
    for r in _rows("ann"):
        d = _roc_to_date(r.get("發言日期") or "")
        subj = re.sub(r"\s+", " ", str(_pick(r, "主旨") or "")).strip()
        t = str(r.get("發言時間") or "").zfill(6)
        out.append({"date": d.isoformat() if d else "", "time": f"{t[:2]}:{t[2:4]}", "code": _code(r),
                    "name": str(r.get("公司名稱") or r.get("CompanyName") or "").strip(), "subject": subj[:300]})
    return out


def _read(path):
    """本機累積檔（排程寫入時用）。"""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _read_shared(path):
    """網站讀取：優先用 GitHub 上排程最新寫入的版本。"""
    text = read_text(os.path.basename(path))
    return list(csv.DictReader(io.StringIO(text))) if text else []


def append_csv(path, cols, rows, key):
    """把今天的資料併入累積檔（依 key 去重），回傳新增筆數。"""
    old = _read(path)
    seen = {key(r) for r in old}
    new = [r for r in rows if key(r) not in seen]
    if new:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            w.writerows(old + new)
    return len(new)


def collect_daily():
    """排程呼叫：累積內部人申報與重大訊息。"""
    n1 = append_csv(INSIDER_CSV, INSIDER_COLS, today_insider(), lambda r: (r["date"], r["code"], r["who"], str(r["shares"])))
    n2 = append_csv(ANN_CSV, ANN_COLS, today_announcements(), lambda r: (r["date"], r["time"], r["code"], r["subject"][:60]))
    return n1, n2


def pledge_map() -> dict:
    """董監質押比例（只有比例偏高的公司會被列出）。"""
    out = {}
    for r in _rows("pledge", ttl=86400):
        for code, pct in re.findall(r"(\d{4,6})\s+\S+\s+([\d.]+)", str(r.get("公司名稱") or r.get("CompanyName") or "")):
            out[code] = float(pct)
    return out


def for_stock(code: str, days: int = 120) -> dict:
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    ins_all = _read_shared(INSIDER_CSV)
    ins = {(r["date"], r["who"], str(r["shares"])): r for r in ins_all if r["code"] == code and r["date"] >= cutoff}
    for r in today_insider():
        if r["code"] == code:
            ins[(r["date"], r["who"], str(r["shares"]))] = r
    anns = {(r["date"], r["time"], r["subject"][:60]): r for r in _read_shared(ANN_CSV) if r["code"] == code and r["date"] >= cutoff}
    for r in today_announcements():
        if r["code"] == code:
            anns[(r["date"], r["time"], r["subject"][:60])] = r
    insider = sorted(ins.values(), key=lambda r: r["date"], reverse=True)
    for r in insider:
        sh, hold = float(r["shares"] or 0), float(r["holding"] or 0)
        r["lots"] = sh / 1000
        r["pct_of_holding"] = sh / hold if hold else None
        m = r.get("method", "")
        r["kind"] = "市場賣出" if any(k in m for k in ("一般交易", "鉅額", "盤後定價", "拍賣")) else \
                    "信託／贈與" if any(k in m for k in ("信託", "贈與")) else "其他"
    short = [r for r in _rows("short", ttl=86400) if _code(r) == code]
    major = [str(r.get("大股東名稱") or "").strip() for r in _rows("major", ttl=86400) if _code(r) == code]
    pledge = pledge_map().get(code)

    notes = []
    recent = [r for r in insider if r["date"] >= (dt.date.today() - dt.timedelta(days=30)).isoformat() and r["kind"] == "市場賣出"]
    if recent:
        lots = sum(r["lots"] for r in recent)
        notes.append((-1, f"近 30 天有 {len(recent)} 筆內部人申報在市場賣出，合計約 {lots:,.0f} 張"))
    if pledge and pledge >= 30:
        notes.append((-1, f"董監持股質押比例 {pledge:.1f}%（偏高，股價大跌時可能被斷頭賣出）"))
    if short:
        notes.append((-1, "全體董事持股低於法定成數"))
    return {"insider": insider[:30], "announcements": sorted(anns.values(), key=lambda r: (r["date"], r["time"]), reverse=True)[:30],
            "pledge": pledge, "director_shortfall": bool(short), "major_holders": major[:10], "notes": notes,
            "history_since": (min(r["date"] for r in ins_all) if ins_all else dt.date.today().isoformat())}
