"""Drive the ``dvrescue`` CLI to produce a frame-level merge and its CSV log.

dvrescue does the real work — align every capture by the tape's absolute track number, pick each
frame's cleanest copy across passes, and write a valid DV stream. We invoke it once::

    dvrescue IN1.dv IN2.dv ... -m MERGED.dv --merge-log LOG.csv --csv

Two operational notes baked in here:

* dvrescue **prompts interactively** if ``-m`` or ``--merge-log`` already exists ("Overwrite?"),
  which would hang an unattended run. Callers always hand us fresh, non-existent paths; we also tie
  stdin to /dev/null so any unexpected prompt hits EOF and aborts instead of blocking forever.
* With ``--csv`` dvrescue still streams a per-frame progress dump to stdout; we discard it and show
  our own elapsed-time line instead.
"""

import os
import shutil
import subprocess
import sys
import time


def find(explicit=None):
    """Locate the dvrescue binary: explicit path, $DVRESCUE, or PATH. Raises if absent."""
    cand = explicit or os.environ.get("DVRESCUE") or "dvrescue"
    path = cand if os.path.sep in cand and os.path.exists(cand) else shutil.which(cand)
    if not path:
        raise RuntimeError("dvrescue not found (install it, or pass --dvrescue / set $DVRESCUE)")
    return path


def merge(files, merged_path, csv_path, binary="dvrescue", quiet=False):
    """Run the merge. ``merged_path`` and ``csv_path`` must not pre-exist. Raises on failure."""
    for p in (merged_path, csv_path):
        if os.path.exists(p):
            raise RuntimeError("refusing to overwrite existing %s (internal: pass a fresh path)" % p)
    argv = [find(binary), *files, "-m", merged_path, "--merge-log", csv_path, "--csv"]

    label = "merging %d capture%s with dvrescue" % (len(files), "" if len(files) == 1 else "s")
    if not quiet:
        sys.stderr.write("  %s …\n" % label)
        sys.stderr.flush()
    t0 = time.monotonic()
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    tty = sys.stderr.isatty()
    while proc.poll() is None:
        if not quiet and tty:
            sys.stderr.write("\r  %s … %ds" % (label, int(time.monotonic() - t0)))
            sys.stderr.flush()
        time.sleep(0.5)
    err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
    if not quiet and tty:
        sys.stderr.write("\r  %s … %ds, done\n" % (label, int(time.monotonic() - t0)))
        sys.stderr.flush()

    if proc.returncode != 0:
        raise RuntimeError("dvrescue failed (exit %d)%s"
                           % (proc.returncode, ": " + err.strip() if err.strip() else ""))
    if not os.path.exists(csv_path):
        raise RuntimeError("dvrescue produced no merge log%s" % (": " + err.strip() if err.strip() else ""))
