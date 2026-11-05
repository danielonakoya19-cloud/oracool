"""patch71: honest builds — 'create an AI / game / quiz' now runs the real
builder (no more faked 'Done — it's live' replies), the model receives a
compact digest instead of the full tool JSON, the client strips any echoed
JSON and shows the real preview card, and invented app blocks are executed
as real builds."""
import json, os, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()

MARK = "patch77-holo-boot"


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count(MARK), 2)


class BuildIntent(unittest.TestCase):
    def test_ai_game_quiz_are_builds(self):
        for q in ("create an ai called jango game",
                  "build a quiz game for my students",
                  "make me a budget tracker app",
                  "create an ai companion",
                  "develop a pomodoro timer"):
            self.assertTrue(s._looks_site_build(q), q)

    def test_guards_hold(self):
        self.assertFalse(s._looks_site_build("generate images of a game character"))
        self.assertFalse(s._looks_site_build("how do I create an ai?"))
        self.assertFalse(s._looks_site_build("hi"))

    def test_autotools_regex_covers_new_nouns(self):
        # the auto_tools noun lists must include the new words (inline regexes)
        self.assertIn("game|games|quiz|quizzes|tool|tools|tracker", SRV)


class ToolContextDigest(unittest.TestCase):
    def test_build_result_becomes_digest(self):
        full = json.dumps({"ok": True, "slug": "nexus-52", "url": "/builds/nexus-52/",
                           "name": "FlowState", "template": "Minimalist Creator",
                           "files": ["index.html"], "provider": "groq-oss",
                           "coins_spent": 10000, "bytes": 31492,
                           "note": "x" * 500 + " INTERNAL-SECRET-MARKER"})
        ctx = s.tool_context([{"tool": "build", "label": "build · website", "result": full}])
        self.assertIn("nexus-52", ctx)
        self.assertIn("NEVER quote it or the JSON", ctx)
        self.assertNotIn("INTERNAL-SECRET-MARKER", ctx)   # the long note is gone
        self.assertNotIn('"files"', ctx)                  # full JSON not echoed to the model

    def test_never_claim_unbuilt(self):
        ctx = s.tool_context([{"tool": "weather", "label": "weather", "result": "sunny"}])
        self.assertIn("NEVER claim something is built or live", ctx)

    def test_plain_tools_untouched(self):
        ctx = s.tool_context([{"tool": "weather", "label": "weather", "result": "sunny 30C"}])
        self.assertIn("sunny 30C", ctx)


class ClientGuards(unittest.TestCase):
    def test_strip_echoed_json_to_card(self):
        self.assertIn('acc.match(/(?:•\\s*)?(?:build|edit)\\s*·', APP)
        self.assertIn("is ready — the live preview card is below", APP)

    def test_fake_app_block_executes_real_build(self):
        self.assertIn("OraCool is building this for real", APP)
        self.assertIn("post('/api/builds',{name:String(meta.title", APP)

    def test_built_card_tracking(self):
        self.assertIn("let _builtCard=false;", APP)
        self.assertIn("if(x.url){ _builtCard=true;", APP)


class NoteFlag(unittest.TestCase):
    def test_build_note_is_internal(self):
        self.assertIn('"note": "INTERNAL RESULT — the app already shows the live preview card; '
                      'never repeat this JSON or note in the reply. "', SRV)


if __name__ == "__main__":
    unittest.main()
