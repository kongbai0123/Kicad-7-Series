"""Verify the author SMD repair in a temporary copy using KiCad's bundled Python.

Windows: & 'C:/Program Files/KiCad/10.0/bin/python.exe' learning/verify_smd_repair.py
Add --write-report to refresh docs/examples/smd-repair-verification.json.
No board is saved outside TemporaryDirectory; learner projects are never opened.
"""
from collections import Counter
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / '15_PCB_Optimization'
EXPECTED = {
    'Schematics.kicad_pcb': 'b38e9e7ad409ddbe2fde293d19a7c98da6d513192349ff030ce1596cfe3b602c',
    'Schematics.kicad_sch': '161de9ca599b81c089d9268ae0e4e023018846c15a00faba803f95820b02479f',
    'Schematics.kicad_pro': '70f34d92bad37d883d2f8d0a07e4d363bc3b8fe0e172fcc022db4208931a14c1',
}
POSITIONS = {
    'MIC': ((223.9772, 95.1942, 0), (220.9772, 104.1942, 180)),
    'LDR': ((221.2594, 95.1434, 0), (220.2594, 91.1434, 180)),
    'LED': ((218.44, 95.24, 0), (219.94, 95.24, 0)),
}


def digest(path):
    return hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest()


def drc(cli, folder, output):
    result = subprocess.run([str(cli), 'pcb', 'drc', '--format', 'json', '--severity-all',
                             '--schematic-parity', '-o', str(output), str(folder / 'Schematics.kicad_pcb')],
                            capture_output=True, timeout=120)
    if result.returncode not in (0, 5) or not output.is_file():
        raise RuntimeError('KiCad DRC did not produce its report.')
    data = json.loads(output.read_text(encoding='utf-8'))
    violations = data.get('violations', [])
    parity = data.get('schematic_parity', [])
    return {
        'boardErrors': sum(v['severity'] == 'error' for v in violations),
        'boardErrorsByType': dict(Counter(v['type'] for v in violations if v['severity'] == 'error')),
        'boardWarnings': sum(v['severity'] == 'warning' for v in violations),
        'boardWarningsByType': dict(Counter(v['type'] for v in violations if v['severity'] == 'warning')),
        'unconnected': len(data.get('unconnected_items', [])),
        'schematicParityItems': len(parity),
        'schematicParityByType': dict(Counter(v['type'] for v in parity)),
    }


