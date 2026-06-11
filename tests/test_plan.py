"""Deterministic tests on a tiny synthetic merge log — no dvrescue or sample captures needed."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dvmerge import parse, plan, report  # noqa: E402

HEADER = ("FramePos,abst,abst_r,abst_nc,tc,tc_r,tc_nc,rdt,rdt_r,rdt_nc,rec_start,rec_end,"
          "Used,Status,Comments,BlockErrors,BlockErrors_Even,IssueFixed,SourceSpeed,FrameSpeed,"
          "InputPos,OutputPos")


def row(n, tc, rdt, status, berr, abst=None):
    cols = [""] * 22
    cols[0] = str(n)
    cols[1] = "" if abst is None else str(abst)
    cols[4] = tc
    cols[7] = rdt
    cols[12] = "0"
    cols[13] = status
    cols[15] = str(berr)
    return ",".join(cols)


def write_csv(rows):
    fd, path = tempfile.mkstemp(suffix=".csv")
    with os.fdopen(fd, "w") as f:
        f.write(HEADER + "\n")
        f.write("\n".join(rows) + "\n")
    return path


class TestPlan(unittest.TestCase):
    def _build(self, rows, files=("A-1.dv", "A-2.dv"), bridge=0.0, min_s=0.0):
        path = write_csv(rows)
        try:
            frames, n = parse.parse(path, 25, nfiles=len(files))
            return plan.build(frames, list(files), 25, bridge_s=bridge, min_s=min_s)
        finally:
            os.remove(path)

    def test_clean_tape_has_no_spans(self):
        rows = [row(i, "00:00:%02d:%02d" % (i // 25, i % 25), "2010-01-01 08:00:00", "  ", 0)
                for i in range(50)]
        p = self._build(rows)
        self.assertEqual(p.spans, [])
        self.assertEqual(p.dmg, 0)
        self.assertEqual(p.miss, 0)
        self.assertIn("Nothing to re-capture", report.render(p))

    def test_mosaic_frame_becomes_span_with_coverage(self):
        # frame 10 damaged, present only in file 0 (status 'P' + 'M')
        rows = []
        for i in range(30):
            st, be = ("  ", 0)
            if i == 10:
                st, be = ("PM", 7)
            rows.append(row(i, "00:00:%02d:%02d" % (i // 25, i % 25), "2010-01-01 08:00:00", st, be))
        p = self._build(rows)
        self.assertEqual(len(p.spans), 1)
        s = p.spans[0]
        self.assertEqual(s.dmg, 1)
        self.assertEqual(s.miss, 0)
        self.assertEqual(s.bmax, 7)
        self.assertEqual(s.cover, {0})
        self.assertEqual(s.kind, "mosaic")

    def test_timecode_gap_is_missing_and_lost(self):
        # frames 0..4 then jump to 10..14 -> frames 5..9 missing in every capture
        rows = [row(i, "00:00:00:%02d" % i, "2010-01-01 08:00:00", "  ", 0) for i in range(5)]
        rows += [row(i, "00:00:00:%02d" % i, "2010-01-01 08:00:00", "  ", 0) for i in range(10, 15)]
        p = self._build(rows)
        self.assertEqual(len(p.spans), 1)
        s = p.spans[0]
        self.assertEqual(s.miss, 5)
        self.assertEqual(s.dmg, 0)
        self.assertEqual(s.kind, "missing")
        self.assertEqual(p.lost_frames, 5)
        self.assertIn("no copy at all", report.render(p))

    def test_abst_continuous_tc_jump_is_not_missing(self):
        # tc leaps 4 -> 1004 (a camera stop/start) but abst stays contiguous (8/frame): nothing lost.
        rows = [row(i, "00:00:00:%02d" % i, "2010-01-01 08:00:00", "  ", 0, abst=100 + 8 * i)
                for i in range(5)]
        rows += [row(5 + i, "00:00:40:%02d" % i, "2010-01-01 08:00:02", "  ", 0, abst=140 + 8 * i)
                 for i in range(5)]   # abst 140 follows 132 -> one step, no gap
        p = self._build(rows)
        self.assertEqual(p.miss, 0)
        self.assertEqual(p.spans, [])

    def test_abst_gap_counts_as_missing(self):
        # abst jumps by 800 (100 frames of 8) with matching tc gap: 99 frames genuinely missing.
        rows = [row(i, "00:00:00:%02d" % i, "2010-01-01 08:00:00", "  ", 0, abst=100 + 8 * i)
                for i in range(5)]
        rows += [row(5 + i, "00:00:04:%02d" % i, "2010-01-01 08:00:00", "  ", 0, abst=932 + 8 * i)
                 for i in range(5)]   # 932 - 132 = 800 -> (800/8 - 1) = 99 missing
        p = self._build(rows)
        self.assertEqual(p.miss, 99)
        self.assertEqual(len(p.spans), 1)
        self.assertEqual(p.lost_frames, 99)

    def test_bridge_merges_nearby_damage(self):
        rows = []
        for i in range(60):
            st, be = ("  ", 0)
            if i in (10, 40):
                st, be = ("PM", 3)
            rows.append(row(i, "00:00:%02d:%02d" % (i // 25, i % 25), "2010-01-01 08:00:00", st, be))
        far = self._build(rows, bridge=0.5)   # 12 frames < 30-frame gap -> two spans
        self.assertEqual(len(far.spans), 2)
        near = self._build(rows, bridge=2.0)  # 50 frames bridges the gap -> one span
        self.assertEqual(len(near.spans), 1)


if __name__ == "__main__":
    unittest.main()
