"""Turn the per-frame view into a re-capture plan: the spans of tape still worth another pass.

Two kinds of frame survive the merge imperfect:

  * **mosaic** — a frame is present but ``BlockErrors > 0``: every capture that had it was damaged,
    so the merged frame still carries concealed blocks.
  * **missing** — a tape frame has no row at all (a jump in ``FramePos``): no capture wrote it, so it
    is absent from the output entirely. Worse than mosaic — there is nothing to show.

Isolated bad frames are everywhere on a dirty tape, so we coalesce them into spans, bridging short
clean stretches (``bridge`` seconds): two damaged patches a second apart are one re-capture target,
because you rewind and re-shoot the region either way. Each span records which captures cover it and
how many frames within it are flat-out missing — that is what tells you whether re-capturing can
even help (you have dirty copies to improve on) or is mandatory (no copy exists).

**Coordinate.** Everything here is laid out on the *physical* axis ``pf`` — ``FramePos`` shifted to
start at zero, i.e. dvrescue's dense index into the reconstructed tape, with truly-missing frames
taking their width (a FramePos jump). NOT on tape timecode. A tape commonly holds several recording
sessions — old footage partly overwritten, different-day footage spliced on, a little over-capture
at each end. Each session's record-run ``tc`` restarts, so tc is not monotonic across the tape;
using it as the layout axis scatters physically-adjacent content and invents enormous phantom gaps
where one session's high tc abuts the next session's low tc. We segment the tape at those session
boundaries (``seam`` — the tc steps backward) and label each segment with its own tc/rec, the way the
tape actually plays.
"""


def _carry(frames, fps, micro):
    """One pass over the frames (already in physical/FramePos order): assign each frame its physical
    position ``pf`` and the count of frames missing immediately before it, carry tc/rec forward across
    rows dvrescue left unlabelled, and flag the recording-session ``seam`` frames (where the record-run
    tc steps backward). Returns a list of per-frame dicts.

    Gaps smaller than ``micro`` frames are absorbed, not counted as missing: a multi-capture dvrescue
    merge leaves periodic tiny (a few-frame) jumps in its index that are not re-capture-worthy losses
    — counting each one would make a mostly-clean tape read as 20% missing and collapse its whole
    re-capture list into one tape-spanning span. ``pf`` is accumulated (it skips absorbed gaps), so a
    consumer's physical axis stays tight."""
    use_fp = bool(frames) and all(f.fp is not None for f in frames)
    info = []
    last_tc, last_tf, last_rdt = "", None, ""
    prev = None
    pos = 0
    for f in frames:
        if prev is None:
            miss = 0
        else:
            if use_fp:
                gap = f.fp - prev.fp - 1
            else:   # no FramePos (old logs): recover missing from a forward tc jump
                gap = (f.tf - prev.tf - 1) if (f.tf is not None and prev.tf is not None
                                               and f.tf > prev.tf) else 0
            miss = gap if gap >= micro else 0
            pos += 1 + miss
        seam = (f.tf is not None and last_tf is not None and f.tf < last_tf - 1)
        info.append({"pf": pos, "miss": miss, "tc": f.tc or last_tc,
                     "tf": f.tf if f.tf is not None else last_tf, "rdt": f.rdt or last_rdt,
                     "cover": f.cover, "berr": f.berr, "damaged": f.damaged, "seam": seam})
        if f.tc:
            last_tc, last_tf = f.tc, f.tf
        if f.rdt:
            last_rdt = f.rdt
        prev = f
    return info


class Span:
    """A contiguous run of imperfect tape worth re-capturing (on the physical axis ``pf``)."""

    __slots__ = ("pf0", "pf1", "tc0", "tc1", "rdt0", "rdt1",
                 "dmg", "miss", "bmax", "cover", "runs")

    def __init__(self, pf0, tc0, rdt0):
        self.pf0 = self.pf1 = pf0
        self.tc0 = self.tc1 = tc0
        self.rdt0 = self.rdt1 = rdt0
        self.dmg = 0          # frames present but damaged
        self.miss = 0         # frames missing from every capture
        self.bmax = 0         # worst single-frame block-error count
        self.cover = set()    # input indices that have *some* frame in this span
        self.runs = []        # tight damaged sub-runs [{pf0,pf1,tc0,tc1}] — the ACTUAL damage
        #                       inside the span (the span bridges short clean gaps for the cue, but
        #                       these are the real scattered runs, for drawing on the map)

    def add_run(self, pf0, pf1, tc0, tc1, tight):
        """Record an imperfect atom as a tight sub-run, coalescing only across gaps <= ``tight``."""
        if self.runs and pf0 - self.runs[-1]["pf1"] <= tight:
            self.runs[-1]["pf1"] = pf1
            self.runs[-1]["tc1"] = tc1
        else:
            self.runs.append({"pf0": pf0, "pf1": pf1, "tc0": tc0, "tc1": tc1})

    @property
    def length(self):
        return self.pf1 - self.pf0 + 1

    @property
    def kind(self):
        if self.miss and self.dmg:
            return "mosaic + missing"
        return "missing" if self.miss else "mosaic"


