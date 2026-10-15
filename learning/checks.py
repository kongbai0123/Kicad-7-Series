"""Automatic step checks that read the learner's saved KiCad files (stdlib only).

Every check works on the lesson's practice folder. ERC, DRC and BOM run on a
temporary copy, so the learner's open project is never written by the tool.
"""
from __future__ import annotations

import csv
from datetime import datetime
import glob
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

import state

TIMEOUT = 120

# KiCad 7 teaching files opened in KiCad 10 always raise these; explain instead of alarming.
EXPLAIN = {
    "lib_symbol_mismatch": ("教材符號與 KiCad 10 元件庫中的版本不同；教材以 KiCad 7 建立，練習時可忽略。", True),
    "lib_footprint_mismatch": ("PCB 上的封裝與 KiCad 10 元件庫中的版本不同；教材以 KiCad 7 建立，練習時可忽略。", True),
    "lib_footprint_issues": ("找不到封裝所屬的元件庫或封裝已改名，多半是 KiCad 7 與 10 的庫差異，練習時可忽略。", True),
    "footprint_link_issues": ("符號指定的封裝在目前元件庫找不到，多半是 KiCad 7 與 10 的庫差異；若是自己新指定的封裝，請核對名稱。", True),
    "invalid_outline": ("板框不存在或沒有封閉；PCB 尚未畫外框的課程會出現，這是正常的。", False),
    "pin_not_connected": ("有元件腳位沒有接線，也沒有放「不連接」標記。", False),
    "power_pin_not_driven": ("電源腳沒有電源來源；通常要在電源網路放 PWR_FLAG。", False),
    "endpoint_off_grid": ("線端或腳位不在格點上，容易造成看似相接其實沒接。", False),
    "unconnected_items": ("有同一網路的焊墊還沒用銅連起來。", False),
    "silk_edge_clearance": ("絲印太靠近板邊，可能被切掉。", False),
    "silk_over_copper": ("絲印壓到裸露銅墊，焊接時會被裁切或影響焊錫。", False),
    "courtyards_overlap": ("兩個元件的庭院（佔位範圍）重疊，實際可能裝不下。", False),
    "starved_thermal": ("覆銅與焊墊的散熱連接腳太少，連接可能不足。", False),
    "clearance": ("兩個不同網路的銅太近，違反間距規則。", False),
    "shorting_items": ("兩個不同網路的銅碰在一起，形成短路。", False),
}

LABELS = {
    "project_open": "KiCad 已開啟本課練習副本",
    "saved_since_start": "練習檔在建立副本後已儲存",
    "erc": "電氣規則檢查（ERC）沒有錯誤",
    "footprints": "每個要裝配的元件都有封裝",
    "drc": "設計規則檢查（DRC）沒有錯誤與未連接",
    "new_file": "已建立自己的新檔案",
    "new_zip": "本次輸出的 ZIP 內容完整",
}


