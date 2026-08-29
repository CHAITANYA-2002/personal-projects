"""Run the Slopewatch pipeline end to end.

    .venv/Scripts/python.exe scripts/run_pipeline.py
    .venv/Scripts/python.exe scripts/run_pipeline.py --from 04 --to 08
    .venv/Scripts/python.exe scripts/run_pipeline.py --list

Stages run in dependency order and the run stops at the first failure, because
a stage built on a broken predecessor produces plausible output rather than an
error — which is far harder to notice.

Every stage is individually resumable, so a stopped run is restarted with
--from at the stage that failed rather than from the beginning.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.logging_setup import configure          # noqa: E402

log = configure("pipeline")

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
if not PYTHON.exists():                          # non-Windows or no venv
    PYTHON = Path(sys.executable)


@dataclass(frozen=True)
class Stage:
    code: str
    script: str
    description: str
    minutes: str


STAGES = (
    Stage("00", "00_init_db.py",
          "schema, date dimension, 40,800-cell grid", "<1"),
    Stage("01", "01_load_landslides.py",
          "NASA catalogue to fact_landslide", "<1"),
    Stage("02", "02_download_dem.py",
          "442 Copernicus DEM tiles, 17.5 GB", "20-40"),
    Stage("03", "03_build_terrain.py",
          "slope, aspect, ruggedness, hill mask", "30-45"),
    Stage("11", "11_label_admin.py",
          "state and district names from geoBoundaries", "2-5"),
    Stage("09", "09_build_exposure.py",
          "OSM roads, settlements, facilities", "5-15"),
    Stage("04", "04_build_sample.py",
          "case-control sample to fact_sample", "1-3"),
    Stage("05", "05_fetch_weather.py",
          "Open-Meteo windows to fact_weather_daily", "30-240"),
    Stage("06", "06_build_features.py",
          "feature matrix and leakage audit", "1-3"),
    Stage("07", "07_train_model.py",
          "train, calibrate, blocked validation", "5-20"),
    Stage("08", "08_score.py",
          "forecast scoring to fact_risk_pred", "3-10"),
    Stage("10", "10_verify.py",
          "end-to-end verification", "<1"),
)


def run_stage(stage: Stage, extra: list[str]) -> bool:
    script = ROOT / "scripts" / stage.script
    log.info("=" * 72)
    log.info("stage %s  %s", stage.code, stage.description)
    log.info("=" * 72)

    started = time.monotonic()
    result = subprocess.run(
        [str(PYTHON), str(script), *extra],
        cwd=str(ROOT),
    )
    elapsed = time.monotonic() - started

    if result.returncode == 0:
        log.info("stage %s finished in %.1f min", stage.code, elapsed / 60)
        return True

    log.error("stage %s failed with exit code %d after %.1f min",
              stage.code, result.returncode, elapsed / 60)
    log.error("resume with: --from %s", stage.code)
    return False


def main(start: str | None, stop: str | None, skip: list[str]) -> int:
    codes = [stage.code for stage in STAGES]

    begin = codes.index(start) if start else 0
    end = codes.index(stop) + 1 if stop else len(STAGES)
    if begin >= end:
        log.error("--from %s comes after --to %s in the pipeline", start, stop)
        return 2

    selected = [
        stage for stage in STAGES[begin:end] if stage.code not in skip
    ]
    log.info("running %d stages: %s",
             len(selected), ", ".join(stage.code for stage in selected))

    started = time.monotonic()
    for stage in selected:
        if not run_stage(stage, []):
            return 1

    log.info("=" * 72)
    log.info("pipeline complete in %.1f min", (time.monotonic() - started) / 60)
    return 0


def show_stages() -> int:
    log.info("%-5s %-26s %-46s %s", "CODE", "SCRIPT", "DOES", "MIN")
    for stage in STAGES:
        log.info("%-5s %-26s %-46s %s",
                 stage.code, stage.script, stage.description, stage.minutes)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", help="first stage code to run")
    parser.add_argument("--to", dest="stop", help="last stage code to run")
    parser.add_argument("--skip", nargs="*", default=[],
                        help="stage codes to skip")
    parser.add_argument("--list", action="store_true",
                        help="show the stages and exit")
    args = parser.parse_args()

    if args.list:
        sys.exit(show_stages())

    sys.exit(main(start=args.start, stop=args.stop, skip=args.skip))
