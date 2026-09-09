import json

from routeA.fetch_degree12_catalog import load_jsonl


def test_load_jsonl_skips_blank_lines(tmp_path) -> None:
    path = tmp_path / "rows.jsonl"
    path.write_text('{"base_t": 1}\n\n{"base_t": 2}\n', encoding="utf-8")
    assert load_jsonl(path) == [{"base_t": 1}, {"base_t": 2}]
