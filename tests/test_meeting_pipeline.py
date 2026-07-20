import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from daemon.pipeline import meeting as meeting_pipeline
from daemon.pipeline.audio_asr import segments_from_mlx_result
from daemon.pipeline.meeting import run_from_audio, run_from_segments
from daemon.speaker.anonymous import normalize_speaker_labels
from daemon.exporters.srt import render_srt
from daemon.exporters.vtt import render_vtt
from daemon.exporters.markdown import render_markdown


class MeetingPipelineTests(unittest.TestCase):
    def test_anonymous_labels_are_stable(self):
        rows = normalize_speaker_labels([
            {"speaker": "alice", "text": "a"},
            {"speaker": "bob", "text": "b"},
            {"speaker": "alice", "text": "c"},
        ])
        self.assertEqual([r["speaker"] for r in rows], ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"])

    def test_pipeline_exports_public_safe_formats(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "meeting.json"
            src.write_text(json.dumps({"segments": [
                {"start": 0, "end": 1, "speaker": "alice", "raw": "今天測試阿布西店"},
                {"start": 1, "end": 2, "speaker": "bob", "raw": "預算是3000元"},
            ]}, ensure_ascii=False), encoding="utf-8")
            result = run_from_segments(src, root / "out")
            self.assertEqual(len(result.segments), 2)
            for name in ["transcript.jsonl", "transcript.md", "transcript.srt", "transcript.vtt", "meeting-result.json"]:
                self.assertTrue((root / "out" / name).exists(), name)
            md = (root / "out" / "transcript.md").read_text(encoding="utf-8")
            self.assertIn("SPEAKER_00", md)
            self.assertIn("⚠", md)

    def test_exporters_do_not_rewrite_text_or_emit_bad_timestamps(self):
        segments = [{"start": 1.9996, "end": 2.5, "speaker": "speaker\ninj", "text": "hello, world\n<script>"}]
        srt = render_srt(segments)
        self.assertIn("00:00:02,000", srt)
        self.assertIn("hello, world <script>", srt)
        vtt = render_vtt(segments)
        self.assertIn("00:00:02.000", vtt)
        self.assertIn("hello, world <script>", vtt)
        md = render_markdown(segments)
        self.assertIn("&lt;script&gt;", md)
        self.assertNotIn("speaker\ninj", md)

    def test_bad_segment_times_are_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            src = root / "bad.json"
            src.write_text(json.dumps({"segments": [{"start": 3, "end": 2, "raw": "bad"}]}, ensure_ascii=False), encoding="utf-8")
            with self.assertRaises(ValueError):
                run_from_segments(src, root / "out")

    def test_local_accepted_glossary_affects_pipeline(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            original = meeting_pipeline.LOCAL_PERSONAL_GLOSSARY
            try:
                meeting_pipeline.LOCAL_PERSONAL_GLOSSARY = root / "personal.json"
                meeting_pipeline.LOCAL_PERSONAL_GLOSSARY.write_text(json.dumps({"replacements": {"阿布西店": "Obsidian"}}, ensure_ascii=False), encoding="utf-8")
                src = root / "meeting.json"
                src.write_text(json.dumps({"segments": [{"start": 0, "end": 1, "raw": "打開阿布西店"}]}, ensure_ascii=False), encoding="utf-8")
                result = run_from_segments(src, root / "out")
                self.assertEqual(result.segments[0]["text"], "打開Obsidian")
            finally:
                meeting_pipeline.LOCAL_PERSONAL_GLOSSARY = original

    def test_segments_from_mlx_result_uses_segment_timestamps(self):
        rows = segments_from_mlx_result({"segments": [
            {"id": 3, "start": 1.2, "end": 2.4, "text": " 測試音檔 "},
            {"id": 4, "start": 2.4, "end": 3.0, "text": ""},
        ]})
        self.assertEqual(rows, [{"start": 1.2, "end": 2.4, "speaker": "SPEAKER", "raw": "測試音檔", "asr_segment_id": 3}])

    def test_audio_path_runs_local_asr_adapter_without_downloading_model(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            audio = root / "meeting.wav"
            audio.write_bytes(b"not a real wav; backend is mocked")
            with patch("daemon.pipeline.audio_asr._transcribe_backend") as backend:
                backend.return_value = {"segments": [{"start": 0, "end": 1.5, "text": "今天測試阿布西店"}]}
                result = run_from_audio(audio, root / "out", language="zh", model="mock-model")
            self.assertEqual(len(result.segments), 1)
            self.assertEqual(result.segments[0]["speaker"], "SPEAKER_00")
            self.assertIn("transcript.md", result.exports["markdown"])
            backend.assert_called_once()


if __name__ == "__main__":
    unittest.main()
