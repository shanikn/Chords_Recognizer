"""Which onnxruntime ops take Beat This!'s time (one 1500-frame chunk, 4 threads).

uv run python experiments/profile/beat_ops.py
"""

import json
import sys
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from chordchart import beat_this  # noqa: E402


def main():
    import onnxruntime as ort

    print("onnxruntime", ort.__version__)
    with tempfile.TemporaryDirectory() as tmp:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.enable_profiling = True
        options.profile_file_prefix = str(Path(tmp) / "prof")
        session = ort.InferenceSession(
            str(beat_this.MODEL), options, providers=["CPUExecutionProvider"]
        )
        inp = session.get_inputs()[0]
        print("input", inp.name, inp.shape, inp.type)
        chunk = (
            np.random.default_rng(0).standard_normal((1, beat_this.CHUNK, 128)).astype(np.float32)
        )
        for _ in range(3):
            session.run(None, {inp.name: chunk})
        events = json.loads(Path(session.end_profiling()).read_text())
    per_op, per_node, total = Counter(), Counter(), 0
    for e in events:
        if e.get("cat") == "Node" and e["name"].endswith("_kernel_time"):
            op = e["args"]["op_name"]
            per_op[op] += e["dur"]
            per_node[e["name"]] += e["dur"]
            total += e["dur"]
    print(f"total node time {total / 3e3:.0f} ms per chunk")
    for op, dur in per_op.most_common(12):
        print(f"  {op:24} {dur / 3e3:8.1f} ms  {100 * dur / total:5.1f}%")
    print("top nodes:")
    for name, dur in per_node.most_common(8):
        print(f"  {name[:70]:70} {dur / 3e3:8.1f} ms")


if __name__ == "__main__":
    main()
