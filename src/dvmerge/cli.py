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
import hashlib
import os
import shutil
import sys
import tempfile

from . import __version__, DEFAULT_FPS
from . import dvrescue, parse, plan, report

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


def _signature(files):
    """Content fingerprint of the input set: name, size, mtime. Keys the merge-log cache."""
    h = hashlib.sha1()
    for f in files:
        st = os.stat(f)
        h.update(("%s|%d|%d\n" % (os.path.basename(f), st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()[:16]


def _merge_log(files, output, args):
    """Return a path to the CSV merge log for ``files``, running dvrescue if needed.

    Returns (csv_path, cleanup) where cleanup() removes any temp artifacts. With ``output`` set we
    always run the merge (the .dv must be produced) and move it into place; otherwise we use the
    cache when fresh and write the merged DV to a temp file we delete.
    """
    sig = None if args.no_cache else _signature(files)
    cache_dir = args.cache_dir or os.path.join(os.path.dirname(os.path.abspath(files[0])), ".dvmerge")
    cache_csv = os.path.join(cache_dir, "merge-%s.csv" % sig) if sig else None

    if not output and cache_csv and os.path.exists(cache_csv):
        return cache_csv, (lambda: None)

    # Need a real merge. Put temp artifacts on the same filesystem as the eventual home (big disk).
    home = os.path.dirname(os.path.abspath(output)) if output else \
        os.path.dirname(os.path.abspath(files[0]))
    fd, tmp_dv = tempfile.mkstemp(prefix=".dvmerge-", suffix=".dv", dir=home)
    os.close(fd)
    os.remove(tmp_dv)  # dvrescue wants to create it fresh
    tmp_csv = tmp_dv[:-3] + ".csv"

    def cleanup():
        for p in (tmp_dv, tmp_csv):
            try:
                os.remove(p)
            except OSError:
                pass

    try:
        dvrescue.merge(files, tmp_dv, tmp_csv, binary=args.dvrescue)
    except Exception:
        cleanup()
        raise

    if cache_csv:
        os.makedirs(cache_dir, exist_ok=True)
        shutil.copyfile(tmp_csv, cache_csv)

    if output:
        os.replace(tmp_dv, output)            # atomic, overwrites without dvrescue's prompt
        return tmp_csv, (lambda: _rm(tmp_csv))
    cleanup()                                  # analyse-only: keep nothing large
    return (cache_csv or tmp_csv), (lambda: None) if cache_csv else (lambda: _rm(tmp_csv))


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


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
        csv_path, cleanup = _merge_log(files, args.output, args)
    except RuntimeError as e:
        print("error: %s" % e, file=sys.stderr)
        return 2

    try:
        frames, _ = parse.parse(csv_path, args.fps, nfiles=len(files))
        if not frames:
            print("error: merge log has no frames — are these the same tape?", file=sys.stderr)
            return 2
        p = plan.build(frames, files, args.fps, bridge_s=args.bridge, min_s=args.min)
        md = report.render(p)
    finally:
        cleanup()

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
