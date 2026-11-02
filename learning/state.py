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
_lock_local = threading.local()


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
        if getattr(_lock_local, "held", False):
            yield
            return
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
                _lock_local.held = True
                yield
            finally:
                _lock_local.held = False
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def progress():
    if not PROGRESS.exists():
        with locked():
            if not PROGRESS.exists():
                template = read_json(LEARNING / "progress.template.json")
                lessons = catalog()["lessons"]
                value = {**template, "revision": 1, "updatedAt": now(),
                         "activeLesson": lessons[0]["id"], "skillsPracticed": [],
                         "lessons": {lesson["id"]: {
                             "status": "not_started", "currentStep": lesson["steps"][0]["id"],
                             "completedSteps": [], "notes": "", "evidence": [], "workspace": None
                         } for lesson in lessons}}
                _atomic_json(PROGRESS, value)
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
    _atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


def _atomic_bytes(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, delete=False, suffix=".tmp") as handle:
            name = handle.name
            handle.write(data)
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


def _record_verification(state, verification, **where):
    """Keep tool results apart from the learner's confirmation, including checks that could not run."""
    if not verification:
        return
    passed = [i["label"] + "：" + i["detail"] for i in verification["items"] if i["ok"]]
    unknown = [i["label"] + "：" + i["detail"] for i in verification["items"] if i["ok"] is None and i["required"]]
    if passed:
        state["evidence"].append({"at": now(), "kind": "tool_verified", **where,
                                  "summary": "自動檢查練習檔：" + "；".join(passed)})
    if unknown:
        state["evidence"].append({"at": now(), "kind": "tool_unverified", **where,
                                  "summary": "必要檢查無法執行，未經工具驗證：" + "；".join(unknown)})


def mutate(payload, *, actor="learner", verification=None):
    lesson_id = payload.get("lessonId")
    lesson = lesson_by_id(lesson_id)
    action = payload.get("action")
    if action not in {"activate_lesson", "confirm_step", "add_note", "confirm_challenge", "record_physical"}:
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
            # Any step may be confirmed in any order; each one is still recorded only once.
            step_id = payload.get("stepId")
            ids = [step["id"] for step in lesson["steps"]]
            if step_id not in ids:
                raise StateError("找不到這個操作步驟。")
            if step_id in state["completedSteps"]:
                raise StateError("這一步已經記錄過，請重新整理。", 409)
            state["completedSteps"] = [key for key in ids if key in set(state["completedSteps"]) | {step_id}]
            state["evidence"].append({"at": now(), "kind": "user_confirmed", "stepId": step_id,
                                      "summary": "學員確認已完成此步驟；不等同工具自動驗證。"})
            _record_verification(state, verification, stepId=step_id)
            remaining = [key for key in ids if key not in state["completedSteps"]]
            state["status"] = "in_progress" if remaining else "completed"
            state.setdefault("startedAt", now())
            state["currentStep"] = remaining[0] if remaining else ids[-1]
            if not remaining:
                state["completedAt"] = now()
            after["activeLesson"] = lesson_id
            event["stepId"] = step_id
        elif action == "confirm_challenge":
            if not lesson.get("challenge"):
                raise StateError("本課沒有練習題。")
            if state.get("challenge"):
                raise StateError("本課練習題已經記錄過，請重新整理。", 409)
            state["challenge"] = {"confirmedAt": now(), "actor": actor}
            state["evidence"].append({"at": now(), "kind": "challenge_confirmed",
                                      "summary": "學員確認已完成本課練習題；不計入主線步驟。"})
            _record_verification(state, verification, challenge=True)
            event["challenge"] = True
        elif action == "record_physical":
            # Physical work (fabrication, soldering, power-on) is only ever reported, never inferred.
            item = payload.get("item")
            known = {entry["id"] for entry in lesson.get("physicalResults", [])}
            if item not in known:
                raise StateError("本課沒有這個實體結果項目。")
            note = payload.get("note", "")
            if not isinstance(note, str) or not note.strip() or len(note) > 2000:
                raise StateError("請填寫 1～2000 字的實際結果，例如板廠、日期、量測或照片位置。")
            state.setdefault("physical", {})[item] = {"at": now(), "actor": actor, "note": note.strip()}
            state["evidence"].append({"at": now(), "kind": "physical_reported", "item": item,
                                      "summary": note.strip()})
            event.update({"item": item, "note": note.strip()})
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
        relative = f"learning/workspaces/{lesson_id}"
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


