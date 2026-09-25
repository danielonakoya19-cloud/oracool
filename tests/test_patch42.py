"""patch42: live build feed — streamed builder call, section-by-section steps, live notes, edit-flow steps."""
import os, sys, io, json, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()


class _FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _sse(pieces, finish="stop"):
    lines = []
    for p in pieces:
        lines.append(("data: " + json.dumps({"choices": [{"delta": {"content": p}}]}) + "\n").encode())
    lines.append(("data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}) + "\n").encode())
    lines.append(b"data: [DONE]\n")
    return _FakeResp(b"".join(lines))


class Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch46-durable"'), 2)
        self.assertNotIn("patch41-studio", SRV)


class Stream(unittest.TestCase):
    def test_streamed_text_and_progress_callbacks(self):
        seen = []
        with mock.patch.object(s.urllib.request, "urlopen", lambda req, timeout=0, context=None: _sse(["<html>", "<body>", "<section id='hero'>hi</section>", "</body></html>"])), \
             mock.patch.object(s, "key", lambda k, d="": "gk" if k == "GROQ_API_KEY" else ""):
            text, prov, finish = s._llm_text_stream("sys", "user", on_delta=lambda t: seen.append(len(t)))
        self.assertEqual(text, "<html><body><section id='hero'>hi</section></body></html>")
        self.assertEqual(prov, "groq-oss"); self.assertEqual(finish, "stop")
        self.assertTrue(seen and seen[-1] == len(text))

    def test_falls_back_when_streams_fail(self):
        def boom(req, timeout=0, context=None):
            raise RuntimeError("no stream")
        with mock.patch.object(s.urllib.request, "urlopen", boom), \
             mock.patch.object(s, "key", lambda k, d="": "gk" if k == "GROQ_API_KEY" else ""), \
             mock.patch.object(s, "_llm_text", lambda *a, **k: ("fallback", "groq", "stop")):
            self.assertEqual(s._llm_text_stream("sys", "user")[0], "fallback")

    def test_late_break_keeps_partial(self):
        big = "x" * 5000
        class Broken(_FakeResp):
            def __iter__(self):
                yield ("data: " + json.dumps({"choices": [{"delta": {"content": big}}]}) + "\n").encode()
                raise ConnectionError("dropped")
        with mock.patch.object(s.urllib.request, "urlopen", lambda req, timeout=0, context=None: Broken(b"")), \
             mock.patch.object(s, "key", lambda k, d="": "gk" if k == "GROQ_API_KEY" else ""):
            text, prov, finish = s._llm_text_stream("sys", "user")
        self.assertEqual(text, big); self.assertEqual(finish, "length")


class Feed(unittest.TestCase):
    def test_sections_become_steps(self):
        s._bp_reset("f@x.com", "Sweet Crumbs"); s._bp_step("f@x.com", "Building Sweet Crumbs", "thinking…", kind="build")
        cb = s._writer_progress("f@x.com")
        cb("")
        self.assertIn("thinking through", s.build_progress("f@x.com")["steps"][-1]["detail"])
        html = ('<header class="site-header"><nav>x</nav></header><section id="hero" class="hero reveal"></section>'
                '<section class="reveal features-grid"></section><section id="pricing"></section>')
        cb(html); cb(html + '<section aria-label="Contact form"></section><footer></footer></body></html>')
        steps = s.build_progress("f@x.com")["steps"]
        titles = [x["title"] for x in steps]
        self.assertEqual(titles, ["Building Sweet Crumbs", "Writing the header & navigation", "Writing the hero section",
                                  "Writing the features grid section", "Writing the pricing section",
                                  "Writing the contact form section", "Writing the footer"])
        self.assertTrue(all(x["kind"] in ("build", "write") for x in steps))
        self.assertIn("closing tags", steps[-1]["detail"])
        self.assertFalse(any("navigation" == t.lower().split()[-1] for t in titles[2:]))  # nav inside header is not a separate row

    def test_note_only_touches_running_step(self):
        s._bp_reset("n@x.com", "X"); s._bp_step("n@x.com", "A", "a"); s._bp_step("n@x.com", "B", "b")
        s._bp_note("n@x.com", "live detail")
        st = s.build_progress("n@x.com")["steps"]
        self.assertEqual(st[0]["detail"], "a"); self.assertEqual(st[1]["detail"], "live detail")
        s._bp_done("n@x.com", True, "/x/"); s._bp_note("n@x.com", "ignored")
        self.assertEqual(s.build_progress("n@x.com")["steps"][1]["detail"], "live detail")

    def test_kinds_and_cap(self):
        self.assertEqual(s._bp_kind("Collecting licensed photos"), "explore")
        self.assertEqual(s._bp_kind("Reading the brief"), "skill")
        self.assertEqual(s._bp_kind("Design review"), "review")
        self.assertEqual(s._bp_kind("Preview ready"), "ready")
        s._bp_reset("c@x.com", "X")
        for i in range(60):
            s._bp_step("c@x.com", "step %d" % i)
        self.assertLessEqual(len(s.build_progress("c@x.com")["steps"]), 40)

    def test_wiring(self):
        self.assertIn('c_raw, prov, finish = _llm_text_stream(system, user, max_tokens=16000, effort="medium", on_delta=_writer_progress(email))', SRV)
        self.assertIn('on_delta=_writer_progress(email, "Rewriting"))', SRV)
        self.assertIn('_bp_step(email, "Reading the current site"', SRV)
        self.assertIn('_bp_step(email, "Preview refreshed"', SRV)
        self.assertIn('_bp_step(email, "Building " + (name or "the site")', SRV)


class Client(unittest.TestCase):
    def test_feed_ui(self):
        for needle in ("function stepsCard(anchor, opts)", "Working for ", "'Building '+name", "const ICON={skill:", "function wantsEdit(q)",
                       "if(session && (_mode==='build' || wantsBuild(text)||wantsEdit(text))) _steps=stepsCard(b);", "const feed=stepsCard(null,{parent:prog});"):
            self.assertIn(needle, APP, needle)


if __name__ == "__main__":
    unittest.main()