def find_cli():
    env = os.environ.get("KICAD_CLI")
    if env and Path(env).is_file():
        return env
    roots = [os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"]
    found = sorted({p for root in roots for p in glob.glob(os.path.join(root, "KiCad", "*", "bin", "kicad-cli.exe"))},
                   key=lambda p: [int(x) if x.isdigit() else 0 for x in Path(p).parts[-3].split(".")])
    return found[-1] if found else shutil.which("kicad-cli")


def _workspace(lesson_id, progress):
    relative = progress["lessons"][lesson_id].get("workspace")
    if not relative:
        return None, None
    folder = state.safe_path(relative)
    meta = folder / "learning-workspace.json"
    created = None
    if meta.is_file():
        created = datetime.fromisoformat(state.read_json(meta)["createdAt"]).timestamp()
    return folder, created


def _design(lesson, folder, kind):
    project = next((f for f in lesson.get("files", []) if f["type"] == "project"), None)
    if not project:
        return None
    stem = Path(project["path"]).stem
    return folder / f"{stem}.kicad_{kind}"


def _item(kind, ok, detail, required, findings=None):
    return {"type": kind, "label": LABELS[kind], "ok": ok, "required": required,
            "detail": detail, "findings": findings or []}


def _run(cli, args):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    done = subprocess.run([cli, *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=TIMEOUT, creationflags=flags)
    return done.returncode


def _copy(folder):
    temp = Path(tempfile.mkdtemp(prefix="kicad-check-"))
    shutil.copytree(folder, temp / "w", ignore=shutil.ignore_patterns(
        "*.lck", "*-backups", ".history", "*.zip", "gerber", "Gerber*"))
    return temp


def _findings(violations):
    out = []
    for v in violations:
        explain, ignorable = EXPLAIN.get(v.get("type"), ("", False))
        out.append({"severity": v.get("severity", ""), "type": v.get("type", ""),
                    "description": v.get("description", ""), "explain": explain, "ignorable": ignorable})
    return out


def _summarize(findings):
    merged = {}
    for f in findings:
        key = (f["severity"], f["type"])
        if key in merged:
            merged[key]["count"] += 1
        else:
            merged[key] = {**f, "count": 1}
    order = {"error": 0, "warning": 1}
    return sorted(merged.values(), key=lambda f: (order.get(f["severity"], 2), f["type"]))


def check_erc(cli, sch):
    temp = _copy(sch.parent)
    try:
        out = temp / "erc.json"
        _run(cli, ["sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(temp / "w" / sch.name)])
        report = json.loads(out.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    found = _findings([v for sheet in report.get("sheets", []) for v in sheet.get("violations", [])])
    errors = sum(f["severity"] == "error" for f in found)
    warnings = len(found) - errors
    return errors == 0, f"錯誤 {errors}、警告 {warnings}。", _summarize(found)


def check_drc(cli, pcb):
    temp = _copy(pcb.parent)
    try:
        out = temp / "drc.json"
        _run(cli, ["pcb", "drc", "--format", "json", "--schematic-parity", "--severity-all",
                   "-o", str(out), str(temp / "w" / pcb.name)])
        report = json.loads(out.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    found = _findings(report.get("violations", []))
    unconnected = len(report.get("unconnected_items", []))
    errors = sum(f["severity"] == "error" for f in found)
    warnings = len(found) - errors
    if unconnected:
        found += _findings([{"severity": "error", "type": "unconnected_items",
                             "description": "Unconnected items"}] * unconnected)
    parity = []
    for v in report.get("schematic_parity", []):
        kind = v.get("type", "")
        refs = " ".join(i.get("description", "") for i in v.get("items", []))
        if kind == "footprint_symbol_field_mismatch":
            explain, ignorable = "PCB 與原理圖的說明或資料表欄位文字不同；KiCad 10 才開始比對，教材檔案可忽略。", True
        elif kind in ("extra_footprint", "duplicate_footprints") and "REF**" in refs:
            explain, ignorable = "PCB 上直接放的圖案或標誌（REF**），沒有對應的原理圖符號；作者有意放置，可忽略。", True
        else:
            explain, ignorable = "PCB 與原理圖不一致；回原理圖執行「從原理圖更新 PCB」。", False
        parity.append({"severity": "warning" if ignorable else "error", "type": "parity:" + kind,
                       "description": v.get("description", ""), "explain": explain, "ignorable": ignorable})
    found += parity
    real_parity = sum(not f["ignorable"] for f in parity)
    ok = errors == 0 and unconnected == 0 and real_parity == 0
    return ok, (f"錯誤 {errors}、警告 {warnings}、未連接 {unconnected}、與原理圖不一致 {real_parity}"
                f"（另有 {len(parity) - real_parity} 項可忽略的版本差異）。"), _summarize(found)


def check_footprints(cli, sch):
    temp = _copy(sch.parent)
    try:
        out = temp / "bom.csv"
        _run(cli, ["sch", "export", "bom", "--fields", "Reference,Footprint", "--labels", "Ref,Footprint",
                   "--ref-range-delimiter", "", "-o", str(out), str(temp / "w" / sch.name)])
        rows = list(csv.DictReader(out.read_text(encoding="utf-8-sig").splitlines()))
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    missing = [r["Ref"] for r in rows if not r.get("Footprint", "").strip()]
    if missing:
        return False, f"還沒有封裝：{', '.join(missing)}。", []
    return True, f"共 {len(rows)} 組元件都已指定封裝。", []


def run_step(lesson, step, progress):
    checks = step.get("checks") or []
    if not checks:
        return None
    folder, created = _workspace(lesson["id"], progress)
    items = []
    cli = None
    for spec in checks:
        kind, required = spec["type"], spec.get("required", True)
        if folder is None or not folder.is_dir():
            items.append(_item(kind, False, "還沒有建立本課練習副本；請先按「開啟練習專案」。", required))
            continue
        try:
            if kind == "project_open":
                design = _design(lesson, folder, spec.get("editor", "sch"))
                lock = design.with_name(f"~{design.name}.lck")
                ok = lock.is_file()
                items.append(_item(kind, ok, f"偵測到 {design.name} 正由 KiCad 開啟。" if ok else
                                   f"沒偵測到 KiCad 開啟 {design.name}；請確認開的是練習副本，且視窗沒有關閉。", required))
            elif kind == "saved_since_start":
                design = _design(lesson, folder, spec.get("editor", "sch"))
                if created is None:
                    items.append(_item(kind, None, "本課沒有副本建立時間，無法比較。", required))
                else:
                    ok = design.is_file() and design.stat().st_mtime > created + 1
                    items.append(_item(kind, ok, f"{design.name} 已在建立副本後儲存。" if ok else
                                       f"{design.name} 還是建立副本時的版本；請在編輯器按 Ctrl+S 儲存。", required))
            elif kind in ("erc", "drc", "footprints"):
                cli = cli or find_cli()
                if not cli:
                    items.append(_item(kind, None, "找不到 kicad-cli，無法自動檢查；請依畫面自行確認。", required))
                    continue
                design = _design(lesson, folder, "pcb" if kind == "drc" else "sch")
                runner = {"erc": check_erc, "drc": check_drc, "footprints": check_footprints}[kind]
                ok, detail, findings = runner(cli, design)
                items.append(_item(kind, ok, detail, required, findings))
            elif kind == "new_file":
                limit = created or 0
                hits = [p for p in folder.rglob(spec["glob"]) if p.stat().st_mtime > limit + 1]
                ok = len(hits) >= spec.get("min", 1)
                items.append(_item(kind, ok, ("找到：" + "、".join(p.name for p in hits[:5])) if ok else
                                   f"練習副本中還沒有新的 {spec['glob']} 檔案；請確認存到本課練習資料夾。", required))
            elif kind == "new_zip":
                limit = created or 0
                zips = sorted((p for p in folder.rglob("*.zip") if p.stat().st_mtime > limit + 1),
                              key=lambda p: p.stat().st_mtime)
                if not zips:
                    items.append(_item(kind, False, "練習副本中還沒有本次新建的 ZIP。", required))
                    continue
                with zipfile.ZipFile(zips[-1]) as archive:
                    names = archive.namelist()
                    broken = archive.testzip()
                lacking = [ext for ext in spec.get("must", []) if not any(n.lower().endswith(ext) for n in names)]
                ok = not lacking and broken is None
                detail = f"{zips[-1].name}：{len(names)} 個檔案。" + (f"缺少 {'、'.join(lacking)}。" if lacking else "") + \
                    (f"壓縮內容損壞：{broken}。" if broken else "")
                items.append(_item(kind, ok, detail, required))
            else:
                items.append(_item(kind, None, "未知的檢查類型。", False))
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, zipfile.BadZipFile) as exc:
            items.append(_item(kind, None, f"自動檢查沒有完成：{exc}", required))
    blocked = any(i["required"] and i["ok"] is False for i in items)
    return {"stepId": step["id"], "passed": not blocked, "ranAt": state.now(), "items": items}
