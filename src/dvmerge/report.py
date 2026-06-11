"""Render a plan as the Markdown report you read each round.

It leads with the **re-capture list** — the tape spots no capture could supply cleanly, each cued by
tape SMPTE timecode and the camera's recording clock — then states what the merged file contains.
Same shape as hdvmerge's report so the two tools read alike.
"""

import os
import unicodedata

_SEP = " -_.·"


def _dw(s):
    """Display width in monospace cells: East Asian wide/fullwidth (CJK) count 2, the rest 1.

    Padding by this — rather than len() — is what keeps the tables aligned in a terminal and in the
    raw .md, where a Chinese filename like 校运会2C-1 occupies two cells per Han character."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in s)


def _table(headers, rows):
    """Render a Markdown table with every column padded to its widest cell (min 3, valid Markdown)."""
    cols = len(headers)
    w = [max(3, _dw(headers[i])) for i in range(cols)]
    for r in rows:
        for i in range(cols):
            w[i] = max(w[i], _dw(r[i]))

    def line(cells):
        return "| " + " | ".join(c + " " * (w[i] - _dw(c)) for i, c in enumerate(cells)) + " |"

    out = [line(headers), "| " + " | ".join("-" * w[i] for i in range(cols)) + " |"]
    out += [line(r) for r in rows]
    return out


def _date(rec):
    return rec.split(" ")[0] if rec and " " in rec else "?"


def _time(rec):
    return rec.split(" ")[1] if rec and " " in rec else (rec or "?")


def _hms(frames, fps):
    s = frames / (fps or 25.0)
    return "%02d:%02d:%02d" % (int(s // 3600), int(s % 3600 // 60), int(s % 60))


def _dur(frames, fps):
    s = frames / (fps or 25.0)
    return "%d:%05.2f" % (int(s // 60), s % 60) if s < 3600 else _hms(frames, fps)


def _tag(path):
    return os.path.splitext(os.path.basename(path))[0]


def _title(files):
    """Batch name from the captures' shared filename prefix (e.g. 'CLIP-A','CLIP-B' -> 'CLIP')."""
    tags = list(dict.fromkeys(_tag(f) for f in files))
    if not tags:
        return ""
    if len(tags) == 1:
        return tags[0]
    p = tags[0]
    for t in tags[1:]:
        while not t.startswith(p):
            p = p[:-1]
    cut = 0
    for i, c in enumerate(p):
        if c in _SEP:
            cut = i + 1
    return p[:cut].rstrip(_SEP)


def _coverage(span, files):
    if span.miss and not span.cover and not span.dmg:
        return "**none** — lost"
    tags = ", ".join(_tag(files[i]) for i in sorted(span.cover) if i < len(files))
    n = len(span.cover)
    cell = "%d %s: %s" % (n, "copy" if n == 1 else "copies", tags)
    if span.miss:
        cell += " · +%d missing" % span.miss
    return cell


def render(plan):
    fps, files = plan.fps, plan.files
    title = _title(files)
    n = len(files)

    L = ["# dvmerge — %s" % title if title else "# dvmerge report", ""]
    L.append("Recorded %s %s–%s · merged %s from %d capture%s."
             % (_date(plan.rdt0), _time(plan.rdt0), _time(plan.rdt1),
                _hms(plan.total_frames, fps), n, "" if n == 1 else "s"))
    L.append("")

    span_f = plan.total_frames or 1
    L.append("Merged output: %d frames (%s) · clean %d (%.1f%%) · mosaic %d (%.1f%%) · "
             "missing %d (%.1f%%)."
             % (plan.total_frames, _hms(plan.total_frames, fps),
                plan.clean, 100 * plan.clean / span_f,
                plan.dmg, 100 * plan.dmg / span_f,
                plan.miss, 100 * plan.miss / span_f))
    L.append("")

    if not plan.spans:
        L.append("## Nothing to re-capture — every tape frame has a clean copy. 🎉")
        L.append("")
    else:
        lost = plan.lost_frames
        warn = (" — incl. %s with **no copy at all** (lost unless re-captured)"
                % _hms(lost, fps)) if lost else ""
        L.append("## Re-capture these — %d region%s still imperfect after merge%s"
                 % (len(plan.spans), "" if len(plan.spans) == 1 else "s", warn))
        L.append("")
        L.append("Cue on **tape TC** (frame-accurate on the deck); **recording time** is the "
                 "camera's wall clock as a cross-check. Re-capture with >=15 s of good footage on "
                 "both sides — and *overlap the previous good take* so the passes join with no gap — "
                 "then drop the new file in and re-run; the list shrinks.")
        L.append("")
        rows = []
        for i, s in enumerate(plan.spans, 1):
            bad = s.dmg + s.miss
            rows.append([
                str(i), _time(s.rdt0), "%s – %s" % (s.tc0, s.tc1), _dur(s.length, fps), s.kind,
                "%d/%d (%.0f%%)%s" % (bad, s.length, 100 * bad / s.length,
                                      "" if not s.bmax else " · max %d blk" % s.bmax),
                _coverage(s, files)])
        L += _table(["#", "recording time", "tape TC", "length", "damage", "bad frames",
                     "coverage"], rows)
        L.append("")

    L.append("## Sources")
    L.append("")
    rows = []
    for i, f in enumerate(files):
        sp = plan.sources[i] if i < len(plan.sources) else None
        if sp:
            tc0, tc1, rdt0, rdt1 = sp
            rows.append([_tag(f), "%s – %s" % (tc0, tc1), "%s – %s" % (_time(rdt0), _time(rdt1))])
        else:
            rows.append([_tag(f), "(not aligned)", "—"])
    L += _table(["capture", "tape TC span", "recording span"], rows)
    L.append("")
    return "\n".join(L)
