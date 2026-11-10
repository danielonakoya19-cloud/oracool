# -*- coding: utf-8 -*-
"""patch82 — brain failover (no more dead-provider 'Brain issue') + compact UI.
llm_complete now fails over across every configured provider (anthropic ->
groq -> gemini -> openai -> agnes); Gemini joined the ladder on its
OpenAI-compatible endpoint; the interface density matches a normal console."""
import os
import unittest
import unittest.mock

import server


class TestPatch82BrainFailover(unittest.TestCase):
    def setUp(self):
        self._keys = dict(server.KEYS)
        self._fetch = server.http_fetch

    def tearDown(self):
        server.KEYS.clear(); server.KEYS.update(self._keys)
        server.http_fetch = self._fetch

    def test_marker_patch82(self):
        src = open("server.py", encoding="utf-8").read()
        assert src.count("patch101-wa-qr") == 2
        assert "def _llm_attempts(" in src and "gemini-3.1-flash-lite" in src

    def test_ladder_includes_gemini(self):
        server.KEYS.update({"GEMINI_API_KEY": "g-test", "GROQ_API_KEY": "q-test"})
        names = [a[3] for a in server._llm_attempts()]
        assert "gemini" in names and "groq" in names
        gm = [a for a in server._llm_attempts() if a[3] == "gemini"][0]
        assert "generativelanguage.googleapis.com" in gm[1]

    def test_llm_complete_fails_over_to_next_provider(self):
        server.KEYS.update({"GROQ_API_KEY": "q1", "GEMINI_API_KEY": "g1",
                            "OPENAI_API_KEY": "", "AGNES_API_KEY": "", "ANTHROPIC_API_KEY": ""})
        calls = []
        def fake_fetch(url, **kw):
            calls.append(url)
            if len(calls) == 1:
                raise Exception("billing: credit balance is 0")
            return (200, __import__("json").dumps(
                {"choices": [{"message": {"content": "BRAIN OK"}}]}).encode(), {})
        server.http_fetch = fake_fetch
        txt, err = server.llm_complete([{"role": "user", "content": "hi"}])
        assert err is None and txt == "BRAIN OK" and len(calls) == 2

    def test_all_providers_down_reports_credits_sentence(self):
        server.KEYS.update({"GROQ_API_KEY": "q1", "GEMINI_API_KEY": "g1"})
        import urllib.error, io
        def fake_fetch(url, **kw):
            raise urllib.error.HTTPError(url, 402, "x", {}, io.BytesIO(b'{"error":"insufficient_quota"}'))
        server.http_fetch = fake_fetch
        txt, err = server.llm_complete([{"role": "user", "content": "hi"}])
        assert txt is None and "no credits left" in err

    def test_empty_response_falls_through(self):
        server.KEYS.update({"GROQ_API_KEY": "q1", "GEMINI_API_KEY": "g1"})
        calls = []
        def fake_fetch(url, **kw):
            calls.append(url)
            if "groq" in url:
                return (200, b'{"choices":[{"message":{"content":""}}]}', {})
            return (200, __import__("json").dumps(
                {"choices": [{"message": {"content": "from gemini"}}]}).encode(), {})
        server.http_fetch = fake_fetch
        txt, err = server.llm_complete([{"role": "user", "content": "hi"}])
        assert err is None and txt == "from gemini"

    def test_free_launch_guard_unchanged(self):
        with unittest.mock.patch.dict(os.environ, {"GROWTH_FREE_MODE": "1"}):
            assert server.free_mode() is True
            assert server.check_tier("x@y.z") == "ultra"
            assert server.coins_gate("image") is None


class TestPatch82CompactUI(unittest.TestCase):
    def test_sidebar_and_drawer_compact(self):
        css = open("console-layout.css", encoding="utf-8").read()
        assert ".sidebar{width:288px;" in css
        assert "width:min(300px,88vw)" in css
        app = open("index.html", encoding="utf-8").read()
        assert "width:min(300px,84vw);" in app

    def test_chat_density(self):
        css = open("console-layout.css", encoding="utf-8").read()
        assert ".chat{padding:18px 18px;gap:14px}" in css
        assert ".bubble{max-width:88%;padding:12px 15px;line-height:1.65;" in css

    def test_typography_compact(self):
        app = open("index.html", encoding="utf-8").read()
        assert ".sess .st{font-size:14.5px;" in app
        assert ".bubble{max-width:88%; padding:11px 15px;" in app
        assert ".brand .title{font-size:15px; letter-spacing:2px;" in app
        css = open("console-layout.css", encoding="utf-8").read()
        assert ".gpt-input .chatinput{flex:1;min-width:0;border:none;background:transparent;box-shadow:none;padding:7px 4px;font-size:13.5px;" in css

    def test_composer_compact(self):
        css = open("console-layout.css", encoding="utf-8").read()
        assert ".gpt-send{width:34px;height:34px;" in css
        assert ".gpt-input .iconbtn{width:34px;height:34px;" in css


if __name__ == "__main__":
    unittest.main()
