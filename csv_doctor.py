#!/usr/bin/env python3
"""csv-doctor: check a CSV for common data problems before you import it.

Zero third-party dependencies - Python standard library only.

Exit codes: 0 = clean (info notes allowed), 1 = errors/warnings found,
2 = file could not be read at all.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

SEVERITIES = ("error", "warn", "info")
_NOT_NUMBERS = {"nan", "inf", "-inf", "+inf", "infinity", "-infinity"}


@dataclass
class Finding:
    """One problem (or note) found in the file."""
    check: str
    severity: str
    title: str
    detail: str = ""
    examples: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "check": self.check,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "examples": list(self.examples),
        }


def read_text(path: Path) -> tuple[str, list[Finding]]:
    """Decode the file, noting BOM or non-UTF-8 fallback."""
    findings: list[Finding] = []
    raw = path.read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
        findings.append(Finding(
            "encoding", "info", "UTF-8 byte-order mark (BOM) detected",
            "Stripped automatically here; some loaders turn it into a stray "
            "character in the first header name."))
    else:
        encoding = "utf-8"
    try:
        return raw.decode(encoding), findings
    except UnicodeDecodeError:
        findings.append(Finding(
            "encoding", "warn", "File is not valid UTF-8",
            "Decoded with latin-1 as a fallback; mixed encodings can corrupt "
            "text columns."))
        return raw.decode("latin-1"), findings


def sniff_delimiter(text: str) -> str:
    try:
        return csv.Sniffer().sniff(text[:65536], delimiters=",;\t|:").delimiter
    except csv.Error:
        return ","


def classify(cell: str) -> str:
    """Classify a stripped cell as empty / int / float / text."""
    s = cell.strip()
    if not s:
        return "empty"
    if "_" in s or s.lower() in _NOT_NUMBERS:
        return "text"
    try:
        int(s)
        return "int"
    except ValueError:
        pass
    try:
        float(s)
        return "float"
    except ValueError:
        return "text"


def analyze(path: Path, max_examples: int = 5, delimiter: str | None = None) -> tuple[list[Finding], dict]:
    """Run every check; return (findings, summary)."""
    findings, summary = [], {}
    text, pre = read_text(path)
    findings.extend(pre)

    delim = delimiter or sniff_delimiter(text)
    if delim != ",":
        findings.append(Finding(
            "delimiter", "info", f"Delimiter detected: {delim!r}",
            "Not a comma - make sure your import tool agrees."))

    # Use io.StringIO to preserve multiline cells instead of splitlines()
    all_rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    blank = sum(1 for r in all_rows if not any(c.strip() for c in r))
    if blank:
        findings.append(Finding(
            "blank-lines", "warn", f"{blank} blank line(s)",
            "Importers usually turn these into empty/NaN rows."))
    rows = [r for r in all_rows if any(c.strip() for c in r)]

    summary = {"file": path.name, "rows": max(len(rows) - 1, 0),
               "columns": len(rows[0]) if rows else 0}
    if not rows:
        findings.append(Finding(
            "structure", "error", "File has no rows", "Nothing to import."))
        return findings, summary
    header, data = rows[0], rows[1:]
    ncols = len(header)
    summary["columns"] = ncols

    # -- headers ------------------------------------------------------
    unnamed = [i + 1 for i, h in enumerate(header) if not h.strip()]
    if unnamed:
        findings.append(Finding(
            "headers", "warn", f"{len(unnamed)} column(s) with empty header name",
            "Position(s): " + ", ".join(map(str, unnamed))))
    dup_names = [n for n, c in Counter(h.strip() for h in header).items() if c > 1]
    if dup_names:
        findings.append(Finding(
            "headers", "warn", f"Duplicate header name(s): {', '.join(map(repr, dup_names))}",
            "Most loaders rename or silently overwrite duplicate columns."))
    ws_headers = [h for h in header if h != h.strip()]
    if ws_headers:
        findings.append(Finding(
            "headers", "warn", f"{len(ws_headers)} header name(s) have stray whitespace",
            "Example: " + repr(sorted(ws_headers)[0])))

    # -- structure ----------------------------------------------------
    ragged = [(i + 2, len(r)) for i, r in enumerate(data) if len(r) != ncols]
    if ragged:
        ex = [f"line {ln}: {n} field(s), expected {ncols}"
              for ln, n in ragged[:max_examples]]
        findings.append(Finding(
            "ragged-rows", "error",
            f"{len(ragged)} row(s) with the wrong number of fields",
            "Short rows become missing values, long rows get truncated or "
            "shift columns.", ex))

    # -- duplicates ---------------------------------------------------
    counts = Counter(tuple(r) for r in data)
    dups = {k: c for k, c in counts.items() if c > 1}
    if dups:
        extra = sum(c - 1 for c in dups.values())
        ex = [repr(k)[:80] for k in list(dups)[:max_examples]]
        findings.append(Finding(
            "duplicates", "warn",
            f"{len(dups)} duplicate row(s) ({extra} extra copies)",
            "Exact copies of earlier rows; decide whether they are true "
            "duplicates before aggregating.", ex))

    # -- per-column scans --------------------------------------------
    # Safe bounds-checked colname to guard against rows with extra columns
    colname = lambda i: (header[i].strip() if i < len(header) else f"(column {i + 1})") or f"(column {i + 1})"
    ws_counter: Counter = Counter()
    ws_examples: list[str] = []
    empty_counter: Counter = Counter()
    type_counter: dict[int, Counter] = {}
    zero_counter: Counter = Counter()
    zero_examples: list[str] = []
    distinct: dict[int, set] = {}

    for row in data:
        for i, cell in enumerate(row):
            kinds = type_counter.setdefault(i, Counter())
            distinct.setdefault(i, set())
            if cell != cell.strip():
                ws_counter[i] += 1
                if len(ws_examples) < max_examples:
                    ws_examples.append(f"{colname(i)}: {cell!r}")
            kind = classify(cell)
            if kind == "empty":
                empty_counter[i] += 1
                continue
            kinds[kind] += 1
            distinct[i].add(cell.strip())
            if re.fullmatch(r"0\d+", cell.strip()):
                zero_counter[i] += 1
                if len(zero_examples) < max_examples:
                    zero_examples.append(f"{colname(i)}: {cell.strip()!r}")

    nrows = len(data)
    if ws_counter:
        worst = ", ".join(f"{colname(i)} ({c})" for i, c in
                          ws_counter.most_common(max_examples))
        findings.append(Finding(
            "whitespace", "warn",
            f"{sum(ws_counter.values())} cell(s) with leading/trailing whitespace",
            "Breaks joins, grouping and lookups. Worst: " + worst, ws_examples))
    for i, c in sorted(empty_counter.items()):
        pct = 100 * c // max(nrows, 1)
        if pct >= 50:
            findings.append(Finding(
                "empty-cells", "warn",
                f"Column {colname(i)!r} is {pct}% empty ({c}/{nrows})",
                "Half-empty columns are often a broken export or a shifted row."))
    for i, kinds in sorted(type_counter.items()):
        present = set(kinds)
        if present == {"int", "float"}:
            continue  # plain numeric mix is fine
        if len(present) > 1:
            breakdown = ", ".join(f"{k}: {v}" for k, v in kinds.most_common())
            findings.append(Finding(
                "mixed-types", "warn",
                f"Column {colname(i)!r} mixes types ({breakdown})",
                "Forces the whole column to text in Excel/pandas; check for "
                "stray notes like 'N/A' among numbers."))
    if zero_counter:
        worst = ", ".join(f"{colname(i)} ({c})" for i, c in
                          zero_counter.most_common(max_examples))
        findings.append(Finding(
            "leading-zeros", "warn",
            f"{sum(zero_counter.values())} numeric-looking cell(s) with leading zeros",
            "Excel and float parsing drop them (zip codes, account IDs). "
            "Import those columns as text. Worst: " + worst, zero_examples))
    for i, values in sorted(distinct.items()):
        if nrows > 1 and len(values) == 1:
            findings.append(Finding(
                "constant-column", "info",
                f"Column {colname(i)!r} has a single value: {next(iter(values))!r}",
                "Harmless if intended; a red flag for a broken merge or fill."))

    return findings, summary


def fix_csv(path: Path, out_path: Path, delimiter: str | None = None) -> dict:
    """Auto-repair CSV: trim whitespace, strip blank lines, deduplicate, pad ragged rows."""
    text, _ = read_text(path)
    delim = delimiter or sniff_delimiter(text)
    all_rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    rows = [r for r in all_rows if any(c.strip() for c in r)]
    if not rows:
        raise ValueError("Cannot fix an empty CSV file")

    header = [h.strip() for h in rows[0]]
    ncols = len(header)

    seen = set()
    clean_data = []
    removed_dups = 0
    adjusted_rows = 0

    for r in rows[1:]:
        clean_row = [c.strip() for c in r]
        if len(clean_row) < ncols:
            clean_row = clean_row + [""] * (ncols - len(clean_row))
            adjusted_rows += 1
        elif len(clean_row) > ncols:
            clean_row = clean_row[:ncols]
            adjusted_rows += 1
        row_tuple = tuple(clean_row)
        if row_tuple in seen:
            removed_dups += 1
            continue
        seen.add(row_tuple)
        clean_data.append(clean_row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter=delim)
        writer.writerow(header)
        writer.writerows(clean_data)

    return {
        "output_file": str(out_path),
        "rows": len(clean_data),
        "removed_duplicates": removed_dups,
        "adjusted_ragged_rows": adjusted_rows,
    }


def render(findings: list[Finding], summary: dict) -> str:
    lines = [f"csv-doctor report: {summary.get('file', '?')} "
             f"({summary.get('rows', 0)} data rows, "
             f"{summary.get('columns', 0)} columns)", ""]
    real = [f for f in findings if f.severity != "info"]
    for f in findings:
        head = f"{f.severity.upper():5}  {f.check:16}  {f.title}"
        lines.append(head)
        if f.detail:
            lines.append(f"{'':7}{f.detail}")
        for ex in f.examples:
            lines.append(f"{'':11}- {ex}")
    if not findings:
        lines.append("No problems found.")
    errors = sum(1 for f in real if f.severity == "error")
    warns = sum(1 for f in real if f.severity == "warn")
    notes = len(findings) - len(real)
    lines.append("")
    if real:
        lines.append(f"verdict: {len(real)} problem(s) "
                     f"({errors} error, {warns} warning)"
                     + (f", {notes} note(s)" if notes else ""))
    else:
        lines.append("verdict: clean"
                     + (f" ({notes} note(s))" if notes else ""))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="csv_doctor",
        description="Check a CSV for common data problems before you import it.")
    ap.add_argument("file", help="path to the CSV to check")
    ap.add_argument("--json", action="store_true",
                    help="print a machine-readable report instead")
    ap.add_argument("--max-examples", type=int, default=5,
                    help="examples to show per finding (default: 5)")
    ap.add_argument("-d", "--delimiter", default=None,
                    help="explicit delimiter (e.g. ',', ';', '\\t')")
    ap.add_argument("--error-only", action="store_true",
                    help="only return exit code 1 on errors, ignore warnings")
    ap.add_argument("-q", "--quiet", action="store_true",
                    help="suppress console output (exit code only)")
    ap.add_argument("--fix", nargs="?", const="", default=None,
                    metavar="OUT_FILE",
                    help="auto-clean whitespace, blank lines, and duplicates into a fixed CSV")
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.is_file():
        if not args.quiet:
            print(f"csv-doctor: cannot read {args.file}", file=sys.stderr)
        return 2

    # Auto-fix mode
    if args.fix is not None:
        out_path = Path(args.fix) if args.fix else path.with_name(f"{path.stem}_clean.csv")
        try:
            res = fix_csv(path, out_path, delimiter=args.delimiter)
            if not args.quiet:
                if args.json:
                    print(json.dumps(res, indent=2))
                else:
                    print(f"csv-doctor: fixed CSV written to {out_path} "
                          f"({res['rows']} rows, removed {res['removed_duplicates']} duplicate(s), "
                          f"adjusted {res['adjusted_ragged_rows']} ragged row(s))")
            return 0
        except Exception as exc:
            if not args.quiet:
                print(f"csv-doctor: fix failed: {exc}", file=sys.stderr)
            return 2

    try:
        findings, summary = analyze(path, args.max_examples, delimiter=args.delimiter)
    except (OSError, csv.Error, UnicodeError) as exc:
        if not args.quiet:
            print(f"csv-doctor: failed to analyse {args.file}: {exc}",
                  file=sys.stderr)
        return 2

    if not args.quiet:
        if args.json:
            real = [f for f in findings if f.severity != "info"]
            print(json.dumps({
                "summary": summary,
                "problems": len(real),
                "findings": [f.to_dict() for f in findings],
            }, indent=2))
        else:
            print(render(findings, summary))

    if args.error_only:
        return 1 if any(f.severity == "error" for f in findings) else 0
    return 1 if any(f.severity in ("error", "warn") for f in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
