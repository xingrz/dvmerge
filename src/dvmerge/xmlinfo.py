"""Parse a dvrescue ``-x`` XML into a per-input error profile.

The CSV merge log only counts errors per frame; dvrescue's XML carries, per input file (``<media>``)
and per frame, the DV error-concealment status (STA) — including ``n_even``, how many concealed
blocks fell on the even DIF sequences (one azimuth head). STA != 0 means a block was unreadable and
concealed (the visible mosaic); the code says how (10 = filled from the previous frame, 14 =
unspecified, both with continuity not guaranteed; 7/15 = hard error). We aggregate this into a
compact per-capture profile — how often a capture is concealed, how heavily, by which method, and
whether the damage favours one azimuth head — so a consumer can characterise each pass at a glance
(e.g. "concealed 20%/frame, head-balanced" for a head-mismatch transfer vs a clean-but-dropping one).
"""

import os
import xml.etree.ElementTree as ET

PAL_BLOCKS = 1620      # video blocks per PAL DV25 frame (12 DIF sequences)
NTSC_BLOCKS = 1350     # 10 DIF sequences

# STA code -> short human method. '*' marks "continuity not guaranteed" (the worse variants).
STA_METHOD = {
    2: "prev-frame", 4: "next-frame", 6: "concealed", 7: "in-block error",
    10: "prev-frame*", 12: "next-frame*", 14: "concealed*", 15: "uncorrected",
}


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_profiles(xml_path, fps=25.0):
    """``{basename: profile}`` for each ``<media>``. profile keys: ``framesSeen``,
    ``framesConcealed``, ``concealedFrac`` (0..1), ``avgConcealedPct`` (0..1, over concealed frames),
    ``evenSharePct`` (0..1 azimuth split of concealed blocks), ``staCode``, ``staMethod``."""
    out = {}
    if not xml_path or not os.path.exists(xml_path):
        return out
    blocks = NTSC_BLOCKS if round(fps) == 30 else PAL_BLOCKS
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return out
    for media in root.iter():
        if _local(media.tag) != "media":
            continue
        name = os.path.basename(media.attrib.get("ref") or "")
        frames = concealed_frames = total_err = even_err = 0
        sum_pct = 0.0
        sta_count = {}
        for fr in media.iter():
            if _local(fr.tag) != "frame":
                continue
            frames += 1
            ftot = feven = 0
            # the frame-level <sta> summaries (direct children, carrying n + n_even); the per-dseq
            # <sta> live under <dseq> and are skipped here so we don't double-count.
            for c in fr:
                if _local(c.tag) != "sta":
                    continue
                t = int(c.attrib.get("t", 0) or 0)
                if t == 0:
                    continue
                n = int(c.attrib.get("n", 0) or 0)
                ftot += n
                feven += int(c.attrib.get("n_even", 0) or 0)
                sta_count[t] = sta_count.get(t, 0) + n
            if ftot > 0:
                concealed_frames += 1
                sum_pct += ftot / blocks
                total_err += ftot
                even_err += feven
        if frames == 0:
            continue
        code = max(sta_count, key=lambda c: sta_count[c]) if sta_count else 0
        out[name] = {
            "framesSeen": frames,
            "framesConcealed": concealed_frames,
            "concealedFrac": concealed_frames / frames,
            "avgConcealedPct": (sum_pct / concealed_frames) if concealed_frames else 0.0,
            "evenSharePct": (even_err / total_err) if total_err else 0.0,
            "staCode": code,
            "staMethod": STA_METHOD.get(code, "error"),
        }
    return out
