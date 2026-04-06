from __future__ import annotations

import argparse
import json

from tracker.scheduler import JOB_REGISTRY


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one sync job once.")
    parser.add_argument("job_name", choices=sorted(JOB_REGISTRY.keys()))
    args = parser.parse_args()
    print(json.dumps(JOB_REGISTRY[args.job_name](), indent=2, default=str))


if __name__ == "__main__":
    main()
