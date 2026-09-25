"""patch31 — private community: no public rooms, owner-only visibility, membership by OraCool number, sticker packs."""
import os, re, sys, unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
COM = open(os.path.join(ROOT, "community.py"), encoding="utf-8").read()


def _post(action, body):
    import server as s
    with mock.patch.object(s, "_COMMUNITY_SERVICE", None), \
         mock.patch.object(s, "audit_log", lambda *a, **k: None), \
         mock.patch.object(s, "emit_event", lambda *a, **k: None), \
         mock.patch.object(s, "notify_admins", lambda *a, **k: None):
        return s.community_route(action, body, self_host="oracoolai.onrender.com")


class PrivacyTests(unittest.TestCase):
    """Full-stack privacy engine: routes -> Service -> mini-PostgREST (borrowed from Patch 15)."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("t15", root / "tests" / "test_patch15.py")
        cls.t15 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.t15)

    def setUp(self):
        import server as s
        self.s = s
        self.fr = self.t15.FakeRest()
        self.patches = [
            mock.patch.object(s, "_COMMUNITY_SERVICE", None),
            mock.patch.object(s, "community_rest", side_effect=self.fr.rest),
            mock.patch.object(s, "audit_log"),
            mock.patch.object(s, "emit_event"),
            mock.patch.object(s, "notify_admins"),
            mock.patch.dict(getattr(s, "KEYS", {}), {}, clear=True),
        ]
        for pt in self.patches:
            pt.start()
        svc = s.community_service()
        svc.async_reviews = False
        self.svc = svc
        for em, un, num in (("ada@t.com", "ada", None), ("ben@t.com", "ben", None), ("cy@t.com", "cy", None)):
            prof = svc.ensure_profile(em, username=un, display_name=un.title())
        # give them explicit numbers for add-by-number tests
        for r in self.fr.tables["comm_profiles"]["rows"]:
            r["oracool_number"] = {"ada@t.com": "0701111111", "ben@t.com": "0702222222", "cy@t.com": "0703333333"}[r["email"]]

    def tearDown(self):
        for pt in reversed(self.patches):
            pt.stop()

    def post(self, action, body):
        return self.s.community_route(action, dict(body, email=body["email"]), self_host="oracoolai.onrender.com")

    def rooms_of(self, email, kind=("group", "channel")):
        # FakeRest grants every new profile membership in the seeded legacy rooms
        # (the migration patch31 simulates) — assert on created groups/channels only.
        return [x for x in self.post("rooms", {"email": email}).get("rooms", []) if x.get("kind") in kind]

    def test_created_room_never_public(self):
        r = self.post("rooms/create", {"email": "ben@t.com", "name": "ben club", "kind": "channel", "public": True})
        self.assertTrue(r.get("ok"), r)
        row = [x for x in self.fr.tables["comm_rooms"]["rows"] if x["slug"] == r["room"]["id"]][0]
        self.assertIs(row["is_public"], False)

    def test_no_public_browsing_only_mine(self):
        # seeded lounge/markets/help are membership-grandfathered; NO other room is listed
        self.assertEqual(self.rooms_of("ben@t.com"), [])
        for x in self.post("rooms", {"email": "ben@t.com"}).get("rooms", []):
            self.assertIn(x["id"], ("lounge", "markets", "help"))
        r = self.post("rooms/create", {"email": "ada@t.com", "name": "ada fam", "kind": "group"})
        slug = r["room"]["id"]
        mine = self.rooms_of("ada@t.com")
        self.assertEqual([x["id"] for x in mine], [slug])
        self.assertTrue(mine[0]["yours"])
        self.assertEqual(self.rooms_of("ben@t.com"), [])
        return slug

    def test_join_is_dead_numbers_only(self):
        slug = self.test_no_public_browsing_only_mine()
        r = self.post("rooms/join", {"email": "ben@t.com", "slug": slug})
        self.assertTrue(r.get("error"))
        self.assertIn("number", r.get("error", "").lower())

    def test_add_member_by_number_gives_access(self):
        slug = self.test_no_public_browsing_only_mine()
        r = self.post("rooms/add-member", {"email": "ada@t.com", "room": slug, "who": "0702222222"})
        self.assertTrue(r.get("ok"), r)
        rb = self.rooms_of("ben@t.com")
        self.assertEqual([x["id"] for x in rb], [slug])
        self.assertFalse(rb[0]["yours"])          # added, not owned
        self.assertEqual(rb[0]["kind"], "group")
        self.assertFalse(self.post("room/messages", {"email": "ben@t.com", "room": slug}).get("error"))
        send = self.post("room/send", {"email": "ben@t.com", "room": slug, "body": "added and posting \u270c\ufe0f"})
        self.assertTrue(send.get("ok"), send)

    def test_third_party_still_blocked(self):
        slug = self.test_no_public_browsing_only_mine()
        self.post("rooms/add-member", {"email": "ada@t.com", "room": slug, "who": "0702222222"})
        self.assertTrue(self.post("room/messages", {"email": "cy@t.com", "room": slug}).get("error"))
        self.assertTrue(self.post("room/send", {"email": "cy@t.com", "room": slug, "body": "let me in"}).get("error"))
        self.assertEqual(self.rooms_of("cy@t.com"), [])

    def test_membership_is_owner_managed(self):
        slug = self.test_no_public_browsing_only_mine()
        self.post("rooms/add-member", {"email": "ada@t.com", "room": slug, "who": "0702222222"})
        self.assertTrue(self.post("rooms/remove-member", {"email": "ben@t.com", "room": slug, "who": "ada"}).get("error"))
        self.assertFalse(self.post("rooms/remove-member", {"email": "ben@t.com", "room": slug, "who": "ada"}).get("ok"))
        self.assertTrue(self.post("rooms/add-member", {"email": "ben@t.com", "room": slug, "who": "0703333333"}).get("error"))
        self.assertTrue(self.post("rooms/leave", {"email": "ada@t.com", "room": slug}).get("error"))  # owner deletes instead
        self.assertTrue(self.post("rooms/leave", {"email": "ben@t.com", "room": slug}).get("ok"))
        self.assertEqual(self.rooms_of("ben@t.com"), [])

    def test_numbers_only_visible_to_owner_and_self(self):
        slug = self.test_no_public_browsing_only_mine()
        self.post("rooms/add-member", {"email": "ada@t.com", "room": slug, "who": "0702222222"})
        memb = self.post("rooms/members", {"email": "ben@t.com", "room": slug})
        self.assertFalse(memb.get("can_manage"))
        for x in memb["members"]:
            if not x.get("me"):
                self.assertIsNone(x.get("number"))   # others' numbers hidden from plain members
        own = self.post("rooms/members", {"email": "ada@t.com", "room": slug})
        self.assertTrue(own["can_manage"])
        self.assertIn("0702222222", str(own["members"]))

    def test_ai_digest_lists_private_rooms_too(self):
        # the moderator digest must not leak non-member groups; rooms() call in chat_brief uses membership
        src_seg = COM[COM.index("def chat_brief"):COM.index("def chat_brief") + 2000]
        self.assertIn("mine_ids", src_seg)

class StickerTests(unittest.TestCase):
    def test_save_list_delete_roundtrip(self):
        import server as s
        pack = {}
        with mock.patch.object(s, "supabase_kv_get", lambda k, default=None: pack.get(k, default)), \
             mock.patch.object(s, "supabase_kv_put", lambda k, merged: pack.__setitem__(k, merged) or True), \
             mock.patch.object(s, "upload_community_media", lambda email, du: {"ok": True, "media_url": "https://cdn/x.png"}):
            r = s.stickers_save("ada@t.com", "data:image/png;base64,AAAA", "SHAKE")
            self.assertTrue(r.get("ok"))
            self.assertEqual(r["url"], "https://cdn/x.png")
            lst = s.stickers_list("ada@t.com")
            self.assertEqual(len(lst["stickers"]), 1)
            self.assertEqual(s.stickers_delete("ada@t.com", "https://cdn/x.png")["stickers"], [])

    def test_cap_30(self):
        import server as s
        pack = {}
        with mock.patch.object(s, "supabase_kv_get", lambda k, default=None: pack.get(k, default)), \
             mock.patch.object(s, "supabase_kv_put", lambda k, merged: pack.__setitem__(k, merged) or True), \
             mock.patch.object(s, "upload_community_media", lambda email, du: {"ok": True, "media_url": "https://cdn/" + du[-4:]}):
            for i in range(35):
                s.stickers_save("cap@t.com", "data:image/png;base64,X%02d" % i, "t")
            self.assertLessEqual(len(s.stickers_list("cap@t.com")["stickers"]), 30)


class WiringTests(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch46-durable"'), 2)

    def test_service_privacy_gates(self):
        for fn in ("def _room_member_ok", "def room_add_member", "def room_remove_member",
                   "def room_leave", "def room_members", "def _resolve_number_or_handle"):
            self.assertIn(fn, COM)
        # the public read-bypass is gone from the message path entirely
        self.assertNotIn("allow_public_read", COM[COM.index("def room_messages"):COM.index("def room_send")])
        # create stores is_public False unconditionally
        seg = COM[COM.index("def create_room"):COM.index("def create_room") + 3500]
        self.assertIn('"is_public": False', seg)
        # router never forces public again
        self.assertNotIn('True if pub is None', SRV)

    def test_router(self):
        for a in ('rooms/members', 'rooms/add-member', 'rooms/remove-member', 'rooms/leave', 'stickers/save', 'stickers/list', 'stickers/delete'):
            self.assertIn('action == "%s"' % a, SRV)
        self.assertNotIn('public:$(\'#nrPublic\')', APP)
        self.assertNotIn('id="nrPublic"', APP)

    def test_client_bits(self):
        for k in ("function membersModal", "function stickerMaker", "async function sendSticker",
                  'data-a="sticker"', "dmNumIn", "roomMembers", "commNewRoom2"):
            self.assertIn(k, APP)
        m = re.search(r"const WA_EMOJIS=\[([^\]]*)\]", APP)
        self.assertGreaterEqual(len([e for e in m.group(1).split(",") if e.strip("'")]), 150)
        self.assertNotIn("{id:'lounge',name:'Lounge'}", APP)  # fake lounge fallback is gone

    def test_lounge_default_gone_from_send_paths(self):
        seg = SRV[SRV.index("def community_route"):SRV.index("def community_route") + 9000]
        self.assertNotIn('or "lounge"', seg)
        self.assertIn('action == "rooms/leave"', SRV)


if __name__ == "__main__":
    unittest.main()
