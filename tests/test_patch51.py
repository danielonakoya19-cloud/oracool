"""patch51: Enterprise OSINT/security toolbox — safe nmap/Wireshark/John/Hydra-style capabilities wired into the AI."""
import base64, hashlib, os, sys, struct, unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
import server as s  # noqa: E402

SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


def sample_pcap_b64():
    eth = b"\xaa" * 6 + b"\xbb" * 6 + b"\x08\x00"
    ip = bytearray(20)
    ip[0] = 0x45
    ip[2:4] = (40).to_bytes(2, "big")
    ip[8] = 64
    ip[9] = 6
    ip[12:16] = bytes([1, 2, 3, 4])
    ip[16:20] = bytes([5, 6, 7, 8])
    tcp = (1234).to_bytes(2, "big") + (80).to_bytes(2, "big") + b"\x00" * 16
    pkt = eth + bytes(ip) + tcp
    gh = b"\xd4\xc3\xb2\xa1" + struct.pack("<HHIIII", 2, 4, 0, 0, 65535, 1)
    rec = struct.pack("<IIII", 1, 0, len(pkt), len(pkt)) + pkt
    return base64.b64encode(gh + rec).decode()


class Marker(unittest.TestCase):
    def test_marker_and_routes(self):
        self.assertEqual(SRV.count('"patch51-security-osint"'), 2)
        for needle in ("/api/security/nmap", "/api/security/pcap", "/api/security/hash", "/api/security/login",
                       "secscan", "pcap", "hashaudit", "login_audit",
                       "Nmap-style port inventory", "Wireshark/pcap triage", "John weak-hash audit", "Hydra login-defense audit"):
            self.assertIn(needle, SRV, needle)
        self.assertNotIn('"ip", "domain", "email", "username", "phone", "weather"', SRV)


class SecurityTools(unittest.TestCase):
    def test_status_and_gating_shape(self):
        st = s.security_tool_status()
        self.assertEqual(st["tier"], "enterprise")
        self.assertIn("secscan", st["tools"])
        self.assertIn("hydra", st["system_binaries"])
        self.assertIn("Authorization required", s.security_port_inventory("example.com", authorized=False)["error"])
        self.assertIn("blocked", s.security_port_inventory("127.0.0.1", authorized=True)["error"].lower())

    def test_safe_port_inventory(self):
        class Ctx:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def fake_conn(addr, timeout=0):
            ip, port = addr
            if ip == "93.184.216.34" and port == 80:
                return Ctx()
            raise OSError("closed")
        with mock.patch.object(s.socket, "create_connection", fake_conn):
            r = s.security_port_inventory("93.184.216.34", ports="80,443", authorized=True)
        self.assertEqual(r["tool"], "secscan")
        self.assertEqual(r["ports_checked"], 2)
        self.assertEqual(r["open"][0]["port"], 80)
        self.assertIn("no NSE", r["limits"])

    def test_hash_audit_weak_list_only(self):
        md5 = hashlib.md5(b"password").hexdigest()
        r = s.security_hash_audit(md5, authorized=True)
        self.assertEqual(r["tool"], "hashaudit")
        self.assertEqual(r["weak_matches"], 1)
        self.assertEqual(r["results"][0]["matched_password"], "password")
        self.assertIn("no custom wordlists", r["engine"].lower())

    def test_pcap_triage_and_file_analyzer(self):
        b64 = sample_pcap_b64()
        r = s.security_pcap_analyze(b64, "one.pcap")
        self.assertEqual(r["tool"], "pcap")
        self.assertEqual(r["packets"], 1)
        self.assertEqual(r["protocols"][0]["value"], "tcp")
        self.assertEqual(r["top_destination_ports"][0]["value"], "tcp/80")
        a = s.analyze_file("one.pcap", "application/vnd.tcpdump.pcap", b64)
        self.assertEqual(a["type"], "pcap")
        self.assertIn("Wireshark-style", a["note"])

    def test_auto_tools_enterprise_and_locked(self):
        with mock.patch.object(s, "security_port_inventory", lambda *a, **k: {"tool": "secscan", "open": []}):
            out = s.auto_tools("nmap scan example.com ports 80,443 — I am authorized", tier="enterprise", email="e@example.com")
        self.assertTrue(any(x["tool"] == "secscan" for x in out), out)
        out2 = s.auto_tools("run john the ripper on this hash", tier="pro", email="p@example.com")
        self.assertTrue(any(x["tool"] == "locked" and "Enterprise security toolbox" in x["label"] for x in out2), out2)


if __name__ == "__main__":
    unittest.main()
