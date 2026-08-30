# davis_analyzer/metrics/report.py
"""聚合报告:按内容组(grp)统计最新快照,回答「哪条内容线有流量」。"""
from __future__ import annotations

import statistics
import sqlite3
from datetime import datetime

from davis_analyzer.metrics.db import connect


def _latest_note_rows(c: sqlite3.Connection, account_id: str | None) -> list[sqlite3.Row]:
    q = ("SELECT n.note_id,n.title,n.grp,n.published_at,m.views,m.likes,m.collects,m.captured_at "
         "FROM notes n JOIN note_metrics m ON m.note_id=n.note_id "
         "WHERE m.captured_at=(SELECT MAX(m2.captured_at) FROM note_metrics m2 WHERE m2.note_id=n.note_id)")
    if account_id:
        q += " AND n.account_id=?"
        return list(c.execute(q + " ORDER BY m.views DESC", (account_id,)))
    return list(c.execute(q + " ORDER BY m.views DESC"))


def report(account_id: str | None = None, out_md: str | None = None) -> str:
    lines = [f"# 运营数据报告 {datetime.now():%Y-%m-%d %H:%M}", ""]
    with connect() as c:
        for acc in c.execute("SELECT account_id,name FROM accounts"):
            if account_id and acc["account_id"] != account_id:
                continue
            row = c.execute(
                "SELECT followers,total_likes,captured_at FROM account_metrics WHERE account_id=? "
                "ORDER BY captured_at DESC LIMIT 1", (acc["account_id"],)).fetchone()
            if row:
                lines.append(f"账号 {acc['account_id']}({acc['name'] or ''}):"
                             f"粉丝 {row['followers']} 获赞藏 {row['total_likes']}"
                             f" @ {row['captured_at'][:16]}")
        lines.append("")
        rows = _latest_note_rows(c, account_id)
        if not rows:
            lines.append("(暂无笔记数据)")
        else:
            by_grp: dict[str, list[sqlite3.Row]] = {}
            for r in rows:
                by_grp.setdefault(r["grp"] or "未分组", []).append(r)
            lines.append("| 内容组 | 篇数 | 阅读中位 | 阅读合计 | 赞 | 藏 | 收藏率 |")
            lines.append("|---|---|---|---|---|---|---|")
            for g, rs in sorted(by_grp.items(), key=lambda kv: -statistics.median(
                    [x["views"] or 0 for x in kv[1]])):
                views = [x["views"] or 0 for x in rs]
                likes = sum(x["likes"] or 0 for x in rs)
                coll = sum(x["collects"] or 0 for x in rs)
                lines.append(f"| {g} | {len(rs)} | {int(statistics.median(views))} | {sum(views)} "
                             f"| {likes} | {coll} | {sum(views) and f'{coll/sum(views)*100:.1f}%' or '-'} |")
            lines.append("")
            lines.append("**单篇 TOP5(按最新阅读)**")
            for r in rows[:5]:
                lines.append(f"- {r['title'][:24]} | 阅读 {r['views']} 赞 {r['likes']} 藏 {r['collects']}"
                             f" | {r['grp'] or '未分组'} | 快照 {r['captured_at'][:16]}")
    text = "\n".join(lines)
    if out_md:
        from pathlib import Path
        Path(out_md).write_text(text, encoding="utf-8")
    print(text)
    return text
