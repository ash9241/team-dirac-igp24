import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SAGE = Path("/usr/local/bin/sage")
SCRIPT = ROOT / "agent_f9_k3_safe_5027_r0.sage.py"
OUTPUTS = (
    ROOT / "data/agent_f9_k3_safe_5027_r0_results.jsonl",
    ROOT / "data/agent_f9_k3_safe_5027_r0_summary.json",
    ROOT / "outbox/agent_f9_k3_safe_5027_r0_live.txt",
)
COLLISION_CANDIDATES = OUTPUTS + tuple(Path(str(path) + ".tmp") for path in OUTPUTS)


class K3Safe5027PreflightTest(unittest.TestCase):
    def test_baseline_exclusion_is_fail_closed_and_nonwriting(self):
        before = {
            path: path.stat().st_mtime_ns for path in COLLISION_CANDIDATES if path.exists()
        }
        process = subprocess.run(
            [str(SAGE), "-python", str(SCRIPT), "--preflight-only"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )

        self.assertNotEqual(process.returncode, 0)
        if before:
            self.assertIn("not overwrite existing outputs", process.stderr)
        else:
            self.assertIn("sealed baseline exclusion", process.stderr)
            self.assertEqual(process.stdout, "")

        after = {
            path: path.stat().st_mtime_ns for path in COLLISION_CANDIDATES if path.exists()
        }
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()
