"""Serialize a :class:`~dvmerge.plan.Plan` to a JSON-ready dict.

This is the *structured* counterpart to :mod:`dvmerge.report`'s human Markdown: a faithful dump of
dvmerge's own model — the merged-output tallies, the re-capture spans (with their coverage), and the
per-capture coverage spans — so another program can consume the analysis without scraping Markdown.
It is emitted by ``dvmerge --json``. It exposes dvmerge's model **as-is** and deliberately does NOT
normalise to any external schema — that mapping belongs to the consumer.

Kept in lock-step with the model by ``tests/test_jsonout.py`` so a future refactor that silently
breaks this output (a path normal CLI use never exercises) fails the suite loudly.
"""

import os

from . import __version__

SCHEMA = "dvmerge.analysis/1"


def _tag(path):
    return os.path.splitext(os.path.basename(path))[0]


def _span(s):
    """One re-capture span. ``cover`` is the sorted input indices (into ``files``) with some frame
    here; empty means no capture has it at all (lost unless re-captured). ``pf0/pf1`` are the
    physical-axis (read-order) extent; ``tc0/tc1`` the deck cue points."""
    return {
        "pf0": s.pf0,
        "pf1": s.pf1,
        "length": s.length,
        "tc0": s.tc0,
        "tc1": s.tc1,
        "rdt0": s.rdt0,
        "rdt1": s.rdt1,
        "kind": s.kind,          # 'mosaic' | 'missing' | 'mosaic + missing'
        "dmg": s.dmg,            # frames present but damaged
        "miss": s.miss,          # frames missing from every capture
        "bmax": s.bmax,          # worst single-frame block-error count
        "cover": sorted(s.cover),
        # the actual scattered damaged sub-runs inside the (gap-bridged) span, for drawing on the map
        "runs": [{"pf0": r["pf0"], "pf1": r["pf1"], "tc0": r["tc0"], "tc1": r["tc1"]}
                 for r in s.runs],
    }


def _segment(seg):
    """A tc-linear covered run on the physical axis: ``pf0/pf1`` is where to lay it out, ``tc0/tc1``
    and ``rdt0/rdt1`` how to label it, ``break_before`` why it broke from the previous run —
    ``seam`` (new recording session), ``gap`` (missing frames), ``stop`` (camera pause) or null."""
    return {"pf0": seg["pf0"], "pf1": seg["pf1"], "tc0": seg["tc0"], "tc1": seg["tc1"],
            "rdt0": seg["rdt0"], "rdt1": seg["rdt1"], "break_before": seg["break_before"]}


def _source(src, src_pf, tag, damage, coverage):
    """Per-capture: its coverage span (or ``aligned: False``), ``damage`` — the tape-TC runs where
    this capture is itself damaged — and ``coverage``, the contiguous runs it actually holds (split
    at internal gaps, each with a tape-TC span for labels and a physical ``pf`` span for layout), so
    a consumer can draw the real held regions and drops on its lane."""
    base = {"tag": tag, "aligned": src is not None,
            "damage": damage or [], "coverage": coverage or []}
    if src is not None:
        tc0, tc1, rdt0, rdt1 = src
        base.update({"tc0": tc0, "tc1": tc1, "rdt0": rdt0, "rdt1": rdt1})
        if src_pf is not None:
            base.update({"pf0": src_pf[0], "pf1": src_pf[1]})
    return base


def analysis(plan):
    """A JSON-ready dict capturing the whole analysis of the merged output: tape span, frame
    tallies (clean / mosaic / missing), the re-capture spans, and per-capture coverage. Faithful to
    dvmerge's model; the consumer normalises.

    ``complete`` is dvmerge's own "nothing to re-capture" verdict — every tape frame in the merged
    output has a clean copy, so no span survives."""
    tags = [_tag(f) for f in plan.files]
    sd = getattr(plan, "source_damage", None) or []
    sc = getattr(plan, "source_coverage", None) or []
    spf = getattr(plan, "source_pf", None) or []
    return {
        "schema": SCHEMA,
        "version": __version__,
        "fps": plan.fps,
        "total_frames": plan.total_frames,   # PHYSICAL frame count (read order, missing have width)
        "tc0": plan.tc0,
        "tc1": plan.tc1,
        "rdt0": plan.rdt0,
        "rdt1": plan.rdt1,
        "clean": plan.clean,
        "dmg": plan.dmg,
        "miss": plan.miss,
        "lost_frames": plan.lost_frames,
        "complete": not plan.spans,
        # The tape holds more than one recording session (record-run tc restarts at a seam): lay the
        # map out on the physical axis, not tc, and label each segment with its own tc/rec.
        "multi_session": bool(getattr(plan, "multi_session", False)),
        # physical positions (pf) at which a new recording session begins — for seam markers
        "seams": list(getattr(plan, "seams", []) or []),
        # coarse covered runs on the physical axis (layout + per-segment labels)
        "segments": [_segment(s) for s in getattr(plan, "segments", []) or []],
        # sampled pf -> (tc, rec) curve so a consumer can label any physical position by interpolation
        "anchors": [{"pf": a["pf"], "tc": a["tc"], "rdt": a["rdt"]}
                    for a in getattr(plan, "anchors", []) or []],
        "files": tags,
        "spans": [_span(s) for s in plan.spans],
        "sources": [_source(plan.sources[i] if i < len(plan.sources) else None,
                            spf[i] if i < len(spf) else None, tags[i],
                            sd[i] if i < len(sd) else [],
                            sc[i] if i < len(sc) else [])
                    for i in range(len(plan.files))],
    }
