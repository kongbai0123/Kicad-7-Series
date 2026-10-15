"""Single source of truth for the local KiCad learning project (stdlib only)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time

REPO = Path(__file__).resolve().parent.parent
LEARNING = REPO / "learning"
PROGRESS = LEARNING / "progress.json"
_thread_lock = threading.RLock()


class StateError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def catalog():
    return read_json(LEARNING / "catalog.json")


def lesson_by_id(lesson_id):
    for lesson in catalog()["lessons"]:
        if lesson["id"] == lesson_id:
            return lesson
    raise StateError("找不到這個課程。")


def safe_path(relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise StateError("檔案路徑不正確。")
    path = (REPO / relative).resolve()
    if not path.is_relative_to(REPO) or any(p.startswith(".") for p in Path(relative).parts):
        raise StateError("只能使用本專案內的學習檔案。", 403)
    return path


@contextmanager
def locked():
    """Share an OS lock with the server and the tutor's command line tool."""
    LEARNING.mkdir(exist_ok=True)
    with _thread_lock:
        with (LEARNING / ".progress.lock").open("a+b") as handle:
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            deadline = time.monotonic() + 8
            acquired = False
            while not acquired:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except OSError:
                    if time.monotonic() >= deadline:
                        raise StateError("進度正在儲存，請稍候再試。", 409)
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def progress():
    value = read_json(PROGRESS)
    if value.get("schemaVersion") != 1 or not isinstance(value.get("revision"), int):
        raise StateError("進度檔格式不正確，請由對話協助檢查。", 500)
    known = {lesson["id"]: lesson for lesson in catalog()["lessons"]}
    if value.get("activeLesson") not in known or set(value.get("lessons", {})) != set(known):
        raise StateError("進度檔與課程目錄不一致，請由對話協助檢查。", 500)
    for key, state in value["lessons"].items():
        step_ids = {step["id"] for step in known[key]["steps"]}
        if state.get("status") not in ("not_started", "in_progress", "completed"):
            raise StateError("課程狀態不正確。", 500)
        if state.get("currentStep") not in step_ids or not set(state.get("completedSteps", [])).issubset(step_ids):
            raise StateError("課程步驟與進度不一致。", 500)
        if state["status"] == "completed" and set(state["completedSteps"]) != step_ids:
            raise StateError("完成狀態缺少已確認步驟。", 500)
    return value


