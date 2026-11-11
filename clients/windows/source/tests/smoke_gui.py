"""Optional interactive-display smoke check (Linux: xvfb-run -a python tests/smoke_gui.py)."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows_gui import Application
from desktop_editor import SettingsEditor
from core.desktop_settings import load_config


def main():
    with tempfile.TemporaryDirectory() as tmp:
        with patch('windows_gui.app_dir', return_value=Path(tmp)):
            app = Application()
            app.withdraw()
            app.update()
            assert len(app.table.get_children()) == 2
            with patch('core.client.httpx.AsyncClient', side_effect=AssertionError('demo attempted network')):
                app.demo_next()
                assert app.table.set('target-B', 'status') == '虚构样例：已满'
                app.demo_next()
                assert '发现余量' in app.table.set('target-B', 'status')
                app.demo_next()
                assert app.table.set('target-B', 'status') == '虚构样例：已满'
                app.demo_next()  # Replays from snapshot 1, not a fourth fake sample.
                assert app.table.set('target-B', 'status') == '虚构样例：已满'
            app.check_config()  # Restores user's configured table, not fictional data.
            with patch('windows_gui.messagebox.showinfo') as preflight_dialog:
                app.preflight()
            assert '阻止启动' in preflight_dialog.call_args.args[1]
            with patch('windows_gui.messagebox.showwarning') as warning, \
                 patch('windows_gui.MonitorWorker', side_effect=AssertionError('network worker started')):
                app.start_monitor()
            warning.assert_called_once()
            assert app.worker is None
            assert '无法启动' in app.status.get()
            app.check_config()
            assert len(app.table.get_children()) == 2
            assert app.table.set('A', 'course') == 'FILL_FROM_OFFICIAL_PAGE'
            with patch('windows_gui.webbrowser.open', return_value=True) as web_open:
                app.open_official_portal()
                web_open.assert_called_once_with('https://jwmis.ncwu.edu.cn')
            editor = SettingsEditor(app, Path(app.path.get()), app.check_config)
            editor.withdraw()
            editor.update()
            editor.semester.set('2026-2027-2')
            editor.courses[0]['course_id'] = 'LOCAL_TEST_101'
            with patch('desktop_editor.messagebox.showinfo'):
                editor.save()
            app.update()
            config = load_config(Path(app.path.get()))
            assert config['xnxq'] == '2026-2027-2'
            assert config['courses'][0]['course_id'] == 'LOCAL_TEST_101'
            assert app.table.set('A', 'course') == 'LOCAL_TEST_101'
            app.close()
            print('PASS: Tk init + 3-step offline fictional demo + user config restore + school browser link + editor + atomic save + close')


if __name__ == '__main__':
    main()
