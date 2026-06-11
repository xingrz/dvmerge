"""Turn the per-frame view into a re-capture plan: the spans of tape still worth another pass.

Two kinds of frame survive the merge imperfect:

  * **mosaic** — a frame is present but ``BlockErrors > 0``: every capture that had it was damaged,
    so the merged frame still carries concealed blocks.
  * **missing** — a tape frame has no row at all (a gap in the timecode sequence): no capture wrote
    it, so it is absent from the output entirely. Worse than mosaic — there is nothing to show.

Isolated bad frames are everywhere on a dirty tape, so we coalesce them into spans, bridging short
clean stretches (``bridge`` seconds): two damaged patches a second apart are one re-capture target,
because you rewind and re-shoot the region either way. Each span records which captures cover it and
how many frames within it are flat-out missing — that is what tells you whether re-capturing can
even help (you have dirty copies to improve on) or is mandatory (no copy exists).
"""

import collections


def _abst_step(frames):
    """The tracks-per-frame abst increment (8 for PAL), taken as the *mode* of consecutive deltas.

    Mode, not minimum: an overlap seam occasionally yields a delta of 5–7 or a near-duplicate, and a
    single small outlier would otherwise be mistaken for the unit and make every normal +8 look like
    a missing frame. None if no capture reported abst, in which case we fall back to the tc delta."""
    deltas = collections.Counter()
    prev = None
    for f in frames:
        if f.abst is not None:
            if prev is not None and f.abst > prev:
                deltas[f.abst - prev] += 1
            prev = f.abst
    return deltas.most_common(1)[0][0] if deltas else None


def _missing_between(prev, cur, step):
    """How many tape frames are truly missing between two consecutive present frames.

    The physical track number ``abst`` is the arbiter: a tape with continuous abst but a jumping tc
    (a camera stop/start, or non-record-run timecode) is *not* missing anything — that jump counts as
    zero. Rounded to the nearest whole frame-step so a one-track abst irregularity isn't a gap. Only
    when neither side reports abst do we fall back to the tc delta."""
    if step and prev.abst is not None and cur.abst is not None:
        return max(0, int(round((cur.abst - prev.abst) / step)) - 1)
    return max(0, cur.tf - prev.tf - 1)


class Span:
    """A contiguous run of imperfect tape worth re-capturing."""

    __slots__ = ("tf0", "tf1", "tc0", "tc1", "rdt0", "rdt1",
                 "dmg", "miss", "bmax", "cover", "runs")

    def __init__(self, tf0, tc0, rdt0):
        self.tf0 = self.tf1 = tf0
        self.tc0 = self.tc1 = tc0
        self.rdt0 = self.rdt1 = rdt0
        self.dmg = 0          # frames present but damaged
        self.miss = 0         # frames missing from every capture
        self.bmax = 0         # worst single-frame block-error count
        self.cover = set()    # input indices that have *some* frame in this span
        self.runs = []        # tight damaged sub-runs [{tf0,tf1,tc0,tc1}] — the ACTUAL damage
        #                       inside the span (the span bridges short clean gaps for the cue, but
        #                       these are the real scattered runs, for drawing on the map)

    def add_run(self, tf0, tf1, tc0, tc1, tight):
        """Record an imperfect atom as a tight sub-run, coalescing only across gaps <= ``tight``."""
        if self.runs and tf0 - self.runs[-1]["tf1"] <= tight:
            self.runs[-1]["tf1"] = tf1
            self.runs[-1]["tc1"] = tc1
        else:
            self.runs.append({"tf0": tf0, "tf1": tf1, "tc0": tc0, "tc1": tc1})

    @property
    def length(self):
        return self.tf1 - self.tf0 + 1

    @property
    def kind(self):
        if self.miss and self.dmg:
            return "mosaic + missing"
        return "missing" if self.miss else "mosaic"


class Plan:
    __slots__ = ("fps", "files", "rdt0", "rdt1", "tc0", "tc1", "total_frames",
                 "clean", "dmg", "miss", "spans", "lost_frames", "sources", "source_damage")

    def __init__(self):
        self.spans = []
        self.sources = []   # per input index: (tc0, tc1, rdt0, rdt1) it covers, or None
        self.source_damage = []  # per input index: list of {tc0, tc1, frames} damaged runs ('P')


