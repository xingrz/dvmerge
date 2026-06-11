"""Parse dvrescue's CSV merge log into a per-frame view of the merged tape.

``dvrescue ... -m OUT.dv --merge-log LOG.csv --csv`` writes one row per frame that made it into the
merged output, in tape order. The columns dvmerge reads:

  ``tc``           tape SMPTE timecode (HH:MM:SS:FF / ;FF for drop-frame) — cue point on the deck
  ``rdt``          recording date-time, the camera's wall clock — a human-readable cross-check
  ``BlockErrors``  residual bad DV blocks in this frame *after* the merge picked the best copy;
                   0 means a clean copy was found, >0 means every capture was damaged here
  ``Status``       one character per input capture, in the order the files were passed:
                     ``' '`` the capture has this frame, clean
                     ``'P'`` the capture has this frame, but damaged
                     ``'M'`` the capture is missing this frame entirely

Frames missing from *every* capture are never written, so they have no row — dvmerge recovers them
downstream as jumps in the absolute track number ``abst`` (the physical tape position; see
:mod:`dvmerge.plan`). Every written row has at least one non-``M`` character, i.e. coverage >= 1.
"""

import csv


def tc_to_frames(tc, fps):
    """Tape timecode 'HH:MM:SS:FF' (or ';FF' drop-frame separator) -> absolute frame count.

    Non-drop arithmetic, which is correct for PAL (25) and NTSC non-drop. Drop-frame ('11;59;59;29')
    is parsed but counted non-drop; for re-capture cueing the small label drift is immaterial and
    the original ``tc`` string is what we print anyway.
    """
    h, m, s, f = (int(x) for x in tc.replace(";", ":").split(":"))
    return ((h * 60 + m) * 60 + s) * int(round(fps)) + f


def frames_to_tc(n, fps):
    r = int(round(fps))
    n = int(round(n))
    return "%02d:%02d:%02d:%02d" % (n // (r * 3600), n // (r * 60) % 60, n // r % 60, n % r)


class Frame:
    """One merged tape frame. ``cover`` is the set of input indices that hold it (non-``M``);
    ``damaged`` is the subset that hold it but *damaged* (Status ``'P'``)."""

    __slots__ = ("tf", "tc", "rdt", "berr", "cover", "abst", "damaged")

    def __init__(self, tf, tc, rdt, berr, cover, abst, damaged=frozenset()):
        self.tf = tf            # tape frame number (from tc), used for ordering and labels
        self.tc = tc            # raw tape timecode string, for display
        self.rdt = rdt          # recording date-time string, for display
        self.berr = berr        # residual block errors after merge (0 == clean)
        self.cover = cover      # frozenset of input indices that have this frame at all
        self.abst = abst        # absolute track number on tape (physical position), or None.
        #                         The *physical* coordinate: unlike tc it can't jump at a
        #                         camera stop/start, so it is the arbiter for what is truly missing.
        self.damaged = damaged  # frozenset of input indices that have this frame but DAMAGED ('P')


def parse(csv_path, fps, nfiles=None):
    """Read the CSV merge log. Returns (frames sorted by tape frame, nfiles seen)."""
    frames = []
    seen_n = 0
    with open(csv_path, newline="") as fh:
        for r in csv.DictReader(fh):
            tc = r.get("tc") or ""
            if not tc:
                continue
            st = r.get("Status") or ""
            seen_n = max(seen_n, len(st))
            cover = frozenset(i for i, c in enumerate(st) if c != "M")
            damaged = frozenset(i for i, c in enumerate(st) if c == "P")
            ab = r.get("abst") or ""
            frames.append(Frame(tc_to_frames(tc, fps), tc, r.get("rdt", "") or "",
                                int(r.get("BlockErrors") or 0), cover, int(ab) if ab else None,
                                damaged))
    frames.sort(key=lambda f: f.tf)
    return frames, (nfiles or seen_n)
