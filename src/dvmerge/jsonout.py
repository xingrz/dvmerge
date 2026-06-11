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
    here; empty means no capture has it at all (lost unless re-captured)."""
    return {
        "tf0": s.tf0,
        "tf1": s.tf1,
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
        "runs": [{"tc0": r["tc0"], "tc1": r["tc1"]} for r in s.runs],
    }


def _source(src, tag, damage):
    """Per-capture: its coverage span (or ``aligned: False``) plus ``damage`` — the tape-TC runs
    where this capture is itself damaged, for showing on its lane."""
    base = {"tag": tag, "aligned": src is not None, "damage": damage or []}
    if src is not None:
        tc0, tc1, rdt0, rdt1 = src
        base.update({"tc0": tc0, "tc1": tc1, "rdt0": rdt0, "rdt1": rdt1})
    return base


def analysis(plan):
    """A JSON-ready dict capturing the whole analysis of the merged output: tape span, frame
    tallies (clean / mosaic / missing), the re-capture spans, and per-capture coverage. Faithful to
    dvmerge's model; the consumer normalises.

    ``complete`` is dvmerge's own "nothing to re-capture" verdict — every tape frame in the merged
    output has a clean copy, so no span survives."""
    tags = [_tag(f) for f in plan.files]
    sd = getattr(plan, "source_damage", None) or []
    return {
        "schema": SCHEMA,
        "version": __version__,
        "fps": plan.fps,
        "total_frames": plan.total_frames,
        "tc0": plan.tc0,
        "tc1": plan.tc1,
        "rdt0": plan.rdt0,
        "rdt1": plan.rdt1,
        "clean": plan.clean,
        "dmg": plan.dmg,
        "miss": plan.miss,
        "lost_frames": plan.lost_frames,
        "complete": not plan.spans,
        "files": tags,
        "spans": [_span(s) for s in plan.spans],
        "sources": [_source(plan.sources[i] if i < len(plan.sources) else None, tags[i],
                            sd[i] if i < len(sd) else [])
                    for i in range(len(plan.files))],
    }
