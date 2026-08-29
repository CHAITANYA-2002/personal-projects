"""Put the project root on sys.path so tests import the same modules the
scripts do, without needing the package installed."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
