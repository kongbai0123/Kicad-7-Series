"""Stage 2 invariants; every write and injected defect stays in a TemporaryDirectory."""
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
import uuid
from unittest import mock
import zipfile
from urllib.request import Request, urlopen

import checks
import pcb_checks as pcb
import state
import server
import test_learning

BOARD = '''(kicad_pcb (version 20260206) (generator "learning-tests")
 (general (thickness 1.6)) (layers (0 "F.Cu" signal) (2 "B.Cu" signal) (25 "Edge.Cuts" user))
 (setup (aux_axis_origin 5 6))
 (footprint "Test:Conn" (layer "F.Cu") (at 10 20)
  (property "Reference" "J1") (property "Value" "Conn")
  (pad "1" thru_hole rect (at 0 0) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask") (net "+3.3V"))
  (pad "2" thru_hole circle (at 2.54 0) (size 1.7 1.7) (drill 1) (layers "*.Cu" "*.Mask") (net "GND")))
 (gr_rect (start 8 18) (end 16 22) (stroke (width 0.05) (type default)) (fill none) (layer "Edge.Cuts")))'''


class PcbTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='kicad-pcb-test-')
        self.root = Path(self.temp.name)
        self.board = self.root / 'Test.kicad_pcb'
        self.board.write_text(BOARD, encoding='utf-8')
        os.utime(self.board, (time.time()-10, time.time()-10))

    def tearDown(self):
        self.temp.cleanup()

    def test_saved_settings_validate_copper_count_thickness_and_stackup(self):
        self.assertTrue(pcb.settings(self.board, {})[0])
        self.assertFalse(pcb.settings(self.board, {'copperLayers': 4})[0])
        self.assertFalse(pcb.settings(self.board, {'thickness': .8})[0])
        self.board.write_text(BOARD.replace('(setup ', '(setup (stackup (layer "F.Cu" (thickness 0.035))) '))
        self.assertFalse(pcb.settings(self.board, {})[0])

    def test_netclasses_validate_numbers_and_pattern_assignments(self):
        project = self.root / 'Test.kicad_pro'
        project.write_text(json.dumps({'net_settings': {'classes': [{'name': 'power', 'track_width': .5}],
          'netclass_patterns': [{'pattern': '+3.3V', 'netclass': 'power'}, {'pattern': 'GND', 'netclass': 'power'}]}}))
        spec = {'classes': [{'name': 'power', 'trackWidth': .5, 'nets': ['+3.3V', 'GND']}]}
        self.assertTrue(pcb.netclasses(project, spec)[0])
        spec['classes'][0]['trackWidth'] = .3
        self.assertFalse(pcb.netclasses(project, spec)[0])
        spec['classes'][0] = {'name': 'signal', 'nets': ['/LED']}
        self.assertFalse(pcb.netclasses(project, spec)[0])

    def test_netclass_setting_does_not_hide_wrong_existing_copper_width(self):
        project = self.root/'Test.kicad_pro'
        project.write_text(json.dumps({'net_settings': {'classes': [{'name':'power','track_width':.5}]}}))
        spec = {'classes':[{'name':'power','trackWidth':.5}], 'actualTrackWidths':{'+3.3V':.5}}
        self.assertFalse(pcb.netclasses(project,spec)[0])
        self.board.write_text(BOARD[:-1]+'(segment (start 10 20) (end 11 20) (width 0.25) (net "+3.3V")))')
        self.assertFalse(pcb.netclasses(project,spec)[0])
        self.board.write_text(self.board.read_text().replace('(width 0.25)','(width 0.5)'))
        self.assertTrue(pcb.netclasses(project,spec)[0])

    def test_conflicting_wildcard_class_cannot_override_required_power_values(self):
        self._conflicting_netclass('pattern')

    def test_conflicting_explicit_class_cannot_override_required_power_values(self):
        self._conflicting_netclass('explicit')

    def _conflicting_netclass(self, mode):
        project = self.root/'Test.kicad_pro'
        data = {'net_settings': {'classes': [{'name':'Default','track_width':.25},
            {'name':'signal','track_width':.3}, {'name':'DiyaPower','track_width':.5}],
            'netclass_patterns': [{'pattern':net,'netclass':'DiyaPower'} for net in ('+3.3V','GND')]}}
        spec = {'classes':[{'name':'DiyaPower','trackWidth':.5,'nets':['+3.3V','GND']}]}
        project.write_text(json.dumps(data))
        self.assertTrue(pcb.netclasses(project,spec)[0])
        if mode == 'pattern':
            data['net_settings']['netclass_patterns'].insert(0,{'pattern':'*','netclass':'signal'})
        else:
            data['net_settings']['netclass_assignments'] = {'+3.3V':'signal','GND':'signal'}
        project.write_text(json.dumps(data))
        ok,_detail,findings = pcb.netclasses(project,spec)
        self.assertFalse(ok)
        for net in ('+3.3V','GND'):
            self.assertTrue(any(net in f['description'] and 'signal' in f['description'] and '衝突' in f['description']
                                for f in findings),findings)

    def test_outline_rejects_missing_gap_duplicate_and_multiple_loops(self):
        self.assertTrue(pcb.outline(self.board, {})[0])
        edge = '(gr_line (start 0 0) (end 1 0) (layer "Edge.Cuts"))'
        self.board.write_text(BOARD[:BOARD.rfind('(gr_rect')] + edge + ')')
        self.assertFalse(pcb.outline(self.board, {})[0])
        rect = '(gr_rect (start 0 0) (end 1 1) (layer "Edge.Cuts"))'
        self.board.write_text(BOARD[:-1] + rect + ')')
        self.assertFalse(pcb.outline(self.board, {})[0])

    def test_pcb_is_compared_to_own_schematic_not_reference_board(self):
        parts = [{'ref': 'J1', 'footprint': 'Test:Conn'}]
        nets = [[('J1', '1')], [('J1', '2')]]
        self.assertTrue(pcb.matches(self.board, parts, nets)[0])
        self.assertFalse(pcb.matches(self.board, parts, [[('J1', '1'), ('J1', '2')]])[0])
        self.assertFalse(pcb.matches(self.board, [{'ref': 'J1', 'footprint': 'Wrong:Conn'}], nets)[0])

    def exports(self, origin=(0, 0)):
        date = datetime.now(timezone.utc).isoformat(timespec='seconds')
        header = f'%TF.CreationDate,{date}*%\n%TF.ProjectId,Test,uuid,rev*%\n%TF.SameCoordinates,Original*%\n'
        points = ''.join(f'X{round((x-origin[0])*1e6)}Y{round(-(y-origin[1])*1e6)}D03*\n' for x,y in ((10,20),(12.54,20)))
        for index, layer in enumerate(sorted(pcb.LAYERS)):
            text = header + f'%TF.FileFunction,{layer}*%\n%FSLAX46Y46*%\n%MOMM*%\n%ADD10C,1.7*%\nD10*\n' + points + 'M02*\n'
            (self.root / f'Test-{index}.gbr').write_text(text)
        drill = f'M48\n; #@! TF.CreationDate,{date}\n; #@! TF.FileFunction,Plated,1,2,PTH\nMETRIC\nT1C1.000\n%\nG90\nT1\n'
        drill += ''.join(f'X{x-origin[0]:.3f}Y{-(y-origin[1]):.3f}\n' for x,y in ((10,20),(12.54,20))) + 'M30\n'
        (self.root / 'Test-PTH.drl').write_text(drill)

    def pack(self, alter=None, extra=None):
        path = self.root / 'Test.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for source in sorted(self.root.glob('*')):
                if source.suffix in ('.gbr', '.drl'):
                    text = source.read_text()
                    archive.writestr(source.name, alter(source, text) if alter else source.read_bytes())
            if extra:
                archive.writestr(*extra)
        return path

    def check_zip(self, path):
        return pcb.fabrication_zip(path, self.root, self.board, 0)

    def test_zip_accepts_seven_identities_and_actual_drill_copper_alignment(self):
        self.exports()
        self.assertTrue(self.check_zip(self.pack())[0])

    def test_zip_accepts_consistent_auxiliary_origin(self):
        self.exports((5, 6))
        self.assertTrue(self.check_zip(self.pack())[0])

    def test_zip_rejects_missing_or_duplicate_layer_identity(self):
        self.exports()
        source = next(self.root.glob('*.gbr'))
        source.unlink()
        self.assertFalse(self.check_zip(self.pack())[0])
        self.exports()
        source = next(p for p in self.root.glob('*.gbr') if pcb._attribute(p.read_text(), 'FileFunction') != 'Copper,L1,Top')
        source.write_text(source.read_text().replace(pcb._attribute(source.read_text(), 'FileFunction'), 'Copper,L1,Top'))
        self.assertFalse(self.check_zip(self.pack())[0])

    def test_zip_rejects_nested_old_archives_or_duplicate_basename(self):
        self.exports()
        self.assertFalse(self.check_zip(self.pack(extra=('old/Gerber.zip', b'old')))[0])
        one = next(self.root.glob('*.gbr'))
        self.assertFalse(self.check_zip(self.pack(extra=('old/'+one.name, one.read_bytes())))[0])

    def test_zip_rejects_stale_or_changed_content_even_when_extension_is_right(self):
        self.exports()
        self.assertFalse(self.check_zip(self.pack(alter=lambda _p,t: t+'changed'))[0])
        for source in self.root.glob('*.gbr'):
            source.write_text(source.read_text().replace('TF.CreationDate,', 'TF.OldDate,'))
        self.assertFalse(self.check_zip(self.pack())[0])

    def test_zip_rejects_drill_origin_shift_missing_hole_and_wrong_tool(self):
        for old,new in [('X10.000Y-20.000','X11.000Y-20.000'), ('X10.000Y-20.000\n',''), ('T1C1.000','T1C0.800')]:
            self.exports()
            source = self.root / 'Test-PTH.drl'
            source.write_text(source.read_text().replace(old,new))
            self.assertFalse(self.check_zip(self.pack())[0], (old,new))