def verify():
    # Route wx diagnostics to stderr so a missing author font never opens a modal dialog.
    import wx
    wx.DisableAsserts()
    app = wx.App(False)
    wx.Log.SetActiveTarget(wx.LogStderr())
    import pcbnew
    cli = Path(pcbnew.__file__).resolve()
    cli = next((folder / 'kicad-cli.exe' for folder in cli.parents if (folder / 'kicad-cli.exe').is_file()), None)
    if cli is None:
        raise RuntimeError('Run this verifier with the installed KiCad bundled Python.')
    original = {name: digest(SOURCE / name) for name in EXPECTED}
    if original != EXPECTED:
        raise RuntimeError('The author source changed; review the repair and update its source hashes first.')
    result = {'source': '15_PCB_Optimization', 'sourceSha256NormalizedLf': original,
              'kicadVersion': pcbnew.GetBuildVersion(), 'boardOutlineChanged': False,
              'allTrackNetsPreserved': True, 'allPadNetsPreserved': True,
              'manufacturingReady': False,
              'scope': 'Board-level geometry/connectivity exercise only; schematic parity and warnings remain separate.'}
    with tempfile.TemporaryDirectory(prefix='kicad-smd-repair-') as raw:
        folder = Path(raw) / 'experiment'
        shutil.copytree(SOURCE, folder, ignore=shutil.ignore_patterns('*.zip', '*.lck', '*-backups'))
        result['before'] = drc(cli, folder, Path(raw) / 'before.json')
        if result['before']['boardErrorsByType'] != {'courtyards_overlap': 3, 'starved_thermal': 4}:
            raise RuntimeError('The baseline is no longer the seven-error exercise.')
        path = folder / 'Schematics.kicad_pcb'
        board = pcbnew.LoadBoard(str(path))
        traces = list(board.GetTracks())
        nets_before = [track.GetNetname() for track in traces]
        pad_nets_before = [(fp.GetReference(), pad.GetNumber(), pad.GetNetname())
                           for fp in board.GetFootprints() for pad in fp.Pads()]
        outline_before = [(shape.GetStart().x, shape.GetStart().y, shape.GetEnd().x, shape.GetEnd().y)
                          for shape in board.GetDrawings() if shape.GetLayer() == pcbnew.Edge_Cuts]
        moved, changed_pads, endpoints = [], [], []
        for ref, (before, after) in POSITIONS.items():
            matches = [fp for fp in board.GetFootprints() if fp.GetReference() == ref]
            if len(matches) != 1:
                raise RuntimeError(f'Expected exactly one author connector {ref}.')
            fp = matches[0]
            actual = (pcbnew.ToMM(fp.GetPosition().x), pcbnew.ToMM(fp.GetPosition().y), fp.GetOrientationDegrees())
            if any(abs(a-b) > .00001 for a, b in zip(actual, before)) or len(list(fp.Pads())) != 2:
                raise RuntimeError(f'The start geometry of {ref} changed.')
            old = [(pad.GetNumber(), pad.GetPosition(), pad.GetNetname()) for pad in fp.Pads()]
            fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(after[0]), pcbnew.FromMM(after[1])))
            fp.SetOrientationDegrees(after[2])
            new = {pad.GetNumber(): pad.GetPosition() for pad in fp.Pads()}
            for track in traces:
                if isinstance(track, pcbnew.PCB_VIA):
                    continue
                for pin, position, net in old:
                    for getter, setter, endpoint in ((track.GetStart, track.SetStart, 'start'),
                                                     (track.GetEnd, track.SetEnd, 'end')):
                        if getter() != position:
                            continue
                        if track.GetNetname() != net:
                            raise RuntimeError('An attached track has the wrong net; do not move it automatically.')
                        setter(new[pin])
                        endpoints.append({'reference': ref, 'pad': pin, 'net': net, 'endpoint': endpoint,
                                          'fromMm': [pcbnew.ToMM(position.x), pcbnew.ToMM(position.y)],
                                          'toMm': [pcbnew.ToMM(new[pin].x), pcbnew.ToMM(new[pin].y)]})
            moved.append({'reference': ref, 'fromMmDegrees': list(before), 'toMmDegrees': list(after)})
            for pad in fp.Pads():
                if (ref, pad.GetNumber()) in (('MIC', '1'), ('LDR', '2')):
                    if pad.GetNetname() != 'GND':
                        raise RuntimeError('The intended solid pad is no longer GND.')
                    pad.SetLocalZoneConnection(pcbnew.ZONE_CONNECTION_FULL)
                    changed_pads.append({'reference': ref, 'pad': pad.GetNumber(), 'net': 'GND', 'zoneConnection': 'solid'})
        assert [track.GetNetname() for track in traces] == nets_before
        assert [(fp.GetReference(), pad.GetNumber(), pad.GetNetname())
                for fp in board.GetFootprints() for pad in fp.Pads()] == pad_nets_before
        assert [(shape.GetStart().x, shape.GetStart().y, shape.GetEnd().x, shape.GetEnd().y)
                for shape in board.GetDrawings() if shape.GetLayer() == pcbnew.Edge_Cuts] == outline_before
        assert len(endpoints) == 4 and len(changed_pads) == 2
        board.BuildConnectivity()
        pcbnew.ZONE_FILLER(board).Fill(board.Zones())
        pcbnew.SaveBoard(str(path), board)
        result.update(movedConnectors=moved, solidPads=changed_pads, reattachedTrackEndpoints=endpoints)
        result['after'] = drc(cli, folder, Path(raw) / 'after.json')
        if result['after']['boardErrors'] or result['after']['unconnected']:
            raise RuntimeError('The repair failed its board-level DRC acceptance.')
    if {name: digest(SOURCE / name) for name in EXPECTED} != original:
        raise RuntimeError('Original author files changed during verification.')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-report', action='store_true')
    args = parser.parse_args()
    result = verify()
    text = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.write_report:
        # The only persistent output is the explicitly requested public verification summary.
        (REPO / 'docs/examples/smd-repair-verification.json').write_text(text, encoding='utf-8')
    print(text)
