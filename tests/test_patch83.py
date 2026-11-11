# -*- coding: utf-8 -*-
"""patch83 — resilient brain: circuit breaker + sticky healthy provider.
Failed providers cool down (credits 30 min, rate-limit 90 s, empty 60 s) and
sink to the ladder tail; the last provider that worked answers the next call
first; nothing is ever removed from the ladder (no dead ends)."""
import os
import time
import unittest
import unittest.mock

import server


class TestPatch83BrainResilience(unittest.TestCase):
    def setUp(self):
        self._keys = dict(server.KEYS)
        self._fetch = server.http_fetch
        self._state = dict(server._BRAIN_STATE)
        server._BRAIN_STATE.clear()

    def tearDown(self):
        server.KEYS.clear(); server.KEYS.update(self._keys)
        server.http_fetch = self._fetch
        server._BRAIN_STATE.clear(); server._BRAIN_STATE.update(self._state)

    def test_marker_patch83(self):
        src = open("server.py", encoding="utf-8").read()
        assert src.count("patch105-wa-instant") == 2
        assert "def _brain_note_fail(" in src and "def _brain_health_report(" in src
        assert '"/api/admin/brain/status"' in src

    def test_cooldown_classification(self):
        server._brain_note_fail("x", "billing: credit balance is 0")
        assert server._BRAIN_STATE["x"]["bad_until"] - time.time() > 1700
        server._brain_note_fail("y", "rate limit exceeded", 429)
        assert 60 < server._BRAIN_STATE["y"]["bad_until"] - time.time() <= 95
        server._brain_note_fail("z", "empty response")
        assert 40 < server._BRAIN_STATE["z"]["bad_until"] - time.time() <= 65
        server._brain_note_fail("w", "boom")
        assert 25 < server._BRAIN_STATE["w"]["bad_until"] - time.time() <= 50

    def test_sticky_winner_leads(self):
        server.KEYS.update({"GROQ_API_KEY": "q1", "GEMINI_API_KEY": "g1",
                            "OPENAI_API_KEY": "", "AGNES_API_KEY": "", "ANTHROPIC_API_KEY": ""})
        server._brain_note_ok("gemini", 1.0)
        assert server._llm_attempts()[0][3] == "gemini"

    def test_cooling_provider_sinks_but_stays(self):
        server.KEYS.update({"GROQ_API_KEY": "q1", "GEMINI_API_KEY": "g1",
                            "OPENAI_API_KEY": "", "AGNES_API_KEY": "", "ANTHROPIC_API_KEY": ""})
        server._brain_note_fail("groq", "billing: no credits")
        names = [a[3] for a in server._llm_attempts()]
        assert names[-1] == "groq" and len(names) == 3  # groq-oss, gemini, groq

    def test_all_cooling_still_returns_ladder(self):
        server.KEYS.update({"GROQ_API_KEY": "q1", "GEMINI_API_KEY": "g1",
                            "OPENAI_API_KEY": "", "AGNES_API_KEY": "", "ANTHROPIC_API_KEY": ""})
        for p in ("groq-oss", "groq", "gemini", "openai", "agnes"):
            server._brain_note_fail(p, "rate limit")
        got = server._llm_attempts()
        assert len(got) == 3  # groq-oss + groq + gemini all present even while cooling

    def test_breaker_skips_failed_provider_on_next_call(self):
        server.KEYS.update({"GROQ_API_KEY": "q1", "GEMINI_API_KEY": "g1",
                            "OPENAI_API_KEY": "", "AGNES_API_KEY": "", "ANTHROPIC_API_KEY": ""})
        calls = []
        def fake_fetch(url, **kw):
            calls.append(url)
            if "groq" in url:
                raise Exception("billing: credit balance is 0")
            import json as _j
            return (200, _j.dumps({"choices": [{"message": {"content": "HI"}}]}).encode(), {})
        server.http_fetch = fake_fetch
        txt, err = server.llm_complete([{"role": "user", "content": "x"}])
        assert err is None and txt == "HI" and len(calls) == 3  # groq-oss + groq fail, gemini answers
        calls.clear()
        txt, err = server.llm_complete([{"role": "user", "content": "x"}])
        assert err is None and len(calls) == 1 and "groq" not in calls[0]

    def test_agnes_timeout_capped(self):
        server.KEYS.update({"GROQ_API_KEY": "q1", "GEMINI_API_KEY": "g1", "AGNES_API_KEY": "a1",
                            "OPENAI_API_KEY": "", "ANTHROPIC_API_KEY": ""})
        seen = {}
        import json as _j
        def fake_fetch(url, **kw):
            seen[url] = kw.get("timeout")
            if "groq" in url or "generativelanguage" in url:
                raise Exception("down")
            return (200, _j.dumps({"choices": [{"message": {"content": "A"}}]}).encode(), {})
        server.http_fetch = fake_fetch
        txt, err = server.llm_complete([{"role": "user", "content": "x"}])
        assert err is None and txt == "A"
        assert max(v for v in seen.values()) <= 45

    def test_brain_status_shape(self):
        server.KEYS.update({"GROQ_API_KEY": "q1"})
        server._brain_note_fail("groq", "rate limit", 429)
        st = server._brain_health_report()
        by = {x["provider"]: x for x in st}
        assert by["groq"]["state"] == "cooling" and by["groq"]["cooldown_s"] > 0
        assert "last_err" in by["groq-oss"]

    def test_free_launch_guard_unchanged(self):
        with unittest.mock.patch.dict(os.environ, {"GROWTH_FREE_MODE": "1"}):
            assert server.free_mode() is True
            assert server.check_tier("x@y.z") == "ultra"
            assert server.coins_gate("image") is None


if __name__ == "__main__":
    unittest.main()
