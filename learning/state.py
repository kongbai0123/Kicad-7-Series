"""Single source of truth for the local KiCad learning project (stdlib only)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
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
    def __init__(self, message: str, status: int = 400, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


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
        if state.get("status") == "not_started" and not state.get("completedSteps") \
                and state.get("currentStep") not in step_ids:
            # A lesson not yet begun follows renumbered steps; begun lessons must still match exactly.
            state["currentStep"] = known[key]["steps"][0]["id"]
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


_COPY_IGNORE = shutil.ignore_patterns("*.lck", ".history", ".git", "*-backups", "*.kicad_prl")


def shared_project(lesson):
    """The learner's own project that this lesson continues, or None when the lesson has its own copy."""
    practice = lesson.get("practice") or {}
    key = practice.get("project")
    if key is None:
        return None
    if not isinstance(key, str) or not re.fullmatch(r"[a-z0-9-]+", key):
        raise StateError("共用練習專案名稱不正確。", 500)
    return {"key": key, "relative": f"learning/workspaces/{key}", "snapshots": f"learning/workspaces/{key}-snapshots",
            "startFrom": practice.get("startFrom"), "label": f"你的 {key.capitalize()} 練習專案"}


def workspace_path(lesson):
    shared = shared_project(lesson)
    return shared["relative"] if shared else f"learning/workspaces/{lesson['id']}"


def _chain(key):
    """Lessons that build on the same shared project, in course order."""
    return [lesson for lesson in catalog()["lessons"] if (shared_project(lesson) or {}).get("key") == key]


def _touched(current):
    return bool(current.get("workspace") or current["status"] != "not_started" or current.get("completedSteps")
                or current.get("notes") or current.get("evidence"))


def _fresh(lesson):
    return {"status": "not_started", "currentStep": lesson["steps"][0]["id"],
            "completedSteps": [], "notes": "", "evidence": [], "workspace": None}


def _meta_path(folder: Path):
    return folder / "learning-workspace.json"


def _read_meta(folder: Path):
    path = _meta_path(folder)
    return read_json(path) if path.is_file() else {}


