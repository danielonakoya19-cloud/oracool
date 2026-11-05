"""patch73: cinematic graphics bar for built games — the app/game brain must
specify engine-grade canvas techniques (parallax, additive glow, particles,
shaded characters, trails, screen shake, cinematic HUD; optional Three.js for
explicit 3D), and the reviewer fails flat-graphics games and forces a rewrite."""
import os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
MARK = "patch80-trade-signals"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)


class GraphicsBar(unittest.TestCase):
    def test_system_specifies_techniques(self):
        for tech in ("GRAPHICS BAR", "PARALLAX", "shadowBlur", "globalCompositeOperation='lighter'",
                     "PARTICLE", "screen shake", "vignette", "Three.js"):
            self.assertIn(tech, s._APP_SYSTEM, tech)

    def test_flat_rects_banned(self):
        self.assertIn("flat colored rectangles are BANNED", s._APP_SYSTEM)


class ReviewerEnforcement(unittest.TestCase):
    def test_flat_game_flagged(self):
        flat = ('<html><body><canvas></canvas><script>let score=0;localStorage.setItem("b",1);'
                'function loop(){requestAnimationFrame(loop);}requestAnimationFrame(loop);'
                'addEventListener("keydown",()=>{});addEventListener("touchstart",()=>{});'
                'addEventListener("click",()=>{});'
                'function gameOver(){show("game over - tap to restart");}</script></body></html>' + " " * 20000)
        issues = [i for i in s._app_quality_issues(flat) if "flat" in i]
        self.assertEqual(len(issues), 1)
        self.assertIn("UNREAL BAR", issues[0])

    def test_cinematic_game_passes_graphics_check(self):
        cine = ('<html><body><canvas></canvas><script>'
                'parallax layers; ctx.shadowBlur=20; ctx.globalCompositeOperation="lighter";'
                'particles spawn explosion trail; let g=ctx.createRadialGradient();'
                'screenShake(); vignette(); localStorage.setItem("b",1);'
                'function loop(t){requestAnimationFrame(loop);}requestAnimationFrame(loop);'
                'addEventListener("keydown",()=>{});addEventListener("touchstart",()=>{});'
                'addEventListener("pointerdown",()=>{});'
                'function gameOver(){show("game over - restart / play again");}</script></body></html>' + " " * 20000)
        issues = [i for i in s._app_quality_issues(cine) if "flat" in i]
        self.assertEqual(issues, [])

    def test_non_game_pages_not_flagged(self):
        tool = ('<html><body><div id="app">budget tool</div><script>'
                'localStorage.setItem("x",1);addEventListener("click",()=>{});'
                'addEventListener("input",()=>{});addEventListener("submit",()=>{});</script></body></html>' + " " * 20000)
        issues = [i for i in s._app_quality_issues(tool) if "flat" in i]
        self.assertEqual(issues, [])  # graphics bar only applies to games


if __name__ == "__main__":
    unittest.main()
