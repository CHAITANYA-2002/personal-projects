"""Download Copernicus DEM GLO-30 tiles for the study area.

    python scripts/02_download_dem.py            # download everything missing
    python scripts/02_download_dem.py --plan     # report size and exit
    python scripts/02_download_dem.py --workers 8

Long running — roughly 17.5 GB across 442 tiles. Safe to interrupt and rerun:
a tile whose local size matches the remote content-length is skipped, and
partial files are written to .part and only renamed once complete.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src.data import dem                         # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("download_dem")


def main(workers: int, plan_only: bool) -> int:
    settings.ensure_dirs()

    tiles = dem.tiles_for_study_area()
    missing = dem.missing_tiles(tiles)

    log.info("study area needs %d tiles", len(tiles))
    log.info("already present   %d", len(tiles) - len(missing))
    log.info("to download       %d (~%.1f GB at 40 MB/tile)",
             len(missing), len(missing) * 40 / 1000)

    if plan_only:
        log.info("--plan given, stopping before download")
        return 0

    if not missing:
        log.info("nothing to do")
        return 0

    summary = dem.download_tiles(missing, workers=workers)

    log.info("--- summary ---")
    for key, value in summary.items():
        log.info("%-12s %s", key, value)

    still_missing = dem.missing_tiles(tiles)
    if still_missing:
        log.warning(
            "%d tiles still missing — rerun to retry: %s",
            len(still_missing),
            ", ".join(tile.name for tile in still_missing[:5]),
        )
        return 1

    log.info("all %d tiles present", len(tiles))
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=6,
                        help="parallel downloads (default 6)")
    parser.add_argument("--plan", action="store_true",
                        help="report what would be downloaded, then exit")
    args = parser.parse_args()

    try:
        sys.exit(main(workers=args.workers, plan_only=args.plan))
    except KeyboardInterrupt:
        log.warning("interrupted — rerun to resume, completed tiles are kept")
        sys.exit(130)
    except Exception as exc:
        log.error("download failed: %s", exc)
        raise
