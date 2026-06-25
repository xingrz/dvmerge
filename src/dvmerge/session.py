"""Detect and repair dvrescue's *duplicated-tape* failure on multi-session tapes.

dvrescue aligns every capture by ``abst`` (the absolute track number — a monotone tape clock it
synthesises). When one physical tape holds two recording sessions whose timecode BOTH restart at
``00:00:00`` — e.g. a short head scene shot one day, then the main body shot another, the camera
having reset its TC in between — the head's low ``abst`` range collides with the body's start. With a
full-pass capture that physically spans the seam *and* separate re-capture fragments, dvrescue cannot
fold the body's copies onto one axis: it emits **two islands laid end to end**, duplicating the body.
The merged output then runs ~2× the true tape length, with a backward ``rdt`` jump and an ``abst``
reset at the splice.

The repair, mirroring how hdvmerge walks tape *islands* and stitches them by a monotone clock: split
the inputs into their true recording sessions (cutting the few files that span a seam at exact frame
boundaries — raw DV is fixed-size headerless frames, so a byte offset ``n × frame_size`` is a clean
cut), merge **each session on its own** (no cross-session ``abst`` collision → dvrescue folds every
copy into one clean island), then concatenate the per-session ``.dv`` and stitch the per-session merge
logs into one tape-ordered CSV. Downstream (:mod:`dvmerge.parse`, :mod:`dvmerge.plan`) then sees a
single tape with an ordinary recording **seam** at the boundary — exactly a clean multi-session tape.

This whole path runs ONLY when :func:`detect_duplication` fires on dvrescue's naive merge; a
single-session tape, or a multi-session tape dvrescue already folded cleanly (monotone ``abst``, no
re-covered time), is left completely untouched.
"""

import csv
import datetime
import os
import xml.etree.ElementTree as ET

from . import dvrescue, parse


# Raw DV frames are fixed-size and headerless: concatenation is a byte append and any frame boundary
# is a byte offset of ``n × frame_size``. PAL DV25 is 144000 bytes/frame, NTSC DV25 is 120000.
PAL_FRAME_BYTES = 144000
NTSC_FRAME_BYTES = 120000

# Detection thresholds (see detect_duplication). dvrescue's per-island ``abst`` restarts near 1 (it
# counts ~8 per frame), so a drop from a large value to a tiny one is a new-island axis, never the
# monotone drift of one island.
ABST_RESET_MAX = 200      # abst at/below this right after a large value == a reset to a new island
ABST_PRE_MIN = 5000       # ...and the frame before must have been at least this (we were deep in tape)
MIN_ISLAND_FRAMES = 50    # ignore micro-islands; a real duplicated body is large
RDT_SLACK = datetime.timedelta(seconds=2)


def frame_size(fps):
    """Bytes per DV frame for the tape's frame rate (NTSC 29.97 -> 120000, else PAL 144000)."""
    return NTSC_FRAME_BYTES if round(fps) == 30 else PAL_FRAME_BYTES


def _pdt(s):
    """Parse a dvrescue ``rdt`` ('YYYY-MM-DD HH:MM:SS') to datetime, or None if blank/malformed."""
    s = (s or "").strip()
    try:
        return datetime.datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _rdt_span(frames, lo, hi):
    """(min, max) recording-datetime over frames[lo:hi], ignoring unlabelled rows. (None, None) if none."""
    mn = mx = None
    for i in range(lo, hi):
        d = _pdt(frames[i].rdt)
        if d is None:
            continue
        mn = d if mn is None or d < mn else mn
        mx = d if mx is None or d > mx else mx
    return mn, mx


def _nearest_rdt(frames, k, step):
    """First non-blank rdt at or beyond k walking by ``step`` (+1 forward, -1 backward)."""
    i = k
    while 0 <= i < len(frames):
        d = _pdt(frames[i].rdt)
        if d is not None:
            return d
        i += step
    return None


