# dvmerge

Align, merge, and report overlapping **DV tape captures** — the same workflow as
[hdvmerge](https://github.com/xingrz/hdvmerge), but for DV instead of HDV. When a worn DV tape
reads poorly you capture it in several passes — stop, rewind a little, retry whenever mosaic starts
creeping in — ending up with several `.dv` files that overlap and each carry some errors. dvmerge
folds them into one best-of file and tells you exactly which tape spots, if any, still need another
pass.

It is a thin wrapper around **[dvrescue](https://mediaarea.net/DVRescue)** (MIPoPS), which already
does the hard part for DV: it aligns every capture by the tape's absolute track number, picks each
frame's cleanest copy across passes block by block, and writes a valid DV stream with its metadata
intact. dvmerge drives one dvrescue merge and turns its per-frame CSV log into the **re-capture
list** — the report you read each round.

## Why

A dirty tape doesn't fail all at once: it reads fine for a while, then mosaic builds up as debris
loads onto the heads, and you can't watch it every second. So you re-capture blind — rewind a bit,
go again — and hope the new pass caught what the last one smeared. dvmerge removes the guesswork: it
compares every pass and prints the spots where *no* pass has a clean frame, each cued by **tape
timecode** (to find it on the deck) and the camera's **recording time** (as a cross-check). You
re-shoot those, drop the new file in, and the list shrinks.

## How it works

DV capture over FireWire copies the tape bit-for-bit, and every DV frame carries its absolute track
number on tape (`abst`) plus the camera's recording clock (`rdt`) and SMPTE timecode (`tc`).
dvrescue uses `abst` as a frame-accurate, metadata-free coordinate to line all the captures up and
pick the best copy of each frame. dvmerge reads the result:

```
   overlapping captures (.dv, read-only)
        │
      merge    one `dvrescue … -m merged.dv --merge-log log.csv --csv` run:
        │      aligns every capture by tape position, picks each frame's cleanest copy,
        │      writes the merged DV + a per-frame log. (Cached by input fingerprint.)
        ▼
      report   parse the log: every frame is clean / mosaic (present but still damaged) /
        │      missing (no capture has it). Coalesce the imperfect ones into re-capture spans,
        │      each tagged with tape TC, recording time, and how many captures cover it.
        └─►  with -o   keep merged.dv and write merged.dv.report.md beside it
```

The merge log is cached next to the captures (`.dvmerge/`), so re-reading the report or re-tuning
`--bridge` is instant; adding or changing a capture re-runs the merge. Without `-o`, nothing large
is kept — the merged DV goes to a temp file that is deleted once its log is parsed.

## Install

Python 3.9+ and the `dvrescue` CLI on your PATH. No third-party Python packages.

```sh
pip install -e .          # provides the `dvmerge` command
```

`dvrescue` is from MediaArea/MIPoPS (`brew install dvrescue` on macOS).

## Use

```sh
dvmerge CLIP-*.dv                  # analyse: merge, print the re-capture list
dvmerge CLIP-*.dv -o merged.dv     # same, then keep merged.dv and write merged.dv.report.md
```

The loop: run it to see what needs re-capturing → re-capture those tape spots (cue on the printed
**tape TC**, leave ≥15 s of good footage on both sides, and *overlap the previous good take* so the
passes join with no gap) → drop the new file in → run again until the list is only physically
unreadable spots → add `-o` to keep the merged file.

## Options

| Flag | Purpose |
| --- | --- |
| `INPUT…` | Capture files (`.dv`) or a directory of them. Aligned and merged together. |
| `-o FILE` | Keep the merged DV at `FILE` and write `FILE.report.md` beside it; without `-o`, only analyse and print. |
| `--fps N` | Tape frame rate (default `25` for PAL; `29.97` for NTSC). |
| `--bridge SEC` | Merge damaged patches less than `SEC` apart into one re-capture target (default `3`). |
| `--min SEC` | Omit re-capture regions shorter than `SEC` (default `0.5`). |
| `--no-cache` | Don't read or write the merge-log cache. |
| `--cache-dir DIR` | Store the merge-log cache in `DIR`. |
| `--dvrescue PATH` | Path to the dvrescue binary (or set `$DVRESCUE`). |

## What you get

- One **best-of** DV from many overlapping captures: dvrescue picks each tape frame's cleanest copy,
  block by block, across every pass — so a mosaic in one capture is repaired from another wherever a
  cleaner copy exists.
- A **re-capture list**: the exact tape spans where the merged frame is still imperfect, split into
  *mosaic* (present but damaged — you have dirty copies to improve on) and *missing* (no capture has
  it — content is absent until re-captured), each with tape TC, recording time, duration, severity,
  and which captures cover it.
- The merge, the merged DV, and its metadata are all dvrescue's — dvmerge never re-encodes.

## Limitations

- The re-capture report reflects what made it into the **merged output**. Frames missing from every
  capture have no row in dvrescue's log; dvmerge recovers them from jumps in the absolute track
  number `abst` (the physical tape position), so a camera stop/start or non-record-run timecode is
  *not* mistaken for missing tape — only a real gap in tape position counts. If a capture reports no
  `abst` at all, it falls back to the timecode delta there.
- `--fps` is a single value per run; mixing PAL and NTSC captures in one merge is not supported
  (nor would aligning them make sense).
- dvmerge delegates the merge entirely to dvrescue; its correctness and metadata handling are
  dvrescue's. dvmerge owns only discovery, caching, and the report.

## Development

```sh
python -m unittest discover -s tests        # deterministic; no dvrescue or sample captures needed
```

Tests build tiny synthetic merge logs in [tests/test_plan.py](tests/test_plan.py).
