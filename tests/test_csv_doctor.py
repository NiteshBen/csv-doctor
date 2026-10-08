"""Tests for csv_doctor — each check is exercised on a small crafted file."""
import json

import pytest

from csv_doctor import analyze, main


def write_csv(tmp_path, content, name="t.csv"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def by_check(findings, check):
    return next(f for f in findings if f.check == check)


CLEAN = "id,name,score\n1,Amy,9.5\n2,Bob,8.0\n3,Carol,7.5\n"
DIRTY = (
    "order_id,customer,zip ,amount,status\n"
    "1001,Amy Smith,02134,49.99,shipped\n"
    "\n"
    "1002,Bob Jones,94107,19.99,shipped\n"
    "1002,Bob Jones,94107,19.99,shipped\n"
    "1003,Carol Wu,02134,N/A,shipped\n"
    "1004,Dan Patel,10001,shipped\n"
    "1005, Eve Lee ,33101,5.00,shipped\n"
)


def test_clean_csv_has_no_errors_or_warnings(tmp_path):
    findings, summary = analyze(write_csv(tmp_path, CLEAN))
    assert not [f for f in findings if f.severity in ("error", "warn")]
    assert summary["rows"] == 3 and summary["columns"] == 3


def test_ragged_rows_reported_with_line_numbers(tmp_path):
    findings, _ = analyze(write_csv(tmp_path, DIRTY))
    f = by_check(findings, "ragged-rows")
    assert f.severity == "error"
    assert "line 6" in f.examples[0]      # 1004 row is short
    assert "expected 5" in f.examples[0]


def test_duplicate_rows_counted(tmp_path):
    findings, _ = analyze(write_csv(tmp_path, DIRTY))
    f = by_check(findings, "duplicates")
    assert "1 duplicate row" in f.title
    assert "1002" in f.examples[0]


def test_whitespace_and_header_problems(tmp_path):
    findings, _ = analyze(write_csv(tmp_path, DIRTY))
    assert by_check(findings, "whitespace").examples[0].startswith("customer")
    dup = by_check(findings, "headers")
    assert "whitespace" in dup.title          # header "zip " has trailing space


def test_mixed_types_and_leading_zeros_flagged(tmp_path):
    findings, _ = analyze(write_csv(tmp_path, DIRTY))
    mixed = by_check(findings, "mixed-types")
    assert "amount" in mixed.title and "text" in mixed.detail
    zeros = by_check(findings, "leading-zeros")
    assert any("02134" in e for e in zeros.examples)


def test_blank_lines_flagged_and_ignored(tmp_path):
    findings, summary = analyze(write_csv(tmp_path, DIRTY))
    assert by_check(findings, "blank-lines").severity == "warn"
    assert summary["rows"] == 6               # blank line not counted as data


def test_constant_column_is_info_only(tmp_path):
    csv_text = "id,name,status\n1,Amy,ok\n2,Bob,ok\n3,Carol,ok\n"
    findings, _ = analyze(write_csv(tmp_path, csv_text))
    f = by_check(findings, "constant-column")
    assert f.severity == "info" and "status" in f.title


def test_exit_code_reflects_findings(tmp_path, capsys):
    dirty = write_csv(tmp_path, DIRTY)
    clean = write_csv(tmp_path, CLEAN, name="clean.csv")
    missing = tmp_path / "nope.csv"
    assert main([str(dirty)]) == 1
    assert main([str(clean)]) == 0
    assert main([str(missing)]) == 2
    assert "csv-doctor report" in capsys.readouterr().out


def test_json_output_shape(tmp_path, capsys):
    rc = main([str(write_csv(tmp_path, DIRTY)), "--json"])
    out = capsys.readouterr().out
    assert rc == 1
    report = json.loads(out)
    assert report["summary"]["rows"] == 6
    assert report["problems"] >= 1
    assert {"check", "severity", "title"} <= set(report["findings"][0])


def test_int_and_float_mix_is_not_a_problem(tmp_path):
    csv_text = "id,price\n1,10\n2,9.5\n3,8\n"
    findings, _ = analyze(write_csv(tmp_path, csv_text))
    assert "mixed-types" not in {f.check for f in findings}
