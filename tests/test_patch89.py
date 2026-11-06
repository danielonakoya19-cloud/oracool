"""patch89 — (1) Private chat REALLY never persists (browser cache leaked it: saveChat cached
private turns into localStorage and restoreChat resurfaced them after reload) + a loud ON state;
(2) one-tap install on the landing page with notifications+location permissions (camera later);
(3) voice call with the floating liquid-glass logo + video call with a moving matrix AI stage."""
import re
import unittest
from pathlib import Path

SRV = Path("server.py").read_text(encoding="utf-8")
APP = Path("index.html").read_text(encoding="utf-8")
LAND = Path("landing.html").read_text(encoding="utf-8")


class TestPatch89Marker(unittest.TestCase):
    def test_marker(self):
        self.assertEqual(SRV.count('"patch90-call-vision-fit"'), 2)


class TestPatch89PrivateNeverPersists(unittest.TestCase):
    def test_histpush_flags_private_turns(self):
        self.assertIn("function histPush(m){ try{ if(privMode) m._priv=true; }catch(e){} history.push(m); return m; }", APP)

    def test_savechat_strips_private(self):
        i = APP.index("function saveChat(){")
        self.assertIn("return !m._priv;", APP[i:i + 500])

    def test_restorechat_skips_private(self):
        i = APP.index("function restoreChat(){")
        self.assertIn("if(m._priv) return;", APP[i:i + 1200])

    def test_runassistantreply_private_aware(self):
        self.assertIn("session_id:(privMode?'':await ensureSession()),private:!!privMode", APP)

    def test_live_turns_routed_through_histpush(self):
        # send()/compare/tool follow-ups all flag via histPush; only restore re-pushes stay raw
        raw = re.findall(r"(?<!histPush\()(?<!function )history\.push\(\{role:'", APP)
        self.assertEqual(len(raw), 4, "only the 4 restore/openSession re-pushes stay raw")

    def test_private_toggle_is_loud(self):
        self.assertIn("Private ON", APP)
        self.assertIn("priv-on", APP)
        self.assertIn("privBanner", APP)
        i = APP.index("#privBanner")
        self.assertIn("body.priv-on #privBanner", APP)

    def test_server_gate_intact(self):
        self.assertIn('_conv_id = ""   # every conv_append guards on conv_id', SRV)


class TestPatch89InstallAndPerms(unittest.TestCase):
    def test_landing_installs_immediately(self):
        self.assertIn("landInstall", LAND)
        self.assertIn("dp.prompt()", LAND)
        self.assertIn("beforeinstallprompt", LAND)

    def test_permissions_on_install(self):
        self.assertIn("Notification.requestPermission()", LAND)
        self.assertIn("geolocation.getCurrentPosition", LAND)
        self.assertIn("askPerms", LAND)
        # app side requests too after the prompt
        self.assertIn("try{ askPerms(); }catch(e){}", APP)

    def test_camera_only_when_needed(self):
        # camera is requested by the video call, not at install
        self.assertIn("getUserMedia", APP)
        i = LAND.index("askPerms")
        self.assertNotIn("getUserMedia", LAND)


class TestPatch89VoiceVideoCalls(unittest.TestCase):
    def test_call_button_and_overlay(self):
        self.assertIn('id="callBtn"', APP)
        self.assertIn('id="callOv"', APP)
        self.assertIn('id="callEnd"', APP)
        self.assertIn("function startCall(", APP)
        self.assertIn("function endCall(", APP)

    def test_orb_talks(self):
        self.assertIn("function callSay(", APP)
        self.assertIn("speechSynthesis", APP)
        self.assertIn("talking", APP)
        self.assertIn("callTalk", APP)

    def test_voice_pipeline(self):
        self.assertIn("function callListen(", APP)
        self.assertIn("webkitSpeechRecognition", APP)
        self.assertIn("voice_mode:true", APP)
        self.assertIn("private:!!PrivMode" .replace("PrivMode","privMode"), APP)
        self.assertIn("callTyped", APP)  # typed fallback where recognition is unavailable

    def test_video_call_matrix_stage(self):
        self.assertIn('id="mxCv"', APP)
        self.assertIn("function startMatrix(", APP)
        self.assertIn("call-video", APP)
        self.assertIn('id="selfView"', APP)
        self.assertIn("facingMode", APP)

    def test_overlay_wiring_is_deferred_to_dom_ready(self):
        # patch89b: the call overlay markup sits after the main script — wiring must wait for
        # the DOM (the mute/speaker/video/end buttons were dead otherwise)
        self.assertIn("function wireCall(){", APP)
        self.assertIn("window._wireCallOv=wireCall", APP)
        self.assertIn("document.addEventListener('DOMContentLoaded',wireCall)", APP)
        self.assertIn("if(window._wireCallOv) window._wireCallOv()", APP)
        self.assertIn("if(wired) return;", APP)

    def test_call_controls(self):
        for cid in ('callMute', 'callSpk', 'callCam', 'callT'):
            self.assertIn('id="%s"' % cid, APP)
        self.assertIn("function fmtCallT(", APP)


if __name__ == "__main__":
    unittest.main()
