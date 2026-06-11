"""The library entry (run.analyze). Driven through a *pre-seeded* merge-log cache so the test needs
no dvrescue and no real .dv: it proves analyze() resolves the cache, parses, and plans."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dvmerge import run  # noqa: E402

HEADER = ("FramePos,abst,abst_r,abst_nc,tc,tc_r,tc_nc,rdt,rdt_r,rdt_nc,rec_start,rec_end,"
          "Used,Status,Comments,BlockErrors,BlockErrors_Even,IssueFixed,SourceSpeed,FrameSpeed,"
          "InputPos,OutputPos")


def _row(n, tc, status="  ", berr=0):
    cols = [""] * 22
    cols[0] = str(n)
    cols[4] = tc
    cols[7] = "2010-01-01 08:00:00"
    cols[12] = "0"
    cols[13] = status
    cols[15] = str(berr)
    return ",".join(cols)


class TestRun(unittest.TestCase):
    def test_signature_tracks_content(self):
        tmp = tempfile.mkdtemp()
        a = os.path.join(tmp, "A-1.dv")
        with open(a, "wb") as f:
            f.write(b"\x00" * 16)
        s1 = run.signature([a])
        with open(a, "wb") as f:
            f.write(b"\x00" * 32)            # size changed
        self.assertNotEqual(s1, run.signature([a]))

    def test_analyze_reuses_cached_merge_log(self):
        tmp = tempfile.mkdtemp()
        files = []
        for name in ("A-1.dv", "A-2.dv"):
            p = os.path.join(tmp, name)
            open(p, "wb").close()           # existence is enough; merge won't run (cache hit)
            files.append(p)
        cache_dir = os.path.join(tmp, "cache")
        os.makedirs(cache_dir)
        rows = [_row(i, "00:00:%02d:%02d" % (i // 25, i % 25)) for i in range(50)]
        sig = run.signature(files)
        with open(os.path.join(cache_dir, "merge-%s.csv" % sig), "w") as f:
            f.write(HEADER + "\n" + "\n".join(rows) + "\n")

        plan = run.analyze(files, fps=25, cache_dir=cache_dir)
        self.assertEqual(plan.total_frames, 50)
        self.assertEqual(plan.spans, [])     # the cached log is a clean tape
        self.assertEqual(plan.clean, 50)


if __name__ == "__main__":
    unittest.main()