def _stage_copy(source: Path, target: Path):
    """Copy beside the destination first, so a half-finished copy never appears as the target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    try:
        shutil.copytree(source, stage, dirs_exist_ok=True, ignore=_COPY_IGNORE)
        os.rename(stage, target)
    finally:
        if stage.exists():
            if not stage.resolve().is_relative_to(target.parent.resolve()):
                raise StateError("練習副本暫存路徑不正確，已停止清理。", 500)
            shutil.rmtree(stage)


def _discard_copy(path: Path):
    """Remove a copy this transaction just made; refuse anything outside the practice area."""
    root = (REPO / "learning" / "workspaces").resolve()
    resolved = path.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise OSError(f"拒絕移除練習區以外的路徑：{path}")
    shutil.rmtree(path)


def _shared_workspace(lesson, shared, payload, before):
    lesson_id, relative = lesson["id"], shared["relative"]
    target = _reset_path(relative)
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise StateError("共用練習專案的位置不正確，已停止。", 500)
    current = before["lessons"][lesson_id]
    rescue = payload.get("source") == "rescue"
    if current.get("workspace") == relative:
        if not target.is_dir():
            raise StateError(f"找不到{shared['label']}；請用「重新學習本課」或「改用作者起點」恢復。", 409,
                             "project_missing")
        if before["activeLesson"] == lesson_id:
            return {"progress": before, "path": relative, "created": False}
        after = json.loads(json.dumps(before))
        after["activeLesson"] = lesson_id
        _save(before, after, {"action": "prepare_workspace", "lessonId": lesson_id, "path": relative,
                             "actor": "learner"})
        return {"progress": after, "path": relative, "created": False}

    snapshot = _reset_path(f"{shared['snapshots']}/{lesson_id}")
    if snapshot.exists():
        raise StateError("本課的起點快照已存在，但進度顯示尚未開始；請由對話協助檢查。", 500)
    created = not target.exists()
    if not created and rescue:
        raise StateError(f"已經有{shared['label']}；要改用作者起點，請按「改用作者起點」，目前版本會先封存。", 409)
    if created:
        if shared["startFrom"] is None:
            source, origin = LEARNING / "templates" / f"{shared['key']}-blank", "blank"
        elif rescue:
            source, origin = safe_path(shared["startFrom"]), f"rescue:{shared['startFrom']}"
        else:
            raise StateError(f"還沒有{shared['label']}。可以回到前面的課從頭建立，或改用作者本課的起點接續。", 409,
                             "needs_project")
        if not source.is_dir():
            raise StateError("找不到建立練習專案的來源檔案。", 500)
        _stage_copy(source, target)
        meta = {"project": shared["key"], "createdAt": now(), "createdFrom": origin, "lessons": {}, "history": []}
    else:
        origin = "own"
        meta = _read_meta(target) or {"project": shared["key"], "createdAt": now(), "createdFrom": "unknown",
                                       "lessons": {}, "history": []}
    started = now()
    meta.setdefault("lessons", {})[lesson_id] = {"startedAt": started, "from": origin}
    meta.setdefault("history", []).append({"at": started, "lessonId": lesson_id, "action": "start", "from": origin})
    try:
        _atomic_json(_meta_path(target), meta)
        # The start snapshot is what "重新學習本課" returns to.
        _stage_copy(target, snapshot)
        after = json.loads(json.dumps(before))
        state = after["lessons"][lesson_id]
        state.update(workspace=relative, projectFrom=origin)
        if state["status"] == "not_started":
            state["status"] = "in_progress"
            state["startedAt"] = started
        after["activeLesson"] = lesson_id
        _save(before, after, {"action": "prepare_workspace", "lessonId": lesson_id, "path": relative,
                             "from": origin, "actor": "learner"})
    except Exception:
        # Undo only what this call created: the start snapshot and a brand-new project copy.
        if snapshot.exists():
            _discard_copy(snapshot)
        if created and target.exists():
            _discard_copy(target)
        raise
    return {"progress": after, "path": relative, "created": created}


def workspace(payload):
    lesson_id = payload.get("lessonId")
    lesson = lesson_by_id(lesson_id)
    with locked():
        before = progress()
        if type(payload.get("expectedRevision")) is not int or payload["expectedRevision"] != before["revision"]:
            raise StateError("進度已有更新，請重新整理後再開啟練習。", 409)
        shared = shared_project(lesson)
        if shared:
            return _shared_workspace(lesson, shared, payload, before)
        source = safe_path(lesson["folder"])
        relative = f"learning/workspaces/{lesson_id}"
        target = safe_path(relative)
        created = False
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            stage = Path(tempfile.mkdtemp(prefix=f".{lesson_id}-", dir=target.parent))
            try:
                shutil.copytree(source, stage, dirs_exist_ok=True, ignore=_COPY_IGNORE)
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


def _refuse_if_open(folder: Path, label: str, relative: str):
    locks = _kicad_locks(folder)
    if locks:
        raise StateError(
            f"{label}似乎還在 KiCad 開著（{'、'.join(locks)}）。"
            "請先在 KiCad 選「檔案 → 關閉專案」，再重新開始。"
            "請確認所有相關的 KiCad 管理員、原理圖與 PCB 編輯器都已關閉。"
            f"若已全部關閉，可能是上次異常結束留下的鎖定檔，"
            f"可到 {relative}/ 刪除檔名以 ~ 開頭、副檔名為 .lck 的檔案後再試。", 423)


class _Archive:
    """Moves into learning/archive/ plus fresh copies, all undone together if a later step fails."""

    def __init__(self):
        self.root = _reset_path("learning/archive")
        self.stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.progress_before = PROGRESS.read_bytes()
        self.events_path = LEARNING / "events.jsonl"
        self.events_before = self.events_path.read_bytes() if self.events_path.exists() else None
        self.created, self.moved, self.copies, self.archived = [], [], [], []

    def folder(self, name, record):
        destination = self.root / f"{name}-{self.stamp}"
        suffix = 1
        while destination.exists():
            suffix += 1
            destination = self.root / f"{name}-{self.stamp}-{suffix}"
        destination.mkdir(parents=True)
        self.created.append(destination)
        _atomic_bytes(destination / "progress-full-before.json", self.progress_before)
        _atomic_json(destination / "progress-before.json", record)
        self.archived.append(destination.relative_to(REPO).as_posix())
        return destination

    def move(self, source: Path, destination: Path):
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.rename(source, destination)
        self.moved.append((destination, source))

    def copy(self, source: Path, target: Path):
        _stage_copy(source, target)
        self.copies.append(target)

    def undo(self):
        errors = []
        try:
            if PROGRESS.read_bytes() != self.progress_before:
                _atomic_bytes(PROGRESS, self.progress_before)
            if self.events_before is None:
                self.events_path.unlink(missing_ok=True)
            elif self.events_path.read_bytes() != self.events_before:
                _atomic_bytes(self.events_path, self.events_before)
        except OSError as recovery:
            errors.append(f"進度或事件回復失敗：{recovery}")
        for target in reversed(self.copies):
            try:
                if target.exists():
                    _discard_copy(target)
            except OSError as recovery:
                errors.append(f"新建立的副本未能移除：{target.relative_to(REPO).as_posix()}：{recovery}")
        for source, original in reversed(self.moved):
            try:
                if original.exists():
                    raise OSError(f"原練習路徑已有檔案：{original}")
                os.rename(source, original)
            except OSError as recovery:
                errors.append(f"副本仍保留於 {source.relative_to(REPO).as_posix()}：{recovery}")
        if not errors:
            for destination in reversed(self.created):
                try:
                    # Only remove our metadata and emptied folders, never recursively delete a failed archive.
                    for name in ("progress-before.json", "progress-full-before.json"):
                        (destination / name).unlink(missing_ok=True)
                    if (destination / "snapshots").is_dir():
                        (destination / "snapshots").rmdir()
                    destination.rmdir()
                except OSError as recovery:
                    errors.append(f"封存目錄保留：{destination.relative_to(REPO).as_posix()}：{recovery}")
        return errors

    def fail(self, exc, verb):
        errors = self.undo()
        if errors:
            locations = "、".join(path.relative_to(REPO).as_posix() for path in self.created)
            raise StateError(f"{verb}沒有完成；部分回復未完成，請勿再次重置。"
                             f"封存與進度備份保留於 {locations}。" + "；".join(errors), 500) from exc
        if isinstance(exc, StateError):
            raise exc
        raise StateError(f"{verb}沒有完成，已還原原本的進度與練習副本：{exc}", 500) from exc


def _shared_plan(before, lesson, shared, everything, ignore_locks):
    """What a reset of a shared-project lesson touches: it and every later lesson built on the same project."""
    chain = _chain(shared["key"])
    start = 0 if everything else [item["id"] for item in chain].index(lesson["id"])
    affected = [item for item in chain[start:] if _touched(before["lessons"][item["id"]])]
    target = _reset_path(shared["relative"])
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        raise StateError("共用練習專案的位置不正確，已停止。", 500)
    snapshots = _reset_path(shared["snapshots"])
    if not affected:
        return None
    if target.exists() and not ignore_locks:
        _refuse_if_open(target, shared["label"], shared["relative"])
    restore = None
    if not everything:
        first = affected[0]["id"]
        if (snapshots / first).is_dir():
            restore = first
        elif any(_touched(before["lessons"][item["id"]]) for item in chain[:start]):
            raise StateError("找不到本課開始時的專案快照，無法只重設本課；請由對話協助檢查。", 500)
    return {"shared": shared, "affected": affected, "target": target, "snapshots": snapshots, "restore": restore}


def _archive_shared(archive, before, plan):
    destination = archive.folder(plan["shared"]["key"], {
        "project": plan["shared"]["key"], "archivedAt": now(),
        "lessons": {item["id"]: before["lessons"][item["id"]] for item in plan["affected"]}})
    if plan["target"].exists():
        archive.move(plan["target"], destination / "project")
    for item in plan["affected"]:
        snapshot = plan["snapshots"] / item["id"]
        if snapshot.exists():
            archive.move(snapshot, destination / "snapshots" / item["id"])
    if plan["restore"]:
        archive.copy(destination / "snapshots" / plan["restore"], plan["target"])


def reset(payload, *, actor="learner", ignore_locks=False):
    """Return one lesson (or every lesson) to its first step.

    The practice copy and the lesson's recorded progress are archived under
    learning/archive/ instead of being deleted, so a repeat attempt never costs
    earlier work. The original course folders are never touched. A lesson that
    continues a shared project also resets the later lessons built on it, and the
    project goes back to how it was when that lesson began.
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
        plan, shared_plans = [], {}
        for lesson in targets:
            lesson_id = lesson["id"]
            current = before["lessons"][lesson_id]
            shared = shared_project(lesson)
            if shared:
                if shared["key"] not in shared_plans:
                    entry = _shared_plan(before, lesson, shared, scope == "all", ignore_locks)
                    if entry:
                        shared_plans[shared["key"]] = entry
                continue
            managed = f"learning/workspaces/{lesson_id}"
            folder = None
            if current.get("workspace") == managed:
                candidate = _reset_path(managed)
                if candidate.is_symlink() or (candidate.exists() and not candidate.is_dir()):
                    raise StateError(f"第 {lesson_id} 課的練習副本位置不正確，已停止。", 500)
                if candidate.exists():
                    folder = candidate
            if folder is not None and not ignore_locks:
                _refuse_if_open(folder, f"第 {lesson_id} 課的練習專案", managed)
            if _touched(current):
                plan.append((lesson, current, folder))
        if not plan and not shared_plans:
            raise StateError("沒有需要重新開始的內容。")

        archive = _Archive()
        try:
            for lesson, current, folder in plan:
                destination = archive.folder(lesson["id"], {"lessonId": lesson["id"], "archivedAt": now(),
                                                            "state": current})
                if folder is not None:
                    archive.move(folder, destination / "workspace")
            for entry in shared_plans.values():
                _archive_shared(archive, before, entry)
            after = json.loads(json.dumps(before))
            for lesson, _current, _folder in plan:
                after["lessons"][lesson["id"]] = _fresh(lesson)
            for entry in shared_plans.values():
                for lesson in entry["affected"]:
                    after["lessons"][lesson["id"]] = _fresh(lesson)
            if scope == "all":
                after["activeLesson"] = lessons[0]["id"]
                after["skillsPracticed"] = []
            _save(before, after, {"action": "reset_all" if scope == "all" else "reset_lesson",
                                  "lessonId": scope, "actor": actor, "archived": archive.archived,
                                  "reset": [item["id"] for entry in shared_plans.values() for item in entry["affected"]]
                                  + [lesson["id"] for lesson, _current, _folder in plan]})
        except Exception as exc:
            archive.fail(exc, "重新開始")
        return {"progress": after, "archived": archive.archived}


