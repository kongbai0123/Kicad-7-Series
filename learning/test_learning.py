"""Meaningful persistence and API checks; all mutations use an isolated fixture."""
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import server
import state
from reader import render_document


class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kicad-learning-test-")
        self.root = Path(self.temp.name).resolve()
        self.original = (state.REPO, state.LEARNING, state.PROGRESS)
        state.REPO = self.root
        state.LEARNING = self.root / "learning"
        state.LEARNING.mkdir()
        state.PROGRESS = state.LEARNING / "progress.json"
        self.write(state.LEARNING / "catalog.json", {"lessons": [
            {"id": "01", "folder": "1_Introduction", "steps": [{"id": "01-01"}], "files": []},
            {"id": "02", "folder": "2_User Interface", "steps": [{"id": "02-01"}, {"id": "02-02"}],
             "files": [{"type": "project", "path": "2_User Interface/User Interface.kicad_pro"}]}]})
        self.write(state.PROGRESS, {"schemaVersion": 1, "revision": 1, "activeLesson": "02", "updatedAt": "fixture",
            "lessons": {
                "01": {"status": "completed", "currentStep": "01-01", "completedSteps": ["01-01"], "notes": "", "evidence": [], "workspace": "1_Introduction"},
                "02": {"status": "not_started", "currentStep": "02-01", "completedSteps": [], "notes": "", "evidence": [], "workspace": None}}})
        self.write(state.LEARNING / "resources.json", {"journal": [], "guides": []})
        (self.root / "2_User Interface").mkdir()
        (self.root / "2_User Interface/User Interface.kicad_pro").write_text("original", encoding="utf-8")

    def tearDown(self):
        state.REPO, state.LEARNING, state.PROGRESS = self.original
        self.assertTrue(self.root.name.startswith("kicad-learning-test-"))
        self.temp.cleanup()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def act(self, action, **kwargs):
        return state.mutate({"expectedRevision": state.progress()["revision"], "lessonId": "02", "action": action, **kwargs})

    def test_viewing_never_starts_a_lesson(self):
        before = state.PROGRESS.read_bytes()
        self.assertEqual(state.progress()["lessons"]["02"]["status"], "not_started")
        state.catalog()
        self.assertEqual(state.PROGRESS.read_bytes(), before)

    def test_confirmations_advance_one_step_and_only_finish_the_selected_lesson(self):
        first = self.act("confirm_step", stepId="02-01")
        self.assertEqual(first["lessons"]["02"]["currentStep"], "02-02")
        self.assertEqual(first["lessons"]["02"]["status"], "in_progress")
        final = self.act("confirm_step", stepId="02-02")
        self.assertEqual(final["lessons"]["02"]["status"], "completed")
        self.assertEqual(final["lessons"]["02"]["evidence"][0]["kind"], "user_confirmed")
        self.assertEqual(final["activeLesson"], "02")
        self.assertEqual(final["lessons"]["01"]["completedSteps"], ["01-01"])

    def test_skipping_a_step_is_rejected_without_writing(self):
        before = state.PROGRESS.read_bytes()
        with self.assertRaises(state.StateError) as caught:
            self.act("confirm_step", stepId="02-02")
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(state.PROGRESS.read_bytes(), before)

    def test_stale_revision_cannot_overwrite_newer_progress(self):
        self.act("add_note", note="找不到工具")
        with self.assertRaises(state.StateError) as caught:
            state.mutate({"expectedRevision": 1, "lessonId": "02", "action": "activate_lesson"})
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(state.progress()["lessons"]["02"]["notes"], "找不到工具")
        self.assertEqual(state.progress()["lessons"]["02"]["status"], "not_started")

    def test_simultaneous_writers_preserve_one_consistent_revision(self):
        payload = {"expectedRevision": 1, "lessonId": "02", "action": "add_note", "note": "並行紀錄"}
        def writer(_):
            try:
                state.mutate(payload)
                return 200
            except state.StateError as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(writer, range(2))), [200, 409])
        self.assertEqual(state.progress()["revision"], 2)

    def test_workspace_reuse_preserves_student_edits_and_original_material(self):
        result = state.workspace({"lessonId": "02", "expectedRevision": 1})
        target = self.root / result["path"] / "User Interface.kicad_pro"
        self.assertTrue(result["created"])
        target.write_text("my practice", encoding="utf-8")
        repeated = state.workspace({"lessonId": "02", "expectedRevision": result["progress"]["revision"]})
        self.assertFalse(repeated["created"])
        self.assertEqual(target.read_text(encoding="utf-8"), "my practice")
        self.assertEqual((self.root / "2_User Interface/User Interface.kicad_pro").read_text(encoding="utf-8"), "original")
        self.assertEqual(state.open_target("02", "project"), target)

    def test_failed_check_blocks_confirmation_and_passing_check_is_recorded(self):
        catalog = json.loads((state.LEARNING / "catalog.json").read_text(encoding="utf-8"))
        catalog["lessons"][1]["steps"][0]["checks"] = [{"type": "project_open"},
                                                       {"type": "saved_since_start", "required": False}]
        self.write(state.LEARNING / "catalog.json", catalog)
        result = state.workspace({"lessonId": "02", "expectedRevision": 1})
        folder = self.root / result["path"]
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{httpd.server_port}/api/progress"
        def confirm():
            body = json.dumps({"lessonId": "02", "expectedRevision": state.progress()["revision"],
                               "action": "confirm_step", "stepId": "02-01"}).encode()
            return urlopen(Request(url, data=body, headers={"Content-Type": "application/json"}))
        try:
            before = state.PROGRESS.read_bytes()
            with self.assertRaises(HTTPError) as caught:
                confirm()
            self.assertEqual(caught.exception.code, 409)
            items = json.load(caught.exception)["check"]["items"]
            self.assertEqual([i["ok"] for i in items], [False, False])
            self.assertEqual(state.PROGRESS.read_bytes(), before)
            (folder / "~User Interface.kicad_sch.lck").write_text("{}", encoding="utf-8")
            with confirm() as response:
                lesson = json.load(response)["progress"]["lessons"]["02"]
            self.assertEqual(lesson["currentStep"], "02-02")
            self.assertEqual([e["kind"] for e in lesson["evidence"]], ["user_confirmed", "tool_verified"])
            self.assertIn("KiCad 已開啟本課練習副本", lesson["evidence"][1]["summary"])
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    def test_paths_and_unprepared_projects_are_rejected(self):
        for path in ("../private", ".git/config", "learning/../../private", "C:\\Windows\\win.ini"):
            with self.assertRaises(state.StateError):
                state.safe_path(path)
        with self.assertRaises(state.StateError):
            state.open_target("02", "project")

    def test_journal_images_resolve_locally_and_html_is_escaped(self):
        journal = state.LEARNING / "journal"
        journal.mkdir()
        source = journal / "entry.md"
        source.write_text('# 學習紀錄\n\n![孔位](../assets/hole.png)\n\n<script>alert(1)</script>\n\n[連結](javascript:alert)\n', encoding="utf-8")
        rendered = render_document(source, self.root)
        self.assertIn('src="/files/learning/assets/hole.png"', rendered)
        self.assertIn('&lt;script&gt;', rendered)
        self.assertNotIn('<script>', rendered)
        self.assertNotIn('href="javascript:', rendered)

    def test_http_blocks_foreign_origins_and_reading_does_not_change_progress(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{httpd.server_port}"
        try:
            before = state.PROGRESS.read_bytes()
            with urlopen(url + "/api/bootstrap") as response:
                self.assertEqual(json.load(response)["progress"]["revision"], 1)
            self.assertEqual(state.PROGRESS.read_bytes(), before)
            payload = json.dumps({"lessonId": "02", "expectedRevision": 1, "action": "activate_lesson"}).encode()
            request = Request(url + "/api/progress", data=payload, headers={"Content-Type": "application/json", "Origin": "https://unrelated.example"})
            with self.assertRaises(HTTPError) as caught:
                urlopen(request)
            self.assertEqual(caught.exception.code, 403)
            self.assertEqual(state.PROGRESS.read_bytes(), before)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
