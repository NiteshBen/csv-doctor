# csv-doctor

Check a CSV for common data problems **before** you import it — in one command, with zero dependencies.

You just received `quarterly_report.csv` from someone else. You open it in pandas or Excel, and
only later discover the zip codes lost their leading zeros, two rows are exact duplicates, one row
is missing a field so every column after it shifted, and a `"N/A"` note turned the whole amount
column into text. csv-doctor surfaces all of that up front, with line numbers and examples, and
returns a non-zero exit code so you can gate a pipeline on it.

## What it checks

| Check | Severity | What it catches |
|---|---|---|
| ragged-rows | error | rows with the wrong number of fields (the #1 cause of silently shifted columns) |
| duplicates | warn | exact duplicate data rows |
| headers | warn | empty, duplicate, or whitespace-padded column names |
| whitespace | warn | leading/trailing spaces in cells (breaks joins and group-bys) |
| mixed-types | warn | a column that mixes numbers and text (`19.99` next to `N/A`) |
| leading-zeros | warn | numeric-looking values like `02134` that Excel/float parsing will mangle |
| empty-cells | warn | columns that are ≥ 50 % empty (often a broken export) |
| blank-lines | warn | blank lines that become NaN rows downstream |
| encoding | warn/info | non-UTF-8 files (latin-1 fallback), BOM notes |
| delimiter | info | sniffed delimiter when it is not a comma |
| constant-column | info | a column with a single value |

## How to run

Python 3.9+ standard library only — nothing to install.

```
py csv_doctor.py examples/dirty_sales.csv
```

```
csv-doctor report: dirty_sales.csv (6 data rows, 5 columns)

WARN   blank-lines       1 blank line(s)
       Importers usually turn these into empty/NaN rows.
WARN   headers           1 header name(s) have stray whitespace
       Example: 'zip '
ERROR  ragged-rows       1 row(s) with the wrong number of fields
       Short rows become missing values, long rows get truncated or shift columns.
           - line 6: 4 field(s), expected 5
WARN   duplicates        1 duplicate row(s) (1 extra copies)
       Exact copies of earlier rows; decide whether they are true duplicates before aggregating.
           - ('1002', 'Bob Jones', '94107', '19.99', 'shipped')
WARN   whitespace        1 cell(s) with leading/trailing whitespace
       Breaks joins, grouping and lookups. Worst: customer (1)
           - customer: ' Eve Lee '
WARN   mixed-types       Column 'amount' mixes types (float: 4, text: 2)
       Forces the whole column to text in Excel/pandas; check for stray notes like 'N/A' among numbers.
WARN   leading-zeros     2 numeric-looking cell(s) with leading zeros
       Excel and float parsing drop them (zip codes, account IDs). Import those columns as text. Worst: zip (2)
           - zip: '02134'
           - zip: '02134'
INFO   constant-column   Column 'status' has a single value: 'shipped'
       Harmless if intended; a red flag for a broken merge or fill.

verdict: 7 problem(s) (1 error, 6 warning), 1 note(s)
```

### Machine-readable output and exit codes

```
py csv_doctor.py data.csv --json          # JSON report for scripts/CI
py csv_doctor.py data.csv --max-examples 20
```

| Exit code | Meaning |
|---|---|
| 0 | clean (informational notes allowed) |
| 1 | at least one error or warning found |
| 2 | file could not be read |

Use it as a gate, e.g. `py csv_doctor.py upload.csv || echo "fix the file first"`.

## Limitations

- Line numbers are 1-based over physical lines; a quoted field spanning multiple lines shifts them.
- Type detection is intentionally simple (int / float / text) — it is a smoke alarm, not a schema validator.

## Testing

```
py -m pytest
```

10 tests, one per check plus the CLI exit codes and JSON shape.

## License

MIT — see [LICENSE](LICENSE).