def _atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp") as handle:
            name = handle.name
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _save(before, after, event):
    history = LEARNING / ".history"
    history.mkdir(exist_ok=True)
    _atomic_json(history / f"progress-{before['revision']:05d}.json", before)
    after["revision"] = before["revision"] + 1
    after["updatedAt"] = now()
    _atomic_json(PROGRESS, after)
    event.update({"at": after["updatedAt"], "revision": after["revision"]})
    with (LEARNING / "events.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def mutate(payload, *, actor="learner", verification=None):
    lesson_id = payload.get("lessonId")
    lesson = lesson_by_id(lesson_id)
    action = payload.get("action")
    if action not in {"activate_lesson", "confirm_step", "add_note"}:
        raise StateError("不支援這個進度操作。")
    with locked():
        before = progress()
        if type(payload.get("expectedRevision")) is not int or payload["expectedRevision"] != before["revision"]:
            raise StateError("進度已有更新，已重新整理；請確認目前步驟後再操作。", 409)
        after = json.loads(json.dumps(before))
        state = after["lessons"][lesson_id]
        event = {"action": action, "lessonId": lesson_id, "actor": actor}
        if action == "activate_lesson":
            after["activeLesson"] = lesson_id
            if state["status"] == "not_started":
                state["status"] = "in_progress"
                state["startedAt"] = now()
        elif action == "confirm_step":
            step_id = payload.get("stepId")
            if state["status"] == "completed":
                raise StateError("本課已完成，可選擇複習或其他課程。")
            if step_id != state["currentStep"]:
                raise StateError("這不是目前待確認的步驟，請重新整理。", 409)
            ids = [step["id"] for step in lesson["steps"]]
            if step_id not in ids:
                raise StateError("找不到這個操作步驟。")
            state["completedSteps"] = [key for key in ids if key in set(state["completedSteps"]) | {step_id}]
            state["evidence"].append({"at": now(), "kind": "user_confirmed", "stepId": step_id,
                                      "summary": "學員確認已完成此步驟；不等同工具自動驗證。"})
            if verification:
                passed = [i["label"] + "：" + i["detail"] for i in verification["items"] if i["ok"]]
                if passed:
                    state["evidence"].append({"at": now(), "kind": "tool_verified", "stepId": step_id,
                                              "summary": "自動檢查練習檔：" + "；".join(passed)})
            remaining = [key for key in ids if key not in state["completedSteps"]]
            state["status"] = "in_progress" if remaining else "completed"
            state.setdefault("startedAt", now())
            state["currentStep"] = remaining[0] if remaining else ids[-1]
            if not remaining:
                state["completedAt"] = now()
            after["activeLesson"] = lesson_id
            event["stepId"] = step_id
        else:
            note = payload.get("note", "")
            if not isinstance(note, str) or not note.strip() or len(note) > 4000:
                raise StateError("請填寫 1～4000 字的操作結果或問題。")
            state["notes"] = note.strip()
            state["evidence"].append({"at": now(), "kind": "learner_note", "summary": note.strip()})
            event["note"] = note.strip()
        _save(before, after, event)
        return after


def workspace(payload):
    lesson_id = payload.get("lessonId")
    lesson = lesson_by_id(lesson_id)
    with locked():
        before = progress()
        if type(payload.get("expectedRevision")) is not int or payload["expectedRevision"] != before["revision"]:
            raise StateError("進度已有更新，請重新整理後再開啟練習。", 409)
        source = safe_path(lesson["folder"])
        relative = lesson["folder"] if lesson_id == "01" else f"learning/workspaces/{lesson_id}"
        target = safe_path(relative)
        created = False
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=f".{lesson_id}-", dir=target.parent))
            try:
                shutil.copytree(source, stage, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns("*.lck", ".history", ".git", "*-backups", "*.kicad_prl"))
                _atomic_json(stage / "learning-workspace.json", {
                    "lessonId": lesson_id, "source": lesson["folder"], "createdAt": now(),
                    "note": "本課獨立練習副本；教材既有輸出屬原作者參考成果。"})
                os.rename(stage, target)
                created = True
            finally:
                if stage.exists():
                    if not stage.resolve().is_relative_to(target.parent.resolve()):
                        raise StateError("練習副本暫存路徑不正確，已停止清理。", 500)
                    shutil.rmtree(stage)
        if before["lessons"][lesson_id].get("workspace") == relative and before["activeLesson"] == lesson_id:
            return {"progress": before, "path": relative, "created": created}
        after = json.loads(json.dumps(before))
        state = after["lessons"][lesson_id]
        state["workspace"] = relative
        if state["status"] == "not_started":
            state["status"] = "in_progress"
            state["startedAt"] = now()
        after["activeLesson"] = lesson_id
        _save(before, after, {"action": "prepare_workspace", "lessonId": lesson_id,
                             "path": relative, "actor": "learner"})
        return {"progress": after, "path": relative, "created": created}


def open_target(lesson_id, target):
    lesson = lesson_by_id(lesson_id)
    state = progress()["lessons"][lesson_id]
    relative = state.get("workspace")
    expected = lesson["folder"] if lesson_id == "01" else f"learning/workspaces/{lesson_id}"
    if relative != expected:
        raise StateError("請先建立本課練習副本。")
    folder = safe_path(relative)
    if target == "folder":
        path = folder
    elif target == "project":
        project = next((item for item in lesson["files"] if item["type"] == "project"), None)
        if not project:
            raise StateError("本課沒有專案檔案。")
        path = (folder / Path(project["path"]).relative_to(lesson["folder"])).resolve()
        if not path.is_relative_to(folder):
            raise StateError("專案路徑不正確。", 403)
    else:
        raise StateError("只能開啟課程專案或練習資料夾。")
    if not path.exists():
        raise StateError("找不到練習檔案，請在對話中協助檢查。", 404)
    return path
