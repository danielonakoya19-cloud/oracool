"""patch84 — video actually generates (Wan2.2 cascade + fast CVRON fail + Agnes cap +
Pexels stock localization), Arena-style model modes (Agent/Direct/Side by Side/Battle),
and builder edits that use the CLIENT'S images (media markers -> exact-URL directives)."""
import re
import unittest
from pathlib import Path

SRV = Path("server.py").read_text(encoding="utf-8")
PAGE = Path("index.html").read_text(encoding="utf-8")


class TestPatch84VideoGen(unittest.TestCase):
    def test_marker_is_patch84(self):
        self.assertEqual(SRV.count('"patch84-video-modes-edit"'), 2)

    def test_wan22_frame_cascade(self):
        # frames come from gemini image -> agnes image -> nexa image, NEVER the dead CVRON
        fn = SRV[SRV.index("def _hf_wan22_video"):SRV.index("def _hf_wan22_video") + 3200]
        self.assertIn("_gemini_image", fn)
        self.assertIn("_agnes_image", fn)
        self.assertIn("_nexa_image", fn)

    def test_agnes_video_deadline_short(self):
        fn = SRV[SRV.index("def _agnes_video"):]
        fn = fn[:fn.index("\ndef ", 10)]
        self.assertRegex(fn, r"deadline = time\.monotonic\(\) \+ 120")

    def test_cvron_fast_fails(self):
        i = SRV.index("def _cvron_image")
        self.assertIn("timeout=20", SRV[i:i + 2200])

    def test_han22_spaces_include_verified(self):
        blk = SRV[SRV.index("HF_WAN22_SPACES = ["):]
        blk = blk[:blk.index("]")]
        for sp in ("saravutw", "jcishere", "obsxrver"):
            self.assertIn(sp, blk)

    def test_pexels_stock_fallback(self):
        self.assertIn("def _pexels_videos", SRV)
        self.assertIn("pexels-stock", SRV)
        fn = SRV[SRV.index("def _pexels_videos"):]
        fn = fn[:fn.index("\ndef ")]
        self.assertIn("api.pexels.com/videos/search", fn)
        self.assertIn("User-Agent", fn)          # Pexels 403s python-urllib UA
        self.assertIn('"/generated/" + os.path.basename(fn)', fn)  # clip localized locally


class TestPatch84ModePicker(unittest.TestCase):
    def test_menu_present(self):
        for bit in ('id="mmBtn"', 'id="mmMenu"', 'data-mm="agent"', 'data-mm="direct"',
                    'data-mm="side"', 'data-mm="battle"'):
            self.assertIn(bit, PAGE)

    def test_compare_engine(self):
        self.assertIn("async function sendCompare(", PAGE)
        self.assertIn("[[cmp:", PAGE)
        self.assertIn("function parseCmp(", PAGE)
        self.assertIn("function renderCompare(", PAGE)
        self.assertGreaterEqual(PAGE.count("renderCmpOrRich(bd"), 2)  # both restore renderers

    def test_direct_provider_override(self):
        self.assertIn("provider:(modelMode==='direct'&&mmA?mmA:", PAGE)

    def test_gemini_direct_provider_server(self):
        import inspect
        import server
        cls = next(v for v in vars(server).values()
                   if inspect.isclass(v) and hasattr(v, "_resolve_provider"))
        class H(cls):
            def __init__(self):
                self.headers = {}
        h = H()
        k, base, model, prov = cls._resolve_provider(h, {"provider": "gemini"})
        self.assertIn("generativelanguage.googleapis.com", base)
        self.assertEqual(prov, "gemini")
        k2, b2, m2, p2 = cls._resolve_provider(h, {"provider": "anthropic"})
        self.assertEqual(p2, "anthropic")


class TestPatch84BuilderEdits(unittest.TestCase):
    def test_client_assets_directive_in_edit(self):
        i = SRV.index("def build_edit")
        self.assertIn("CLIENT ASSETS", SRV[i:i + 6000])
        self.assertIn(r"\[\[media:", SRV[i:i + 6000])

    def test_client_assets_directive_in_build(self):
        i = SRV.index("def build_site")
        self.assertIn("CLIENT ASSETS", SRV[i:i + 2000])

    def test_media_marker_regex_extracts_urls(self):
        pat = re.compile(r"\[\[media:[^\]|]*\|[^|]*\|([^\]]+)\]\]")
        t = "make a site\n[[media:logo.png|image|/media/abc/logo.png]]\n[[media:hero.jpg|image|https://x.supabase.co/hero.jpg]]"
        self.assertEqual(pat.findall(t),
                         ["/media/abc/logo.png", "https://x.supabase.co/hero.jpg"])


if __name__ == "__main__":
    unittest.main()
