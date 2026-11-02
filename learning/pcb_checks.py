"""Read-only PCB and fabrication checks.

Board tokens follow https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/.
These checks establish saved-file consistency, never a fabricator's acceptance.
"""
from collections import Counter, defaultdict
from datetime import datetime
import fnmatch
import math
from pathlib import Path, PurePosixPath
import re
import zipfile


def parse(text):
    stack, root = [], []
    current = root
    for token in re.findall(r'"(?:\\.|[^"\\])*"|[()]|[^\s()]+', text):
        if token == '(':
            child = []
            current.append(child)
            stack.append(current)
            current = child
        elif token == ')':
            if not stack:
                raise ValueError('KiCad 檔案括號不完整。')
            current = stack.pop()
        else:
            current.append(re.sub(r'\\(["\\])', r'\1', token[1:-1]) if token.startswith('"') else token)
    if stack or len(root) != 1 or not isinstance(root[0], list):
        raise ValueError('KiCad 檔案格式不完整。')
    return root[0]


def children(node, name):
    return [item for item in node if isinstance(item, list) and item and item[0] == name]


def child(node, name, default=None):
    return next(iter(children(node, name)), default if default is not None else [name])


def board(path):
    result = parse(path.read_text(encoding='utf-8-sig'))
    if result[0] != 'kicad_pcb':
        raise ValueError('這不是 KiCad PCB 檔案。')
    return result


def fail(text):
    return {'severity': 'error', 'type': 'pcb', 'description': text,
            'explain': text, 'ignorable': False, 'count': 1}


def outcome(findings, success):
    return not findings, success if not findings else f'有 {len(findings)} 項需要修正。', findings


def settings(pcb, spec):
    data, findings = board(pcb), []
    copper = [item[1] for item in child(data, 'layers')[1:] if isinstance(item, list) and item[1].endswith('.Cu')]
    thick = float(child(child(data, 'general'), 'thickness', ['', '0'])[1])
    if len(copper) != spec.get('copperLayers', 2):
        findings.append(fail(f'銅層目前是 {len(copper)} 層；本課要求 {spec.get("copperLayers", 2)} 層。'))
    if abs(thick - spec.get('thickness', 1.6)) > 0.001:
        findings.append(fail(f'板厚目前是 {thick:g} mm；本課要求 {spec.get("thickness", 1.6):g} mm。'))
    stack = child(child(data, 'setup'), 'stackup')
    layers = children(stack, 'layer')
    if layers:
        total = sum(float(child(item, 'thickness', ['', '0'])[1]) for item in layers)
        if abs(total - thick) > .025:
            findings.append(fail(f'疊構各層厚度合計 {total:g} mm，和整板厚度 {thick:g} mm 不一致。'))
    return outcome(findings, f'{len(copper)} 層銅、板厚 {thick:g} mm；已核對儲存的板設定。')


