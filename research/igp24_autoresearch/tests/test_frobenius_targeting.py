from __future__ import annotations

import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DISCRIMINATOR = ROOT / "frobenius_discriminate.sage.py"


class DummyGap:
    def eval(self, _expression):
        return object()


def load_discriminator_without_sage():
    sage = types.ModuleType("sage")
    sage_all = types.ModuleType("sage.all")
    sage_all.GF = object()
    sage_all.PolynomialRing = object()
    sage_all.libgap = DummyGap()
    sage_all.prime_range = object()
    sage_env = types.ModuleType("sage.env")
    sage_env.SAGE_VERSION = "test"
    specification = importlib.util.spec_from_file_location(
        "frobenius_discriminate_test_module", DISCRIMINATOR
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("could not load discriminator source")
    module = importlib.util.module_from_spec(specification)
    with mock.patch.dict(
        sys.modules,
        {"sage": sage, "sage.all": sage_all, "sage.env": sage_env},
    ):
        specification.loader.exec_module(module)
    return module


class FrobeniusTargetFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.module = load_discriminator_without_sage()

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "rows.jsonl"
        self.rows = [
            {"sourceLabel": "24T10", "sourceR": 4},
            {"sourceLabel": "24T10", "sourceR": 8},
            {"sourceLabel": "24T20", "sourceR": 8},
        ]
        self.path.write_text(
            "".join(json.dumps(row) + "\n" for row in self.rows),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_exact_source_pair_filter(self) -> None:
        self.assertEqual(self.module.load_rows(self.path, set()), self.rows)
        selected = self.module.load_rows(
            self.path, set(), {("24T10", 8)}
        )
        self.assertEqual(selected, [self.rows[1]])

    def test_label_and_pair_filters_are_intersected(self) -> None:
        selected = self.module.load_rows(
            self.path, {"24T20"}, {("24T10", 8)}
        )
        self.assertEqual(selected, [])

    def test_repeatable_pairs_are_validated_and_deduplicated(self) -> None:
        pairs = self.module.parse_source_pairs(
            [["24T10", "8"], ["24T10", "8"], ["24T20", "24"]]
        )
        self.assertEqual(pairs, {("24T10", 8), ("24T20", 24)})
        with self.assertRaisesRegex(ValueError, "must be even"):
            self.module.parse_source_pairs([["24T10", "7"]])
        with self.assertRaisesRegex(ValueError, "invalid degree-24"):
            self.module.parse_source_pairs([["bad-label", "8"]])

    def test_help_exposes_exact_pair_and_atomic_output_options(self) -> None:
        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["frobenius_discriminate", "--help"]),
            redirect_stdout(output),
            self.assertRaises(SystemExit) as exit_context,
        ):
            self.module.main()
        self.assertEqual(exit_context.exception.code, 0)
        self.assertIn("--source-pair LABEL R", output.getvalue())
        self.assertIn("--output OUTPUT", output.getvalue())

    def test_atomic_certificate_writer_replaces_complete_text(self) -> None:
        output = Path(self.temporary_directory.name) / "certificate.json"
        output.write_text("old\n", encoding="utf-8")
        self.module.write_text_atomic(output, "new\n")
        self.assertEqual(output.read_text(encoding="utf-8"), "new\n")
        self.assertFalse(list(output.parent.glob(".certificate.json.*.tmp")))


if __name__ == "__main__":
    unittest.main()
