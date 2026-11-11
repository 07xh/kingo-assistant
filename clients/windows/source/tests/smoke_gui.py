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
                app.table.selection_set('target-B')
                with patch('windows_gui.messagebox.showinfo') as showinfo:
                    assert app.copy_selected_field('course_id') is False
                    showinfo.assert_called_once()
                app.demo_next()
                assert app.table.set('target-B', 'status') == '虚构样例：已满'
                app.demo_next()  # Replays from snapshot 1, not a fourth fake sample.
                assert app.table.set('target-B', 'status') == '虚构样例：已满'
            app.check_config()  # Restores user's configured table, not fictional data.
            assert len(app.table.get_children()) == 2
            assert app.table.set('A', 'course') == 'FILL_FROM_OFFICIAL_PAGE'
            app.table.selection_set('A')
            with patch('windows_gui.messagebox.showwarning') as showwarning:
                assert app.copy_selected_field('course_id') is False
                showwarning.assert_called_once()
            with patch('windows_gui.webbrowser.open', return_value=True) as web_open:
                app.open_official_portal()
                web_open.assert_called_once_with('https://jwmis.ncwu.edu.cn')
            editor = SettingsEditor(app, Path(app.path.get()), app.check_config)
            editor.withdraw()
            editor.update()
            editor.semester.set('2026-2027-2')
            editor.courses[0]['course_id'] = 'LOCAL_TEST_101'
            assert editor.interval_preset.get() == '最快（60 秒）'
            editor.interval_preset.set('均衡（120 秒）')
            editor._apply_interval_preset()
            assert (editor.interval_low.get(), editor.interval_high.get()) == ('120', '120')
            editor.interval_low.set('75')
            editor.interval_high.set('135')
            assert editor.interval_preset.get() == '自定义区间'
            with patch('desktop_editor.messagebox.showerror') as showerror:
                editor.interval_low.set('59')
                editor.save()
                showerror.assert_called_once()
            editor.interval_low.set('75')
            # Exercise fast preset as an opt-in without making a school request.
            editor.interval_preset.set('加速（1 秒，需确认）')
            editor._apply_interval_preset()
            with patch('desktop_editor.messagebox.showerror') as no_opt_in:
                editor.save()
                no_opt_in.assert_called_once()
            editor.fast_polling_opt_in.set(True)
            with patch('desktop_editor.messagebox.showinfo'):
                editor.save()
            app.update()
            config = load_config(Path(app.path.get()))
            assert config['xnxq'] == '2026-2027-2'
            assert config['courses'][0]['course_id'] == 'LOCAL_TEST_101'
            assert config['strategy']['interval_range'] == [1.0, 1.0]
            assert config['strategy']['fast_polling_opt_in'] is True
            assert app.table.set('A', 'course') == 'LOCAL_TEST_101'
            assert app.table.set('A', 'class_id') == '—'
            app.table.selection_set('A')
            assert app.copy_selected_field('course_id') is True
            assert app.clipboard_get() == 'LOCAL_TEST_101'
            with patch('windows_gui.webbrowser.open', return_value=True) as browser:
                app.prepare_selected_course()
                browser.assert_called_once_with('https://jwmis.ncwu.edu.cn')
            assert app.clipboard_get() == 'LOCAL_TEST_101'
            app.close()
            print('PASS: Tk init + 3-step offline fictional demo + user config restore + school browser link + editor + atomic save + close')


if __name__ == '__main__':
    main()
