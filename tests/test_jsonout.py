"""The structured (JSON) analysis output — the contract a GUI/consumer reads instead of the
Markdown. Normal CLI use never exercises this path, so without these tests a later model refactor
could silently break it; they pin the shape and the field meanings to the model. Synthetic CSV
logs, no dvrescue or sample captures needed.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dvmerge import parse, plan, jsonout  # noqa: E402

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


class TestJsonOut(unittest.TestCase):
    def _analysis(self, rows, files=("A-1.dv", "A-2.dv"), bridge=0.0, min_s=0.0):
        path = write_csv(rows)
        try:
            frames, _ = parse.parse(path, 25, nfiles=len(files))
            p = plan.build(frames, list(files), 25, bridge_s=bridge, min_s=min_s)
            return jsonout.analysis(p)
        finally:
            os.remove(path)

    def test_clean_analysis_shape_and_is_json_serializable(self):
        rows = [row(i, "00:00:%02d:%02d" % (i // 25, i % 25), "2010-01-01 08:00:00", "  ", 0)
                for i in range(50)]
        d = self._analysis(rows)
        # round-trips through JSON unchanged (cover sets become sorted lists, nothing else leaks)
        self.assertEqual(json.loads(json.dumps(d)), d)

        self.assertEqual(d["schema"], "dvmerge.analysis/1")
        self.assertTrue(d["version"])
        self.assertEqual(d["fps"], 25)
        self.assertEqual(d["total_frames"], 50)
        self.assertEqual(d["clean"], 50)
        self.assertEqual(d["dmg"], 0)
        self.assertEqual(d["miss"], 0)
        self.assertTrue(d["complete"])                   # nothing to re-capture
        self.assertEqual(d["spans"], [])
        self.assertEqual(d["files"], ["A-1", "A-2"])     # tags (basename stems), cover indexes into this

    def test_mosaic_span_carries_coverage_and_cue_points(self):
        # frame 10 damaged, present only in file 0 (status 'P' + 'M')
        rows = []
        for i in range(30):
            st, be = ("  ", 0)
            if i == 10:
                st, be = ("PM", 7)
            rows.append(row(i, "00:00:%02d:%02d" % (i // 25, i % 25), "2010-01-01 08:00:00", st, be))
        d = self._analysis(rows)
        self.assertFalse(d["complete"])
        self.assertEqual(len(d["spans"]), 1)
        s = d["spans"][0]
        self.assertEqual(s["kind"], "mosaic")
        self.assertEqual(s["dmg"], 1)
        self.assertEqual(s["miss"], 0)
        self.assertEqual(s["bmax"], 7)
        self.assertEqual(s["cover"], [0])                # only capture A-1 has this frame
        self.assertTrue(s["tc0"])                        # cue point on the deck
        self.assertTrue(s["rdt0"])                       # wall-clock cross-check

    def test_source_carries_its_own_damage_runs(self):
        # frames 10..13 damaged in file 0 (status 'P '); file 1 clean throughout
        rows = []
        for i in range(30):
            st = "P " if 10 <= i <= 13 else "  "
            rows.append(row(i, "00:00:%02d:%02d" % (i // 25, i % 25), "2010-01-01 08:00:00",
                            st, 7 if 10 <= i <= 13 else 0))
        d = self._analysis(rows)
        a1 = next(s for s in d["sources"] if s["tag"] == "A-1")
        a2 = next(s for s in d["sources"] if s["tag"] == "A-2")
        self.assertTrue(a1["damage"], "A-1 should list its own damaged run")
        self.assertEqual(a1["damage"][0]["frames"], 4)
        self.assertTrue(a1["damage"][0]["tc0"])
        self.assertEqual(a2["damage"], [])               # A-2 is clean
        self.assertEqual(json.loads(json.dumps(d)), d)

    def test_span_runs_are_tight_not_bridged(self):
        # damaged frames at 10, 30, 50 — within one bridged re-capture span, but >0.5 s apart, so the
        # map should see THREE tight sub-runs (not one filled block)
        rows = []
        for i in range(70):
            dmg = i in (10, 30, 50)
            rows.append(row(i, "00:00:%02d:%02d" % (i // 25, i % 25), "2010-01-01 08:00:00",
                            "P " if dmg else "  ", 5 if dmg else 0))
        d = self._analysis(rows, bridge=3.0)             # bridge the clean gaps into one span
        self.assertEqual(len(d["spans"]), 1)            # one re-capture span (bridged)
        self.assertEqual(len(d["spans"][0]["runs"]), 3)  # but three precise runs for the map

    def test_missing_span_has_empty_coverage(self):
        # frames 0..4 then jump to 10..14 -> frames 5..9 missing in every capture
        rows = [row(i, "00:00:00:%02d" % i, "2010-01-01 08:00:00", "  ", 0) for i in range(5)]
        rows += [row(i, "00:00:00:%02d" % i, "2010-01-01 08:00:00", "  ", 0) for i in range(10, 15)]
        d = self._analysis(rows)
        self.assertEqual(len(d["spans"]), 1)
        s = d["spans"][0]
        self.assertEqual(s["kind"], "missing")
        self.assertEqual(s["miss"], 5)
        self.assertEqual(s["cover"], [])                 # nothing to improve on — lost unless re-captured
        self.assertEqual(d["lost_frames"], 5)


if __name__ == "__main__":
    unittest.main()