class Plan:
    __slots__ = ("fps", "files", "rdt0", "rdt1", "tc0", "tc1", "total_frames",
                 "clean", "dmg", "miss", "spans", "lost_frames", "sources", "source_damage",
                 "source_coverage", "segments", "seams", "source_pf", "anchors", "multi_session")

    def __init__(self):
        self.spans = []
        self.sources = []   # per input index: (tc0, tc1, rdt0, rdt1) it covers, or None
        self.source_damage = []   # per input index: list of {tc0, tc1, frames} damaged runs ('P')
        self.source_coverage = []  # per input index: list of {tc0, tc1, pf0, pf1} covered runs
        self.segments = []  # coarse covered runs on the physical axis (see _segments)
        self.seams = []     # physical positions (pf) of recording-session boundaries
        self.anchors = []   # sampled (pf -> tc, rdt) curve for ruler labels (see _anchors)
        self.source_pf = []  # per input index: (pf0, pf1) physical coverage span, or None
        self.multi_session = False


def source_damage(info, nfiles, fps, bridge_s=0.5):
    """Per input capture, the tape-TC runs where *that capture itself* is damaged (Status ``'P'``),
    independent of whether the merge repaired it from another copy. Consecutive damaged frames within
    ``bridge_s`` (by physical position, so a seam doesn't merge unrelated damage) are coalesced."""
    bridge = max(1, int(bridge_s * fps))
    out = [[] for _ in range(nfiles)]
    for d in info:
        for i in d["damaged"]:
            if i >= nfiles:
                continue
            runs = out[i]
            if runs and d["pf"] - runs[-1]["_pf1"] <= bridge:
                runs[-1]["tc1"] = d["tc"]
                runs[-1]["_pf1"] = d["pf"]
                runs[-1]["frames"] += 1
            else:
                runs.append({"tc0": d["tc"], "tc1": d["tc"], "_pf1": d["pf"], "frames": 1})
    for runs in out:
        for run in runs:
            del run["_pf1"]
    return out


def source_coverage(info, nfiles, fps, gap_s=0.5):
    """Per input capture, the contiguous runs it actually holds, split at internal drops by physical
    position so a consumer can draw the real gaps on the lane. Each run carries its tape-TC span
    (labels) and its physical ``pf`` span (layout). Mirrors hdvmerge's ``_source_coverage``."""
    gap_thresh = max(1, int(gap_s * fps))
    out = [[] for _ in range(nfiles)]
    cur = [None] * nfiles      # open run [tc0, tc1, pf0, last_pf] per input, or None
    for d in info:
        for i in d["cover"]:
            if i >= nfiles:
                continue
            run = cur[i]
            if run is not None and d["pf"] - run[3] >= gap_thresh:
                out[i].append({"tc0": run[0], "tc1": run[1], "pf0": run[2], "pf1": run[3]})
                run = None
            if run is None:
                cur[i] = [d["tc"], d["tc"], d["pf"], d["pf"]]
            else:
                run[1], run[3] = d["tc"], d["pf"]
    for i in range(nfiles):
        if cur[i] is not None:
            r = cur[i]
            out[i].append({"tc0": r[0], "tc1": r[1], "pf0": r[2], "pf1": r[3]})
    return out


def _segments(info, hole):
    """Coarse covered runs on the physical axis: the green coverage bar, split only where it really
    breaks — at a recording-session ``seam`` (tc restarts) or a ``gap`` of >= ``hole`` truly-missing
    frames (a visible hole). Smaller gaps are bridged into the run; the actual missing frames within
    are still drawn on top as damage. Each run carries its pf extent (layout) and tc/rec ends (labels,
    carried across unlabelled frames), so a consumer lays it out by pf and labels it by tc."""
    segs = []
    cur = None
    for i, d in enumerate(info):
        brk = None
        if i > 0:
            if d["seam"]:
                brk = "seam"
            elif d["miss"] >= hole:
                brk = "gap"
        if cur is None or brk is not None:
            cur = {"pf0": d["pf"], "pf1": d["pf"], "tc0": d["tc"], "tc1": d["tc"],
                   "rdt0": d["rdt"], "rdt1": d["rdt"], "break_before": brk}
            segs.append(cur)
        else:
            cur["pf1"], cur["tc1"], cur["rdt1"] = d["pf"], d["tc"], d["rdt"]
    return segs


