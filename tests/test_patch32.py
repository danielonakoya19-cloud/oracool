"""patch32 — human barge-in: talking or typing while the AI replies interrupts it and the
new message is answered; voice mode listens even while speaking (echo-guarded); no button needed."""
import os, re, subprocess, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
VOICE = open(os.path.join(ROOT, "voice-reminders.js"), encoding="utf-8").read()
SRV = open(os.path.join(ROOT, "server.py"), encoding="utf-8").read()


class BargeInTests(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch41-studio"'), 2)

    def test_send_never_swallows_new_messages(self):
        self.assertNotIn("if(busy) return;", APP)
        self.assertNotIn("if(busy) return; ", APP)
        self.assertIn("const myTurn=++CHAT_TURN;", APP)
        self.assertIn("window.OraVoice.interrupt()", APP)
        self.assertIn("window.__chatAbort()", APP)          # abort the in-flight reply
        self.assertIn("speechSynthesis.cancel(); stopWave(); resumeWake();", APP)  # silence TTS too

    def test_stale_turn_cannot_corrupt_state(self):
        # error box + cleanup belong to the current turn only
        self.assertIn("}else if(myTurn===CHAT_TURN){", APP)
        self.assertIn("if(myTurn===CHAT_TURN){ window.__chatAbort=null;", APP)
        # the send button stays enabled so a click can interrupt
        self.assertIn("_sb.disabled=false;_sb.title='Send — interrupts the current reply'", APP)

    def test_voice_listens_while_speaking(self):
        self.assertNotIn("if(!enabled||busy||activeSpeech||streaming||document.hidden", VOICE)
        self.assertIn("if(capture)return;", VOICE)                      # no recognizer pile-ups
        self.assertIn("cancelSpeech();interruptTurn();accept(f);", VOICE)  # talk over it -> answered
        self.assertIn("rms>0.045){cancelSpeech();interruptTurn();}", VOICE)  # VAD fallback barge-in
        self.assertNotIn("pauseCapture();activeSpeech=true", VOICE)     # mic no longer muted during speech

    def test_echo_guard(self):
        self.assertIn("function isEcho(t)", VOICE)
        self.assertIn("lastSpoken=text", VOICE)
        self.assertIn("if(r.text&&!isEcho(r.text))await accept(r.text);", VOICE)
        # isEcho body sanity: empty transcript is treated as echo (can't launch a reply on noise)
        seg = VOICE[VOICE.index("function isEcho"):VOICE.index("function isEcho") + 320]
        self.assertIn("if(!n)return true", seg)

    def test_interrupt_api_and_button(self):
        self.assertIn("interrupt(){cancelSpeech();interruptTurn();resume();}", VOICE)
        self.assertIn("interruptTurn();cancelSpeech();state('Reply interrupted')", VOICE)
        self.assertIn("just talk over me", VOICE)
        self.assertIn("Talk over the AI any time", VOICE)

    def test_cache_bust_and_js_valid(self):
        self.assertIn('/voice-reminders.js?v=40', APP)
        for label, script in (("voice", VOICE),):
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(script); p = f.name
            self.assertEqual(subprocess.run(["node", "--check", p], capture_output=True).returncode, 0, label)
            os.unlink(p)
        for m in re.finditer(r"<script([^>]*)>(.*?)</script>", APP, re.S):
            if "src=" in m.group(1):
                continue
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
                f.write(m.group(2)); p = f.name
            self.assertEqual(subprocess.run(["node", "--check", p], capture_output=True).returncode, 0,
                             "inline script @line %d broken" % APP[:m.start()].count("\n"))
            os.unlink(p)


if __name__ == "__main__":
    unittest.main()
