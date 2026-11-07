"""patch90 — (1) the call actually answers: TTS unlocked from the gesture (iOS 15 silently
blocks out-of-gesture speech) + recognition commits whatever was heard so the AI always
replies; (2) video calls SEE the user + surroundings (one camera frame described per turn,
text only — the raw frame is never stored); (3) iPhone-7-class screens: compact composer
and call controls that fit without looking cramped."""
import unittest
from pathlib import Path

SRV = Path("server.py").read_text(encoding="utf-8")
APP = Path("index.html").read_text(encoding="utf-8")


class TestPatch90Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch93-connectors"'), 2)


class TestPatch90CallAnswers(unittest.TestCase):
    def test_tts_unlocked_from_gesture(self):
        self.assertIn("function callUnlockTTS()", APP)
        self.assertIn("speechSynthesis.resume()", APP)
        self.assertIn("callUnlockTTS();", APP)

    def test_speech_stall_never_stops_the_loop(self):
        i = APP.index("function callSay(")
        seg = APP[i:i + 2200]
        self.assertIn("if(!settled && !(window.speechSynthesis", seg)
        self.assertIn("let settled=false;", seg)

    def test_recognition_commits_what_was_heard(self):
        i = APP.index("function callListen(")
        seg = APP[i:i + 3200]
        self.assertIn("some engines never flag isFinal", seg)
        self.assertIn("callTurn(said)", seg)

    def test_turn_errors_are_visible(self):
        self.assertIn("Connection hiccup (", APP)
        self.assertIn("say that again?", APP)

    def test_systemprompt_guarded(self):
        self.assertIn("You are OraCool, a helpful voice assistant", APP)


class TestPatch90VideoVision(unittest.TestCase):
    def test_camera_frame_described_each_turn(self):
        self.assertIn("/api/files/analyze", APP)
        i = APP.index("async function callTurn(")
        seg = APP[i:i + 3000]
        self.assertIn("callCamStream", seg)
        self.assertIn("toDataURL('image/jpeg'", seg)
        self.assertIn("Live camera frame", seg)

    def test_raw_frame_never_stored(self):
        # the frame is turned into text and only the clean spoken line is history-pushed
        i = APP.index("async function callTurn(")
        seg = APP[i:i + 3000]
        self.assertIn("histPush({role:'user',content:text});", seg)
        self.assertNotIn("histPush({role:'user',content:tail", seg)


class TestPatch90SmallScreens(unittest.TestCase):
    def test_composer_compacts(self):
        self.assertIn(".gpt-hint{display:none !important;}", APP)
        self.assertIn(".gpt-actions{flex-wrap:nowrap !important", APP)
        self.assertIn(".modebtn{height:32px !important", APP)

    def test_call_controls_fit(self):
        self.assertIn(".callbtn{min-width:52px !important", APP)
        self.assertIn("env(safe-area-inset-bottom)", APP)
        self.assertIn("@media (max-width: 400px)", APP)

    def test_ios_text_sizing(self):
        self.assertIn(".chatinput{font-size:16px !important;}", APP)


if __name__ == "__main__":
    unittest.main()
