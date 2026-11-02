"""Regenerate the Diya data derived from the author's files (maintainers only; needs KiCad's kicad-cli).

- learning/diya-spec.json: parts and connections from the author's lesson 6 schematic. Labels and roles
  are written here by hand; footprints use their KiCad 10 names.
- learning/templates/diya-before-erc/: the author's lesson 5 schematic without its PWR_FLAG symbols and
  no-connect flags, i.e. what a learner has after drawing the spec in lesson 3. It is the rescue start of
  lessons 4 and 5 and must still match the spec.
- learning/assets/lesson-03/diya-target-schematic.svg: a kicad-cli render of that schematic.

Every output is checked with kicad-cli before it is written; learners and CI only read the results.
"""
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

import checks
import state

SPEC_SOURCE = "6_Assign_Footprint"
ERC_SOURCE = "5_Perform_ERC"
CURATED = {
    "BT1": {"label": "鈕扣電池", "role": "供電；負極經 SW1 開關接到 GND。"},
    "SW1": {"label": "電源開關", "role": "接通或切斷電池負極與 GND；中間腳 B 是共用腳。", "swap": [["1", "3"]]},
    "U1": {"label": "ATtiny85 微控制器", "role": "讀取光感與聲音，控制 LED。"},
    "R1": {"label": "10k 電阻", "role": "和光敏電阻串成分壓，中點接 ATtiny85 第 7 腳（PB2）。", "swap": [["1", "2"]]},
    "R2": {"label": "光敏電阻", "role": "光線越亮電阻越小；另一端接 GND。", "swap": [["1", "2"]]},
    "R3": {"label": "5.6k 電阻", "role": "供電給麥克風，訊號接 ATtiny85 第 2 腳（PB3）。", "swap": [["1", "2"]]},
    "MK1": {"label": "麥克風", "role": "偵測聲音；以 2 腳排針連接。"},
    "D1": {"label": "LED", "role": "陽極接 ATtiny85 第 6 腳（PB1），由程式點亮。"},
    "R4": {"label": "470Ω 電阻", "role": "LED 的限流電阻，接在陰極與 GND 之間。", "swap": [["1", "2"]]},
}
# KiCad 10 renamed this footprint; description and pads (1, 2, 3 plus two mounting holes) are unchanged.
RENAMED_FOOTPRINTS = {
    "Button_Switch_THT:SW_CuK_OS102011MA1QN1_SPDT_Angled": "Button_Switch_THT:SW_Slide_SPDT_Angled_CK_OS102011MA1Q",
}
LEARNER_ERC_TYPES = {"power_pin_not_driven", "pin_not_connected", "lib_symbol_mismatch"}


def spans(text):
    """(head, start, end) of every direct child of the root S-expression."""
    depth, start, in_str, esc = 0, None, False, False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
            if depth == 2:
                start = i
        elif ch == ")":
            if depth == 2 and start is not None:
                yield re.match(r"\((\w+)", text[start:]).group(1), start, i + 1
                start = None
            depth -= 1


def without_flags(text):
    """Drop placed PWR_FLAG symbols, the short wires leading only to them, and no-connect flags.

    Everything else stays byte for byte, so the connections between real parts are unchanged.
    """
    flag = re.compile(r'\(symbol \(lib_id "power:PWR_FLAG"\) \(at ([\d.]+) ([\d.]+) ')
    flag_pins = {match.groups() for match in flag.finditer(text)}
    removed = {"PWR_FLAG": 0, "no_connect": 0, "wire": 0}

    def leads_to_flag(chunk):
        ends = re.match(r"\(wire \(pts \(xy ([\d.]+) ([\d.]+)\) \(xy ([\d.]+) ([\d.]+)\)\)", chunk)
        return bool(ends) and bool({ends.group(1, 2), ends.group(3, 4)} & flag_pins)

    for head, start, end in sorted(spans(text), key=lambda item: -item[1]):
        chunk = text[start:end]
        kind = ("PWR_FLAG" if head == "symbol" and flag.match(chunk)
                else "no_connect" if head == "no_connect"
                else "wire" if head == "wire" and leads_to_flag(chunk) else None)
        if kind:
            line_start = text.rfind("\n", 0, start)
            text = text[:line_start] + text[end:]
            removed[kind] += 1
    return text, removed


def erc_types(cli, sch):
    temp = Path(tempfile.mkdtemp(prefix="diya-erc-"))
    try:
        shutil.copytree(sch.parent, temp / "w")
        out = temp / "erc.json"
        checks._run(cli, ["sch", "erc", "--format", "json", "--severity-all", "-o", str(out), str(temp / "w" / sch.name)])
        report = state.read_json(out)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    found = {}
    for sheet in report.get("sheets", []):
        for violation in sheet.get("violations", []):
            key = (violation["severity"], violation["type"])
            found[key] = found.get(key, 0) + 1
    return found


