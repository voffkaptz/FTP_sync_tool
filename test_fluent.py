"""Offline checks: no FTP connections and no remote deletions."""
import configparser
import logging
import os
import tempfile
import unittest
from unittest.mock import patch
import ftp_sync_tool as core
from fluent_ui import validate


def config():
    cfg = configparser.ConfigParser()
    cfg.read_dict(core.DEFAULTS)
    return cfg


class FakeFTP:
    def __init__(self, fail=False):
        self.fail = fail
        self.uploads = []
        self.closed = False
    def mkd(self, path): pass
    def storbinary(self, command, stream):
        if self.fail: raise OSError('simulated upload failure')
        self.uploads.append((command, stream.read()))
    def sendcmd(self, command): pass
    def quit(self): self.closed = True
    def close(self): self.closed = True


class Checks(unittest.TestCase):
    def test_validation(self):
        cfg = config()
        validate(cfg)
        for section, key, value in [('FTP', 'port', '70000'), ('FTP', 'timeout', 'abc'),
                                    ('Sync', 'interval_minutes', '0'), ('Logging', 'backup_count', '-1')]:
            bad = config()
            bad.set(section, key, value)
            with self.assertRaises(ValueError): validate(bad)

    def execute(self, fail=False):
        with tempfile.TemporaryDirectory() as folder:
            with open(os.path.join(folder, 'sample.txt'), 'w') as f: f.write('hello')
            cfg = config()
            cfg.set('Source', 'path', folder)
            cfg.set('Sync', 'state_file', os.path.join(folder, 'state.json'))
            cfg.set('Sync', 'delete_orphaned', 'false')
            ftp = FakeFTP(fail)
            events = []
            with patch.object(core, 'connect_ftp', return_value=ftp), patch.object(core, 'list_remote_files', return_value=({}, set())):
                result = core._sync_once_impl(cfg, logging.getLogger('test'), lambda phase, **data: events.append((phase, data)))
            self.assertTrue(ftp.closed)
            self.assertEqual(result['failed'], int(fail))
            self.assertEqual(result['uploaded'], int(not fail))
            self.assertTrue(any(p == 'progress' and d['current'] == 1 and d['total'] == 1 for p, d in events))
            manifest = core.load_manifest(cfg.get('Sync', 'state_file'))
            self.assertEqual('sample.txt' in manifest, not fail)

    def test_success_and_progress(self): self.execute()
    def test_failed_upload_not_success(self): self.execute(True)
    def test_missing_source(self):
        cfg = config()
        cfg.set('Source', 'path', os.path.join(tempfile.gettempdir(), 'nonexistent-ftp-test-path'))
        with self.assertRaises(FileNotFoundError): core._sync_once_impl(cfg, logging.getLogger('test'))
    def test_other_instance_is_not_reported_as_success(self):
        with patch.object(core, 'acquire_lock', return_value=False):
            self.assertEqual(core.sync_once(config(), logging.getLogger('test')), {'skipped_run': True})
    def test_lock_released_after_exception(self):
        with patch.object(core, 'acquire_lock', return_value=True), patch.object(core, 'release_lock') as release, patch.object(core, '_sync_once_impl', side_effect=OSError('failure')):
            with self.assertRaises(OSError): core.sync_once(config(), logging.getLogger('test'))
            release.assert_called_once()
    def test_logger_can_be_reconfigured(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = config()
            logger = core.setup_logging(cfg, folder)
            old = logger.handlers[0]
            logger = core.setup_logging(cfg, folder)
            self.assertIsNone(old.stream)
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)


class LogWindowChecks(unittest.TestCase):
    def test_log_actions_stay_visible_and_copy_at_small_window_sizes(self):
        import tkinter as tk
        import tkinter.font as tkfont
        from fluent_ui import FluentApp
        with tempfile.TemporaryDirectory() as folder:
            try:
                app = FluentApp(os.path.join(folder, 'test.ini'))
            except tk.TclError as e:
                if 'display' in str(e).lower():
                    self.skipTest('GUI check requires a graphical display')
                raise
            try:
                app.root.withdraw()
                log_path = os.path.join(folder, 'test.log')
                log_content = '[INFO] sample log line\n[ERROR] sample error\n'
                with open(log_path, 'w', encoding='utf-8') as stream:
                    stream.write(log_content)
                app.active_log_path = log_path
                app.open_logs()
                win = app.logs_window
                win.deiconify()
                def descendants(widget):
                    for child in widget.winfo_children():
                        yield child
                        yield from descendants(child)
                buttons = [w for w in descendants(win) if isinstance(w, tk.Button)]
                expected = {'Обновить', 'Копировать', 'Открыть папку', 'Закрыть'}
                self.assertEqual({str(w['text']) for w in buttons}, expected)
                for geometry in ('960x600', '680x400'):
                    win.geometry(geometry)
                    win.update()
                    for button in buttons:
                        font = tkfont.Font(root=app.root, font=button['font'])
                        self.assertGreaterEqual(button.winfo_width(), font.measure(button['text']))
                        self.assertGreaterEqual(button.winfo_height(), button.winfo_reqheight())
                        self.assertGreaterEqual(button.winfo_y(), 0)
                        self.assertLessEqual(button.winfo_rooty() + button.winfo_height(),
                                             win.winfo_rooty() + win.winfo_height())
                        self.assertTrue(button.winfo_viewable())
                next(w for w in buttons if w['text'] == 'Обновить').invoke()
                next(w for w in buttons if w['text'] == 'Копировать').invoke()
                self.assertEqual(win.clipboard_get(), log_content)
            finally:
                app.root.destroy()


if __name__ == '__main__': unittest.main()