def rescue(payload, *, actor="learner", ignore_locks=False):
    """Restart a shared-project lesson from the author's starting files; the learner's version is archived."""
    lesson = lesson_by_id(payload.get("lessonId"))
    shared = shared_project(lesson)
    if not shared or not shared["startFrom"]:
        raise StateError("本課沒有可以接續的作者起點。")
    if payload.get("confirm") is not True:
        raise StateError("改用作者起點前需要先確認；目前的版本會先封存，不會刪除。")
    source = safe_path(shared["startFrom"])
    if not source.is_dir():
        raise StateError("找不到作者的起點檔案。", 500)
    with locked():
        before = progress()
        if type(payload.get("expectedRevision")) is not int or payload["expectedRevision"] != before["revision"]:
            raise StateError("進度已有更新，請重新整理後再操作。", 409)
        chain = _chain(shared["key"])
        start = [item["id"] for item in chain].index(lesson["id"])
        affected = [item for item in chain[start:]
                    if item["id"] == lesson["id"] or _touched(before["lessons"][item["id"]])]
        target = _reset_path(shared["relative"])
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise StateError("共用練習專案的位置不正確，已停止。", 500)
        snapshots = _reset_path(shared["snapshots"])
        if target.exists() and not ignore_locks:
            _refuse_if_open(target, shared["label"], shared["relative"])
        previous = _read_meta(target) if target.exists() else {}
        archive = _Archive()
        try:
            entry = {"shared": shared, "affected": affected, "target": target, "snapshots": snapshots, "restore": None}
            if target.exists() or any((snapshots / item["id"]).exists() for item in affected):
                _archive_shared(archive, before, entry)
            started, origin = now(), f"rescue:{shared['startFrom']}"
            archive.copy(source, target)
            _atomic_json(_meta_path(target), {
                "project": shared["key"], "createdAt": started, "createdFrom": origin,
                "lessons": {lesson["id"]: {"startedAt": started, "from": origin}},
                "history": previous.get("history", []) + [
                    {"at": started, "lessonId": lesson["id"], "action": "rescue", "from": origin}]})
            archive.copy(target, snapshots / lesson["id"])
            after = json.loads(json.dumps(before))
            for item in affected:
                after["lessons"][item["id"]] = _fresh(item)
            after["lessons"][lesson["id"]].update(status="in_progress", startedAt=started,
                                                  workspace=shared["relative"], projectFrom=origin)
            after["activeLesson"] = lesson["id"]
            _save(before, after, {"action": "rescue_lesson", "lessonId": lesson["id"], "from": origin, "actor": actor,
                                  "archived": archive.archived, "reset": [item["id"] for item in affected]})
        except Exception as exc:
            archive.fail(exc, "改用作者起點")
        return {"progress": after, "archived": archive.archived}


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
    expected = workspace_path(lesson)
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
