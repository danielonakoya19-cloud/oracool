"""patch72: app/game build quality — briefs detected as apps get a dedicated
game/app engineer system prompt (canvas, loop, touch, score, restart, sound),
an app-specific quality reviewer that flags landing-page masquerades (the
'jango game' failure), and a client freshness poller so devices always run
the newest build of OraCool itself."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

MARK = "patch99-viz-clean"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)


class AppBrain(unittest.TestCase):
    def test_dedicated_app_system(self):
        self.assertIn("_APP_SYSTEM", SRV)
        for req in ("<canvas>", "requestAnimationFrame", "localStorage", "game-over",
                    "touch controls", "WebAudio", "no pricing section"):
            self.assertIn(req, s._APP_SYSTEM)

    def test_app_brief_bans_landing_copy(self):
        low = s._APP_SYSTEM.lower()
        for banned in ("pricing", "faq", "testimonials", "contact form"):
            self.assertIn(banned, low)  # named as BANS

    def test_system_swap_wired(self):
        self.assertIn("system = _APP_SYSTEM +", SRV)
        self.assertNotIn(" APP MODE:", SRV)  # old paragraph replaced by the dedicated brain


class AppReviewer(unittest.TestCase):
    def test_jango_landing_page_fails_review(self):
        landing = ('<html><head><title>x</title></head><body><h1>Choices</h1>'
                   '<section>pricing</section><footer>f</footer>'
                   '<p>talk about a game with collision and a game over screen</p></body></html>')
        issues = s._app_quality_issues(landing)
        self.assertTrue(any("canvas" in i for i in issues))
        self.assertTrue(any("landing-page sections" in i for i in issues))
        self.assertTrue(any("localStorage" in i for i in issues))

    def test_real_game_snippet_passes(self):
        game = ("<html><body><canvas id=c></canvas><script>"
                "const c=document.getElementById('c');let score=0,best=localStorage.getItem('b')||0;"
                "parallax layers scroll; ctx.shadowBlur=20; ctx.globalCompositeOperation='lighter';"
                "particles explosion trail; vignette();"
                "let last=0;function loop(t){requestAnimationFrame(loop);const dt=(t-last)/1000;last=t;"
                "score+=dt;}requestAnimationFrame(loop);"
                "addEventListener('keydown',e=>{});addEventListener('touchstart',e=>{});"
                "addEventListener('pointerdown',e=>{});"
                "function gameOver(){if(score>best)localStorage.setItem('b',score);"
                "show('game over — tap to restart / play again');}"
                "function pause(){}</script></body></html>")
        # 18000-byte floor is the only expected complaint on this minimal snippet
        issues = [i for i in s._app_quality_issues(game) if "too thin" not in i]
        self.assertEqual(issues, [])


class Wiring(unittest.TestCase):
    def test_qa_mode_and_gate(self):
        self.assertIn('"mode": ("app" if _is_app else "site")', SRV)
        self.assertIn("_app_quality_issues(_idx[\"content\"]) if _is_app", SRV)
        self.assertIn("_new_issues = ((_app_quality_issues(_new) if _is_app", SRV)

    def test_user_brief_branch(self):
        self.assertIn("Build this app/game now — the REAL working product, not a page about it. ", SRV)

    def test_client_freshness_poller(self):
        self.assertIn("build freshness", APP)
        self.assertIn("j.build !== _b0", APP)
        self.assertIn("location.reload()", APP)


if __name__ == "__main__":
    unittest.main()