def netclasses(project, spec):
    import json
    data = json.loads(project.read_text(encoding='utf-8-sig')).get('net_settings', {})
    classes = {item['name']: item for item in data.get('classes', [])}
    findings = []
    fields = {'clearance': 'clearance', 'trackWidth': 'track_width',
              'viaDiameter': 'via_diameter', 'viaDrill': 'via_drill'}
    for want in spec['classes']:
        have = classes.get(want['name'])
        if have is None:
            findings.append(fail(f'還沒有網路類別 {want["name"]}。'))
            continue
        for key, stored in fields.items():
            if key in want and abs(float(have.get(stored, -1)) - want[key]) > .0001:
                findings.append(fail(f'{want["name"]} 的 {stored} 應是 {want[key]} mm，目前是 {have.get(stored, "未設定")}。'))
        for net in want.get('nets', []):
            assigned = (data.get('netclass_assignments') or {}).get(net)
            matches = [item['netclass'] for item in data.get('netclass_patterns', [])
                       if fnmatch.fnmatchcase(net, item['pattern'])]
            if assigned != want['name'] and want['name'] not in matches:
                findings.append(fail(f'{net} 還沒有分配到 {want["name"]} 類別。'))
            # KiCad composes matching classes in priority order. Merely containing the required
            # class does not prove its numbers take effect; conservatively reject conflicting
            # explicit/pattern classes instead of pretending to reproduce that priority engine.
            matched_classes = set(matches)
            if assigned:
                matched_classes.update([assigned] if isinstance(assigned, str) else assigned)
            for other_name in sorted(matched_classes - {want['name']}):
                other = classes.get(other_name)
                if other is None:
                    findings.append(fail(f'{net} 另匹配到不存在的類別 {other_name}；請移除失效分配。'))
                    continue
                conflicts = [stored for key, stored in fields.items() if key in want and
                             other.get(stored) is not None and abs(float(other[stored]) - want[key]) > .0001]
                if conflicts:
                    findings.append(fail(f'{net} 同時匹配 {other_name}，其 {"、".join(conflicts)} 與 {want["name"]} 要求衝突；'
                                         '請移除或修正其他類別的萬用字元／明確分配，避免實際生效值被覆蓋。'))
    if spec.get('actualTrackWidths'):
        data = board(project.with_suffix('.kicad_pcb'))
        net_names = {item[1]: item[-1] for item in children(data, 'net')}
        tracks = children(data, 'segment') + children(data, 'arc')
        for net, width in spec['actualTrackWidths'].items():
            routed = [item for item in tracks if net_names.get(child(item, 'net')[-1], child(item, 'net')[-1]) == net]
            if not routed:
                findings.append(fail(f'{net} 還沒有可核對線寬的實際走線。'))
            elif any(abs(float(child(item, 'width')[1]) - width) > .0001 for item in routed):
                findings.append(fail(f'{net} 有實際銅線不是 {width:g} mm；更改網路類別不會自動改既有線段。'))
    return outcome(findings, '已核對網路類別的數值與指定網路。')


def outline(pcb, spec):
    data, edges, closed, findings = board(pcb), [], 0, []
    for item in data:
        if not isinstance(item, list) or not item or not item[0].startswith('gr_'):
            continue
        if child(item, 'layer', ['', ''])[1] != 'Edge.Cuts':
            continue
        if item[0] in ('gr_circle', 'gr_rect', 'gr_poly'):
            if item[0] in ('gr_circle', 'gr_rect'):
                start = child(item, 'center' if item[0] == 'gr_circle' else 'start')
                a = list(map(float, start[1:3]))
                b = list(map(float, child(item, 'end')[1:3]))
                if math.dist(a, b) < .1 or item[0] == 'gr_rect' and (abs(a[0]-b[0]) < .1 or abs(a[1]-b[1]) < .1):
                    findings.append(fail('Edge.Cuts 的圓或矩形尺寸為零或過小。'))
            closed += 1
        elif item[0] in ('gr_line', 'gr_arc'):
            edges.append(tuple(tuple(round(float(v), 3) for v in child(item, key)[1:3]) for key in ('start', 'end')))
        else:
            findings.append(fail('板框含目前無法核對的圖形；請使用線段、圓弧、圓或矩形。'))
    if edges:
        if len({frozenset(edge) for edge in edges}) != len(edges):
            # Two arcs may share endpoints when forming a circle; their midpoints distinguish them.
            # Do not reduce arcs to endpoint-only duplicates, only exact line segments below.
            lines = [item for item in data if isinstance(item, list) and item and item[0] == 'gr_line'
                     and child(item, 'layer', ['', ''])[1] == 'Edge.Cuts']
            keys = [frozenset(tuple(round(float(v), 3) for v in child(item, key)[1:3]) for key in ('start','end')) for item in lines]
            if len(set(keys)) != len(keys):
                findings.append(fail('Edge.Cuts 有重複線段；請刪除重疊的板框線。'))
        degree, adjacency = Counter(), defaultdict(set)
        for a, b in edges:
            degree.update((a, b))
            adjacency[a].add(b)
            adjacency[b].add(a)
        if any(count != 2 for count in degree.values()):
            findings.append(fail('Edge.Cuts 線段端點沒有兩兩相接；請修正缺口、分叉或重複線。'))
        else:
            unseen = set(degree)
            while unseen:
                todo = [unseen.pop()]
                while todo:
                    for point in adjacency[todo.pop()] & unseen:
                        unseen.remove(point)
                        todo.append(point)
                closed += 1
    if closed != 1:
        findings.append(fail(f'本課要求一個封閉外框，目前辨識到 {closed} 個；請檢查 Edge.Cuts。'))
    return outcome(findings, 'Edge.Cuts 有一個端點封閉的外框；交付前仍需 DRC 核對幾何有效性。')


