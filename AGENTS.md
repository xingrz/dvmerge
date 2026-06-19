# Working context for agents

`dvmerge` merges overlapping **DV** tape captures (`.dv`) and prints a re-capture list. It is the DV
sibling of `hdvmerge` (which does the same for HDV/MPEG-TS) and is meant to feel identical at the
CLI: `dvmerge CLIP-*.dv` to analyse, `-o FILE` to also keep the merged DV and write `FILE.report.md`.

## The load-bearing idea

Unlike hdvmerge, dvmerge does **not** implement the merge itself. `dvrescue` (MIPoPS) already aligns
DV captures by the tape's absolute track number (`abst`), picks each frame's cleanest copy block by
block, and writes a valid DV stream. dvmerge runs exactly one merge:

    dvrescue IN… -m merged.dv --merge-log log.csv --csv -x log.xml

and treats the **CSV merge log** as the source of truth for the merged tape. Each row is one frame
written to the merged output, in tape order, with `tc` (tape SMPTE), `rdt` (recording clock),
`BlockErrors` (0 = a clean copy was found), and `Status` (one char per input: `' '` clean / `'P'`
damaged / `'M'` missing). The same run's **`-x` XML** carries the per-input, per-frame detail the CSV
omits (concealment by STA type + audio errors), which becomes the per-capture error profile.

## What dvmerge owns (and the rest must not creep into)

- **Discovery** of `.dv` inputs (order matters: it maps to `Status` columns).
- **Driving dvrescue** ([dvrescue.py](src/dvmerge/dvrescue.py)). Two gotchas encoded there:
  dvrescue prompts to overwrite an existing `-m`/`--merge-log` target (would hang) — so callers pass
  fresh temp paths and stdin is tied to /dev/null; and `--csv` still spams per-frame progress to
  stdout, which we discard.
- **Parsing** the CSV ([parse.py](src/dvmerge/parse.py)) and **planning** re-capture spans
  ([plan.py](src/dvmerge/plan.py)): coalesce imperfect frames, bridge short clean gaps, recover
  *missing* frames as gaps in the `tc` sequence (they have no CSV row), and compute per-span
  coverage from `Status`.
- **Per-capture error profile** ([xmlinfo.py](src/dvmerge/xmlinfo.py)): mine the `-x` XML for each
  input's concealment — `framesConcealed` / `concealedFrac` (the TRUE rate, from the `<frames count>`
  totals, not the verbosity-dependent emitted `<frame>` count), `avgConcealedPct`, `evenSharePct`
  (azimuth split), the dominant `staMethod`, the full `staHistogram`, and the audio side
  (`audioConcealedFrac`). Attached to the `Plan` as `source_profiles`.
- **Rendering** ([report.py](src/dvmerge/report.py)) in hdvmerge's report shape, plus a structured
  **JSON dump** ([jsonout.py](src/dvmerge/jsonout.py), `--json`): a faithful serialization of the
  `Plan` (tallies, re-capture spans with coverage, per-capture spans) that a GUI/tool consumes
  instead of scraping Markdown — NOT a normalised external schema (the consumer normalises). Normal
  CLI use never reads it, so [tests/test_jsonout.py](tests/test_jsonout.py) pins its shape to the
  model; keep them in lock-step when you touch `plan.py` / `parse.py`.
- **Caching** the merge log by input fingerprint (`.dvmerge/merge-<sig>.csv`) so re-reads and
  `--bridge` tweaks don't re-merge; any input change re-runs dvrescue.
- **A library entry** ([run.py](src/dvmerge/run.py)): `run.analyze(files, …) -> Plan` (and the lower
  `run.merge_log`) — discover-implied → drive dvrescue (cached) → parse → plan in one call, so the
  CLI and importing tools (the tapeflow GUI sidecar) share **one** path. Import this; don't
  re-implement the orchestration. `cli.py` is a thin wrapper over it.

## Invariants

- Analyse mode (no `-o`) keeps nothing large: the merged DV goes to a temp file deleted after its log
  is parsed. `-o` writes the merged DV via `os.replace` (atomic, no dvrescue prompt) and the report
  beside it.
- The report describes the **merged output**, not a hypothetical full tape. "missing" = a jump in the
  physical track number `abst` between consecutive written frames (no capture had that tape position).
  `abst`, not `tc`, is the arbiter — a tc jump with continuous abst is a camera stop/start, not lost
  tape. Falls back to the `tc` delta only where no `abst` is reported.
- Pure standard library. The only external dependency is the `dvrescue` binary. No ffmpeg.

## Tests

`python -m unittest discover -s tests` — synthetic CSV logs, no dvrescue or sample captures.
