from __future__ import annotations

import argparse
import json
from importlib import import_module

from tracker.scheduler import JOB_REGISTRY


def _resolve_job_runner(job_name: str):
    entry = JOB_REGISTRY[job_name]
    if callable(entry):
        return entry
    if isinstance(entry, tuple) and len(entry) == 2:
        module_name, func_name = entry
        module = import_module(str(module_name))
        return getattr(module, str(func_name))
    raise TypeError(f"Unsupported JOB_REGISTRY entry for {job_name}: {entry!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one sync job once.")
    parser.add_argument("job_name", choices=sorted(JOB_REGISTRY.keys()))
    args = parser.parse_args()
    runner = _resolve_job_runner(args.job_name)
    print(json.dumps(runner(), indent=2, default=str))


if __name__ == "__main__":
    main()
