#!/usr/bin/env python3
"""Manual data lifecycle management utilities for Trip Planner.

Usage:
    python scripts/cleanup.py [--jobs] [--exports] [--all]

Examples:
    python scripts/cleanup.py --all          # Clean up both jobs and exports
    python scripts/cleanup.py --jobs         # Clean up old planning jobs only
    python scripts/cleanup.py --exports      # Clean up old export files only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Ensure the backend package is importable when running from the project root
_script_dir = Path(__file__).resolve().parent  # backend/scripts/
_backend_src = _script_dir.parent              # backend/
if str(_backend_src) not in sys.path:
    sys.path.insert(0, str(_backend_src))

from app.core.config import settings, resolve_backend_path
from app.services.export_service import cleanup_old_exports
from app.services.job_service import job_store

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger("cleanup")


def main() -> None:
    parser = argparse.ArgumentParser(description="Trip Planner data lifecycle management")
    parser.add_argument("--jobs", action="store_true", help="Clean up old planning jobs")
    parser.add_argument("--exports", action="store_true", help="Clean up old export artifacts")
    parser.add_argument("--all", action="store_true", help="Clean up both jobs and exports")
    args = parser.parse_args()

    if not any((args.jobs, args.exports, args.all)):
        parser.print_help()
        sys.exit(0)

    total_removed = 0

    if args.jobs or args.all:
        removed = job_store.cleanup_old_jobs()
        total_removed += removed
        logger.info("Jobs cleanup: removed %d job(s)", removed)

    if args.exports or args.all:
        removed = cleanup_old_exports()
        total_removed += removed
        logger.info("Exports cleanup: removed %d file(s)", removed)

    logger.info("Cleanup complete: %d item(s) removed", total_removed)


if __name__ == "__main__":
    main()