class SafeExperimentTests(unittest.TestCase):
    setUp = test_learning.LearningTests.setUp
    tearDown = test_learning.LearningTests.tearDown
    write = staticmethod(test_learning.LearningTests.write)

    def prepare(self):
        catalog = state.catalog()
        lesson = catalog['lessons'][1]
        lesson.update(practice={'project': 'diya', 'startFrom': None}, experiments=[
            {'id': 'thickness', 'kind': 'board-thickness', 'title': '板厚實驗'},
            {'id': 'diagnose', 'kind': 'drc-clearance', 'title': '診斷實驗'},
            {'id': 'library', 'kind': 'author-library', 'title': '作者診斷'}])
        self.write(state.LEARNING/'catalog.json', catalog)
        blank = state.LEARNING/'templates/diya-blank'
        blank.mkdir(parents=True)
        (blank/'User Interface.kicad_pro').write_text('{}')
        (blank/'User Interface.kicad_pcb').write_text(BOARD)
        result = state.workspace({'lessonId': '02', 'expectedRevision': 1})
        return state.safe_path(result['path'])

    def experiment(self, name):
        return state.experiment({'lessonId': '02', 'experimentId': name, 'expectedRevision': state.progress()['revision']})

    def test_experiments_never_overwrite_own_project_progress_or_prior_copies(self):
        source = self.prepare()
        before, design = state.PROGRESS.read_bytes(), (source/'User Interface.kicad_pcb').read_bytes()
        first, second = self.experiment('thickness'), self.experiment('thickness')
        self.assertNotEqual(first['path'], second['path'])
        self.assertEqual(state.PROGRESS.read_bytes(), before)
        self.assertEqual((source/'User Interface.kicad_pcb').read_bytes(), design)
        self.assertTrue(state.experiment_target('02', first['path']).is_file())
        self.assertFalse((state.safe_path(first['path'])/'learning-workspace.json').exists())

    def test_api_retains_created_experiment_path_when_windows_open_fails(self):
        self.prepare()
        before = state.PROGRESS.read_bytes()
        httpd = ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        thread = threading.Thread(target=httpd.serve_forever,daemon=True)
        thread.start()
        body = json.dumps({'lessonId':'02','experimentId':'thickness',
                           'expectedRevision':state.progress()['revision'],'open':True}).encode()
        try:
            with mock.patch.object(server.os,'startfile',side_effect=OSError('no association'),create=True):
                with urlopen(Request(f'http://127.0.0.1:{httpd.server_port}/api/experiment',data=body,
                                     headers={'Content-Type':'application/json'})) as response:
                    result = json.load(response)
            self.assertFalse(result['opened'])
            self.assertTrue(state.experiment_target('02',result['path']).is_file())
            self.assertEqual(state.PROGRESS.read_bytes(),before)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=3)

    def test_drc_injection_only_changes_independent_copy_and_records_defect(self):
        source = self.prepare()
        before = (source/'User Interface.kicad_pcb').read_bytes()
        result = self.experiment('diagnose')
        target = state.safe_path(result['path'])
        changed = pcb.board(target/'User Interface.kicad_pcb')
        self.assertEqual(len(pcb.children(changed, 'segment')), 1)
        self.assertEqual((source/'User Interface.kicad_pcb').read_bytes(), before)
        self.assertIn('shorting_items', state.read_json(target/'learning-experiment.json')['injected']['expectedTypes'])

    def test_author_diagnosis_uses_only_isolated_author_source(self):
        self.prepare()
        author = state.REPO/'13_Create_Custom_Library'
        author.mkdir()
        (author/'User Interface.kicad_pro').write_text('author project')
        result = self.experiment('library')
        copied = state.safe_path(result['path'])/'User Interface.kicad_pro'
        copied.write_text('experiment edit')
        self.assertEqual((author/'User Interface.kicad_pro').read_text(), 'author project')
        self.assertEqual(result['origin'], 'author:13_Create_Custom_Library')

    def test_lock_revision_and_wrong_experiment_paths_are_rejected(self):
        source = self.prepare()
        (source/'~User Interface.kicad_pcb.lck').write_text('open')
        with self.assertRaises(state.StateError):
            self.experiment('diagnose')
        with self.assertRaises(state.StateError):
            state.experiment({'lessonId':'02','experimentId':'thickness','expectedRevision':0})
        with self.assertRaises(state.StateError):
            state.experiment_target('02','13_Create_Custom_Library')

    def test_controlled_template_for_early_drc_and_arbitrary_source_is_refused(self):
        self.prepare()
        template = state.LEARNING/'templates/diya-routing-start'
        template.mkdir()
        (template/'User Interface.kicad_pro').write_text('{}')
        (template/'User Interface.kicad_pcb').write_text(BOARD)
        catalog = state.catalog()
        spec = catalog['lessons'][1]['experiments'][1]
        spec['source'] = 'learning/templates/diya-routing-start'
        self.write(state.LEARNING/'catalog.json',catalog)
        result = self.experiment('diagnose')
        self.assertTrue(result['origin'].startswith('template:'))
        self.assertIn('learning/templates/diya-routing-start',
                      (state.safe_path(result['path'])/'EXPERIMENT-README.txt').read_text(encoding='utf-8'))
        spec['source'] = 'learning/workspaces/diya'
        self.write(state.LEARNING/'catalog.json',catalog)
        with self.assertRaises(state.StateError):
            self.experiment('diagnose')

    def test_shared_late_save_failure_restores_progress_metadata_and_keeps_own_work(self):
        source = self.prepare()
        catalog = state.catalog()
        lesson = dict(catalog['lessons'][1], id='03', steps=[{'id':'03-01'}])
        catalog['lessons'].append(lesson)
        self.write(state.LEARNING/'catalog.json', catalog)
        progress = state.read_json(state.PROGRESS)
        progress['lessons']['03'] = state._fresh(lesson)
        self.write(state.PROGRESS, progress)
        before = state.PROGRESS.read_bytes()
        meta = (source/'learning-workspace.json').read_bytes()
        original_save = state._save
        def late_failure(*args, **kwargs):
            original_save(*args, **kwargs)
            raise OSError('event append failed after replace')
        with mock.patch.object(state, '_save', side_effect=late_failure):
            with self.assertRaises(state.StateError):
                state.workspace({'lessonId':'03','expectedRevision':progress['revision']})
        self.assertEqual(state.PROGRESS.read_bytes(), before)
        self.assertEqual((source/'learning-workspace.json').read_bytes(), meta)
        self.assertEqual((source/'User Interface.kicad_pcb').read_text(), BOARD)
        self.assertFalse((state.LEARNING/'workspaces/diya-snapshots/03').exists())

    def test_shared_failed_rollback_preserves_registered_project_snapshot_and_metadata(self):
        source = self.prepare()
        catalog = state.catalog()
        lesson = dict(catalog['lessons'][1], id='03', steps=[{'id':'03-01'}])
        catalog['lessons'].append(lesson)
        self.write(state.LEARNING/'catalog.json', catalog)
        progress = state.read_json(state.PROGRESS)
        progress['lessons']['03'] = state._fresh(lesson)
        self.write(state.PROGRESS, progress)
        save, atomic = state._save, state._atomic_bytes
        writes = []
        def late_failure(*args, **kwargs):
            save(*args, **kwargs)
            raise OSError('event append failed')
        def fail_restore(path, raw):
            if path == state.PROGRESS:
                writes.append(path)
                if len(writes) == 2:
                    raise OSError('restore refused')
            return atomic(path, raw)
        with mock.patch.object(state, '_save', side_effect=late_failure), mock.patch.object(state, '_atomic_bytes', side_effect=fail_restore):
            with self.assertRaisesRegex(state.StateError, '部分回復未完成'):
                state.workspace({'lessonId':'03','expectedRevision':progress['revision']})
        self.assertTrue((source/'User Interface.kicad_pcb').is_file())
        self.assertTrue((state.LEARNING/'workspaces/diya-snapshots/03/User Interface.kicad_pcb').is_file())
        self.assertIn('03',state.read_json(source/'learning-workspace.json')['lessons'])
        self.assertEqual(state.progress()['lessons']['03']['workspace'],'learning/workspaces/diya')