def _kicad_locks(folder: Path):
    """KiCad writes ~<file>.lck next to a design while it is open."""
    return sorted({item.name for item in folder.rglob("*.lck") if item.name.startswith("~")})


def _reset_path(relative: str):
    """Reject links/junctions, including aliases to original course material."""
    path = safe_path(relative)
    if path != (REPO / relative).absolute():
        raise StateError("重置路徑含有符號連結或目錄接合點，已停止以保護原檔案。", 500)
    return path


def reset(payload, *, actor="learner", ignore_locks=False):
    """Return one lesson (or every lesson) to its first step.

    The practice copy and the lesson's recorded progress are archived under
    learning/archive/ instead of being deleted, so a repeat attempt never costs
    earlier work. The original course folders are never touched.
    """
    scope = payload.get("lessonId")
    if payload.get("confirm") is not True:
        raise StateError("重新開始前需要先確認；進度與練習副本會封存，不會直接刪除。")
    lessons = catalog()["lessons"]
    if scope == "all":
        targets = lessons
    else:
        targets = [lesson_by_id(scope)]
    with locked():
        before = progress()
        if type(payload.get("expectedRevision")) is not int or payload["expectedRevision"] != before["revision"]:
            raise StateError("進度已有更新，請重新整理後再重新開始。", 409)
        plan = []
        for lesson in targets:
            lesson_id = lesson["id"]
            current = before["lessons"][lesson_id]
            managed = f"learning/workspaces/{lesson_id}"
            folder = None
            if current.get("workspace") == managed:
                candidate = _reset_path(managed)
                if candidate.is_symlink() or (candidate.exists() and not candidate.is_dir()):
                    raise StateError(f"第 {lesson_id} 課的練習副本位置不正確，已停止。", 500)
                if candidate.exists():
                    folder = candidate
            if folder is not None and not ignore_locks:
                locks = _kicad_locks(folder)
                if locks:
                    raise StateError(
                        f"第 {lesson_id} 課的練習專案似乎還在 KiCad 開著（{'、'.join(locks)}）。"
                        "請先在 KiCad 選「檔案 → 關閉專案」，再重新開始。"
                        "請確認所有相關的 KiCad 管理員、原理圖與 PCB 編輯器都已關閉。"
                        f"若已全部關閉，可能是上次異常結束留下的鎖定檔，"
                        f"可到 {managed}/ 刪除檔名以 ~ 開頭、副檔名為 .lck 的檔案後再試。", 423)
            touched = (folder is not None or current.get("workspace") or current["status"] != "not_started"
                       or current.get("completedSteps") or current.get("notes") or current.get("evidence"))
            if touched:
                plan.append((lesson, current, folder))
        if not plan:
            raise StateError("沒有需要重新開始的內容。")

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        archive_root = _reset_path("learning/archive")
        progress_before = PROGRESS.read_bytes()
        events_path = LEARNING / "events.jsonl"
        events_before = events_path.read_bytes() if events_path.exists() else None
        moved, created, archived = [], [], []
        try:
            for lesson, current, folder in plan:
                destination = archive_root / f"{lesson['id']}-{stamp}"
                suffix = 1
                while destination.exists():
                    suffix += 1
                    destination = archive_root / f"{lesson['id']}-{stamp}-{suffix}"
                destination.mkdir(parents=True)
                created.append(destination)
                _atomic_bytes(destination / "progress-full-before.json", progress_before)
                _atomic_json(destination / "progress-before.json",
                             {"lessonId": lesson["id"], "archivedAt": now(), "state": current})
                if folder is not None:
                    os.rename(folder, destination / "workspace")
                    moved.append((destination / "workspace", folder))
                archived.append(destination.relative_to(REPO).as_posix())
            after = json.loads(json.dumps(before))
            for lesson, _current, _folder in plan:
                after["lessons"][lesson["id"]] = {
                    "status": "not_started", "currentStep": lesson["steps"][0]["id"],
                    "completedSteps": [], "notes": "", "evidence": [], "workspace": None}
            if scope == "all":
                after["activeLesson"] = lessons[0]["id"]
                after["skillsPracticed"] = []
            _save(before, after, {"action": "reset_all" if scope == "all" else "reset_lesson",
                                  "lessonId": scope, "actor": actor, "archived": archived})
        except Exception as exc:
            recovery_errors = []
            try:
                if PROGRESS.read_bytes() != progress_before:
                    _atomic_bytes(PROGRESS, progress_before)
                if events_before is None:
                    events_path.unlink(missing_ok=True)
                elif events_path.read_bytes() != events_before:
                    _atomic_bytes(events_path, events_before)
            except OSError as recovery:
                recovery_errors.append(f"進度或事件回復失敗：{recovery}")
            for source, original in reversed(moved):
                try:
                    if original.exists():
                        raise OSError(f"原練習路徑已有檔案：{original}")
                    os.rename(source, original)
                except OSError as recovery:
                    recovery_errors.append(f"副本仍保留於 {source.relative_to(REPO).as_posix()}：{recovery}")
            if not recovery_errors:
                for destination in reversed(created):
                    try:
                        # Only remove our metadata, never recursively delete a failed archive.
                        for name in ("progress-before.json", "progress-full-before.json"):
                            (destination / name).unlink(missing_ok=True)
                        destination.rmdir()
                    except OSError as recovery:
                        recovery_errors.append(f"封存目錄保留：{destination.relative_to(REPO).as_posix()}：{recovery}")
            if recovery_errors:
                locations = "、".join(path.relative_to(REPO).as_posix() for path in created)
                raise StateError(f"重新開始沒有完成；部分回復未完成，請勿再次重置。"
                                 f"封存與進度備份保留於 {locations}。"
                                 + "；".join(recovery_errors), 500) from exc
            if isinstance(exc, StateError):
                raise
            raise StateError(f"重新開始沒有完成，已還原原本的進度與練習副本：{exc}", 500) from exc
        return {"progress": after, "archived": archived}


