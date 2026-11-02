"""Maintainer-only: derive labelled rescue starts without touching learner workspaces."""
from pathlib import Path
import copy
import json
import shutil
import tempfile

import checks
import state
from build_diya_data import RENAMED_FOOTPRINTS, spans


def main():
    cli = checks.find_cli()
    if not cli:
        raise RuntimeError('KiCad CLI is required to verify derived rescue data.')
    sources = {'diya-ready-pcb': '6_Assign_Footprint', 'diya-board-configured': '6_Assign_Footprint',
               'diya-routing-start': '9_PCB_Layout', 'diya-routed-start': '11_PCB_DRC'}
    spec = checks.load_spec('diya')
    for name, source in sources.items():
        with tempfile.TemporaryDirectory(prefix='diya-rescue-build-') as temp:
            stage = Path(temp)
            for suffix in ('sch', 'pro', 'pcb'):
                origin = state.REPO / source / f'Schematics.kicad_{suffix}'
                if name in ('diya-ready-pcb', 'diya-board-configured') and suffix == 'pcb':
                    origin = state.REPO / '9_PCB_Layout/Schematics.kicad_pcb'
                text = origin.read_text(encoding='utf-8-sig')
                for old, new in RENAMED_FOOTPRINTS.items():
                    text = text.replace(old, new)
                if suffix == 'pcb':
                    for head, start, end in sorted(spans(text), key=lambda item: -item[1]):
                        chunk = text[start:end]
                        blank = name in ('diya-ready-pcb', 'diya-board-configured')
                        decorative = head == 'footprint' and '"REF**"' in chunk and '(pad ' not in chunk
                        if (blank and (head.startswith('gr_') or head in ('footprint','segment','arc','via','zone','group') or
                                       head == 'net' and not chunk.startswith('(net 0 '))) or decorative:
                            text = text[:start] + text[end:]
                    text = '\n'.join(line.rstrip() for line in text.splitlines()) + '\n'
                (stage / f'Schematics.kicad_{suffix}').write_text(text, encoding='utf-8')
            if name != 'diya-ready-pcb':
                project = stage / 'Schematics.kicad_pro'
                value = json.loads(project.read_text(encoding='utf-8'))
                settings = value['net_settings']
                power = copy.deepcopy(next((item for item in settings['classes'] if item['name'] == 'power'), settings['classes'][0]))
                power.update(name='DiyaPower', clearance=0.2, track_width=0.5, via_diameter=0.8, via_drill=0.4)
                settings['classes'] = [item for item in settings['classes'] if item['name'] not in ('power', 'DiyaPower')] + [power]
                for item in settings.get('netclass_patterns', []):
                    if item.get('netclass') == 'power':
                        item['netclass'] = 'DiyaPower'
                patterns = settings.setdefault('netclass_patterns', [])
                for net in ('+3.3V','GND'):
                    if not any(item.get('pattern') == net and item.get('netclass') == 'DiyaPower' for item in patterns):
                        patterns.append({'pattern': net, 'netclass': 'DiyaPower'})
                settings['netclass_assignments'] = {key: 'DiyaPower' if val == 'power' else val
                                                    for key, val in (settings.get('netclass_assignments') or {}).items()}
                project.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
            ok, detail, findings = checks.check_netlist(cli, stage / 'Schematics.kicad_sch', spec)
            if not ok:
                raise RuntimeError((name, detail, findings))
            parts, _, _ = checks.read_netlist(cli, stage / 'Schematics.kicad_sch')
            ok, detail, findings = checks.compare_footprints(spec, parts)
            if not ok:
                raise RuntimeError((name, detail, findings))
            target = state.LEARNING / 'templates' / name
            target.mkdir(exist_ok=True)
            for file in stage.iterdir():
                if file.is_file() and file.suffix in ('.kicad_sch', '.kicad_pro', '.kicad_pcb'):
                    shutil.copy2(file, target / file.name)
            (target / 'SOURCE.md').write_text(
                f'# Rescue start: {name}\n\nDerived from `{source}/` in pcbcupid/Kicad-7-Series (MIT). '
                'This is author-derived rescue data, never a learner achievement. '
                'The KiCad 10 switch footprint name is normalized; padless REF** decorative footprints are omitted to avoid duplicate-reference parity violations. '
                + ('The PCB keeps only the author lesson 9 board settings, with no components, copper, nets or outline. ' if name in ('diya-ready-pcb','diya-board-configured') else
                   'The author power class is renamed DiyaPower to match the lessons; geometry and routing remain author work. ')
                + ('The DiyaPower class and +3.3V/GND assignments are prepared for lesson 9. ' if name == 'diya-board-configured' else '')
                + 'Regenerate with `python learning/build_stage2_data.py`; schematic connectivity and footprints are checked before copying.\n', encoding='utf-8')
            print(name + ': verified schematic and footprints')


if __name__ == '__main__':
    main()