@unittest.skipUnless(checks.find_cli(), 'needs kicad-cli')
class RealDiagnosticTests(unittest.TestCase):
    def test_diya_real_cli_fabrication_zip_has_seven_layers_and_all_27_round_holes(self):
        """Exercise the exact KiCad export formats accepted by the saved-output ZIP check."""
        source = Path(__file__).resolve().parent/'templates/diya-routed-start'
        if not source.is_dir():
            self.skipTest('needs the public Diya routed rescue template')
        originals = {p.name:p.read_bytes() for p in source.iterdir() if p.is_file()}
        with tempfile.TemporaryDirectory(prefix='kicad-diya-zip-test-') as temp:
            copied = Path(temp)/'w'
            shutil.copytree(source,copied)
            design = copied/'Schematics.kicad_pcb'
            exported = copied/'gerber'
            exported.mkdir()
            cli = checks.find_cli()
            self.assertEqual(checks._run(cli,['pcb','export','gerbers','--layers',
                'F.Cu,B.Cu,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts',
                '--no-protel-ext','-o',str(exported)+os.sep,str(design)]),0)
            self.assertEqual(checks._run(cli,['pcb','export','drill','--excellon-separate-th',
                '--excellon-units','mm','--excellon-zeros-format','decimal',
                '-o',str(exported)+os.sep,str(design)]),0)
            self.assertEqual(len(list(exported.glob('*.gbr'))),7)
            self.assertEqual(len(list(exported.glob('*.drl'))),2)
            self.assertEqual(len(list(exported.glob('*.gbrjob'))),1)
            holes = pcb.holes(pcb.board(design))
            self.assertEqual(len(holes),27)
            self.assertEqual(sum(hole[3] for hole in holes),27)
            points = {pcb._attribute(p.read_text(encoding='utf-8-sig'),'FileFunction'):
                      pcb._drill_points(p.read_text(encoding='utf-8-sig')) for p in exported.glob('*.drl')}
            self.assertEqual(len(points['Plated,1,2,PTH']),27)
            self.assertEqual(points['NonPlated,1,2,NPTH'],[])
            package = copied/'Diya-manufacturing.zip'
            with zipfile.ZipFile(package,'w',zipfile.ZIP_DEFLATED) as archive:
                for file in sorted(exported.iterdir()):
                    if file.suffix.lower() in ('.gbr','.drl','.gbrjob'):
                        archive.write(file,file.name)
            since = min(time.time()-5,design.stat().st_mtime-5)
            ok,detail,findings = pcb.fabrication_zip(package,copied,design,since)
            self.assertTrue(ok,(detail,findings))
            with zipfile.ZipFile(package) as archive:
                self.assertEqual(len(archive.infolist()),10)
        self.assertEqual({p.name:p.read_bytes() for p in source.iterdir() if p.is_file()},originals)

    def test_injected_short_is_reported_by_real_drc_in_public_fixture_copy(self):
        public = Path(__file__).resolve().parent.parent/'docs/examples/led-board'
        with tempfile.TemporaryDirectory(prefix='kicad-drc-test-') as temp:
            copied = Path(temp)/'w'
            shutil.copytree(public, copied)
            board = copied/'Introduction.kicad_pcb'
            original = (public/'Introduction.kicad_pcb').read_bytes()
            lesson = {'id':'01','folder':'1_Introduction','files':[
                {'type':'project','path':'1_Introduction/Introduction.kicad_pro'}]}
            step = {'id':'01-connected','checks':[{'type':'pcb_connected'}]}
            progress = {'lessons':{'01':{'workspace':'learning/workspaces/01'}}}
            with mock.patch.object(checks,'_workspace',return_value=(copied,None)):
                self.assertTrue(checks.run_step(lesson,step,progress)['passed'])
            state._inject_drc(board)
            ok, detail, findings = checks.check_drc(checks.find_cli(), board)
            self.assertFalse(ok, detail)
            self.assertTrue({'shorting_items','clearance'} & {f['type'] for f in findings}, findings)
            with mock.patch.object(checks,'_workspace',return_value=(copied,None)):
                result = checks.run_step(lesson,step,progress)
            self.assertFalse(result['passed'],result)
            self.assertIs(result['items'][0]['ok'],False)
            self.assertTrue({'shorting_items','tracks_crossing'} &
                            {f['type'] for f in result['items'][0]['findings']},result)
            self.assertEqual((public/'Introduction.kicad_pcb').read_bytes(), original)
            # Repair by removing the injected change, then verify the teaching defect disappears.
            board.write_bytes(original)
            _ok, _detail, repaired = checks.check_drc(checks.find_cli(), board)
            self.assertFalse({'shorting_items','clearance'} & {f['type'] for f in repaired}, repaired)

    def test_custom_connector_real_cli_accepts_correct_supply_and_rejects_reversed_pins(self):
        """Extend an isolated Diya schematic with a project-local, embedded passive two-pin symbol."""
        import build_diya_data
        root = Path(__file__).resolve().parent.parent
        source = root/'learning/templates/diya-ready-pcb'
        name, lib = 'Diya_Test_1x02', 'Diya_Learning:Diya_Test_1x02'
        footprint = 'Diya_Learning:Diya_Test_1x02_P2.54mm'
        effects = '(effects (font (size 1.27 1.27)))'
        definition = f'''(symbol "{lib}" (pin_names (offset 1.016)) (in_bom yes) (on_board yes)
          (property "Reference" "J" (at 0 2.54 0) {effects})
          (property "Value" "{name}" (at 0 -5.08 0) {effects})
          (property "Footprint" "{footprint}" (at 0 0 0) {effects})
          (symbol "{name}_1_1"
           (pin passive line (at -5.08 0 0) (length 2.54) (name "VCC" {effects}) (number "1" {effects}))
           (pin passive line (at -5.08 -2.54 0) (length 2.54) (name "GND" {effects}) (number "2" {effects}))))'''
        with tempfile.TemporaryDirectory(prefix='kicad-connector-test-') as temp:
            target = Path(temp)/'w'
            shutil.copytree(source,target)
            schematic = target/'Schematics.kicad_sch'
            text = schematic.read_text(encoding='utf-8-sig')
            root_id = pcb.child(pcb.parse(text),'uuid')[1]
            for head,start,end in build_diya_data.spans(text):
                if head == 'lib_symbols':
                    text = text[:end-1] + '\n' + definition + '\n' + text[end-1:]
                    break
            placed = f'''(symbol (lib_id "{lib}") (at 50 150 0) (unit 1) (in_bom yes) (on_board yes)
              (uuid "{uuid.uuid4()}")
              (property "Reference" "J1" (at 50 147.46 0) {effects})
              (property "Value" "{name}" (at 50 155.08 0) {effects})
              (property "Footprint" "{footprint}" (at 50 150 0) {effects})
              (pin "1" (uuid "{uuid.uuid4()}")) (pin "2" (uuid "{uuid.uuid4()}"))
              (instances (project "Schematics" (path "/{root_id}" (reference "J1") (unit 1)))))'''
            def label(name,y):
                return f'(global_label "{name}" (shape input) (at 44.92 {y} 0) {effects} (uuid "{uuid.uuid4()}"))'
            good = text.rstrip()[:-1] + placed + label('+3.3V',150) + label('GND',152.54) + ')'
            schematic.write_text(good,encoding='utf-8')
            (target/'Diya_Learning.kicad_sym').write_text(
                '(kicad_symbol_lib (version 20211014) (generator "learning-test")'+definition.replace(f'(symbol "{lib}"',f'(symbol "{name}"',1)+')',encoding='utf-8')
            (target/'sym-lib-table').write_text('(sym_lib_table (version 7) (lib (name "Diya_Learning") (type "KiCad") (uri "${KIPRJMOD}/Diya_Learning.kicad_sym") (options "") (descr "Test only")))',encoding='utf-8')
            spec = {'lib':lib,'footprint':footprint,'pins':{'1':'+3.3V','2':'GND'}}
            ok,detail,findings = checks.check_connector(checks.find_cli(),schematic,target/'Schematics.kicad_pcb',spec)
            self.assertTrue(ok,(detail,findings))
            parts,_pins,nets = checks.read_netlist(checks.find_cli(),schematic)
            self.assertTrue(checks.compare_netlist(checks.load_spec('diya'),parts,nets,allow_extra=True)[0])
            catalog = json.loads((root/'learning/catalog.json').read_text(encoding='utf-8-sig'))
            covered = set()
            for lesson in catalog['lessons']:
                if not '03' <= lesson['id'] <= '15':
                    continue
                for entry in lesson['steps'] + ([lesson['challenge']] if lesson.get('challenge') else []):
                    for check in entry.get('checks',[]):
                        if check['type'] == 'netlist_matches' and check.get('spec') == 'diya':
                            with self.subTest(lesson=lesson['id'],step=entry.get('id','challenge')):
                                ok,detail,findings = checks.compare_netlist(checks.load_spec('diya'),parts,nets,
                                    check.get('parts'),check.get('allowExtra',False))
                                self.assertTrue(ok,(detail,findings))
                            covered.add((lesson['id'],entry.get('id','challenge')))
            self.assertTrue({('03','challenge'),('04','challenge')} <= covered,covered)
            extra_pin = f'(pin passive line (at -5.08 -5.08 0) (length 2.54) (name "EXTRA" {effects}) (number "3" {effects}))'
            variants = {
                'wrong_names':definition.replace('(name "VCC"','(name "WRONG_VCC"').replace('(name "GND"','(name "WRONG_GND"'),
                'wrong_types':definition.replace('(pin passive line','(pin input line'),
                'third_pin':definition[:-2]+extra_pin+definition[-2:],
            }
            library_path = target/'Diya_Learning.kicad_sym'
            library_good = library_path.read_text(encoding='utf-8')
            for variant,bad_definition in variants.items():
                with self.subTest(connector_definition=variant):
                    schematic.write_text(good.replace(definition,bad_definition),encoding='utf-8')
                    library_path.write_text('(kicad_symbol_lib (version 20211014) (generator "learning-test")'+
                        bad_definition.replace(f'(symbol "{lib}"',f'(symbol "{name}"',1)+')',encoding='utf-8')
                    ok,detail,findings = checks.check_connector(checks.find_cli(),schematic,target/'Schematics.kicad_pcb',spec)
                    self.assertFalse(ok,(detail,findings))
                    self.assertTrue(any(f['type']=='connector_definition' for f in findings),findings)
            schematic.write_text(good,encoding='utf-8')
            library_path.write_text(library_good,encoding='utf-8')
            connector_board = target/'Schematics.kicad_pcb'
            connector_board.write_text(BOARD.replace('Test:Conn',footprint),encoding='utf-8')
            ok,detail,findings = checks.check_connector(checks.find_cli(),schematic,connector_board,dict(spec,pcb=True))
            self.assertTrue(ok,(detail,findings))
            connector_board.write_text(connector_board.read_text().replace('(net "GND")','(net "+3.3V")'),encoding='utf-8')
            self.assertFalse(checks.check_connector(checks.find_cli(),schematic,connector_board,dict(spec,pcb=True))[0])
            bad = text.rstrip()[:-1] + placed + label('GND',150) + label('+3.3V',152.54) + ')'
            schematic.write_text(bad,encoding='utf-8')
            ok,_detail,findings = checks.check_connector(checks.find_cli(),schematic,target/'Schematics.kicad_pcb',spec)
            self.assertFalse(ok)
            self.assertEqual(len(findings),2,findings)


if __name__ == '__main__':
    unittest.main(verbosity=2)
