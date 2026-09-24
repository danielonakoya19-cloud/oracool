"""patch36 — OraCool can SHOW things: playable chess/ludo boards and live charts/plots/
diagrams rendered from ```oracool-app fenced JSON; chat/voice moves drive the board."""
import os, re, shutil, subprocess, sys, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRV = open(ROOT / 'server.py', encoding='utf-8').read()
APP = open(ROOT / 'index.html', encoding='utf-8').read()
JS = open(ROOT / 'apps.js', encoding='utf-8').read()


class StaticWiring(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch37-brand"'), 2)

    def test_server_serves_apps_js(self):
        self.assertIn('elif path == "/apps.js":', SRV)
        self.assertIn('os.path.join(BASE_DIR, "apps.js"), "text/javascript"', SRV)
        self.assertTrue((ROOT / 'apps.js').exists())

    def test_client_wiring(self):
        for k in ('<script src="/apps.js?v=36"></script>',
                  "OraApps.extract(text)",                       # renderRich pulls fences out before escaping
                  'OraApps.render(_apps.specs[+h.dataset.i], h)',  # …and mounts them after innerHTML
                  "OraApps.command(text)",                        # chat/voice moves drive the active board
                  "OraApps.onSay=(t)=>",                          # engine replies are spoken as OraCool
                  "function _noApp(t)",                           # JSON never read aloud / shown mid-stream
                  "turnSpeaker.finish(_noApp(_cleanReply(acc)))",
                  "VISUALS & GAMES (you can SHOW things, not just say them)",
                  '{"app":"chess"}', '{"app":"ludo"}', '"app":"chart"', '"app":"plot"', '"app":"flow"',
                  '.oc-board{display:grid;grid-template-columns:repeat(8,1fr)',
                  '.oc-ludo{display:grid;grid-template-columns:repeat(15,1fr)'):
            self.assertIn(k, APP, k)

    def test_game_command_runs_before_the_ai_call(self):
        i_cmd = APP.index("OraApps.command(text)")
        i_fetch = APP.index("const turnSpeaker=window.OraVoice?window.OraVoice.newTurn():null;")
        self.assertLess(i_cmd, i_fetch)

    def test_apps_js_is_umd_and_exposes_engines(self):
        self.assertIn("root.OraApps = api", JS)
        for k in ("render: render", "extract: extract", "stripForSpeech: stripForSpeech", "command: command",
                  "chess: { newChess", "ludo: { TRACK", "viz: { chart"):
            self.assertIn(k, JS)

    def test_no_external_resources_in_apps_js(self):
        self.assertNotRegex(JS, r"https?://")
        self.assertNotIn("innerHTML = spec", JS)


@unittest.skipUnless(shutil.which("node"), "node not available")
class EngineSuites(unittest.TestCase):
    def test_pure_engine_suite(self):
        r = subprocess.run(["node", str(ROOT / "tests" / "apps_engine_test.js")], capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        self.assertIn("engine checks passed", r.stdout)

    def test_dom_suite_if_jsdom_present(self):
        env = dict(os.environ)
        if os.path.isdir("/tmp/domtest/node_modules"):
            env["NODE_PATH"] = "/tmp/domtest/node_modules"
        r = subprocess.run(["node", str(ROOT / "tests" / "apps_dom_test.js")], capture_output=True, text=True, timeout=240, env=env)
        self.assertEqual(r.returncode, 0, r.stdout[-1500:] + r.stderr[-1500:])
        self.assertTrue("DOM checks passed" in r.stdout or "skipped" in r.stdout, r.stdout[-400:])


if __name__ == "__main__":
    unittest.main()
