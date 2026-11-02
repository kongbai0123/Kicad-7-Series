"""Shared-project lessons (one learner project carried from lesson to lesson) and the spec netlist check."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import checks
import server
import state

REPO = Path(__file__).resolve().parent.parent


class SharedProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="kicad-shared-test-")
        self.root = Path(self.temp.name).resolve()
        self.original = (state.REPO, state.LEARNING, state.PROGRESS)
        state.REPO = self.root
        state.LEARNING = self.root / "learning"
        state.PROGRESS = state.LEARNING / "progress.json"
        project = lambda folder: [{"type": "project", "path": f"{folder}/Schematics.kicad_pro"}]
        lessons = [
            {"id": "01", "folder": "1_Introduction", "steps": [{"id": "01-01"}], "files": []},
            {"id": "03", "folder": "3_Schematics", "steps": [{"id": "03-01"}, {"id": "03-02"}],
             "files": project("3_Schematics"), "practice": {"project": "diya", "startFrom": None}},
            {"id": "04", "folder": "4_Prettify_Schematics", "steps": [{"id": "04-01"}, {"id": "04-02"}],
             "files": project("4_Prettify_Schematics"), "practice": {"project": "diya", "startFrom": "3_Schematics"}},
            {"id": "05", "folder": "5_Perform_ERC", "steps": [{"id": "05-01"}],
             "files": project("5_Perform_ERC"), "practice": {"project": "diya", "startFrom": "4_Prettify_Schematics"}},
        ]
        state.LEARNING.mkdir()
        self.write(state.LEARNING / "catalog.json", {"lessons": lessons})
        self.write(state.PROGRESS, {"schemaVersion": 1, "revision": 1, "activeLesson": "01", "updatedAt": "fixture",
                                    "lessons": {lesson["id"]: {"status": "not_started", "currentStep": lesson["steps"][0]["id"],
                                                               "completedSteps": [], "notes": "", "evidence": [],
                                                               "workspace": None} for lesson in lessons}})
        self.write(state.LEARNING / "resources.json", {"journal": [], "guides": []})
        blank = state.LEARNING / "templates" / "diya-blank"
        blank.mkdir(parents=True)
        (blank / "Schematics.kicad_pro").write_text("{}", encoding="utf-8")
        (blank / "Schematics.kicad_sch").write_text("blank", encoding="utf-8")
        for folder, text in (("3_Schematics", "author 03"), ("4_Prettify_Schematics", "author 04"),
                             ("5_Perform_ERC", "author 05")):
            (self.root / folder).mkdir()
            (self.root / folder / "Schematics.kicad_pro").write_text("{}", encoding="utf-8")
            (self.root / folder / "Schematics.kicad_sch").write_text(text, encoding="utf-8")
        self.project = self.root / "learning/workspaces/diya"
        self.snapshots = self.root / "learning/workspaces/diya-snapshots"

    def tearDown(self):
        state.REPO, state.LEARNING, state.PROGRESS = self.original
        self.assertTrue(self.root.name.startswith("kicad-shared-test-"))
        self.temp.cleanup()

    @staticmethod
    def write(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def start(self, lesson, **extra):
        return state.workspace({"lessonId": lesson, "expectedRevision": state.progress()["revision"], **extra})

    def schematic(self, folder=None):
        return ((folder or self.project) / "Schematics.kicad_sch").read_text(encoding="utf-8")

    def work(self, text):
        (self.project / "Schematics.kicad_sch").write_text(text, encoding="utf-8")

    def test_first_lesson_starts_from_the_blank_template_and_keeps_a_start_snapshot(self):
        result = self.start("03")
        self.assertEqual((result["path"], result["created"]), ("learning/workspaces/diya", True))
        self.assertEqual(self.schematic(), "blank")
        self.assertEqual(self.schematic(self.snapshots / "03"), "blank")
        meta = state.read_json(self.project / "learning-workspace.json")
        self.assertEqual((meta["createdFrom"], meta["lessons"]["03"]["from"]), ("blank", "blank"))
        lesson = result["progress"]["lessons"]["03"]
        self.assertEqual((lesson["status"], lesson["projectFrom"], lesson["workspace"]),
                         ("in_progress", "blank", "learning/workspaces/diya"))
        self.assertEqual(self.schematic(self.root / "3_Schematics"), "author 03")

    def test_next_lesson_continues_the_learners_own_project(self):
        self.start("03")
        self.work("my lesson 3 drawing")
        result = self.start("04")
        self.assertFalse(result["created"])
        self.assertEqual(self.schematic(), "my lesson 3 drawing")
        self.assertEqual(self.schematic(self.snapshots / "04"), "my lesson 3 drawing")
        self.assertEqual(result["progress"]["lessons"]["04"]["projectFrom"], "own")
        meta = state.read_json(self.project / "learning-workspace.json")
        _folder, started = checks._workspace("04", state.progress())
        self.assertEqual(started, checks.datetime.fromisoformat(meta["lessons"]["04"]["startedAt"]).timestamp())
        self.assertEqual(self.schematic(self.root / "4_Prettify_Schematics"), "author 04")

    def test_reopening_a_started_lesson_reuses_the_project(self):
        self.start("03")
        self.work("keep me")
        again = self.start("03")
        self.assertFalse(again["created"])
        self.assertEqual(self.schematic(), "keep me")

    def test_a_later_lesson_without_a_project_asks_before_using_author_files(self):
        before = state.PROGRESS.read_bytes()
        with self.assertRaises(state.StateError) as caught:
            self.start("05")
        self.assertEqual((caught.exception.status, caught.exception.code), (409, "needs_project"))
        self.assertEqual(state.PROGRESS.read_bytes(), before)
        self.assertFalse(self.project.exists())
        result = self.start("05", source="rescue")
        self.assertEqual(self.schematic(), "author 04")
        self.assertEqual(result["progress"]["lessons"]["05"]["projectFrom"], "rescue:4_Prettify_Schematics")

    def test_rescue_through_workspace_is_refused_when_the_learner_already_has_a_project(self):
        self.start("03")
        self.work("mine")
        with self.assertRaises(state.StateError):
            self.start("04", source="rescue")
        self.assertEqual(self.schematic(), "mine")

    def test_reset_returns_to_the_lesson_start_and_resets_later_lessons(self):
        self.start("03")
        self.work("lesson 3 done")
        self.start("04")
        self.work("lesson 4 done")
        state.mutate({"lessonId": "04", "action": "confirm_step", "stepId": "04-01",
                      "expectedRevision": state.progress()["revision"]})
        result = state.reset({"lessonId": "04", "confirm": True, "expectedRevision": state.progress()["revision"]})
        self.assertEqual(self.schematic(), "lesson 3 done")
        lessons = result["progress"]["lessons"]
        self.assertEqual((lessons["04"]["status"], lessons["04"]["completedSteps"], lessons["04"]["workspace"]),
                         ("not_started", [], None))
        self.assertEqual(lessons["03"]["status"], "in_progress")
        self.assertFalse((self.snapshots / "04").exists())
        self.assertTrue((self.snapshots / "03").exists())
        archive = self.root / result["archived"][0]
        self.assertEqual(self.schematic(archive / "project"), "lesson 4 done")
        self.assertEqual(self.schematic(archive / "snapshots/04"), "lesson 3 done")
        self.assertEqual(state.read_json(archive / "progress-before.json")["lessons"]["04"]["completedSteps"], ["04-01"])

    def test_resetting_an_earlier_lesson_also_resets_the_lessons_built_on_it(self):
        self.start("03")
        self.work("lesson 3 done")
        self.start("04")
        self.work("lesson 4 done")
        result = state.reset({"lessonId": "03", "confirm": True, "expectedRevision": state.progress()["revision"]})
        self.assertEqual(self.schematic(), "blank")
        self.assertEqual({key: value["status"] for key, value in result["progress"]["lessons"].items()},
                         {"01": "not_started", "03": "not_started", "04": "not_started", "05": "not_started"})
        self.assertFalse(self.snapshots.exists() and any(self.snapshots.iterdir()))

    def test_rescue_archives_own_work_and_restarts_the_lesson_from_the_author_start(self):
        self.start("03")
        self.work("mine")
        result = state.rescue({"lessonId": "04", "confirm": True, "expectedRevision": state.progress()["revision"]})
        self.assertEqual(self.schematic(), "author 03")
        self.assertEqual(self.schematic(self.snapshots / "04"), "author 03")
        lessons = result["progress"]["lessons"]
        self.assertEqual(lessons["03"]["status"], "in_progress")
        self.assertEqual((lessons["04"]["status"], lessons["04"]["projectFrom"]), ("in_progress", "rescue:3_Schematics"))
        self.assertEqual(self.schematic(self.root / result["archived"][0] / "project"), "mine")
        meta = state.read_json(self.project / "learning-workspace.json")
        self.assertEqual([entry["action"] for entry in meta["history"]], ["start", "rescue"])
        self.assertEqual(self.schematic(self.root / "3_Schematics"), "author 03")

    def test_rescue_needs_confirmation_and_an_author_start(self):
        with self.assertRaises(state.StateError):
            state.rescue({"lessonId": "04", "expectedRevision": 1})
        with self.assertRaises(state.StateError):
            state.rescue({"lessonId": "03", "confirm": True, "expectedRevision": 1})
        with self.assertRaises(state.StateError):
            state.rescue({"lessonId": "01", "confirm": True, "expectedRevision": 1})
        self.assertFalse(self.project.exists())

    def test_shared_reset_and_rescue_wait_while_kicad_has_the_project_open(self):
        self.start("03")
        (self.project / "~Schematics.kicad_sch.lck").write_text("{}", encoding="utf-8")
        before = state.PROGRESS.read_bytes()
        for action in (state.reset, state.rescue):
            lesson = "03" if action is state.reset else "04"
            with self.subTest(action=action.__name__), self.assertRaises(state.StateError) as caught:
                action({"lessonId": lesson, "confirm": True, "expectedRevision": state.progress()["revision"]})
            self.assertEqual(caught.exception.status, 423)
        self.assertEqual(state.PROGRESS.read_bytes(), before)
        self.assertFalse((state.LEARNING / "archive").exists())

    def test_failed_shared_reset_puts_project_snapshots_and_progress_back(self):
        self.start("03")
        self.work("lesson 3 done")
        self.start("04")
        self.work("lesson 4 done")
        before = state.PROGRESS.read_bytes()
        with mock.patch.object(state, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(state.StateError):
                state.reset({"lessonId": "04", "confirm": True, "expectedRevision": state.progress()["revision"]})
        self.assertEqual(state.PROGRESS.read_bytes(), before)
        self.assertEqual(self.schematic(), "lesson 4 done")
        self.assertEqual(self.schematic(self.snapshots / "04"), "lesson 3 done")
        archive = state.LEARNING / "archive"
        self.assertTrue(not archive.exists() or not any(archive.iterdir()))

    def test_failed_rescue_puts_the_learners_project_back(self):
        self.start("03")
        self.work("mine")
        before = state.PROGRESS.read_bytes()
        with mock.patch.object(state, "_save", side_effect=OSError("disk full")):
            with self.assertRaises(state.StateError):
                state.rescue({"lessonId": "04", "confirm": True, "expectedRevision": state.progress()["revision"]})
        self.assertEqual(state.PROGRESS.read_bytes(), before)
        self.assertEqual(self.schematic(), "mine")
        self.assertFalse((self.snapshots / "04").exists())

    def test_reset_all_archives_the_shared_project_and_snapshots(self):
        self.start("03")
        self.work("mine")
        result = state.reset({"lessonId": "all", "confirm": True, "expectedRevision": state.progress()["revision"]})
        self.assertFalse(self.project.exists())
        self.assertFalse((self.snapshots / "03").exists())
        archived = [self.root / path for path in result["archived"]]
        self.assertTrue(any((path / "project/Schematics.kicad_sch").is_file() for path in archived))

    def test_open_target_uses_the_shared_project(self):
        self.start("04", source="rescue")
        self.assertEqual(state.open_target("04", "project"), self.project / "Schematics.kicad_pro")
        with self.assertRaises(state.StateError):
            state.open_target("05", "project")

    def test_lessons_not_started_follow_renumbered_steps(self):
        value = json.loads(state.PROGRESS.read_text(encoding="utf-8"))
        value["lessons"]["05"]["currentStep"] = "05-99"
        self.write(state.PROGRESS, value)
        self.assertEqual(state.progress()["lessons"]["05"]["currentStep"], "05-01")
        value["lessons"]["05"].update(status="in_progress")
        self.write(state.PROGRESS, value)
        with self.assertRaises(state.StateError):
            state.progress()

    def test_http_reports_why_a_project_is_needed_and_rescue_needs_confirmation(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{httpd.server_port}"

        def post(route, body):
            return urlopen(Request(url + route, data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json"}))
        try:
            with self.assertRaises(HTTPError) as caught:
                post("/api/workspace", {"lessonId": "05", "expectedRevision": 1})
            self.assertEqual(caught.exception.code, 409)
            self.assertEqual(json.load(caught.exception)["code"], "needs_project")
            with self.assertRaises(HTTPError) as caught:
                post("/api/rescue", {"lessonId": "05", "expectedRevision": 1})
            self.assertEqual(caught.exception.code, 400)
            with post("/api/rescue", {"lessonId": "05", "expectedRevision": 1, "confirm": True}) as response:
                lesson = json.load(response)["progress"]["lessons"]["05"]
            self.assertEqual(lesson["projectFrom"], "rescue:4_Prettify_Schematics")
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)


SPEC = {"parts": [
    {"key": "BT1", "label": "電池", "lib": "Device:Battery_Cell", "value": "3V", "pins": {"1": "+", "2": "-"}},
    {"key": "R1", "label": "10k 電阻", "lib": "Device:R", "value": "10k", "ohms": 10000, "pins": {"1": "", "2": ""},
     "swap": [["1", "2"]]},
    {"key": "R4", "label": "470Ω 電阻", "lib": "Device:R", "value": "470R", "ohms": 470, "pins": {"1": "", "2": ""},
     "swap": [["1", "2"]]},
    {"key": "D1", "label": "LED", "lib": "Device:LED", "value": "LED", "pins": {"1": "K", "2": "A"}},
], "nets": [
    [{"part": "BT1", "pin": "1"}, {"part": "R1", "pin": "1"}],
    [{"part": "R1", "pin": "2"}, {"part": "D1", "pin": "2"}],
    [{"part": "D1", "pin": "1"}, {"part": "R4", "pin": "1"}],
    [{"part": "R4", "pin": "2"}, {"part": "BT1", "pin": "2"}],
]}


def part(ref, lib, value):
    return {"ref": ref, "lib": lib, "value": value, "footprint": ""}


GOOD_PARTS = [part("BT1", "Device:Battery_Cell", "3V"), part("R7", "Device:R", "470"), part("R2", "Device:R", "10K"),
              part("D1", "Device:LED", "LED")]
GOOD_NETS = [[("BT1", "1"), ("R2", "2")], [("R2", "1"), ("D1", "2")], [("D1", "1"), ("R7", "2")],
             [("R7", "1"), ("BT1", "2")], [("D1", "9")]]


class NetlistCompareTests(unittest.TestCase):
    def test_numbering_order_value_spelling_and_resistor_direction_do_not_matter(self):
        ok, detail, findings = checks.compare_netlist(SPEC, GOOD_PARTS, GOOD_NETS)
        self.assertTrue(ok, findings)
        self.assertIn("4 個元件、4 條連線", detail)

    def test_a_missing_connection_names_the_pins_to_join(self):
        nets = [net for net in GOOD_NETS if ("D1", "2") not in net] + [[("R2", "1")], [("D1", "2")]]
        ok, _detail, findings = checks.compare_netlist(SPEC, GOOD_PARTS, nets)
        self.assertFalse(ok)
        self.assertEqual([f["type"] for f in findings], ["missing"])
        self.assertIn("LED（陽極 A）", findings[0]["description"])
        self.assertIn("10k 電阻的一端", findings[0]["description"])

    def test_a_short_between_different_connections_is_reported(self):
        nets = [[("BT1", "1"), ("R2", "2"), ("D1", "1"), ("R7", "2")], [("R2", "1"), ("D1", "2")],
                [("R7", "1"), ("BT1", "2")]]
        ok, _detail, findings = checks.compare_netlist(SPEC, GOOD_PARTS, nets)
        self.assertFalse(ok)
        self.assertIn("extra", [f["type"] for f in findings])

    def test_parts_with_wrong_value_duplicates_or_unknown_symbols_are_named(self):
        parts = GOOD_PARTS + [part("R9", "Device:R", "22k"), part("R8", "Device:R", "10k"), part("U9", "Device:C", "1u")]
        _ok, _detail, findings = checks.compare_netlist(SPEC, parts, GOOD_NETS)
        text = " ".join(f["description"] for f in findings)
        self.assertIn("R9 的數值「22k」", text)
        self.assertIn("R2 和 R8", text)
        self.assertIn("U9（Device:C）", text)

    def test_missing_parts_are_reported_once_without_noise_from_their_connections(self):
        parts = [p for p in GOOD_PARTS if p["ref"] != "D1"]
        nets = [[("BT1", "1"), ("R2", "2")], [("R7", "1"), ("BT1", "2")]]
        ok, _detail, findings = checks.compare_netlist(SPEC, parts, nets)
        self.assertFalse(ok)
        self.assertEqual([f["description"] for f in findings], ["還沒放「LED」（符號 LED）。"])

    def test_unnumbered_symbols_stop_the_comparison_with_one_clear_message(self):
        parts = GOOD_PARTS + [part("R?", "Device:R", "10k")]
        ok, detail, findings = checks.compare_netlist(SPEC, parts, GOOD_NETS)
        self.assertFalse(ok)
        self.assertEqual(len(findings), 1)
        self.assertIn("標註原理圖", findings[0]["description"])

    def test_scope_checks_only_the_section_drawn_so_far(self):
        parts = [part("BT1", "Device:Battery_Cell", "3V"), part("R1", "Device:R", "10k")]
        ok, _detail, findings = checks.compare_netlist(SPEC, parts, [[("BT1", "1"), ("R1", "1")]], ["BT1", "R1"])
        self.assertTrue(ok, findings)

    def test_resistance_spellings(self):
        for text, value in (("10k", 10000), ("5k6", 5600), ("5.6K", 5600), ("470R", 470), ("470 Ω", 470), ("4R7", 4.7)):
            self.assertEqual(checks.ohms(text), value, text)
        for text in ("R", "abc", "5.6k6", ""):
            self.assertIsNone(checks.ohms(text), text)


@unittest.skipUnless(checks.find_cli() and (REPO / "6_Assign_Footprint").is_dir(), "needs KiCad's kicad-cli")
class AuthorStageNetlistTests(unittest.TestCase):
    """The committed Diya spec agrees with the author's finished schematic and catches real stage differences."""

    def check(self, folder):
        spec = json.loads((REPO / "learning/diya-spec.json").read_text(encoding="utf-8"))
        return checks.check_netlist(checks.find_cli(), REPO / folder / "Schematics.kicad_sch", spec)

    def test_lesson_6_schematic_matches_the_spec(self):
        ok, detail, findings = self.check("6_Assign_Footprint")
        self.assertTrue(ok, findings)

    def test_lesson_13_schematic_has_a_broken_supply_connection(self):
        ok, _detail, findings = self.check("13_Create_Custom_Library")
        self.assertFalse(ok)
        self.assertIn("ATtiny85 微控制器 第 8 腳（VCC）", findings[0]["description"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
