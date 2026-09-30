"""Generate everything the app's analysis loads, into android/app/src/main/assets/analysis/.

    uv run --with onnx --with onnxscript python android/tools/prepare_assets.py

None of it is in git: the chord and key models are exported from madmom's own model files
(export_models.py), the tables from the Python pipeline's objects (export_tables.py), and
the Beat This! model is copied from chordchart/models. The output is deterministic (the
same bytes on every run), so a build from a fresh clone gives the same app.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    for script in ("export_models.py", "export_tables.py"):
        print(f"== {script}", flush=True)
        result = subprocess.run([sys.executable, str(HERE / script)], check=False)
        if result.returncode != 0:
            return result.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