def footprints(data):
    result = []
    for fp in children(data, 'footprint'):
        ref = next((p[2] for p in children(fp, 'property') if p[1] == 'Reference'), None)
        if ref is None:
            ref = next((p[2] for p in children(fp, 'fp_text') if p[1] == 'reference'), '')
        result.append((ref, fp))
    return result


def pad_nets(data):
    groups = defaultdict(set)
    for ref, fp in footprints(data):
        if not ref or ref == 'REF**':
            continue
        for pad in children(fp, 'pad'):
            net = child(pad, 'net')
            if pad[1] and len(net) > 1 and net[-1]:
                groups[net[-1]].add((ref, pad[1]))
    return set(frozenset(nodes) for nodes in groups.values())


def matches(pcb, parts, nets):
    data, findings = board(pcb), []
    actual = [(ref, fp[1]) for ref, fp in footprints(data) if ref and ref != 'REF**' and
              not ('board_only' in child(fp, 'attr') and not children(fp, 'pad'))]
    counts = Counter(ref for ref, _ in actual)
    expected = {p['ref']: p['footprint'] for p in parts if p.get('footprint')}
    have = dict(actual)
    for ref, footprint in expected.items():
        if counts[ref] != 1 or have.get(ref) != footprint:
            findings.append(fail(f'{ref} 的 PCB 封裝缺少、重複或與自己的原理圖不同；請從原理圖更新 PCB。'))
    for ref in have.keys() - expected.keys():
        findings.append(fail(f'PCB 的 {ref} 在自己的原理圖中沒有對應元件。'))
    relevant = set(expected)
    def restrict(groups):
        return {frozenset((ref, pin) for ref, pin in group if ref in relevant) for group in groups
                if any(ref in relevant for ref, _ in group)}
    if restrict(pad_nets(data)) != restrict(nets):
        findings.append(fail('PCB 焊墊的網路分組與自己的原理圖不同；請從原理圖更新 PCB，不能只看飛線數量。'))
    return outcome(findings, f'{len(expected)} 個 PCB 封裝與焊墊網路符合自己的原理圖；此項尚未檢查實際銅連通。')


def holes(data):
    result = []
    for _ref, fp in footprints(data):
        at = [float(x) for x in child(fp, 'at', ['', '0', '0'])[1:]]
        angle = math.radians(at[2] if len(at) > 2 else 0)
        for pad in children(fp, 'pad'):
            drill = child(pad, 'drill')
            size = [float(x) for x in drill[1:] if not isinstance(x, list) and x != 'oval']
            if not size or size[0] <= 0:
                continue
            xy = [float(x) for x in child(pad, 'at', ['', '0', '0'])[1:3]]
            x = at[0] + xy[0] * math.cos(angle) + xy[1] * math.sin(angle)
            y = at[1] - xy[0] * math.sin(angle) + xy[1] * math.cos(angle)
            result.append((x, y, size[0], pad[2] != 'np_thru_hole'))
    for via in children(data, 'via'):
        x, y = map(float, child(via, 'at')[1:3])
        result.append((x, y, float(child(via, 'drill')[1]), True))
    return result


LAYERS = {'Copper,L1,Top', 'Copper,L2,Bot', 'Soldermask,Top', 'Soldermask,Bot',
          'Legend,Top', 'Legend,Bot', 'Profile,NP'}


