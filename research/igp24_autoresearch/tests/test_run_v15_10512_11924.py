from __future__ import annotations

import copy
import unittest
from unittest import mock

import run_v15_10512_11924 as lane


class V15RouteTests(unittest.TestCase):
    def test_route_certificate_and_conditional_envelope_are_pinned(self) -> None:
        result = lane.validate_route()
        self.assertEqual(result["mappedTargetR"], [8, 16])
        self.assertEqual(result["reachableGoldR"], [16])

    def test_route_tamper_is_rejected(self) -> None:
        row = lane.read_jsonl(lane.ROUTE_MAP)[3]
        tampered = copy.deepcopy(row)
        tampered["routes"][0]["mappedTargetR"] = [16]
        with mock.patch.object(lane, "read_jsonl", return_value=[tampered]):
            with self.assertRaisesRegex(ValueError, "certificate"):
                lane.validate_route()

    def test_canonical_source_hash_rejects_wrong_degree(self) -> None:
        with self.assertRaisesRegex(ValueError, "degree 24"):
            lane.canonical_line_hash("1,1")

    def test_output_writer_never_overwrites(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "sealed.json"
            lane.write_new(path, b"one\n")
            with self.assertRaises(FileExistsError):
                lane.write_new(path, b"two\n")
            self.assertEqual(path.read_bytes(), b"one\n")


if __name__ == "__main__":
    unittest.main()
