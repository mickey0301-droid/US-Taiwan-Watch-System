from __future__ import annotations

import argparse
import json

from tracker.jobs.bootstrap_existing_people_taiwan_2025_2026 import run_bootstrap_existing_people_taiwan_2025_2026


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bootstrap 2025-2026 Taiwan trackers for existing people (3 news queries + official site), then sync events."
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of people to bootstrap")
    parser.add_argument("--skip-sync", action="store_true", help="Only bootstrap trackers; do not run tracker sync")
    args = parser.parse_args()

    bootstrap_result = run_bootstrap_existing_people_taiwan_2025_2026(limit=args.limit)
    payload: dict[str, object] = {"bootstrap": bootstrap_result}
    if not args.skip_sync:
        from tracker.jobs.sync_trackers import run_sync_trackers

        payload["sync_trackers"] = run_sync_trackers()
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