def _attribute(text, name):
    found = re.findall(r'TF\.' + re.escape(name) + r',([^*%\r\n]+)', text)
    return found[0].strip() if len(found) == 1 else None


def _gerber_points(text):
    fmt = re.search(r'%FSLAX\d(\d)Y\d(\d)\*%', text)
    if not fmt or '%MOMM*%' not in text or not re.search(r'M02\*\s*$', text):
        raise ValueError('Gerber 必須使用 KiCad 的公釐、絕對座標與完整檔尾。')
    if re.search(r'D0[13]\*', text) and not re.search(r'%ADD\d+[A-Z]', text):
        raise ValueError('Gerber 有繪圖命令，卻沒有完整的光圈定義。')
    x = y = None
    flashes = []
    for match in re.finditer(r'(?:X(-?\d+))?(?:Y(-?\d+))?D0([123])\*', text):
        if match[1] is not None:
            x = int(match[1]) / 10 ** int(fmt[1])
        if match[2] is not None:
            y = int(match[2]) / 10 ** int(fmt[2])
        if match[3] == '3' and x is not None and y is not None:
            flashes.append((x, y))
    return flashes


def _drill_points(text):
    if not text.startswith('M48') or 'METRIC' not in text or 'G90' not in text or not re.search(r'M30\s*$', text):
        raise ValueError('鑽孔請使用 Excellon、公釐、絕對座標與十進位格式重新輸出。')
    tools = {number: float(diameter) for number, diameter in re.findall(r'^T(\d+)C([\d.]+)', text, re.M)}
    tool, result = None, []
    for line in text.splitlines():
        if re.fullmatch(r'T\d+', line):
            tool = line[1:]
        if line.startswith('X'):
            match = re.fullmatch(r'X(-?\d+\.\d+)Y(-?\d+\.\d+)', line)
            if not match or tool not in tools:
                raise ValueError('鑽孔含未支援的槽孔或座標格式；請先人工核對並使用十進位圓孔教學設定。')
            result.append((float(match[1]), float(match[2]), tools[tool]))
    return result


