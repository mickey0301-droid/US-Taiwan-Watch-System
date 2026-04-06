from __future__ import annotations

import argparse
import json

from tracker.scheduler import JOB_REGISTRY


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one local job, then sync SQLite results to Google Sheet.")
    parser.add_argument("job_name", choices=sorted(name for name in JOB_REGISTRY.keys() if name != "export_google_sheet_data"))
    parser.add_argument("--skip-sheet-sync", action="store_true")
    args = parser.parse_args()

    job_result = JOB_REGISTRY[args.job_name]()
    payload: dict[str, object] = {"job_result": job_result}
    if not args.skip_sheet_sync:
        payload["google_sheet_sync"] = JOB_REGISTRY["export_google_sheet_data"]()
    print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))


if __name__ == "__main__":
    main()
