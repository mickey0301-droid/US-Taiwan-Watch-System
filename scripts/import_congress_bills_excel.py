from __future__ import annotations

import argparse
import json

from tracker.jobs.import_congress_bills_excel import run_import_congress_bills_excel


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Taiwan-related Congress bills from Excel.")
    parser.add_argument("--excel-path", dest="excel_path", default=None)
    args = parser.parse_args()
    print(json.dumps(run_import_congress_bills_excel(args.excel_path), indent=2, default=str))


if __name__ == "__main__":
    main()
