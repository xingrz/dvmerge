"""Library entry for the DV analysis, so a tool (e.g. the tapeflow GUI sidecar) can ``import
dvmerge`` and drive it directly instead of shelling out to the CLI. The CLI is a thin wrapper over
the same helpers.

``merge_log`` drives one dvrescue merge, cached by input fingerprint; ``analyze`` parses that log
and builds the re-capture :class:`~dvmerge.plan.Plan`. dvrescue is the only external dependency.
"""

import hashlib
import os
import shutil
import tempfile

from . import DEFAULT_FPS
from . import dvrescue, parse, xmlinfo
from . import plan as planmod


def signature(files):
    """Content fingerprint of the input set (name, size, mtime). Keys the merge-log cache."""
    h = hashlib.sha1()
    for f in files:
        st = os.stat(f)
        h.update(("%s|%d|%d\n" % (os.path.basename(f), st.st_size, st.st_mtime_ns)).encode())
    return h.hexdigest()[:16]


def _rm(p):
    try:
        os.remove(p)
    except OSError:
        pass


def merge_log(files, output=None, *, no_cache=False, cache_dir=None, tmp_dir=None,
              dvrescue_bin=None, on_progress=None):
    """Return ``(csv_path, xml_path, cleanup)`` for ``files``, running dvrescue only on a cache miss.

    The same run also produces the ``-x`` XML (per-input STA error detail); both the CSV merge log
    and the XML are cached side by side. With ``output`` set the merge always runs (the .dv must be
    produced) and is moved into place; otherwise a fresh cache entry is reused when present and the
    merged DV goes to a temp file that is removed once parsed. ``cache_dir`` defaults to
    ``<dir-of-first-input>/.dvmerge``. ``cleanup()`` removes any temp artifacts.

    ``tmp_dir`` puts the temp artifacts (the throwaway merged .dv + the CSV/XML) there instead of
    beside the input — pass a scratch dir to keep a read-only analysis from writing anywhere near the
    source (e.g. auditing a master in place). Combine with ``no_cache=True``.
    """
    sig = None if no_cache else signature(files)
    cdir = cache_dir or os.path.join(os.path.dirname(os.path.abspath(files[0])), ".dvmerge")
    cache_csv = os.path.join(cdir, "merge-%s.csv" % sig) if sig else None
    cache_xml = os.path.join(cdir, "merge-%s.xml" % sig) if sig else None

    if not output and cache_csv and os.path.exists(cache_csv):
        return cache_csv, (cache_xml if cache_xml and os.path.exists(cache_xml) else None), \
            (lambda: None)

    # Need a real merge. Put temp artifacts on the same filesystem as the eventual home (big disk),
    # or in an explicit scratch dir (``tmp_dir``) to avoid writing anywhere near a read-only source.
    home = tmp_dir or (os.path.dirname(os.path.abspath(output)) if output
                       else os.path.dirname(os.path.abspath(files[0])))
    fd, tmp_dv = tempfile.mkstemp(prefix=".dvmerge-", suffix=".dv", dir=home)
    os.close(fd)
    os.remove(tmp_dv)  # dvrescue wants to create it fresh
    tmp_csv = tmp_dv[:-3] + ".csv"
    tmp_xml = tmp_dv[:-3] + ".xml"

    def cleanup():
        for p in (tmp_dv, tmp_csv, tmp_xml):
            _rm(p)

    try:
        dvrescue.merge(files, tmp_dv, tmp_csv, xml_path=tmp_xml, binary=dvrescue_bin,
                       on_progress=on_progress)
    except Exception:
        cleanup()
        raise

    if cache_csv:
        os.makedirs(cdir, exist_ok=True)
        shutil.copyfile(tmp_csv, cache_csv)
        if os.path.exists(tmp_xml):
            shutil.copyfile(tmp_xml, cache_xml)

    if output:
        os.replace(tmp_dv, output)            # atomic, overwrites without dvrescue's prompt
        return tmp_csv, (tmp_xml if os.path.exists(tmp_xml) else None), \
            (lambda: (_rm(tmp_csv), _rm(tmp_xml)))
    # analyse-only: drop the big .dv now; keep the (small) csv/xml for the caller to parse.
    if cache_csv:
        cleanup()                              # cached copies remain; remove all temps
        return cache_csv, (cache_xml if os.path.exists(cache_xml) else None), (lambda: None)
    _rm(tmp_dv)
    return tmp_csv, (tmp_xml if os.path.exists(tmp_xml) else None), \
        (lambda: (_rm(tmp_csv), _rm(tmp_xml)))


def analyze(files, *, output=None, fps=DEFAULT_FPS, bridge_s=3.0, min_s=0.5,
            no_cache=False, cache_dir=None, tmp_dir=None, dvrescue_bin=None, on_progress=None):
    """Discover → merge (cached) → parse → plan. Returns a :class:`dvmerge.plan.Plan`.

    Raises ``RuntimeError`` if the merge log has no frames (mismatched inputs) or dvrescue fails.
    With ``output`` set, the merged DV is also kept at that path. ``on_progress(frames_done)`` is
    forwarded to the dvrescue merge so a caller can show export progress."""
    csv_path, xml_path, cleanup = merge_log(files, output, no_cache=no_cache, cache_dir=cache_dir,
                                            tmp_dir=tmp_dir, dvrescue_bin=dvrescue_bin,
                                            on_progress=on_progress)
    try:
        frames, _ = parse.parse(csv_path, fps, nfiles=len(files))
        if not frames:
            raise RuntimeError("merge log has no frames — are these the same tape?")
        plan = planmod.build(frames, list(files), fps, bridge_s=bridge_s, min_s=min_s)
        # per-input STA error profile from the same run's XML (empty/absent -> None per input)
        profiles = xmlinfo.parse_profiles(xml_path, fps) if xml_path else {}
        plan.source_profiles = [profiles.get(os.path.basename(f)) for f in files]
        return plan
    finally:
        cleanup()
