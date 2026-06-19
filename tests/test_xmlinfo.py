"""Parse a tiny synthetic dvrescue -x XML into a per-input error profile — no dvrescue needed."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dvmerge import xmlinfo  # noqa: E402

XML = """<?xml version="1.0"?>
<dvrescue xmlns="https://mediaarea.net/dvrescue">
  <media ref="/cap/A.dv">
    <frames count="3">
      <frame n="0"/>
      <frame n="1">
        <dseq n="0"><sta t="10" n="40"/></dseq>
        <sta t="10" n="200" n_even="120"/>
        <aud t="1" n="9"/>
      </frame>
      <frame n="2">
        <sta t="15" n="100" n_even="40"/>
      </frame>
    </frames>
    <frames count="7"/>
  </media>
  <media ref="/cap/B.dv">
    <frames count="2">
      <frame n="0"/>
      <frame n="1"/>
    </frames>
  </media>
</dvrescue>
"""


class TestXmlInfo(unittest.TestCase):
    def _profiles(self, xml):
        fd, path = tempfile.mkstemp(suffix=".xml")
        with os.fdopen(fd, "w") as f:
            f.write(xml)
        try:
            return xmlinfo.parse_profiles(path, fps=25.0)
        finally:
            os.remove(path)

    def test_per_media_profile(self):
        p = self._profiles(XML)
        self.assertEqual(set(p), {"A.dv", "B.dv"})

        a = p["A.dv"]
        # framesSeen is the TRUE total from the <frames count> ranges (3 + 7), NOT the 3 emitted
        # <frame> elements — so concealedFrac is the real rate over the whole capture.
        self.assertEqual(a["framesSeen"], 10)
        self.assertEqual(a["framesConcealed"], 2)            # frames 1 and 2 carry video errors
        self.assertAlmostEqual(a["concealedFrac"], 2 / 10, places=4)
        # avg over concealed frames of (concealed blocks / 1620 PAL blocks)
        self.assertAlmostEqual(a["avgConcealedPct"], (200 / 1620 + 100 / 1620) / 2, places=4)
        self.assertAlmostEqual(a["evenSharePct"], 160 / 300, places=4)  # n_even sum / total
        self.assertEqual(a["staCode"], 10)                   # 200 blocks of t=10 beats 100 of t=15
        self.assertEqual(a["staMethod"], "prev-frame*")
        # full method distribution, by share, not just the dominant code
        self.assertEqual(a["staHistogram"],
                         [{"code": 10, "method": "prev-frame*", "frac": 200 / 300},
                          {"code": 15, "method": "uncorrected", "frac": 100 / 300}])
        # the audio side (frame 1 carried an <aud> error), which the old profile never showed
        self.assertEqual(a["audioFramesConcealed"], 1)
        self.assertAlmostEqual(a["audioConcealedFrac"], 1 / 10, places=4)

        b = p["B.dv"]                                        # clean capture
        self.assertEqual(b["framesSeen"], 2)
        self.assertEqual(b["framesConcealed"], 0)
        self.assertEqual(b["concealedFrac"], 0.0)
        self.assertEqual(b["staCode"], 0)
        self.assertEqual(b["staHistogram"], [])
        self.assertEqual(b["audioFramesConcealed"], 0)

    def test_missing_xml_is_empty(self):
        self.assertEqual(xmlinfo.parse_profiles("/no/such.xml"), {})
        self.assertEqual(xmlinfo.parse_profiles(None), {})


if __name__ == "__main__":
    unittest.main()
