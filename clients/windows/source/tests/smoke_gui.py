"""Optional interactive-display smoke check (Linux: xvfb-run -a python tests/smoke_gui.py)."""
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows_gui import Application
from windows_visual_shell import MINIMUM_SIZE, TABS
from desktop_editor import SettingsEditor
from core.desktop_settings import load_config


def main():
    with tempfile.TemporaryDirectory() as tmp:
        with patch('windows_gui.app_dir', return_value=Path(tmp)):
            app = Application()
            app.withdraw()
            app.update()
            # Assert only Home and Tools layouts are attached to the Tk Application.
            assert tuple(app.notebook.tab(tab, 'text') for tab in app.notebook.tabs()) == tuple(
                caption for _, caption in TABS)
            assert app.toolbar.winfo_exists()
            assert app.status_bar.winfo_exists()
            assert app.visual_content.winfo_exists()
            assert app.minsize() == MINIMUM_SIZE
            app.geometry('%dx%d' % MINIMUM_SIZE)
            app.deiconify()
            app.update()
            for viewport, body, bar in (
                (app.home_scroll_canvas, app._home_content, app.home_scrollbar),
                (app.tools_scroll_canvas, app._tools_content, app.tools_scrollbar),
            ):
                assert viewport.winfo_exists()
                assert body.winfo_exists()
                assert bar.winfo_exists()
                assert viewport.cget('yscrollcommand')
                assert len(viewport.find_all()) == 1
            app.notebook.select(app.tab_home)
            app.update_idletasks()
            assert app.home_scroll_canvas.bbox('all') is not None
            app.home_scroll_canvas.yview_moveto(1)
            app.update_idletasks()
            assert app.table.winfo_exists()
            assert app.scheduled_start_button.winfo_ismapped()
            assert app.scheduled_start_button.master.master == app._home_footer
            app.home_scroll_canvas.yview_moveto(0)
            app.notebook.select(app.tab_tools)
            app.update_idletasks()
            assert app.tools_scroll_canvas.bbox('all') is not None
            app.tools_scroll_canvas.yview_moveto(1)
            app.update_idletasks()
            assert app.tools_scroll_canvas.yview()[0] > 0, 'Tool/log bottom must be scroll-reachable'
            assert app.log_box.winfo_exists()
            app.tools_scroll_canvas.yview_moveto(0)
            app.notebook.select(app.tab_home)
            app.update_idletasks()
            assert app.notebook.cget('style') == 'Kingo.Content.TNotebook'
            assert app.notebook.select() == str(app.tab_home)
            app.notebook.select(app.tab_tools)
            app.update()
            assert app.notebook.select() == str(app.tab_tools)
            app.notebook.select(app.tab_home)
            app.update()
            assert app.edit_button.cget('style') == 'KingoPrimary.TButton'
            assert app.native_login_button.cget('style') == 'KingoPrimary.TButton'
            assert app.start_button.cget('style') == 'KingoPrimary.TButton'
            for widget in (
                app.session_check_button, app.tools_operations,
                app.scheduled_preflight_button, app.school_page_button,
                app.s202_result_button, app.course_plan_button,
                app.s202_round_button, app.s202_score_button,
                app.term_probe_button, app.config_entry, app.pick_button,
                app.open_button, app.log_frame, app.log_box,
            ):
                assert widget.winfo_exists(), f'missing page widget: {widget}'
            assert app.log_box.cget('state') == 'disabled'
            assert app.advanced_panel.winfo_manager() == ''
            app.toggle_advanced_panel()
            assert app.notebook.select() == str(app.tab_tools)
            assert app.advanced_panel.winfo_manager() == 'pack'
            app.toggle_advanced_panel()
            assert app.advanced_panel.winfo_manager() == ''
            assert app.scheduled_start_button.instate(['disabled'])
            assert app.scheduled_stop_button.instate(['disabled'])
            assert app.stop_button.instate(['disabled'])
            assert tuple(app.table['columns']) == (
                'key', 'course', 'class_id', 'teacher', 'status')
            assert '未接入提交适配器' in app.enrollment_gate_status.get()
            for widget in (
                app.edit_button, app.plans_button,
                app.native_login_button, app.native_prepare_button,
                app.native_clear_button, app.timetable_read_button,
                app.live_catalog_button, app.live_poll_button,
                app.action_panel, app.start_button, app.stop_button,
                app.scheduled_start_button, app.scheduled_stop_button,
                app.course_frame, app.table,
            ):
                assert widget.winfo_exists(), f'missing home widget: {widget}'
            assert 'S202' in app.live_catalog_button.cget('text')
            assert '≥60秒' in app.live_poll_button.cget('text')
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
            app.table.selection_set('A')
            with patch('windows_gui.messagebox.showwarning') as showwarning:
                assert app.copy_selected_field('course_id') is False
                showwarning.assert_called_once()
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
            with patch('desktop_editor.messagebox.showinfo'):
                editor.save()
            app.update()
            config = load_config(Path(app.path.get()))
            assert config['xnxq'] == '2026-2027-2'
            assert config['courses'][0]['course_id'] == 'LOCAL_TEST_101'
            assert config['strategy']['interval_range'] == [75, 135]
            assert app.table.set('A', 'course') == 'LOCAL_TEST_101'
            assert app.table.set('A', 'class_id') == '—'
            app.table.selection_set('A')
            assert app.copy_selected_field('course_id') is True
            assert app.clipboard_get() == 'LOCAL_TEST_101'
            assert app.clipboard_get() == 'LOCAL_TEST_101'
            app.close()
            print('PASS: Tk init + API-only navigation + offline demo + user config restore + editor + atomic save + close')


if __name__ == '__main__':
    main()
