"""patch92 — the community tool can ACT: post to the member's own channels/groups or DMs
(their words, their account, the community's normal rules) and check their inbox — and the
brain must never say posting is impossible again."""
import re
import unittest
from pathlib import Path

SRV = Path("server.py").read_text(encoding="utf-8")
APP = Path("index.html").read_text(encoding="utf-8")


class TestPatch92Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch98-qr-certs"'), 2)


class TestPatch92CommunityCanPost(unittest.TestCase):
    def test_community_action_exists(self):
        self.assertIn("def community_action(email, text):", SRV)
        i = SRV.index("def community_action(")
        seg = SRV[i:i + 6000]
        self.assertIn("svc.room_send(", seg)
        self.assertIn("svc.dm_send(", seg)
        self.assertIn("community post :: channel-or-user :: message", SRV)

    def test_command_and_natural_forms(self):
        i = SRV.index("def community_action(")
        seg = SRV[i:i + 6000]
        self.assertIn("::", seg)
        self.assertIn("saying|say|that says|message|caption|text|body", seg)

    def test_all_channels_fanout(self):
        i = SRV.index("def community_action(")
        seg = SRV[i:i + 6000]
        self.assertIn("all (?:the |my )?(?:community )?(?:channels|groups|rooms)", seg)

    def test_same_rules_as_a_real_tap(self):
        # the send goes through room_send/dm_send which enforce channel-admin, locks, bans
        i = SRV.index("def community_action(")
        seg = SRV[i:i + 6000]
        self.assertNotIn("is_admin(email) or True", seg)

    def test_inbox_reads_dm_threads(self):
        i = SRV.index("community inbox")
        self.assertIn("dm_threads(email)", SRV[i - 3000:i + 200])

    def test_tool_branch_calls_action(self):
        self.assertIn("community_action(email, t)", SRV)


class TestPatch92PromptFixed(unittest.TestCase):
    def test_server_prompt_allows_posting(self):
        self.assertNotIn("you cannot post on their behalf", SRV)
        self.assertIn("you CAN post or send messages for them", SRV)
        self.assertIn("NEVER say you cannot post", SRV)

    def test_client_prompt_allows_posting(self):
        self.assertIn("Never say posting is impossible", APP)

    def test_honesty_boundaries_kept(self):
        self.assertIn("never reveal other members", SRV)
        self.assertIn("locked groups refuse", SRV)


if __name__ == "__main__":
    unittest.main()
