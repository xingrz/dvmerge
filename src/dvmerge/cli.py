"""dvmerge command line — usage mirrors hdvmerge.

    dvmerge CLIP-*.dv                 analyse: merge with dvrescue, print the re-capture list
    dvmerge CLIP-*.dv -o merged.dv    also keep merged.dv and write merged.dv.report.md beside it

One dvrescue merge does the alignment and frame-level picking; dvmerge discovers the captures, drives
that merge, and renders its CSV log as the report. The merge log is cached by input fingerprint, so
re-running to re-read the report or re-tune --bridge is instant; adding or changing a capture re-runs
the merge. Without -o nothing large is kept — the merged DV is written to a temp file and removed once
its log is parsed.
"""

import argparse
import json
import os
import sys

from . import __version__, DEFAULT_FPS
from . import report, jsonout, run

EXTS = (".dv", ".dif")


def _discover(inputs):
    files = []
    for p in inputs:
        if os.path.isdir(p):
            files += [os.path.join(p, n) for n in sorted(os.listdir(p))
                      if n.lower().endswith(EXTS)]
        else:
            files.append(p)
    seen, out = set(), []
    for f in files:
        a = os.path.abspath(f)
        if a not in seen and os.path.exists(f):
            seen.add(a)
            out.append(f)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="dvmerge",
        description="Align, merge, and report overlapping DV tape captures via dvrescue.")
    ap.add_argument("--version", action="version", version="dvmerge " + __version__)
    ap.add_argument("inputs", nargs="+", help="capture files (.dv) or a directory of them")
    ap.add_argument("-o", "--output", metavar="FILE",
                    help="keep the merged DV at FILE and write FILE.report.md beside it; "
                         "without -o, only analyse and print the re-capture report")
    ap.add_argument("--fps", type=float, default=DEFAULT_FPS,
                    help="tape frame rate (default 25 for PAL; use 29.97 for NTSC)")
    ap.add_argument("--bridge", type=float, default=3.0, metavar="SEC",
                    help="merge damaged patches less than SEC apart into one re-capture target (default 3)")
    ap.add_argument("--min", type=float, default=0.5, metavar="SEC",
                    help="omit re-capture regions shorter than SEC (default 0.5)")
    ap.add_argument("--json", action="store_true",
                    help="emit the analysis as one JSON object on stdout instead of the Markdown "
                         "report (a faithful dump of the model for tools to consume); all human "
                         "status goes to stderr")
    ap.add_argument("--no-cache", action="store_true", help="do not read or write the merge-log cache")
    ap.add_argument("--cache-dir", metavar="DIR", help="store the merge-log cache in DIR")
    ap.add_argument("--dvrescue", metavar="PATH", help="path to the dvrescue binary")
    args = ap.parse_args(argv)

    files = _discover(args.inputs)
    if not files:
        print("error: no .dv capture files found", file=sys.stderr)
        return 2

    print("captures: %s" % " + ".join(os.path.basename(f) for f in files), file=sys.stderr)
    try:
        p = run.analyze(files, output=args.output, fps=args.fps, bridge_s=args.bridge,
                        min_s=args.min, no_cache=args.no_cache, cache_dir=args.cache_dir,
                        dvrescue_bin=args.dvrescue)
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    md = report.render(p)
    out_json = json.dumps(jsonout.analysis(p), sort_keys=True) if args.json else None

    if args.json:
        print(out_json)             # stdout: exactly one JSON object; human chatter is on stderr
    else:
        print()
        print(md)

    if args.output:
        rp = args.output + ".report.md"
        with open(rp, "w") as f:
            f.write(md + "\n")
        print("wrote %s (%.2f GB) and %s"
              % (args.output, os.path.getsize(args.output) / 1e9, os.path.basename(rp)),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
