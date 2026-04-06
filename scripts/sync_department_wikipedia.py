from __future__ import annotations

import argparse
import json

from tracker.jobs.sync_single_department_wikipedia import run_sync_single_department_wikipedia


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Wikipedia sync for a single federal department.")
    parser.add_argument("department_name")
    args = parser.parse_args()
    print(json.dumps(run_sync_single_department_wikipedia(args.department_name), indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