def detect_duplication(frames):
    """Spot dvrescue's duplicated-tape artifact in a parsed merge log (FramePos order).

    Returns a list of split FramePos values (the start of each duplicate island) or ``None``.

    A boundary qualifies only when all three hold, which together separate the artifact from a
    legitimate tape:

      * **abst reset** — ``abst`` drops from >= ``ABST_PRE_MIN`` to <= ``ABST_RESET_MAX``. A clean
        multi-session tape that dvrescue folded onto one axis keeps ``abst`` monotone; only a new
        island restarts it. This is the decisive signal.
      * **backward rdt** — the wall clock steps back across the boundary (a rewind, not a forward cut).
      * **re-covered time** — the island after the boundary stays within the recording-time range
        already seen before it (it duplicates earlier tape), rather than extending into new footage
        (which would be a real later session dvrescue simply appended, and is left alone).
    """
    n = len(frames)
    if n < 2 * MIN_ISLAND_FRAMES:
        return None
    # A reset is a small abst right after we were deep in the tape. Compare against the last NON-NULL
    # abst, not literally frames[k-1]: dvrescue leaves abst blank on the frames just before an island
    # break, so the row immediately before the reset often has no abst at all.
    cands = []
    last_abst = None
    for k in range(n):
        a = frames[k].abst
        if a is None:
            continue
        if last_abst is not None and a <= ABST_RESET_MAX and last_abst >= ABST_PRE_MIN:
            cands.append(k)
        last_abst = a
    if not cands:
        return None

    confirmed = []
    pre_min, pre_max = _rdt_span(frames, 0, cands[0])
    ends = cands[1:] + [n]
    for k, seg_hi in zip(cands, ends):
        before = _nearest_rdt(frames, k - 1, -1)
        after = _nearest_rdt(frames, k, +1)
        seg_min, seg_max = _rdt_span(frames, k, seg_hi)
        backward = before is not None and after is not None and after < before
        recovered = (pre_max is not None and seg_max is not None
                     and seg_max <= pre_max + RDT_SLACK)
        if backward and recovered and (seg_hi - k) >= MIN_ISLAND_FRAMES:
            confirmed.append(frames[k].fp)
        if seg_max is not None and (pre_max is None or seg_max > pre_max):
            pre_max = seg_max
        if seg_min is not None and (pre_min is None or seg_min < pre_min):
            pre_min = seg_min
    return confirmed or None


# ---- session partition (which byte range of which file belongs to which recording session) --------

class Run:
    """A contiguous, monotone-TC stretch of one capture file: a byte slice [b0, b1) carrying its
    recording-time span. A file with no internal TC reset is a single whole-file run."""

    __slots__ = ("file_idx", "b0", "b1", "rdt_min", "rdt_max")

    def __init__(self, file_idx, b0, b1, rdt_min, rdt_max):
        self.file_idx = file_idx
        self.b0, self.b1 = b0, b1
        self.rdt_min, self.rdt_max = rdt_min, rdt_max


class Session:
    """One recording session of the tape: the slices (across files) that cover it, in input order.
    ``order_key`` (earliest rdt) puts head before body when laying sessions out on tape."""

    __slots__ = ("runs", "rdt_min", "rdt_max")

    def __init__(self):
        self.runs = []          # list[Run]
        self.rdt_min = self.rdt_max = None

    def add(self, run):
        self.runs.append(run)
        if run.rdt_min is not None:
            self.rdt_min = run.rdt_min if self.rdt_min is None else min(self.rdt_min, run.rdt_min)
        if run.rdt_max is not None:
            self.rdt_max = run.rdt_max if self.rdt_max is None else max(self.rdt_max, run.rdt_max)

    @property
    def order_key(self):
        return self.rdt_min or datetime.datetime.max


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _media_frames(xml_path):
    """Per input file (in dvrescue's input/argv order, NOT by name — the XML's ``ref`` is mojibake
    for non-ASCII paths), the listed ``<frame>`` rows that carry a byte ``pos`` and a ``tc``: a sparse
    but boundary-complete timeline (dvrescue always emits the frame at every range edge, including a
    TC reset). Returns ``[[(pos, tf, rdt), ...], ...]`` indexed by input position, where ``tf`` is the
    tape-frame number parsed from tc (None when unlabelled)."""
    root = ET.parse(xml_path).getroot()
    medias = []
    for media in root.iter():
        if _local(media.tag) != "media":
            continue
        rows = []
        for el in media.iter():
            if _local(el.tag) != "frame":
                continue
            a = el.attrib
            pos = a.get("pos")
            if pos is None:
                continue
            tc = a.get("tc") or ""
            tf = None
            if tc:
                try:
                    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
                    tf = ((h * 60 + m) * 60 + s) * 25 + f   # fps only scales; reset detection is robust to it
                except ValueError:
                    tf = None
            rows.append((int(pos), tf, _pdt(a.get("rdt") or "")))
        medias.append(rows)
    return medias


def _file_runs(rows, file_idx, file_size):
    """Split one file's sparse frame timeline into monotone-TC runs at recording-session seams (the
    tape-frame number stepping a long way backward, i.e. the camera's TC restarting). Each run is a
    byte slice; the boundary byte is the listed ``pos`` of the first frame of the next run, which is
    always frame-aligned (dvrescue reports real frame byte offsets). A file with no reset yields one
    whole-file run."""
    seam_idx = []   # indices into rows where a new run starts
    last_tf = None
    for i, (_pos, tf, _rdt) in enumerate(rows):
        if tf is not None:
            if last_tf is not None and tf < last_tf - 25:   # >1s backward == a TC restart, not a glitch
                seam_idx.append(i)
            last_tf = tf
    bounds = [0] + [rows[i][0] for i in seam_idx] + [file_size]  # byte boundaries, frame-aligned
    # rdt span per run
    runs = []
    seg_starts = [0] + seam_idx + [len(rows)]
    for s in range(len(bounds) - 1):
        b0, b1 = bounds[s], bounds[s + 1]
        rdts = [r[2] for r in rows[seg_starts[s]:seg_starts[s + 1]] if r[2] is not None]
        runs.append(Run(file_idx, b0, b1, min(rdts) if rdts else None, max(rdts) if rdts else None))
    return runs


