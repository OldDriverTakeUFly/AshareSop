# scripts/research_search.py
"""自建研究搜索工具(2026-08-31:MCP搜索配额受限期间的自有替代,免Key)。

通道:
  ddg     DuckDuckGo HTML 端点通用搜索(免费无Key,配合 site: 过滤可定向财经站)
  cninfo  巨潮公告检索(定向权威源,第一手公告)

用法:
  python scripts/research_search.py ddg "晶华新材 涨停 原因" [-n 8]
  python scripts/research_search.py ddg "晶华新材" --site eastmoney.com
  python scripts/research_search.py cninfo 603683 [--kw 减持] [-n 10]

输出 JSON 行:{"title","url","snippet"} / 巨潮含 {"date","title"}。产物是 URL 清单,
精读交给 WebFetch(读全文)——本工具只负责"找到",不负责"读懂"。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")


def _http(url: str, data: bytes | None = None, timeout: int = 15) -> bytes:
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA})
    if data is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def search_ddg(query: str, n: int = 8, site: str = "") -> list[dict]:
    q = f"{query} site:{site}" if site else query
    html = _http("https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(q)).decode("utf-8", "ignore")
    out: list[dict] = []
    # html.duckduckgo.com 的结果结构:<a class="result__a" href="...">title</a> + <a class="result__snippet">
    for m in re.finditer(
            r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?class="result__snippet"[^>]*>(.*?)</a>',
            html, re.S):
        url, title, snip = m.group(1), m.group(2), m.group(3)
        url = urllib.parse.unquote(re.sub(r"^//duckduckgo\.com/l/\?uddg=", "", url)) if "uddg=" in url else url
        clean = lambda s: re.sub(r"<[^>]+>", "", s).strip()
        out.append({"title": clean(title), "url": clean(url), "snippet": clean(snip)[:200]})
        if len(out) >= n:
            break
    return out


def search_cninfo(code: str, kw: str = "", n: int = 10) -> list[dict]:
    """巨潮公告检索:先查 szse_stock.json 拿 orgId(直接传代码会串到别家,2026-08-31 实测),再查公告。"""
    column = "sse" if code.startswith(("6", "9")) else "szse"
    stock_list = json.loads(_http("http://www.cninfo.com.cn/new/data/szse_stock.json"))
    org = next((s["orgId"] for s in stock_list.get("stockList", []) if s.get("code") == code), "")
    if not org:
        raise SystemExit(f"巨潮未找到代码 {code}")
    data = urllib.parse.urlencode({
        "pageNum": 1, "pageSize": n, "column": column, "tabName": "fulltext",
        "stock": f"{code},{org}", "searchkey": kw, "category": "", "seDate": ""}).encode()
    raw = _http("http://www.cninfo.com.cn/new/hisAnnouncement/query", data=data)
    anns = json.loads(raw).get("announcements") or []
    return [{"date": (a.get("announcementTime") and str(a["announcementTime"])[:8]) or "",
             "title": a.get("announcementTitle", ""),
             "url": f"http://static.cninfo.com.cn/{a['adjunctUrl']}"}
            for a in anns if a.get("adjunctUrl")]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="自建研究搜索(ddg/巨潮)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("ddg", help="DuckDuckGo 通用搜索")
    d.add_argument("query")
    d.add_argument("-n", type=int, default=8)
    d.add_argument("--site", default="")
    c = sub.add_parser("cninfo", help="巨潮公告检索")
    c.add_argument("code")
    c.add_argument("--kw", default="")
    c.add_argument("-n", type=int, default=10)
    a = ap.parse_args(argv)
    try:
        results = search_ddg(a.query, a.n, a.site) if a.cmd == "ddg" else search_cninfo(a.code, a.kw, a.n)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"error": repr(e)[:200]}, ensure_ascii=False))
        sys.exit(1)
    for r in results:
        print(json.dumps(r, ensure_ascii=False))
    if not results:
        print(json.dumps({"note": "无结果"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
