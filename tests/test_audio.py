import unittest
from unittest.mock import patch

from theia.audio import speak


class SpeechTests(unittest.TestCase):
    def test_speech_skips_markdown_formatting_and_keeps_the_words(self):
        reply = "The color is **green**.\n\n- _Leaves_\n- [Grass](https://example.com)\n- `2 * 3`"
        with (
            patch("theia.audio.shutil.which", return_value="/usr/bin/pw-play"),
            patch("theia.audio.synthesize_speech", return_value=b"wav") as synthesize,
            patch("theia.audio.subprocess.run") as play,
        ):
            speak(reply)
        synthesize.assert_called_once_with("The color is green.\nLeaves\nGrass\n2 * 3")
        self.assertEqual(play.call_args.kwargs["input"], b"wav")

    def test_speech_skips_replies_with_only_formatting(self):
        with patch("theia.audio.synthesize_speech") as synthesize:
            speak("---")
        synthesize.assert_not_called()