def migrate_intro_workspace(expected_revision):
    """Preserve a legacy learner design before restoring the upstream source."""
    with locked():
        before = progress()
        if expected_revision != before["revision"]:
            raise StateError("進度已有更新，請重新讀取後再遷移。", 409)
        lesson = lesson_by_id("01")
        previous = before["lessons"]["01"].get("workspace")
        if previous != lesson["folder"]:
            raise StateError("本機沒有需要遷移的舊版第 1 課工作路徑。")
        source = safe_path(previous)
        relative = "learning/workspaces/01"
        target = safe_path(relative)
        if target.exists():
            raise StateError("第 1 課副本已存在；為保留成果，請先核對，不自動覆蓋。")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, ignore=shutil.ignore_patterns("*.lck", ".history", "*-backups"))
        _atomic_json(target / "learning-workspace.json", {
            "lessonId": "01", "source": previous, "createdAt": now(), "migration": "legacy-intro"})
        after = json.loads(json.dumps(before))
        after["lessons"]["01"]["workspace"] = relative
        _save(before, after, {"action": "migrate_intro_workspace", "lessonId": "01", "path": relative, "actor": "tutor"})
        return after


def open_target(lesson_id, target):
    lesson = lesson_by_id(lesson_id)
    state = progress()["lessons"][lesson_id]
    relative = state.get("workspace")
    expected = f"learning/workspaces/{lesson_id}"
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
