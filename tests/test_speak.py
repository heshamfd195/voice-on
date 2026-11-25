"""Tests for speak.py: what a reply sounds like, and the on/off state.

Run: python3 -m unittest discover -s tests
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

HOME = tempfile.mkdtemp(prefix="voice-on-test-")
os.environ["VOICE_ON_HOME"] = HOME
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))
import speak  # noqa: E402


class CleanForSpeech(unittest.TestCase):
    def said(self, text):
        return speak.clean_for_speech(text)

    def test_markdown_symbols_are_dropped(self):
        self.assertEqual(self.said("## Done\n\n**All** tests `pass`"), "Done.\n\nAll tests pass.")

    def test_links_are_read_as_their_text(self):
        self.assertEqual(self.said("See [the docs](https://example.com/x)."), "See the docs.")
        self.assertEqual(self.said("Open https://example.com/a/b now"), "Open a link now.")

    def test_paths_are_read_as_the_file_name(self):
        self.assertEqual(self.said("Edit src/utils/foo.ts:42 first"), "Edit foo.ts line 42 first.")

    def test_short_slashed_words_stay(self):
        self.assertEqual(self.said("Run CI/CD and/or tests"), "Run CI/CD and/or tests.")

    def test_long_hyphenated_name_is_fast(self):
        name = "-".join(["word"] * 400)
        self.assertEqual(self.said(name), name + ".")

    def test_table_rows_are_named_by_column(self):
        table = "| Engine | Speed | Cost |\n|---|---|---|\n| Kokoro | fast | ✅ |\n| say | — | free |"
        self.assertEqual(self.said(table),
                         "A table with 2 rows.\nKokoro: Speed fast, Cost yes.\nsay: Cost free.")

    def test_code_is_named_once_per_kind(self):
        text = "Here:\n```python\nprint(1)\n```\nand\n```py\nprint(2)\n```\n```bash\nls\n```"
        self.assertEqual(self.said(text),
                         "Here:\nThere's Python code on screen.\nand.\nThere's a command on screen.")

    def test_drawing_is_read_as_its_labels(self):
        drawing = "```\n┌────────┐    ┌─────┐\n│ Client │ ──▶│ API │\n└────────┘    └─────┘\n```"
        self.assertEqual(self.said(drawing), "A drawing.\nClient to API.")

    def test_mermaid_flowchart_is_described(self):
        chart = "```mermaid\nflowchart LR\n  A[User] -->|types| B[Claude]\n  B --> C[Voice]\n```"
        self.assertEqual(self.said(chart),
                         "A flowchart.\nUser to Claude, types.\nClaude to Voice.")


class Segments(unittest.TestCase):
    REPLY = ("## Plan\n\nFirst **step**: edit `src/a/b.ts`.\n\n- one item\n1. numbered\n\n"
             "| A | B |\n|---|---|\n| x | \u2705 |\n\n```python\nprint(1)\n```\n> a quote")

    def test_blocks_keep_their_kind_and_source(self):
        segs = [s for s in speak.segments(self.REPLY) if s["kind"] != "break"]
        self.assertEqual([s["kind"] for s in segs], ["heading", "text", "item", "item", "table", "code", "quote"])
        self.assertEqual(segs[0]["level"], 2)
        self.assertTrue(segs[2]["bullet"])
        self.assertEqual(segs[4]["source"], "| A | B |\n|---|---|\n| x | \u2705 |")
        self.assertEqual((segs[5]["lang"], segs[5]["source"]), ("python", "print(1)"))

    def test_word_offsets_point_into_the_spoken_text(self):
        segs = speak.segments(self.REPLY)
        words = speak.spoken_text(segs).split()
        for seg in segs:
            self.assertEqual(words[seg["w0"]:seg["w0"] + seg["words"]], seg["spoken"].split())

    def test_chunks_know_where_their_words_start(self):
        text = speak.clean_for_speech(self.REPLY + "\n\n.\n\n" + "A long sentence goes here. " * 20)
        words = text.split()
        chunks = speak.make_chunks(text)
        self.assertLessEqual(len(chunks[0]["text"]), 80)  # the first is short, so speech starts fast
        for c in chunks:
            self.assertEqual(words[c["w0"]:c["w0"] + len(c["text"].split())], c["text"].split())


class Reading(unittest.TestCase):
    def test_now_json_follows_the_reading(self):
        def fake_synth(engine, cfg, chunks, folder):
            for i, _ in enumerate(chunks):
                path = os.path.join(folder, "%d.wav" % i)
                import wave
                with wave.open(path, "wb") as w:
                    w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000)
                    w.writeframes(b"\0\0" * 4000)
                yield path

        with mock.patch.object(speak, "system_synth", fake_synth), \
                mock.patch.object(speak, "pick_engine", lambda cfg: "say"), \
                mock.patch.object(speak, "system_engine", lambda: "say"), \
                mock.patch.object(speak, "audio_player", lambda: ["true"]):
            speak.read_aloud("# Hi\n\nOne. Two.", "chat-1")
            with open(speak.NOW_FILE) as f:
                now = json.load(f)
            self.assertEqual((now["session"], now["state"], now["base"]), ("chat-1", "done", 0))
            self.assertEqual(now["markdown"], "# Hi\n\nOne. Two.")
            self.assertTrue(all(c["t0"] and c["dur"] == 0.5 for c in now["chunks"]))

            speak.read_aloud("# Hi\n\nOne. Two.", "chat-1", part=2)
            with open(speak.NOW_FILE) as f:
                now = json.load(f)
            self.assertEqual((now["part"], now["base"], now["chunks"][0]["text"]), (2, 1, "One. Two."))

            speak.read_aloud("# Hi\n\nOne. Two.\n\nThree.", "chat-1", start=2)  # from block 2 on
            with open(speak.NOW_FILE) as f:
                now = json.load(f)
            self.assertEqual((now["base"], [c["text"] for c in now["chunks"]]), (1, ["One. Two. Three."]))


class SpeedAndGerman(unittest.TestCase):
    def test_speed_is_saved_with_the_other_settings_kept(self):
        with open(speak.CONFIG_FILE, "w") as f:
            json.dump({"german_voice": "de_DE-kerstin-low", "kokoro": {"speed": 1.1}}, f)
        self.assertEqual(speak.set_speed("1.5"), 1.5)
        self.assertEqual(speak.set_speed(9), 2.5)  # kept in range
        cfg = speak.load_config()
        self.assertEqual((cfg["speed"], cfg["german_voice"], cfg["kokoro"]["speed"]), (2.5, "de_DE-kerstin-low", 1.1))
        os.remove(speak.CONFIG_FILE)

    def test_the_system_voice_goes_faster(self):
        cfg = dict(speak.load_config(), speed=1.5)
        cmd = speak.system_command("say", cfg, "/tmp/x.wav")
        self.assertEqual(cmd[cmd.index("-r") + 1], "285")
        self.assertIn("Anna", speak.system_command("say", cfg, "/tmp/x.wav", "de"))

    def test_german_is_marked_only_when_a_german_voice_is_set(self):
        self.assertEqual(speak.clean_for_speech("Say «Guten Tag»", german=False), "Say «Guten Tag».")
        said = speak.clean_for_speech("Say «Guten Tag»", german=True)
        self.assertEqual(said, "Say [de]Guten Tag.[/de]")
        self.assertEqual([(c["lang"], c["w0"], c["text"]) for c in speak.make_chunks(said)],
                         [("en", 0, "Say"), ("de", 1, "Guten Tag.")])


class LastReply(unittest.TestCase):
    def test_only_text_after_the_last_tool_call(self):
        entries = [
            {"type": "user", "message": {"content": "hi"}},
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Let me check."}, {"type": "tool_use", "name": "Bash"}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result"}]}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "All done."}]}},
        ]
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as f:
            f.write("\n".join(json.dumps(e) for e in entries))
        try:
            self.assertEqual(speak.last_reply(f.name), "All done.")
        finally:
            os.remove(f.name)


class OnOff(unittest.TestCase):
    def setUp(self):
        speak.write_on(set())

    def run_main(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            speak.main(["speak.py"] + list(args))
        return out.getvalue()

    def test_on_for_one_chat(self):
        self.assertIn("on for this chat", self.run_main("--on", "--session", "chat-1"))
        self.assertTrue(speak.is_on("chat-1"))
        self.assertFalse(speak.is_on("chat-2"))
        self.assertIn("off here", self.run_main("--status", "--session", "chat-2"))

    def test_on_all_and_off_all(self):
        self.run_main("--on", "--all", "--session", "chat-1")
        self.assertTrue(speak.is_on("anything"))
        self.run_main("--off", "--all", "--session", "chat-1")
        self.assertIsNone(speak.read_on())

    def test_unfilled_session_placeholder_is_ignored(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CODE_SESSION_ID": "from-env"}):
            self.run_main("--on", "--session", "${CLAUDE_SESSION_ID}")
        self.assertTrue(speak.is_on("from-env"))

    def test_stop_hook_saves_and_speaks_only_where_on(self):
        self.run_main("--on", "--session", "chat-1")
        with mock.patch.object(speak, "speak_in_background") as spoken:
            for sid in ("chat-1", "chat-2"):
                hook = {"session_id": sid, "last_assistant_message": "**Hello** there"}
                with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(hook))):
                    speak.hook_stop()
        spoken.assert_called_once_with("**Hello** there", "chat-1")  # the speaker cleans it
        with open(speak.last_paths("chat-2")[0]) as f:
            self.assertEqual(f.read(), "**Hello** there")  # kept for repeat, not spoken

    def test_stop_hook_does_nothing_when_voice_is_off_everywhere(self):
        hook = {"session_id": "chat-9", "last_assistant_message": "secret"}
        with mock.patch.object(sys, "stdin", io.StringIO(json.dumps(hook))):
            speak.hook_stop()
        self.assertFalse(os.path.exists(speak.last_paths("chat-9")[0]))


if __name__ == "__main__":
    unittest.main()
