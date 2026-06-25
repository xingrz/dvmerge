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
import threading
import time


def find(explicit=None):
    """Locate the dvrescue binary: explicit path, $DVRESCUE, or PATH. Raises if absent."""
    cand = explicit or os.environ.get("DVRESCUE") or "dvrescue"
    path = cand if os.path.sep in cand and os.path.exists(cand) else shutil.which(cand)
    if not path:
        raise RuntimeError("dvrescue not found (install it, or pass --dvrescue / set $DVRESCUE)")
    return path


def merge(files, merged_path, csv_path, xml_path=None, binary="dvrescue", quiet=False,
          on_progress=None):
    """Run the merge. ``merged_path`` and ``csv_path`` (and ``xml_path`` if given) must not pre-exist.
    Raises on failure.

    With ``xml_path`` set we also ask for the ``-x`` XML in the same pass — it carries the per-input,
    per-DIF-sequence error-concealment status (STA) the CSV omits, for free (one dvrescue run).

    dvrescue streams one line per processed frame to stdout (``--csv``); ``on_progress(frames_done)``
    is called as those arrive (throttled) so a caller can show real export progress instead of a
    blind spinner."""
    for p in (merged_path, csv_path, xml_path):
        if p and os.path.exists(p):
            raise RuntimeError("refusing to overwrite existing %s (internal: pass a fresh path)" % p)
    argv = [find(binary), *files, "-m", merged_path, "--merge-log", csv_path, "--csv"]
    if xml_path:
        argv += ["-x", xml_path]

    label = "merging %d capture%s with dvrescue" % (len(files), "" if len(files) == 1 else "s")
    if not quiet:
        sys.stderr.write("  %s …\n" % label)
        sys.stderr.flush()
    t0 = time.monotonic()
    # Force a UTF-8 locale for dvrescue. It decodes its argv (the input paths) using the locale's
    # charset; under a non-UTF-8 locale (a bare `LANG=C` NAS, common on Synology) a non-ASCII filename
    # is mangled and dvrescue, unable to open it, falls back to capturing from device 0 ("device not
    # found: 0"). Python may itself be in UTF-8 mode there (so reading the files / emitting JSON works),
    # which is exactly why HDV verify succeeds while DV fails — the difference is this subprocess.
    env = {**os.environ, "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8"}
    proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    # drain stderr in a thread so a chatty error stream can't deadlock the stdout read below
    err_box = []
    th = threading.Thread(target=lambda: err_box.append(proc.stderr.read() if proc.stderr else b""))
    th.daemon = True
    th.start()

    tty = sys.stderr.isatty()
    count = 0
    last = 0.0
    for _line in proc.stdout or ():            # one line per frame dvrescue emits
        count += 1
        now = time.monotonic()
        if now - last >= 0.25:                 # throttle progress + the tty line
            last = now
            if on_progress:
                on_progress(count)
            if not quiet and tty:
                sys.stderr.write("\r  %s … %ds (%d frames)" % (label, int(now - t0), count))
                sys.stderr.flush()
    proc.wait()
    th.join()
    err = (b"".join(err_box)).decode("utf-8", "replace")
    if on_progress:
        on_progress(count)
    if not quiet and tty:
        sys.stderr.write("\r  %s … %ds, done (%d frames)\n" % (label, int(time.monotonic() - t0), count))
        sys.stderr.flush()

    if proc.returncode != 0:
        raise RuntimeError("dvrescue failed (exit %d)%s"
                           % (proc.returncode, ": " + err.strip() if err.strip() else ""))
    if not os.path.exists(csv_path):
        raise RuntimeError("dvrescue produced no merge log%s" % (": " + err.strip() if err.strip() else ""))
    return count    # frames written, for callers that drive a progress bar across several merges