def _overlap(a0, a1, b0, b1):
    """Overlap (seconds) of two rdt intervals; <=0 means disjoint. None-safe (returns -1)."""
    if None in (a0, a1, b0, b1):
        return -1
    lo, hi = max(a0, b0), min(a1, b1)
    return (hi - lo).total_seconds()


def partition(files, xml_path, fps):
    """Group the inputs into recording sessions for a session-aware re-merge.

    Returns ``[Session, ...]`` in tape order (head first). The file that spans the most sessions (the
    full pass) is the template defining the ordered session signatures; every other file's runs are
    assigned to the session whose recording-time range they overlap most. Pure single-session files
    become one whole-file run in their session; only seam-spanning files are sliced.
    """
    medias = _media_frames(xml_path)
    per_file = []
    for idx, rows in enumerate(medias):
        if idx >= len(files):
            break
        per_file.append(_file_runs(rows, idx, os.path.getsize(files[idx])))

    # Template: the file split into the most runs defines the session count and their rdt signatures,
    # in physical (byte) order — head occupies the lower bytes, so this is true tape order.
    template = max(per_file, key=len) if per_file else []
    if len(template) < 2:
        return []   # nothing actually spans a seam; not our case
    sessions = [Session() for _ in template]
    sig = [(r.rdt_min, r.rdt_max) for r in template]

    for runs in per_file:
        for run in runs:
            best, best_ov = 0, None
            for si, (s0, s1) in enumerate(sig):
                ov = _overlap(run.rdt_min, run.rdt_max, s0, s1)
                if best_ov is None or ov > best_ov:
                    best, best_ov = si, ov
            sessions[best].add(run)

    sessions.sort(key=lambda s: s.order_key)
    return sessions


# ---- session-aware re-merge (slice -> per-session dvrescue merge -> concat .dv + stitch logs) ------

_CHUNK = 16 * 1024 * 1024


def _byte_slice(src, b0, b1, dst):
    """Copy bytes [b0, b1) of ``src`` to ``dst`` (a frame-aligned DV slice)."""
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        fi.seek(b0)
        remaining = b1 - b0
        while remaining > 0:
            buf = fi.read(min(_CHUNK, remaining))
            if not buf:
                break
            fo.write(buf)
            remaining -= len(buf)


def _concat(parts, dst):
    """Append raw DV streams ``parts`` into ``dst`` (DV frames are headerless and fixed-size)."""
    with open(dst, "wb") as fo:
        for p in parts:
            with open(p, "rb") as fi:
                while True:
                    buf = fi.read(_CHUNK)
                    if not buf:
                        break
                    fo.write(buf)


def _session_inputs(session, files, slice_dir, tag):
    """Materialise one session's dvrescue inputs. Whole-file runs pass through by their original path;
    seam-spanning runs are byte-sliced into a temp under ``slice_dir``. Returns ``(paths, gmap, temps)``
    where ``gmap[j]`` is the global file index feeding ``paths[j]`` (for Status re-expansion / XML ref
    rewrite) and ``temps`` are the slice files to delete afterward."""
    paths, gmap, temps = [], [], []
    for run in sorted(session.runs, key=lambda r: r.file_idx):
        src = files[run.file_idx]
        if run.b0 == 0 and run.b1 == os.path.getsize(src):
            paths.append(src)
        else:
            tmp = os.path.join(slice_dir, ".dvmerge-%s-i%d.dv" % (tag, run.file_idx))
            _byte_slice(src, run.b0, run.b1, tmp)
            paths.append(tmp)
            temps.append(tmp)
        gmap.append(run.file_idx)
    return paths, gmap, temps


