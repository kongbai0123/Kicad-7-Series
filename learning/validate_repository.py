"""Validate distributable course resources and a clean first launch without user data."""
from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
from urllib.parse import unquote

import state


def main():
    root = state.REPO
    catalog = state.catalog()
    assert len(catalog["lessons"]) == 15, "Expected the 15 upstream lessons"
    all_steps = []
    for lesson in catalog["lessons"]:
        ids = [step["id"] for step in lesson["steps"]]
        assert ids and len(ids) == len(set(ids)), lesson["id"]
        for item in lesson["files"]:
            assert (root / item["path"]).exists(), item["path"]
        for step in lesson["steps"]:
            all_steps.append(step["id"])
            guide = step.get("guide", [])
            assert guide and all(isinstance(row.get(key), str) and row[key].strip()
                                 for row in guide for key in ("where", "do", "see")), step["id"]
            numbers = []
            for figure in step.get("figures", []):
                assert (root / figure["image"]).is_file(), figure["image"]
                assert figure.get("caption"), (step["id"], "missing caption")
                for mark in figure.get("marks", []):
                    assert type(mark["n"]) is int and mark["n"] > 0, step["id"]
                    assert all(isinstance(mark[key], (int, float)) and 0 <= mark[key] <= 100
                               for key in ("x", "y")), (step["id"], mark)
                    assert mark.get("label"), (step["id"], mark)
                    if mark["n"] not in numbers:
                        numbers.append(mark["n"])
            assert numbers == list(range(1, len(numbers) + 1)), (step["id"], numbers)
            referenced = {row["mark"] for row in guide if "mark" in row}
            assert referenced == set(numbers), (step["id"], "figure/text references disagree")
            quiz = step.get("quiz")
            if quiz:
                assert quiz.get("question") and quiz.get("explain"), step["id"]
                assert len(quiz["options"]) >= 2 and type(quiz["answer"]) is int, step["id"]
                assert 0 <= quiz["answer"] < len(quiz["options"]), step["id"]
    assert len(all_steps) == 90 and len(set(all_steps)) == 90, "Expected 90 stable checkpoints"
    for name in ("README.md", "README.en.md"):
        source = root / name
        for target in re.findall(r"!?\[[^\]]*\]\(([^)]+)\)", source.read_text(encoding="utf-8")):
            if "://" not in target and not target.startswith("#"):
                assert (source.parent / unquote(target.split("#")[0])).exists(), (name, target)

    template = state.read_json(state.LEARNING / "progress.template.json")
    assert template["activeLesson"] == "01"
    assert all(value["status"] == "not_started" and not value["completedSteps"]
               and not value["evidence"] and value["workspace"] is None
               for value in template["lessons"].values())
    original = state.REPO, state.LEARNING, state.PROGRESS
    with tempfile.TemporaryDirectory(prefix="kicad-clean-install-") as temp:
        try:
            state.REPO = Path(temp).resolve()
            state.LEARNING = state.REPO / "learning"
            state.PROGRESS = state.LEARNING / "progress.json"
            state.LEARNING.mkdir()
            for name in ("catalog.json", "progress.template.json"):
                shutil.copy2(root / "learning" / name, state.LEARNING / name)
            initial = state.progress()
            assert len(initial["lessons"]) == 15
            assert initial["activeLesson"] == "01"
            assert all(s["status"] == "not_started" for s in initial["lessons"].values())
        finally:
            state.REPO, state.LEARNING, state.PROGRESS = original

    if shutil.which("git") and (root / ".git").exists():
        tracked = subprocess.check_output(["git", "ls-files", "-z"], cwd=root).decode("utf-8").split("\0")
        private = [name for name in tracked if name == "learning/progress.json" or
                   name.startswith(("learning/journal/", "learning/evidence/", "learning/workspaces/", "learning/archive/"))]
        assert not private, f"Personal data is tracked: {private}"
    print("90 guided checkpoints, annotations, quizzes, resource links, blank initialization and private-data separation: OK")


if __name__ == "__main__":
    main()
