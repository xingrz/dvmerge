"""Deterministic tests for session-aware merging — synthetic CSV/XML, no dvrescue or sample media."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dvmerge import parse, plan, session, xmlinfo  # noqa: E402

HEADER = ("FramePos,abst,abst_r,abst_nc,tc,tc_r,tc_nc,rdt,rdt_r,rdt_nc,rec_start,rec_end,"
          "Used,Status,Comments,BlockErrors,BlockErrors_Even,IssueFixed,SourceSpeed,FrameSpeed,"
          "InputPos,OutputPos")


def row(fp, tc, rdt, status, abst=None, berr=0):
    cols = [""] * 22
    cols[0] = str(fp)
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


def _frames(rows, nfiles=2):
    path = write_csv(rows)
    try:
        fr, _ = parse.parse(path, 25, nfiles=nfiles)
        return fr
    finally:
        os.remove(path)


def _tc(i):
    return "00:%02d:%02d:%02d" % (i // 1500, (i // 25) % 60, i % 25)


class TestDetect(unittest.TestCase):
    def test_duplicated_body_is_detected(self):
        # Island 1: a head (June 15) then body (June 17 -> 24), abst climbing to 60000.
        # Island 2: the body AGAIN, abst reset to ~1, rdt rewound to June 17, re-covering seen time.
        rows = []
        for i in range(60):
            rdt = "2011-06-15 18:47:0%d" % min(i, 9) if i < 5 \
                else ("2011-06-17 21:35:00" if i < 41 else "2011-06-24 17:00:00")
            rows.append(row(i, _tc(i), rdt, "  ", abst=(i + 1) * 1000))
        for i in range(60):
            rdt = "2011-06-17 21:35:00" if i < 41 else "2011-06-24 17:00:00"
            rows.append(row(60 + i, _tc(i), rdt, "  ", abst=1 + i * 8))
        dup = session.detect_duplication(_frames(rows))
        self.assertEqual(dup, [60])     # FramePos where the duplicate island starts

    def test_single_session_not_flagged(self):
        rows = [row(i, _tc(i), "2010-01-01 08:00:00", "  ", abst=8 + i * 8) for i in range(120)]
        self.assertIsNone(session.detect_duplication(_frames(rows)))

    def test_clean_multi_session_not_flagged(self):
        # tc restarts (a seam) but rdt moves FORWARD and abst stays monotone — dvrescue folded it onto
        # one axis. No abst reset -> must not be mistaken for a duplicate.
        rows = [row(i, "00:36:%02d:%02d" % (i // 25, i % 25), "2008-06-26 21:58:00", "  ",
                    abst=1000 + i * 100) for i in range(60)]
        rows += [row(60 + i, "00:00:%02d:%02d" % (i // 25, i % 25), "2008-06-27 15:18:00", "  ",
                     abst=7000 + i * 100) for i in range(60)]
        self.assertIsNone(session.detect_duplication(_frames(rows)))

    def test_camera_pause_not_flagged(self):
        rows = [row(i, _tc(i), "2010-01-01 08:00:00", "  ", abst=8 + i * 8) for i in range(60)]
        rows += [row(60 + i, _tc(5000 + i), "2010-01-01 08:30:00", "  ", abst=8 + (60 + i) * 8)
                 for i in range(60)]
        self.assertIsNone(session.detect_duplication(_frames(rows)))


class TestStitchCsv(unittest.TestCase):
    def test_framepos_offset_and_status_reexpansion(self):
        # session 0: inputs (global 0,1), 10 head frames; session 1: inputs (global 0,2,3), 15 body.
        s0 = [row(i, "00:36:00:%02d" % i, "2008-06-26 21:58:00", "ab") for i in range(10)]
        s1 = [row(i, "00:00:00:%02d" % (i % 25), "2008-06-27 15:18:00", "xyz") for i in range(15)]
        c0, c1 = write_csv(s0), write_csv(s1)
        out = tempfile.mkstemp(suffix=".csv")[1]
        try:
            total = session._stitch_csv([c0, c1], [[0, 1], [0, 2, 3]], 4, out)
            self.assertEqual(total, 25)               # 10 + 15 tape frames
            frames, n = parse.parse(out, 25, nfiles=4)
            self.assertEqual(n, 4)
            self.assertEqual(len(frames), 25)
            self.assertEqual([f.fp for f in frames[:12]], list(range(12)))   # session1 offset by 10
            # Status re-expanded to global order: session0 "ab" -> "abMM"; session1 "xyz" -> "xMyz"
            self.assertEqual(frames[0].cover, frozenset({0, 1}))
            self.assertEqual(frames[10].cover, frozenset({0, 2, 3}))
            # plan sees one tape with a recording seam at the junction
            p = plan.build(frames, ["A.dv", "B.dv", "C.dv", "D.dv"], 25, bridge_s=0.0, min_s=0.0)
            self.assertEqual(p.total_frames, 25)
            self.assertTrue(p.multi_session)
            self.assertEqual(p.seams, [10])
        finally:
            for p_ in (c0, c1, out):
                os.remove(p_)


# ---- synthetic dvrescue -x XML for partition / profile tests --------------------------------------

NS = "https://mediaarea.net/dvrescue"


def _media_xml(ref, frames):
    """frames: list of (n, pos, tc, rdt) — tc/rdt may be '' for unlabelled."""
    fr = "".join('<frame n="%d" pos="%d" tc="%s" rdt="%s"/>' % (n, pos, tc, rdt)
                 for (n, pos, tc, rdt) in frames)
    return '<media ref="%s">%s</media>' % (ref, fr)


def _write_xml(medias):
    body = "".join(medias)
    xml = '<?xml version="1.0" encoding="UTF-8"?><dvrescue xmlns="%s">%s</dvrescue>' % (NS, body)
    fd, path = tempfile.mkstemp(suffix=".xml")
    with os.fdopen(fd, "w") as f:
        f.write(xml)
    return path


class TestPartition(unittest.TestCase):
    def test_spanning_file_sliced_pure_file_whole(self):
        d = tempfile.mkdtemp()
        try:
            # file 0 spans a seam: head tc 0..1.9s (n 0..49) then body tc reset (n 50..99). pos=n*100.
            head = [(i, i * 100, _tc(i), "2011-06-15 18:47:00") for i in range(50)]
            body = [(50 + i, (50 + i) * 100, _tc(i), "2011-06-17 21:35:00") for i in range(50)]
            f0 = os.path.join(d, "full.dv")
            with open(f0, "wb") as fh:
                fh.write(b"\0" * (100 * 100))            # 100 frames * 100 bytes
            # file 1: pure body, one run, whole file
            f1 = os.path.join(d, "recap.dv")
            with open(f1, "wb") as fh:
                fh.write(b"\0" * (40 * 100))
            recap = [(i, i * 100, _tc(60 + i), "2011-06-17 21:36:00") for i in range(40)]
            xml = _write_xml([_media_xml("./full.dv", head + body), _media_xml("./recap.dv", recap)])
            try:
                sessions = session.partition([f0, f1], xml, 25)
            finally:
                os.remove(xml)
            self.assertEqual(len(sessions), 2)
            head_sess, body_sess = sessions[0], sessions[1]       # ordered by earliest rdt
            self.assertEqual(len(head_sess.runs), 1)              # only file 0's head slice
            self.assertEqual((head_sess.runs[0].file_idx, head_sess.runs[0].b0,
                              head_sess.runs[0].b1), (0, 0, 50 * 100))
            self.assertEqual(len(body_sess.runs), 2)              # file 0 body slice + file 1 whole
            by_idx = {r.file_idx: (r.b0, r.b1) for r in body_sess.runs}
            self.assertEqual(by_idx[0], (50 * 100, 100 * 100))    # sliced
            self.assertEqual(by_idx[1], (0, 40 * 100))            # whole
        finally:
            import shutil
            shutil.rmtree(d)


class TestProfileSum(unittest.TestCase):
    def test_same_ref_media_blocks_are_summed(self):
        # The combined XML carries a spanning file as two <media> with the same rewritten ref.
        # parse_profiles must sum them into one whole-capture profile.
        m1 = ('<media ref="/x/cap.dv"><frames count="100"/>'
              '<frame n="1"><sta t="10" n="81" n_even="40"/></frame></media>')
        m2 = ('<media ref="/x/cap.dv"><frames count="50"/>'
              '<frame n="2"><sta t="10" n="81" n_even="40"/></frame></media>')
        xml = '<?xml version="1.0" encoding="UTF-8"?><dvrescue xmlns="%s">%s%s</dvrescue>' % (NS, m1, m2)
        fd, path = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "w") as f:
            f.write(xml)
        try:
            prof = xmlinfo.parse_profiles(path, fps=25)
            self.assertIn("cap.dv", prof)
            self.assertEqual(prof["cap.dv"]["framesSeen"], 150)          # 100 + 50
            self.assertEqual(prof["cap.dv"]["framesConcealed"], 2)       # one concealed frame in each
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
