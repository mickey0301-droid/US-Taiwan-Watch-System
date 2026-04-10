"""
backfill_date_published_from_url.py
=====================================
針對 date_published 為 NULL 但 source_url 含有日期資訊的事件，
從 URL 中擷取正確的發布日期並回填。

適用網站:
- CNA:       https://www.cna.com.tw/news/aopl/YYYYMMDDNNNN.aspx
- President: https://www.president.gov.tw/en/news/.../YYYY/MM/DD/...

用法:
    python scripts/backfill_date_published_from_url.py --dry-run   # 預覽
    python scripts/backfill_date_published_from_url.py              # 實際更新
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, update
from tracker.db import session_scope
from tracker.models import Statement


def _extract_date_from_url(url: str) -> datetime | None:
    text = str(url or "")
    # CNA: /YYYYMMDDNNNN.aspx  (8-digit date + optional serial)
    m = re.search(r"/(\d{8})\d{0,6}\.aspx", text, re.I)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            pass
    # President Office: /YYYY/MM/DD/ path
    m = re.search(r"/(\d{4})/(\d{2})/(\d{2})/", text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    return None


def run(dry_run: bool = True) -> None:
    updated = 0
    skipped = 0
    no_date = 0

    with session_scope() as session:
        # 只查 date_published 為 NULL 且來源是 CNA / president 的事件
        rows = session.execute(
            select(Statement.id, Statement.source_url, Statement.date_collected)
            .where(
                Statement.date_published.is_(None),
                Statement.source_url.ilike("%cna.com.tw%")
                | Statement.source_url.ilike("%president.gov.tw%"),
            )
            .order_by(Statement.id.asc())
        ).all()

        print(f"找到 {len(rows)} 筆 date_published=NULL 的 CNA/President 事件")

        for stmt_id, source_url, date_collected in rows:
            extracted = _extract_date_from_url(source_url)
            if not extracted:
                no_date += 1
                continue

            # 做個合理性檢查：不接受未來的日期或超過20年前的日期
            now = datetime.utcnow()
            if extracted > now or (now - extracted).days > 365 * 20:
                skipped += 1
                continue

            if dry_run:
                print(f"  [DRY RUN] id={stmt_id}  {source_url[-60:]}")
                print(f"    date_collected={date_collected}  →  date_published={extracted.date()}")
            else:
                session.execute(
                    update(Statement)
                    .where(Statement.id == stmt_id)
                    .values(date_published=extracted)
                )
            updated += 1

        if not dry_run:
            session.commit()

    print(f"\n{'[DRY RUN] ' if dry_run else ''}結果:")
    print(f"  更新: {updated} 筆")
    print(f"  無法擷取日期: {no_date} 筆")
    print(f"  跳過（日期不合理）: {skipped} 筆")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只顯示不實際寫入")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
