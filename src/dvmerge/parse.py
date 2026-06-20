"""Parse dvrescue's CSV merge log into a per-frame view of the merged tape.

``dvrescue ... -m OUT.dv --merge-log LOG.csv --csv`` writes one row per frame that made it into the
merged output, in tape order. The columns dvmerge reads:

  ``FramePos``     the frame's position in the reconstructed tape — dvrescue's own dense, monotonic
                   index into the merged output. This is the physical layout axis (see
                   :mod:`dvmerge.plan`): it is present on every row and immune to the tc/abst
                   misreads a worn tape produces.
  ``tc``           tape SMPTE timecode (HH:MM:SS:FF / ;FF for drop-frame) — cue point on the deck.
                   May be blank on a present frame dvrescue couldn't relabel; we carry it forward.
  ``rdt``          recording date-time, the camera's wall clock — a human-readable cross-check
  ``BlockErrors``  residual bad DV blocks in this frame *after* the merge picked the best copy;
                   0 means a clean copy was found, >0 means every capture was damaged here
  ``Status``       one character per input capture, in the order the files were passed:
                     ``' '`` the capture has this frame, clean
                     ``'P'`` the capture has this frame, but damaged
                     ``'M'`` the capture is missing this frame entirely

Frames missing from *every* capture are never written, so they have no row — they show up as a
**jump in FramePos**, which is how :mod:`dvmerge.plan` recovers them. Every written row has at least
one non-``M`` character, i.e. coverage >= 1.
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

    __slots__ = ("fp", "tf", "tc", "rdt", "berr", "cover", "abst", "damaged")

    def __init__(self, fp, tf, tc, rdt, berr, cover, abst, damaged=frozenset()):
        self.fp = fp            # FramePos: dvrescue's dense, monotonic index into the reconstructed
        #                         tape. The physical layout axis — present on every row, with a JUMP
        #                         exactly where frames are missing from every capture. Reliable when
        #                         tc and abst are not: tc restarts at every new recording session
        #                         (overwrites, multi-day footage, over-capture) and abst can be a wild
        #                         subcode misread, but FramePos just counts tape frames in order.
        self.tf = tf            # tape frame number (from tc), or None when this frame has no tc — a
        #                         per-session LABEL, NOT a global order (tc restarts each session).
        self.tc = tc            # raw tape timecode string ('' when dvrescue didn't relabel it)
        self.rdt = rdt          # recording date-time string, for display
        self.berr = berr        # residual block errors after merge (0 == clean)
        self.cover = cover      # frozenset of input indices that have this frame at all
        self.abst = abst        # absolute track number on tape, or None — kept for reference; no
        #                         longer used to find missing frames (FramePos is the arbiter now).
        self.damaged = damaged  # frozenset of input indices that have this frame but DAMAGED ('P')


def parse(csv_path, fps, nfiles=None):
    """Read the CSV merge log. Returns (frames in physical read order, nfiles seen).

    Frames are ordered by ``FramePos`` (the order dvrescue read the tape), NOT by tape timecode.
    A tape commonly holds several recording sessions — old footage overwritten in part, different-day
    footage spliced on, a little over-capture at each end — and each session's record-run ``tc``
    restarts, so tc is not a monotonic global coordinate; sorting by it scatters physically-adjacent
    frames and invents huge gaps. Physical read order is the one stable axis (see :mod:`dvmerge.plan`).
    """
    frames = []
    seen_n = 0
    with open(csv_path, newline="") as fh:
        for r in csv.DictReader(fh):
            fp = r.get("FramePos") or ""
            tc = r.get("tc") or ""
            st = r.get("Status") or ""
            if not fp and not tc:
                continue   # a blank line, not a frame
            if fp and not fp.isdigit():
                # dvrescue may write plain diagnostics such as "File read issue." into
                # --merge-log CSV for a readable file with a partial tail frame.
                if not tc and not st:
                    continue
                raise ValueError("invalid FramePos in dvrescue merge log: %r" % fp)
            seen_n = max(seen_n, len(st))
            # A written row is present in >= 1 capture even when dvrescue left tc/abst blank; keep it
            # (dropping it would make its physical span read as missing). cover counts non-'M' chars.
            cover = frozenset(i for i, c in enumerate(st) if c != "M")
            damaged = frozenset(i for i, c in enumerate(st) if c == "P")
            ab = r.get("abst") or ""
            frames.append(Frame(int(fp) if fp else None,
                                tc_to_frames(tc, fps) if tc else None, tc,
                                r.get("rdt", "") or "", int(r.get("BlockErrors") or 0), cover,
                                int(ab) if ab else None, damaged))
    # Physical order. Fall back to tc order only if the log somehow has no FramePos (old logs); for a
    # single-session tape the two orders coincide anyway.
    if frames and all(f.fp is not None for f in frames):
        frames.sort(key=lambda f: f.fp)
    else:
        frames.sort(key=lambda f: (f.tf if f.tf is not None else 0))
    return frames, (nfiles or seen_n)