def fabrication_zip(path, folder, pcb, since):
    """Check identities, current export bytes, holes and a common Gerber/drill origin."""
    findings, layers, drills, dates = [], {}, [], []
    data = board(pcb)
    sources = defaultdict(list)
    for candidate in folder.rglob('*'):
        if candidate.is_file() and candidate.suffix.lower() in ('.gbr', '.drl', '.gbrjob'):
            sources[candidate.name.casefold()].append(candidate)
    with zipfile.ZipFile(path) as archive:
        infos = [i for i in archive.infolist() if not i.is_dir()]
        if len(infos) > 100 or sum(i.file_size for i in infos) > 30_000_000:
            return outcome([fail('ZIP 內容超出本課製板輸出的合理大小。')], '')
        names = [PurePosixPath(i.filename) for i in infos]
        if len({str(n).casefold() for n in names}) != len(names) or len({n.name.casefold() for n in names}) != len(names):
            findings.append(fail('ZIP 有重複的檔名，可能混入兩次輸出。'))
        if any(n.is_absolute() or '..' in n.parts or '\\' in str(n) or n.suffix.lower() not in ('.gbr', '.drl', '.gbrjob') for n in names):
            findings.append(fail('ZIP 只能放本次 Gerber、鑽孔與 job 檔，不能放舊 ZIP、專案或其他內容。'))
        if findings:
            return outcome(findings, '')
        for info, name in zip(infos, names):
            raw = archive.read(info)
            candidates = sources[name.name.casefold()]
            current = [p for p in candidates if p.stat().st_mtime > since + 1 and
                       p.stat().st_mtime + 2 >= pcb.stat().st_mtime and p.read_bytes() == raw]
            if len(current) != 1:
                findings.append(fail(f'{name.name} 找不到唯一對應的本次輸出，或輸出早於目前 PCB；請重新輸出並重新打包。'))
                continue
            text = raw.decode('utf-8-sig')
            if name.suffix.lower() in ('.gbr', '.drl'):
                stamp = _attribute(text, 'CreationDate')
                try:
                    exported = datetime.fromisoformat(stamp).timestamp() if stamp else 0
                except ValueError:
                    exported = 0
                if exported <= since + 1 or exported + 2 < pcb.stat().st_mtime:
                    findings.append(fail(f'{name.name} 的輸出日期早於本課開始或目前 PCB；請重新輸出。'))
                dates.append(exported)
            if name.suffix.lower() == '.gbr':
                identity = _attribute(text, 'FileFunction')
                if identity not in LAYERS or identity in layers:
                    findings.append(fail(f'{name.name} 的 Gerber 層身份缺少、重複或不是本課七個必要層。'))
                    continue
                flashes = _gerber_points(text)
                if not identity.startswith('Legend') and not re.search(r'D0[13]\*', text):
                    findings.append(fail(f'{name.name} 只有標頭，沒有該層圖形內容。'))
                layers[identity] = (text, flashes)
            elif name.suffix.lower() == '.drl':
                identity = _attribute(text, 'FileFunction') or ''
                plating = False if identity.startswith('NonPlated,') else True if identity.startswith('Plated,') else None
                if plating is None:
                    findings.append(fail(f'{name.name} 沒有可辨識的 PTH／NPTH 身份；請分開輸出鍍孔與非鍍孔。'))
                else:
                    drills.append((plating, _drill_points(text)))
        missing = LAYERS - layers.keys()
        if missing:
            findings.append(fail('缺少 Gerber 層：' + '、'.join(sorted(missing)) + '。'))
        for key in ('ProjectId', 'SameCoordinates'):
            values = {_attribute(text, key) for text, _ in layers.values()}
            if None in values or len(values) != 1:
                findings.append(fail(f'Gerber 的 {key} 不一致或缺少，無法確認是同一專案與原點。'))
        if layers and any((_attribute(text, 'ProjectId') or '').split(',')[0] != pcb.stem for text, _ in layers.values()):
            findings.append(fail('Gerber 的專案名稱與本課 PCB 不符。'))
        if dates and max(dates) - min(dates) > 3600:
            findings.append(fail('Gerber 與鑽孔的輸出日期相差超過一小時，請由同一版 PCB 一起重新輸出。'))
        expected = holes(data)
        if not drills or len({p for p, _ in drills}) != len(drills):
            findings.append(fail('鑽孔檔缺少或 PTH／NPTH 檔重複。'))
        aux = child(child(data, 'setup'), 'aux_axis_origin', ['', '0', '0'])
        origins = [(0., 0.), tuple(map(float, aux[1:3]))]
        copper = [point for identity, (_, points) in layers.items() if identity.startswith('Copper,') for point in points]
        def close(a, b, size=False):
            return math.dist(a[:2], b[:2]) < .003 and (not size or abs(a[2] - b[2]) < .003)
        def aligned(origin):
            want = [(x-origin[0], -(y-origin[1]), d, plated) for x, y, d, plated in expected]
            got = [(x, y, d, plated) for plated, points in drills for x, y, d in points]
            # Keep multiplicities: duplicated or omitted holes must not disappear into a set.
            remaining = got[:]
            for hole in want:
                index = next((i for i, actual in enumerate(remaining) if actual[3] == hole[3] and close(hole, actual, True)), None)
                if index is None:
                    return False
                remaining.pop(index)
                if hole[3] and not any(close(hole, point) for point in copper):
                    return False
            return not remaining
        if not any(aligned(origin) for origin in origins):
            findings.append(fail('鑽孔孔數、孔徑或位置與目前 PCB／銅墊不符；請確認 Gerber 和鑽孔使用相同原點。'))
    return outcome(findings, f'{path.name}：七個 Gerber 層身份、本次輸出內容、鑽孔及原點合理一致；仍需 Gerber Viewer 人工疊圖與板廠審核。')