def _stitch_csv(session_csvs, gmaps, nfiles, out_path):
    """Concatenate per-session merge logs into one tape-ordered CSV.

    Each session's ``FramePos`` is shifted by the cumulative tape extent of the earlier sessions, so
    the combined index is monotone with an ordinary recording seam at each junction (the next session's
    tc restarts) — which is exactly what :mod:`dvmerge.plan` already segments on. Each session's
    ``Status`` (one char per session input, in the order they were passed to dvrescue) is re-expanded
    to the global input order, inserting ``'M'`` for files absent from that session. Returns the total
    tape-extent frame count.
    """
    offset = 0
    header = None
    fp_i = st_i = None
    with open(out_path, "w", newline="") as fo:
        w = csv.writer(fo)
        for csv_path, gmap in zip(session_csvs, gmaps):
            with open(csv_path, newline="") as fi:
                r = csv.reader(fi)
                rows = list(r)
            if not rows:
                continue
            if header is None:
                header = rows[0]
                fp_i = header.index("FramePos")
                st_i = header.index("Status")
                w.writerow(header)
            maxfp = 0
            for row in rows[1:]:
                if len(row) <= max(fp_i, st_i):
                    continue
                raw = row[fp_i]
                if raw.isdigit():
                    fp = int(raw)
                    maxfp = max(maxfp, fp)
                    row[fp_i] = str(fp + offset)
                # re-expand Status to global width
                local = row[st_i]
                glob = ["M"] * nfiles
                for j, ch in enumerate(local):
                    if j < len(gmap):
                        glob[gmap[j]] = ch
                row[st_i] = "".join(glob)
                w.writerow(row)
            offset += maxfp + 1
    return offset


def _stitch_xml(session_xmls, gmaps, files, out_path):
    """Concatenate the per-session ``-x`` XMLs into one, rewriting each ``<media>``'s ``ref`` to the
    ORIGINAL input path it was sliced from (in input order). A spanning file then appears as two
    ``<media>`` with the same ref across the two sessions; :func:`dvmerge.xmlinfo.parse_profiles` sums
    same-name blocks, recovering the whole-capture profile."""
    ET.register_namespace("", "https://mediaarea.net/dvrescue")
    out_root = None
    tree0 = None
    for xml_path, gmap in zip(session_xmls, gmaps):
        if not xml_path or not os.path.exists(xml_path):
            continue
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        if out_root is None:
            out_root = root
            tree0 = root
            # rewrite refs in this first root in place
            _rewrite_refs(root, gmap, files)
        else:
            _rewrite_refs(root, gmap, files)
            for media in [el for el in root if _local(el.tag) == "media"]:
                out_root.append(media)
    if out_root is not None:
        ET.ElementTree(out_root).write(out_path, encoding="UTF-8", xml_declaration=True)


def _rewrite_refs(root, gmap, files):
    j = 0
    for el in root:
        if _local(el.tag) != "media":
            continue
        if j < len(gmap):
            el.set("ref", files[gmap[j]])
        j += 1


def remerge(files, sessions, *, combined_csv, combined_xml, output, work_dir, fps,
            slice_dir=None, dvrescue_bin=None, on_progress=None):
    """Merge each recording session on its own, then concatenate into one tape.

    Writes ``combined_csv`` and ``combined_xml``; with ``output`` set, also writes the concatenated
    ``.dv`` there. Per-session merge outputs go to temps under ``work_dir``; the input byte-slices go
    under ``slice_dir`` (default ``work_dir``) — point it at a roomier scratch volume when the slices
    (a de-headed full pass can be ~10 GB) plus the merged output won't both fit on one disk. All temps
    are removed afterward (the only large residue is ``output``). Returns the combined tape-extent
    frame count.
    """
    slice_dir = slice_dir or work_dir
    sess_csvs, sess_xmls, sess_dvs, gmaps, residue = [], [], [], [], []
    base = [0]   # running frame base for a monotone progress bar across sessions

    def progress_for(_n):
        if on_progress:
            on_progress(base[0] + _n)

    def rm(p):
        try:
            os.remove(p)
        except OSError:
            pass

    try:
        for si, s in enumerate(sessions):
            inputs, gmap, temps = _session_inputs(s, files, slice_dir, "s%d" % si)
            dv = os.path.join(work_dir, ".dvmerge-s%d.dv" % si)
            cs = os.path.join(work_dir, ".dvmerge-s%d.csv" % si)
            xm = os.path.join(work_dir, ".dvmerge-s%d.xml" % si)
            for p in (dv, cs, xm):
                rm(p)
            n = dvrescue.merge(inputs, dv, cs, xml_path=xm, binary=dvrescue_bin,
                               on_progress=progress_for)
            base[0] += (n or 0)
            for t in temps:        # input slices are consumed by this merge — free them now, so a
                rm(t)              # later session's slices don't stack on top of this one's
            sess_dvs.append(dv)
            sess_csvs.append(cs)
            sess_xmls.append(xm)
            gmaps.append(gmap)
            residue += [cs, xm]

        total = _stitch_csv(sess_csvs, gmaps, len(files), combined_csv)
        _stitch_xml(sess_xmls, gmaps, files, combined_xml)

        if output:
            part = output + ".part"
            _concat(sess_dvs, part)
            os.replace(part, output)
        return total
    finally:
        for p in residue + sess_dvs:
            rm(p)
