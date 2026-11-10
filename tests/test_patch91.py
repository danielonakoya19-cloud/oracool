"""patch91 — generated images must SHOW, never leak tool JSON or links. The model sometimes
echoes its image tool result as [[{"type":"image","image":{...,"images":[url]}}]]; the reply
renderer now converts those payloads into the image itself (and strips them from previews,
speech and calls), with a prompt line forbidding raw tool JSON in replies."""
import unittest
from pathlib import Path

SRV = Path("server.py").read_text(encoding="utf-8")
APP = Path("index.html").read_text(encoding="utf-8")


class TestPatch91Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch99-viz-clean"'), 2)


class TestPatch91MediaJsonRendering(unittest.TestCase):
    def test_helpers_exist(self):
        self.assertIn("function _mediaJsonBlocks(t, drop){", APP)
        self.assertIn("function _mediaJsonToMarkdown(t)", APP)
        self.assertIn("function _stripMediaJson(t)", APP)

    def test_render_converts_payloads(self):
        i = APP.index("function renderRich(el, text){")
        self.assertIn("text=_mediaJsonToMarkdown(text);", APP[i:i + 200])

    def test_preview_and_speech_scrubbed(self):
        self.assertIn("_stripMediaJson(String(t)).replace(", APP)

    def test_call_replies_scrubbed(self):
        self.assertIn("acc=_stripMediaJson(acc||'')", APP)

    def test_cmp_and_media_markers_pass_through(self):
        # the scanner only treats blocks starting with { or [ as JSON
        i = APP.index("function _mediaJsonBlocks(t, drop){")
        seg = APP[i:i + 1600]
        self.assertIn("cand.charAt(0)==='{'", seg)
        self.assertIn("out+=t.slice(a, b+2);", seg)

    def test_model_told_not_to_paste_links(self):
        self.assertIn("never paste tool JSON, file links or URLs into your reply", APP)

    def test_still_shows_the_image(self):
        # the bare-image renderer stays in place so converted URLs become <img>
        self.assertIn("bare image urls", APP)


if __name__ == "__main__":
    unittest.main()
