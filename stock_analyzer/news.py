"""個股新聞（Yahoo 股市 RSS + Google 新聞 RSS，免費）與關鍵字利多／利空判讀。

判讀只看標題的財經關鍵字，屬於「快速掃描」，不計入評分：
新聞影響通常 1~3 天內就反映在股價，也沒有可驗證的歷史資料。
"""
from __future__ import annotations

import datetime as dt
import email.utils
import html
import re
import urllib.parse

from .http import get_text

POS = ["創新高", "新高", "大漲", "漲停", "噴出", "飆", "勁揚", "走揚", "收紅", "獲利成長", "獲利創", "營收創", "優於預期",
       "超乎預期", "超預期", "上修", "調升", "升評", "買進評等", "看好", "擴產", "接單", "訂單滿", "大單", "拿下", "打入供應鏈",
       "轉盈", "轉虧為盈", "加碼", "買超", "庫藏股", "利多", "樂觀", "強勁", "暢旺", "突破", "增產", "得標", "急單", "旺季",
       "目標價調高", "上調", "雙增", "三率三升", "填息", "狂買", "大買", "買盤湧"]
NEG = ["大跌", "重挫", "跌停", "崩", "暴跌", "收黑", "走低", "翻黑", "衰退", "年減", "虧損", "轉虧", "下修", "調降",
       "降評", "看淡", "砍單", "減產", "裁員", "停工", "違約", "掏空", "搜索", "起訴", "罰", "訴訟", "賣超", "減碼", "利空",
       "疲弱", "不如預期", "低於預期", "示警", "警訊", "庫存調整", "下滑", "失守", "跌破", "提款", "倒貨", "斷頭",
       "目標價調降", "下調", "雙減", "貼息", "注意股", "處置", "狂賣", "賣壓", "下殺", "大賣"]
# 論壇、社群等非新聞來源（內容未經編輯把關，常有情緒性或不雅用語）
BLOCK_SOURCES = ("爆料同學會", "PTT", "Ptt", "Mobile01", "Dcard", "痞客邦", "pixnet", "Facebook", "YouTube", "Threads", "論壇")
NUM_NEG = re.compile(r"(年增|月增|成長|增)[率幅]?\s*[-－]\s*\d")  # 例如「年增-13.13%」其實是衰退


def tone(title: str):
    t = title
    neg_num = bool(NUM_NEG.search(t))
    if neg_num:
        t = NUM_NEG.sub("", t)
    p = [k for k in POS if k in t]
    n = [k for k in NEG if k in t] + (["數字衰退"] if neg_num else [])
    score = len(p) - len(n)
    return (1 if score > 0 else -1 if score < 0 else 0), p, n


def _items(xml: str, source_default: str):
    out = []
    for it in re.findall(r"<item>(.*?)</item>", xml, re.S):
        def tag(n):
            m = re.search(rf"<{n}[^>]*>(.*?)</{n}>", it, re.S)
            v = m.group(1) if m else ""
            v = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", v, flags=re.S)
            return html.unescape(v).strip()
        title, link, pub, src = tag("title"), tag("link"), tag("pubDate"), tag("source") or source_default
        if " - " in title and source_default == "Google 新聞":
            title, _, src2 = title.rpartition(" - ")
            src = src or src2
        try:
            when = email.utils.parsedate_to_datetime(pub).astimezone(dt.timezone(dt.timedelta(hours=8)))
        except (TypeError, ValueError):
            when = None
        out.append({"title": title, "link": link, "source": src, "time": when})
    return out


def fetch(code: str, name: str, tw: bool, limit: int = 15) -> dict:
    items = []
    if tw:
        try:
            items += _items(get_text(f"https://tw.stock.yahoo.com/rss?s={code}", ttl=1800), "Yahoo 股市")
        except Exception:  # noqa: BLE001
            pass
        q = f"{name} {code}" if name else code
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q + " when:14d") + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    else:
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(f"{code} stock when:14d") + "&hl=en-US&gl=US&ceid=US:en"
    try:
        items += _items(get_text(url, ttl=1800), "Google 新聞")
    except Exception:  # noqa: BLE001
        pass

    # 只留和這檔有關的（Yahoo RSS 偶爾混入大盤新聞），去重、依時間排序
    keys = [k for k in (code, name, name[:2] if tw and name else None) if k]
    seen, out = set(), []
    for it in sorted(items, key=lambda x: x["time"] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True):
        norm = re.sub(r"\W", "", it["title"])[:22]
        if not norm or norm in seen:
            continue
        if tw and not any(k in it["title"] for k in keys):
            continue
        if any(b in (it["source"] or "") or b in it["title"] for b in BLOCK_SOURCES):
            continue
        seen.add(norm)
        t, p, n = tone(it["title"])
        out.append({"title": it["title"], "link": it["link"], "source": it["source"],
                    "time": it["time"].strftime("%m/%d %H:%M") if it["time"] else "", "tone": t, "pos": p, "neg": n})
        if len(out) >= limit:
            break
    pos = sum(1 for x in out if x["tone"] > 0)
    neg = sum(1 for x in out if x["tone"] < 0)
    mood = "偏多" if pos >= neg + 2 else "偏空" if neg >= pos + 2 else "中性"
    return {"items": out, "pos": pos, "neg": neg, "mood": mood}
