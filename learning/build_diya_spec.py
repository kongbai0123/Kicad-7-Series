"""Regenerate learning/diya-spec.json from the author's lesson 6 schematic.

Maintainers only; needs KiCad's kicad-cli. The page and the netlist check read the generated JSON,
so learners and CI never need this script. Labels and roles are written here by hand and checked
against the exported connections before anything is saved.
"""
import json
from pathlib import Path
import sys

import checks
import state

SOURCE = "6_Assign_Footprint"
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


def main():
    cli = checks.find_cli()
    if not cli:
        sys.exit("找不到 kicad-cli；請安裝 KiCad 或設定 KICAD_CLI。")
    sch = state.REPO / SOURCE / "Schematics.kicad_sch"
    parts, pins, nets = checks.read_netlist(cli, sch)
    refs = {part["ref"] for part in parts}
    assert refs == set(CURATED), f"教材元件與規格表不一致：{sorted(refs ^ set(CURATED))}"
    spec_parts = []
    for part in sorted(parts, key=lambda p: list(CURATED).index(p["ref"])):
        item = {"key": part["ref"], **{k: v for k, v in CURATED[part["ref"]].items() if k != "swap"},
                "lib": part["lib"], "value": part["value"], "footprint": part["footprint"],
                "pins": pins[part["lib"]]}
        if part["lib"] == "Device:R":
            item["ohms"] = checks.ohms(part["value"])
        if "swap" in CURATED[part["ref"]]:
            item["swap"] = CURATED[part["ref"]]["swap"]
        spec_parts.append(item)
    connected = [sorted(net) for net in nets if len(net) > 1]
    single = sorted(node for net in nets if len(net) == 1 for node in net)
    spec = {
        "schemaVersion": 1,
        "id": "diya",
        "title": "Reactive Diya 規格表",
        "source": {"folder": SOURCE, "file": "Schematics.kicad_sch",
                   "note": "由作者第 6 課原理圖以 kicad-cli 匯出 netlist 產生；元件用途為人工整理。"},
        "parts": spec_parts,
        "nets": [[{"part": ref, "pin": pin} for ref, pin in net] for net in sorted(connected)],
        "unconnected": [{"part": ref, "pin": pin} for ref, pin in single],
    }
    ok, detail, findings = checks.compare_netlist(spec, parts, nets)
    assert ok, (detail, findings)
    target = state.LEARNING / "diya-spec.json"
    target.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{target.relative_to(state.REPO).as_posix()}：{len(spec_parts)} 個元件、{len(connected)} 條連線；{detail}")


if __name__ == "__main__":
    main()
