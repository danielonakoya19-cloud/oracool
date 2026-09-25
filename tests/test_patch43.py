"""patch43: composer modes (Auto / Fast / Build / Expert) + server-announced live build feed."""
import os, sys, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch45-inline-feed"'), 2)
        self.assertNotIn("patch42-feed", SRV)


class SiteBuildDetector(unittest.TestCase):
    def test_builds_any_phrasing(self):
        for t in ("Build me a website for Legend Fintech, a Lagos fintech app",
                  "make me an app for my gym in Lekki",
                  "create a landing page for Sweet Crumbs bakery",
                  "design an online store for Nkem Fabrics",
                  "set up a dashboard for our logistics company"):
            self.assertTrue(s._looks_site_build(t), t)

    def test_edits_count(self):
        self.assertTrue(s._looks_site_build("change the hero headline on my site to 'Money that moves'"))
        self.assertTrue(s._looks_site_build("make the buttons on the website gold"))

    def test_not_questions_or_media(self):
        for t in ("how do I build a website for my gym", "what is the best app for savings",
                  "generate an image of a fintech app", "create a logo for Legend", "hi"):
            self.assertFalse(s._looks_site_build(t), t)


class ForceBuild(unittest.TestCase):
    def test_build_mode_forces_the_builder(self):
        calls = []
        with mock.patch.object(s, "build_site", lambda email, name, brief: calls.append((name, brief)) or {"ok": True, "url": "/builds/x/", "slug": "x"}):
            runs = s.auto_tools("Legend Fintech — instant transfers, savings and virtual cards for Lagos", tier="pro",
                                email="m@x.com", force_build=True)
        self.assertTrue(any(r["tool"] == "build" for r in runs), runs)
        self.assertEqual(len(calls), 1)
        self.assertIn("Legend Fintech", calls[0][0])

    def test_auto_mode_unchanged(self):
        with mock.patch.object(s, "build_site", lambda *a, **k: {"ok": True}):
            runs = s.auto_tools("Legend Fintech — instant transfers, savings and virtual cards for Lagos", tier="pro", email="m@x.com")
        self.assertFalse(any(r["tool"] == "build" for r in runs))


class ChatWiring(unittest.TestCase):
    def test_progress_announcement_and_modes(self):
        self.assertIn('_mode = str(body.get("mode") or "auto").strip().lower()', SRV)
        self.assertIn('_slow = stream and (_mode == "build" or _looks_slow_tool(last_user))', SRV)
        self.assertIn('self.wfile.write(b\'data: {"__progress": "build"}\\n\\n\')', SRV)
        self.assertEqual(SRV.count('force_build=(_mode == "build")'), 2)
        self.assertIn('if _mode == "expert" and "gpt-oss" in str(model):', SRV)
        self.assertIn('payload["reasoning_effort"] = "high"', SRV)
        self.assertIn("_EXPERT_MODE", SRV)


class Client(unittest.TestCase):
    def test_mode_picker(self):
        for needle in ('id="modeBtn"', 'id="modeMenu"', 'data-mode="build"', 'data-mode="expert"', 'data-mode="fast"', 'data-mode="auto"',
                       "const MODES={auto:", "function setMode(m)", "chatMode:'auto'",
                       "if(session && (_mode==='build' || wantsBuild(text)||wantsEdit(text))) _steps=stepsCard(b);",
                       "if(j.__progress && !_steps && session){ try{ _steps=stepsCard(b); }catch(e){} }",
                       "mode:_mode};", "(_mode==='expert'?2200:900)"):
            self.assertIn(needle, APP, needle)


if __name__ == "__main__":
    unittest.main()
