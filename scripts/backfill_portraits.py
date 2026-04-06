from __future__ import annotations

import json

from tracker.db import session_scope
from tracker.services.portrait_backfill_service import PortraitBackfillService


def main() -> None:
    with session_scope() as session:
        result = PortraitBackfillService(session).backfill_all()
    print(
        json.dumps(
            {
                "people_scanned": result.people_scanned,
                "portraits_updated": result.portraits_updated,
                "source_counts": result.source_counts,
                "errors": result.errors[:20],
                "error_count": len(result.errors),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