def source_damage(frames, nfiles, fps, bridge_s=0.5):
    """Per input capture, the tape-TC runs where *that capture itself* is damaged (Status ``'P'``),
    independent of whether the merge repaired it from another copy — so a consumer can show a
    capture's own damage on its lane. Consecutive damaged frames within ``bridge_s`` are coalesced
    into one run."""
    bridge = max(1, int(bridge_s * fps))
    out = [[] for _ in range(nfiles)]
    for f in frames:
        for i in f.damaged:
            if i >= nfiles:
                continue
            runs = out[i]
            if runs and f.tf - runs[-1]["_tf1"] <= bridge:
                runs[-1]["tc1"] = f.tc
                runs[-1]["_tf1"] = f.tf
                runs[-1]["frames"] += 1
            else:
                runs.append({"tc0": f.tc, "tc1": f.tc, "_tf1": f.tf, "frames": 1})
    for runs in out:
        for run in runs:
            del run["_tf1"]
    return out


def build(frames, files, fps, bridge_s=3.0, min_s=0.5):
    """Group residual damage into re-capture spans. ``frames`` is the parsed, sorted CSV."""
    bridge = int(bridge_s * fps)
    min_f = int(min_s * fps)

    # Atoms of imperfection in tape order: damaged frames and missing-frame gaps. A missing gap is
    # labelled by its surrounding good frames (last-good -> first-good), the points you cue between.
    step = _abst_step(frames)
    atoms = []  # (tf0, tf1, dmg, miss, bmax, cover, rdt0, tc0, rdt1, tc1)
    prev = None
    for f in frames:
        if prev is not None:
            miss = _missing_between(prev, f, step)
            if miss > 0:
                atoms.append((prev.tf + 1, prev.tf + miss, 0, miss, 0, frozenset(),
                              prev.rdt, prev.tc, f.rdt, f.tc))
        if f.berr > 0:
            atoms.append((f.tf, f.tf, 1, 0, f.berr, f.cover, f.rdt, f.tc, f.rdt, f.tc))
        prev = f

    tight = max(1, int(0.5 * fps))   # tight coalescing for the actual sub-runs (vs the bridged span)
    spans = []
    for tf0, tf1, dmg, miss, bmax, cover, rdt0, tc0, rdt1, tc1 in atoms:
        if spans and tf0 - spans[-1].tf1 - 1 <= bridge:
            s = spans[-1]
        else:
            s = Span(tf0, tc0, rdt0)
            spans.append(s)
        s.tf1, s.tc1, s.rdt1 = tf1, tc1, rdt1
        s.dmg += dmg
        s.miss += miss
        s.bmax = max(s.bmax, bmax)
        s.cover |= set(cover)
        s.add_run(tf0, tf1, tc0, tc1, tight)

    p = Plan()
    p.fps = fps
    p.files = files
    p.rdt0, p.rdt1 = frames[0].rdt, frames[-1].rdt
    p.tc0, p.tc1 = frames[0].tc, frames[-1].tc
    p.total_frames = frames[-1].tf - frames[0].tf + 1
    p.miss = sum(s.miss for s in spans)
    p.dmg = sum(s.dmg for s in spans)
    p.clean = p.total_frames - p.miss - p.dmg
    p.lost_frames = sum(s.miss for s in spans if not s.cover and s.miss and not s.dmg)
    p.spans = [s for s in spans if s.length >= min_f]
    p.spans.sort(key=lambda s: s.tf0)

    # Per-capture coverage span (first/last frame each input contributes), for the Sources table.
    src = [None] * len(files)
    for f in frames:
        for i in f.cover:
            if i < len(files):
                if src[i] is None:
                    src[i] = [f.tc, f.tc, f.rdt, f.rdt]
                else:
                    src[i][1], src[i][3] = f.tc, f.rdt
    p.sources = [tuple(x) if x else None for x in src]
    p.source_damage = source_damage(frames, len(files), fps)
    return p
