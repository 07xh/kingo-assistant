"""Verify the GUI uses only offline evidence and doesn't alter real access controls."""
import unittest
from pathlib import Path
from unittest.mock import patch
from windows_gui import Application

class CatalogGuiTest(unittest.TestCase):
    def test_menu_exposes_offline_evidence_review(self):
        with patch('windows_gui.inspect_local_file', return_value={
            'status': 'ready_for_manual_review', 'matched_rows': 2,
            'reason_codes': ['STRUCTURE_MATCH_ONLY_NOT_API_VERIFICATION']}) as inspect, \
             patch('windows_gui.filedialog.askopenfilename', return_value='local-evidence.json'), \
             patch('windows_gui.messagebox.showinfo') as info:
            app = object.__new__(Application)
            app.worker = None
            app.status = type('Text', (), {'set': lambda self, x: None})()
            app.log = lambda text: None
            Application.review_catalog_evidence(app)
            inspect.assert_called_once_with(Path('local-evidence.json'))
            self.assertIn('人工复核', info.call_args.args[1])
            self.assertIn('不会自动将接口标记为已验证', info.call_args.args[1])

    def test_menu_blocks_during_active_monitor(self):
        with patch('windows_gui.filedialog.askopenfilename') as dialog, \
             patch('windows_gui.messagebox.showinfo'):
            app = object.__new__(Application)
            app.worker = object()
            Application.review_catalog_evidence(app)
            dialog.assert_not_called()

if __name__ == '__main__':
    unittest.main()