def build_spec(cli):
    parts, pins, nets = checks.read_netlist(cli, state.REPO / SPEC_SOURCE / "Schematics.kicad_sch")
    refs = {part["ref"] for part in parts}
    assert refs == set(CURATED), f"教材元件與規格表不一致：{sorted(refs ^ set(CURATED))}"
    spec_parts = []
    for part in sorted(parts, key=lambda p: list(CURATED).index(p["ref"])):
        item = {"key": part["ref"], **{k: v for k, v in CURATED[part["ref"]].items() if k != "swap"},
                "lib": part["lib"], "value": part["value"],
                "footprint": RENAMED_FOOTPRINTS.get(part["footprint"], part["footprint"]), "pins": pins[part["lib"]]}
        if part["footprint"] in RENAMED_FOOTPRINTS:
            item["authorFootprint"] = part["footprint"]
        if part["lib"] == "Device:R":
            item["ohms"] = checks.ohms(part["value"])
        if "swap" in CURATED[part["ref"]]:
            item["swap"] = CURATED[part["ref"]]["swap"]
        spec_parts.append(item)
    library = Path(cli).resolve().parent.parent / "share" / "kicad" / "footprints"
    for item in spec_parts:
        lib, name = item["footprint"].split(":")
        assert (library / f"{lib}.pretty" / f"{name}.kicad_mod").is_file(), f"KiCad 10 找不到封裝 {item['footprint']}"
    connected = [sorted(net) for net in nets if len(net) > 1]
    spec = {
        "schemaVersion": 1,
        "id": "diya",
        "title": "Reactive Diya 規格表",
        "source": {"folder": SPEC_SOURCE, "file": "Schematics.kicad_sch",
                   "note": "由作者第 6 課原理圖以 kicad-cli 匯出 netlist 產生；元件用途為人工整理，封裝為 KiCad 10 名稱。"},
        "parts": spec_parts,
        "nets": [[{"part": ref, "pin": pin} for ref, pin in net] for net in sorted(connected)],
        "unconnected": [{"part": ref, "pin": pin} for net in nets if len(net) == 1 for ref, pin in net],
    }
    ok, detail, findings = checks.compare_netlist(spec, parts, nets)
    assert ok, (detail, findings)
    return spec, detail


def build_before_erc(cli, spec):
    source = state.REPO / ERC_SOURCE
    target = state.LEARNING / "templates" / "diya-before-erc"
    text, removed = without_flags((source / "Schematics.kicad_sch").read_text(encoding="utf-8"))
    assert removed["PWR_FLAG"] >= 1 and removed["no_connect"] >= 1, removed
    stage = Path(tempfile.mkdtemp(prefix="diya-before-erc-"))
    try:
        for suffix in ("pro", "pcb"):
            shutil.copy2(source / f"Schematics.kicad_{suffix}", stage / f"Schematics.kicad_{suffix}")
        (stage / "Schematics.kicad_sch").write_text(text, encoding="utf-8")
        ok, detail, findings = checks.check_netlist(cli, stage / "Schematics.kicad_sch", spec)
        assert ok, ("移除旗標後連線應仍符合規格表", detail, findings)
        errors = erc_types(cli, stage / "Schematics.kicad_sch")
        assert {kind for _severity, kind in errors} <= LEARNER_ERC_TYPES, errors
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(stage, target)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return removed, errors


# The drawing sits in a band of the A4 sheet; the page shows only that band (sheet millimetres).
TARGET_CROP = (52.0, 69.0, 184.0, 50.0)


def render_target(cli):
    out_dir = state.LEARNING / "assets" / "lesson-03"
    temp = Path(tempfile.mkdtemp(prefix="diya-svg-"))
    try:
        shutil.copytree(state.LEARNING / "templates" / "diya-before-erc", temp / "w")
        checks._run(cli, ["sch", "export", "svg", "--exclude-drawing-sheet", "--no-background-color",
                          "-o", str(temp / "svg"), str(temp / "w" / "Schematics.kicad_sch")])
        svg = next((temp / "svg").glob("*.svg")).read_text(encoding="utf-8")
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    x, y, width, height = TARGET_CROP
    svg, count = re.subn(r'width="[^"]*" height="[^"]*" viewBox="[^"]*"',
                         f'width="{width}mm" height="{height}mm" viewBox="{x} {y} {width} {height}"', svg, count=1)
    assert count == 1, "kicad-cli SVG header changed"
    target = out_dir / "diya-target-schematic.svg"
    target.write_text(svg, encoding="utf-8")
    return target


def main():
    cli = checks.find_cli()
    if not cli:
        sys.exit("找不到 kicad-cli；請安裝 KiCad 或設定 KICAD_CLI。")
    spec, detail = build_spec(cli)
    removed, errors = build_before_erc(cli, spec)
    (state.LEARNING / "diya-spec.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    target = render_target(cli)
    print(f"diya-spec.json：{detail}")
    print(f"templates/diya-before-erc：移除 {removed}；ERC {errors}")
    print(f"{target.relative_to(state.REPO).as_posix()}")


if __name__ == "__main__":
    main()
