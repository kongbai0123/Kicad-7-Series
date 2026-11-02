"""Integration of the shipped curriculum with isolated progress and learner-owned files."""
from pathlib import Path
import shutil
import tempfile
import unittest

import state


class ShippedChainTests(unittest.TestCase):
    def setUp(self):
        self.original = state.REPO, state.LEARNING, state.PROGRESS
        self.temp = tempfile.TemporaryDirectory(prefix='kicad-curriculum-chain-')
        self.root = Path(self.temp.name).resolve()
        self.learning = self.root / 'learning'
        self.learning.mkdir()
        for name in ('catalog.json', 'progress.template.json'):
            shutil.copy2(state.LEARNING / name, self.learning / name)
        shutil.copytree(state.LEARNING / 'templates/diya-blank', self.learning / 'templates/diya-blank')
        state.REPO, state.LEARNING, state.PROGRESS = self.root, self.learning, self.learning / 'progress.json'

    def tearDown(self):
        state.REPO, state.LEARNING, state.PROGRESS = self.original
        self.temp.cleanup()

    def start(self, lesson):
        return state.workspace({'lessonId': lesson, 'expectedRevision': state.progress()['revision']})

    def test_every_diya_lesson_keeps_the_same_learner_files_and_separate_start_snapshot(self):
        self.start('03')
        folder = self.learning / 'workspaces/diya'
        marker = folder / 'learner-notes.txt'
        marker.write_text('my schematic, not the author checkpoint', encoding='utf-8')
        for number in range(4, 16):
            lesson = f'{number:02}'
            with self.subTest(lesson=lesson):
                result = self.start(lesson)
                self.assertEqual(result['path'], 'learning/workspaces/diya')
                self.assertFalse(result['created'])
                self.assertEqual(marker.read_text(encoding='utf-8'), 'my schematic, not the author checkpoint')
                self.assertEqual(result['progress']['lessons'][lesson]['completedSteps'], [])
                snapshot = self.learning / f'workspaces/diya-snapshots/{lesson}'
                self.assertEqual((snapshot / marker.name).read_bytes(), marker.read_bytes())
                self.assertFalse((self.learning / f'workspaces/{lesson}').exists())

    def test_restarting_a_middle_lesson_restores_its_snapshot_and_only_resets_later_chain(self):
        self.start('03')
        marker = self.learning / 'workspaces/diya/learner-notes.txt'
        marker.write_text('before layout', encoding='utf-8')
        self.start('09')
        marker.write_text('after layout', encoding='utf-8')
        self.start('10')
        marker.write_text('after routing', encoding='utf-8')
        self.start('12')
        result = state.reset({'lessonId': '09', 'confirm': True,
                              'expectedRevision': state.progress()['revision']})
        self.assertEqual(marker.read_text(encoding='utf-8'), 'before layout')
        for lesson in ('09', '10', '12'):
            self.assertEqual(result['progress']['lessons'][lesson]['status'], 'not_started')
        self.assertEqual(result['progress']['lessons']['03']['status'], 'in_progress')
        archived = list((self.learning / 'archive').rglob('learner-notes.txt'))
        self.assertTrue(any(p.read_text(encoding='utf-8') == 'after routing' for p in archived))


if __name__ == '__main__':
    unittest.main()