def _anchors(info, fps):
    """A sampled ``pf -> (tc, rec)`` curve for the ruler: enough points that a consumer can read the
    tape timecode and wall clock at any position by interpolation, snapping across a seam. We anchor
    the first and last frame, every seam, and roughly once a second — cheap and accurate without one
    point per frame."""
    step = max(1, int(round(fps)))
    out = []
    last = None
    n = len(info)
    for i, d in enumerate(info):
        if i == 0 or i == n - 1 or d["seam"] or last is None or d["pf"] - last >= step:
            out.append({"pf": d["pf"], "tc": d["tc"], "rdt": d["rdt"]})
            last = d["pf"]
    return out


def build(frames, files, fps, bridge_s=3.0, min_s=0.5, micro_s=0.25):
    """Group residual damage into re-capture spans. ``frames`` is the parsed CSV in physical
    (FramePos) order (see :mod:`dvmerge.parse`). ``micro_s`` is the smallest gap (in seconds) treated
    as a real missing hole; below it dvrescue's index micro-irregularities are absorbed."""
    bridge = int(bridge_s * fps)
    min_f = int(min_s * fps)
    hole = max(1, int(0.5 * fps))    # a missing run this big breaks the coverage bar
    micro = max(2, int(round(micro_s * fps)))

    info = _carry(frames, fps, micro)
    seams = [d["pf"] for d in info if d["seam"]]

    # Atoms of imperfection in physical order: damaged frames and missing-frame gaps. A missing gap
    # spans the pf the absent frames would occupy, labelled by its surrounding good frames.
    atoms = []  # (pf0, pf1, dmg, miss, bmax, cover, rdt0, tc0, rdt1, tc1)
    prev = None
    for d in info:
        if prev is not None and d["miss"] > 0:
            atoms.append((prev["pf"] + 1, d["pf"] - 1, 0, d["miss"], 0, frozenset(),
                          prev["rdt"], prev["tc"], d["rdt"], d["tc"]))
        if d["berr"] > 0:
            atoms.append((d["pf"], d["pf"], 1, 0, d["berr"], d["cover"],
                          d["rdt"], d["tc"], d["rdt"], d["tc"]))
        prev = d

    tight = max(1, int(0.5 * fps))   # tight coalescing for the actual sub-runs (vs the bridged span)
    spans = []
    for pf0, pf1, dmg, miss, bmax, cover, rdt0, tc0, rdt1, tc1 in atoms:
        # bridge nearby damage into one re-capture target — but never across a recording-session
        # seam: the head/tail over-capture of an adjacent recording is its own (small) target, not
        # part of the body. A seam between the last span and this atom forces a new span.
        cross_seam = bool(spans) and any(spans[-1].pf1 < s <= pf0 for s in seams)
        if spans and not cross_seam and pf0 - spans[-1].pf1 - 1 <= bridge:
            s = spans[-1]
        else:
            s = Span(pf0, tc0, rdt0)
            spans.append(s)
        s.pf1, s.tc1, s.rdt1 = pf1, tc1, rdt1
        s.dmg += dmg
        s.miss += miss
        s.bmax = max(s.bmax, bmax)
        s.cover |= set(cover)
        s.add_run(pf0, pf1, tc0, tc1, tight)

    p = Plan()
    p.fps = fps
    p.files = files
    p.rdt0 = info[0]["rdt"] if info else ""
    p.rdt1 = info[-1]["rdt"] if info else ""
    p.tc0 = info[0]["tc"] if info else ""
    p.tc1 = info[-1]["tc"] if info else ""
    p.total_frames = (info[-1]["pf"] + 1) if info else 0
    p.miss = sum(s.miss for s in spans)
    p.dmg = sum(s.dmg for s in spans)
    p.clean = p.total_frames - p.miss - p.dmg
    p.lost_frames = sum(s.miss for s in spans if not s.cover and s.miss and not s.dmg)
    p.spans = [s for s in spans if s.length >= min_f]
    p.spans.sort(key=lambda s: s.pf0)
    p.seams = seams
    p.multi_session = bool(seams)
    p.segments = _segments(info, hole)
    p.anchors = _anchors(info, fps)

    # Per-capture coverage span (first/last frame each input contributes), tc for labels and pf for
    # layout, for the Sources table and the tape map.
    src = [None] * len(files)
    src_pf = [None] * len(files)
    for d in info:
        for i in d["cover"]:
            if i < len(files):
                if src[i] is None:
                    src[i] = [d["tc"], d["tc"], d["rdt"], d["rdt"]]
                    src_pf[i] = [d["pf"], d["pf"]]
                else:
                    src[i][1], src[i][3] = d["tc"], d["rdt"]
                    src_pf[i][1] = d["pf"]
    p.sources = [tuple(x) if x else None for x in src]
    p.source_pf = [tuple(x) if x else None for x in src_pf]
    p.source_damage = source_damage(info, len(files), fps)
    p.source_coverage = source_coverage(info, len(files), fps)
    return p
