"""Fluent-inspired desktop UI. Runtime dependencies: Python standard library only."""
import configparser
import copy
import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

import ftp_sync_tool as core

BG = '#f3f5f9'
INK = '#182235'
MUTED = '#64748b'
BLUE = '#0078d4'


def asset(name):
    return os.path.join(getattr(sys, '_MEIPASS', os.path.dirname(__file__)), 'assets', name)


def validate(cfg):
    for section, key, low, high in [
        ('FTP', 'port', 1, 65535), ('FTP', 'timeout', 1, 86400),
        ('Sync', 'interval_minutes', 1, 525600),
        ('Sync', 'mtime_tolerance_sec', 0, 86400), ('Sync', 'max_run_minutes', 1, 525600),
        ('Logging', 'max_bytes', 1024, 1073741824), ('Logging', 'backup_count', 1, 100),
    ]:
        try:
            n = cfg.getint(section, key)
        except ValueError:
            raise ValueError(f'{section} / {key}: введите целое число.') from None
        if not low <= n <= high:
            raise ValueError(f'{section} / {key}: допустимо от {low} до {high}.')
    for section, key in [('FTP', 'passive'), ('FTP', 'tls'), ('Sync', 'delete_orphaned')]:
        cfg.getboolean(section, key)
    if cfg.get('Sync', 'run_mode') not in ('loop', 'once'):
        raise ValueError('Выберите режим loop или once.')
    if cfg.get('Logging', 'log_level') not in ('DEBUG', 'INFO', 'WARNING', 'ERROR'):
        raise ValueError('Выберите уровень журнала.')
    if not cfg.get('Source', 'path').strip():
        raise ValueError('Укажите каталог-источник.')
    if not cfg.get('Logging', 'log_file').strip():
        raise ValueError('Укажите файл журнала.')


class FluentApp:
    def __init__(self, config_path):
        self.config_path = os.path.abspath(config_path)
        self.cfg = core.load_config(self.config_path)
        self.events = queue.Queue()
        self.busy = False
        self.logs_window = None
        self.active_log_path = None
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except (AttributeError, OSError):
                pass
        self.root = tk.Tk()
        self.root.title('FTP Sync Tool')
        self.root.geometry('1080x850')
        self.root.minsize(900, 700)
        self.root.configure(bg=BG)
        self.root.option_add('*Font', ('Segoe UI', 10))
        self.icons = {}
        for name in ('app', 'folder', 'server', 'sync', 'settings', 'logs', 'save', 'check', 'error'):
            self.icons[name] = tk.PhotoImage(file=asset(name + '.png'))
        self.root.iconphoto(True, self.icons['app'])
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.style()
        self.vars = {}
        self.status = tk.StringVar(value='Готов к работе')
        self.detail = tk.StringVar(value='Настройте источник и FTP-сервер, затем запустите синхронизацию.')
        self.last = tk.StringVar(value='Ещё не запускалась')
        self.result = tk.StringVar(value='—')
        self.connection = tk.StringVar(value='Не проверено')
        self.build()
        self.show_page('Source')
        self.root.after(100, self.poll)

    def style(self):
        s = ttk.Style(self.root)
        s.theme_use('clam')
        s.configure('.', font=('Segoe UI', 10), background=BG, foreground=INK)
        s.configure('TFrame', background=BG)
        s.configure('Card.TFrame', background='white')
        s.configure('TLabel', background=BG, foreground=INK)
        s.configure('Card.TLabel', background='white')
        s.configure('Muted.TLabel', foreground=MUTED)
        s.configure('CardMuted.TLabel', background='white', foreground=MUTED)
        s.configure('Title.TLabel', font=('Segoe UI', 24, 'bold'))
        s.configure('Section.TLabel', background='white', font=('Segoe UI', 15, 'bold'))
        s.configure('TEntry', fieldbackground='#fbfcfe', bordercolor='#cdd5e0',
                    lightcolor='#cdd5e0', darkcolor='#cdd5e0', padding=8)
        s.map('TEntry', bordercolor=[('focus', BLUE)])
        s.configure('TCombobox', padding=7, fieldbackground='#fbfcfe', arrowsize=14)
        s.configure('TButton', padding=(14, 9), background='white', foreground=INK, bordercolor='#d8dee9',
                    lightcolor='white', darkcolor='white', focuscolor=BLUE)
        s.map('TButton', background=[('active', '#edf5ff'), ('pressed', '#dcecff')],
              foreground=[('disabled', MUTED), ('!disabled', INK)])
        s.configure('Accent.TButton', background=BLUE, foreground='white', bordercolor=BLUE,
                    lightcolor=BLUE, darkcolor=BLUE, font=('Segoe UI', 10, 'bold'))
        s.map('Accent.TButton', background=[('disabled', '#a2b7ce'), ('active', '#106ebe'), ('pressed', '#005a9e')],
              foreground=[('disabled', '#f3f5f9'), ('!disabled', 'white')])
        s.configure('Nav.TButton', padding=(12, 12), anchor='w', background=BG, borderwidth=0)
        s.configure('Selected.Nav.TButton', background='#dcecff', foreground='#005a9e')
        s.map('Selected.Nav.TButton', background=[('active', '#d1e5ff')])
        # Keep the native ttk widget and keyboard bindings, replacing only
        # its tiny platform indicator with a larger DPI-aware image element.
        scale = max(1.0, float(self.root.tk.call('tk', 'scaling')) / (96 / 72))
        divisor = max(1, round(3 / scale))
        self.check_images = {
            name: tk.PhotoImage(file=asset('checkbox_' + name + '.png')).subsample(divisor)
            for name in ('off', 'on', 'disabled_off', 'disabled_on')
        }
        s.element_create('FluentCheck.indicator', 'image', self.check_images['off'],
                         ('disabled selected', self.check_images['disabled_on']),
                         ('disabled', self.check_images['disabled_off']),
                         ('selected', self.check_images['on']))
        s.layout('TCheckbutton', [
            ('Checkbutton.padding', {'sticky': 'nswe', 'children': [
                ('FluentCheck.indicator', {'side': 'left', 'sticky': ''}),
                ('Checkbutton.focus', {'side': 'left', 'sticky': 'w', 'children': [
                    ('Checkbutton.label', {'sticky': 'nswe'})
                ]})
            ]})
        ])
        s.configure('TCheckbutton', background='white', foreground=INK, padding=(2, 7))
        s.map('TCheckbutton', background=[('active', 'white')])
        s.configure('Horizontal.TProgressbar', background=BLUE, troughcolor='#e2e8f0', borderwidth=0)

    def button(self, parent, text, command, icon=None, style='TButton'):
        options = dict(text=text, command=command, style=style)
        image = self.icons.get(icon)
        if image is not None:
            options.update(image=image, compound='left')
        else:
            # Text-only buttons must not use the image/text compound layout.
            # Some Windows Tk builds suppress the label for an empty image.
            options['compound'] = 'text'
        return ttk.Button(parent, **options)

    def build(self):
        header = ttk.Frame(self.root, padding=(24, 20, 24, 10))
        header.pack(fill='x')
        ttk.Label(header, image=self.icons['app']).pack(side='left', padx=(0, 12))
        title = ttk.Frame(header)
        title.pack(side='left')
        ttk.Label(title, text='FTP Sync Tool', style='Title.TLabel').pack(anchor='w')
        ttk.Label(title, text='Синхронизация файлов • FTP / FTPS', style='Muted.TLabel').pack(anchor='w')
        self.button(header, ' Логи', self.open_logs, 'logs').pack(side='right')
        metrics = ttk.Frame(self.root, padding=(24, 10, 24, 16))
        metrics.pack(fill='x')
        for i, (label, var) in enumerate([('ПОДКЛЮЧЕНИЕ', self.connection),
                                         ('ПОСЛЕДНИЙ ЗАПУСК', self.last), ('РЕЗУЛЬТАТ', self.result)]):
            metrics.columnconfigure(i, weight=1, uniform='metrics')
            card = ttk.Frame(metrics, style='Card.TFrame', padding=14)
            card.grid(row=0, column=i, sticky='nsew', padx=(0, 10 if i < 2 else 0))
            ttk.Label(card, text=label, style='CardMuted.TLabel', font=('Segoe UI', 9)).pack(anchor='w')
            ttk.Label(card, textvariable=var, style='Card.TLabel', font=('Segoe UI', 11, 'bold')).pack(anchor='w', pady=(6, 0))
        # Fixed actions/status stay visible even on a small screen.
        bottom = ttk.Frame(self.root, padding=(24, 12, 24, 16))
        bottom.pack(side='bottom', fill='x')
        self.state_dot = tk.Label(bottom, text='●', fg='#64748b', bg=BG, font=('Segoe UI', 14))
        self.state_dot.pack(side='left', padx=(0, 8))
        ttk.Label(bottom, textvariable=self.status).pack(side='left')
        self.progress = ttk.Progressbar(bottom, length=180, mode='determinate')
        self.progress.pack(side='right', padx=(14, 0))
        actions = ttk.Frame(self.root, padding=(24, 12, 24, 0))
        actions.pack(side='bottom', fill='x')
        self.save_btn = self.button(actions, ' Сохранить', self.save, 'save')
        self.save_btn.pack(side='left')
        self.test_btn = self.button(actions, ' Проверить FTP', lambda: self.start('test'), 'server')
        self.test_btn.pack(side='left', padx=8)
        self.run_btn = self.button(actions, ' Синхронизировать', lambda: self.start('sync'), 'sync', 'Accent.TButton')
        self.run_btn.pack(side='right')
        hint = ttk.Frame(self.root, padding=(24, 8, 24, 0))
        hint.pack(side='bottom', fill='x')
        ttk.Label(hint, textvariable=self.detail, style='Muted.TLabel', wraplength=960).pack(anchor='w')
        body = ttk.Frame(self.root, padding=(24, 0, 24, 0))
        body.pack(fill='both', expand=True)
        nav = ttk.Frame(body, width=184)
        nav.pack(side='left', fill='y', padx=(0, 18))
        self.nav = {}
        for key, text, icon in [('Source', ' Источник', 'folder'), ('FTP', ' FTP-сервер', 'server'),
                                ('Sync', ' Синхронизация', 'sync'), ('Logging', ' Журнал', 'logs')]:
            b = self.button(nav, text, lambda k=key: self.show_page(k), icon, 'Nav.TButton')
            b.pack(fill='x', pady=(0, 6))
            self.nav[key] = b
        ttk.Label(nav, text='КОНФИГУРАЦИЯ', style='Muted.TLabel', font=('Segoe UI', 8, 'bold')).pack(anchor='w', pady=(24, 8))
        ttk.Label(nav, text=self.config_path, style='Muted.TLabel', wraplength=165).pack(anchor='w')
        container = ttk.Frame(body, style='Card.TFrame')
        container.pack(side='left', fill='both', expand=True)
        self.canvas = tk.Canvas(container, bg='white', highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, command=self.canvas.yview)
        scrollbar.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.pages_host = ttk.Frame(self.canvas, style='Card.TFrame')
        self.canvas_window = self.canvas.create_window((0, 0), window=self.pages_host, anchor='nw')
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.canvas_window, width=e.width))
        self.pages_host.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        def wheel(e):
            if self.canvas.winfo_containing(e.x_root, e.y_root) in (self.canvas, scrollbar):
                self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units')
            elif str(self.canvas.winfo_containing(e.x_root, e.y_root)).startswith(str(self.pages_host)):
                self.canvas.yview_scroll(-1 if e.delta > 0 else 1, 'units')
        self.root.bind('<MouseWheel>', wheel)
        self.pages = {}
        descriptions = {
            'Source': ('Источник файлов', 'Локальная папка или сетевой ресурс SMB.'),
            'FTP': ('Подключение к серверу', 'Параметры FTP и защищённого подключения FTPS.'),
            'Sync': ('Синхронизация', 'Правила зеркалирования и параметры фонового запуска.'),
            'Logging': ('Журнал событий', 'Файл журнала, подробность и ротация.'),
        }
        fields = {
            'Source': [('path', 'Каталог-источник', 'folder'), ('smb_unc_path', 'UNC-путь SMB (необязательно)', ''),
                       ('smb_username', 'Пользователь SMB', ''), ('smb_password', 'Пароль SMB', 'secret')],
            'FTP': [('host', 'Адрес сервера', ''), ('port', 'Порт', ''), ('username', 'Пользователь', ''),
                    ('password', 'Пароль', 'secret'), ('remote_dir', 'Каталог на сервере', ''),
                    ('timeout', 'Таймаут подключения, секунд', ''), ('passive', 'Пассивный режим FTP', 'bool'),
                    ('tls', 'Защищённое подключение FTPS / TLS', 'bool')],
            'Sync': [('interval_minutes', 'Интервал фонового запуска, минут', ''),
                     ('run_mode', 'Режим фонового запуска', ('loop', 'once')),
                     ('exclude_ext', 'Исключить расширения (например .tmp,.log)', ''),
                     ('mtime_tolerance_sec', 'Допуск времени изменения, секунд', ''),
                     ('max_run_minutes', 'Порог зависшего процесса, минут', ''),
                     ('delete_orphaned', 'Удалять на сервере файлы, отсутствующие в источнике', 'bool')],
            'Logging': [('log_file', 'Путь к файлу журнала', ''),
                        ('log_level', 'Уровень подробности', ('DEBUG', 'INFO', 'WARNING', 'ERROR')),
                        ('max_bytes', 'Размер файла до ротации, байт', ''), ('backup_count', 'Количество архивных журналов', '')],
        }
        for section, (title, desc) in descriptions.items():
            page = ttk.Frame(self.pages_host, style='Card.TFrame', padding=22)
            self.pages[section] = page
            page.columnconfigure(0, weight=1)
            ttk.Label(page, text=title, style='Section.TLabel').grid(row=0, column=0, sticky='w')
            ttk.Label(page, text=desc, style='CardMuted.TLabel', wraplength=600).grid(row=1, column=0, sticky='w', pady=(6, 20))
            row = 2
            for key, label, kind in fields[section]:
                if kind == 'bool':
                    var = tk.BooleanVar(value=self.cfg.getboolean(section, key))
                    ttk.Checkbutton(page, text=label, variable=var).grid(row=row, column=0, sticky='w', pady=4)
                    row += 1
                else:
                    var = tk.StringVar(value=self.cfg.get(section, key))
                    ttk.Label(page, text=label, style='Card.TLabel').grid(row=row, column=0, sticky='w', pady=(0, 5))
                    line = ttk.Frame(page, style='Card.TFrame')
                    line.grid(row=row + 1, column=0, sticky='ew', pady=(0, 14))
                    line.columnconfigure(0, weight=1)
                    if isinstance(kind, tuple):
                        widget = ttk.Combobox(line, textvariable=var, values=kind, state='readonly')
                    else:
                        widget = ttk.Entry(line, textvariable=var, show='•' if kind == 'secret' else '')
                    widget.grid(row=0, column=0, sticky='ew')
                    if kind == 'folder':
                        self.button(line, ' Обзор', self.browse, 'folder').grid(row=0, column=1, padx=(8, 0))
                    if kind == 'secret':
                        visible = tk.BooleanVar(value=False)
                        ttk.Checkbutton(line, text='Показать', variable=visible,
                                        command=lambda w=widget, v=visible: w.configure(show='' if v.get() else '•')).grid(row=0, column=1, padx=(8, 0))
                    row += 2
                self.vars[section, key] = var
            if section == 'Sync':
                ttk.Label(page, text='Зеркалирование удаляет лишние файлы на сервере.\nКнопка «Синхронизировать» выполняет один проход.\nИнтервал и режим применяются при фоновом запуске из командной строки.',
                          style='CardMuted.TLabel', wraplength=600).grid(row=row, column=0, sticky='w', pady=(14, 0))
            if section == 'Logging':
                self.button(page, ' Открыть журнал', self.open_logs, 'logs').grid(row=row, column=0, sticky='w', pady=12)

    def show_page(self, key):
        for name, page in self.pages.items():
            page.pack_forget()
            self.nav[name].configure(style='Selected.Nav.TButton' if name == key else 'Nav.TButton')
        self.pages[key].pack(fill='both', expand=True)
        self.canvas.yview_moveto(0)

    def browse(self):
        path = filedialog.askdirectory(parent=self.root)
        if path:
            self.vars['Source', 'path'].set(path)

    def collect(self):
        cfg = copy.deepcopy(self.cfg)
        for (section, key), var in self.vars.items():
            value = var.get()
            cfg.set(section, key, str(value).lower() if isinstance(value, bool) else value)
        validate(cfg)
        return cfg

    def save(self):
        try:
            cfg = self.collect()
            core.save_config(cfg, self.config_path)
            self.cfg = cfg
            self.status.set('Настройки сохранены')
            return cfg
        except (ValueError, OSError, configparser.Error) as e:
            messagebox.showerror('Настройки', str(e), parent=self.root)
            return None

    def start(self, kind):
        if self.busy:
            return
        cfg = self.save()
        if cfg is None:
            return
        if not cfg.get('FTP', 'host').strip():
            messagebox.showerror('Подключение', 'Укажите адрес FTP-сервера.', parent=self.root)
            self.show_page('FTP')
            return
        self.active_log_path = self.log_path(cfg)
        self.busy = True
        for b in (self.save_btn, self.test_btn, self.run_btn):
            b.state(['disabled'])
        self.connection.set('Подключение…')
        self.status.set('Проверка FTP…' if kind == 'test' else 'Запуск синхронизации…')
        self.state_dot.configure(fg=BLUE)
        self.progress.configure(mode='indeterminate')
        self.progress.start(12)
        # Worker never touches Tk; all UI changes pass through the queue.
        def worker():
            logger = None
            try:
                logger = core.setup_logging(cfg, core.get_base_dir())
                if kind == 'test':
                    ftp = core.connect_ftp(cfg, logger)
                    try:
                        ftp.voidcmd('NOOP')
                    finally:
                        try:
                            ftp.quit()
                        finally:
                            ftp.close()
                    result = None
                else:
                    result = core.sync_once(cfg, logger, lambda phase, **data: self.events.put(('progress', phase, data)))
                self.events.put(('done', kind, result))
            except Exception as e:
                if logger:
                    logger.exception('Ошибка операции')
                self.events.put(('error', kind, str(e)))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        for _ in range(200):
            try:
                event = self.events.get_nowait()
            except queue.Empty:
                break
            if event[0] == 'progress':
                _, phase, data = event
                if phase == 'connected':
                    self.connection.set('Подключено')
                    self.progress.stop()
                    self.progress.configure(mode='determinate', maximum=max(1, data['total']), value=0)
                elif phase in ('file', 'progress'):
                    self.progress['value'] = data['current']
                    self.status.set(f"Обработано: {data['current']} из {data['total']}")
                    if 'name' in data:
                        self.detail.set(data['name'])
                else:
                    self.status.set({'scan': 'Сканирование источника…', 'connect': 'Подключение к FTP…',
                                     'cleanup': 'Проверка лишних файлов…'}.get(phase, 'Синхронизация…'))
            else:
                self.finish(event)
        self.root.after(100, self.poll)

    def finish(self, event):
        self.busy = False
        self.active_log_path = None
        for b in (self.save_btn, self.test_btn, self.run_btn):
            b.state(['!disabled'])
        self.progress.stop()
        self.progress.configure(mode='determinate', maximum=100, value=0)
        if event[0] == 'error':
            self.status.set('Ошибка — откройте журнал')
            self.detail.set(event[2])
            self.connection.set('Нет активного соединения')
            self.state_dot.configure(fg='#d13438')
            self.result.set('Ошибка')
            messagebox.showerror('Ошибка', event[2], parent=self.root)
        elif event[1] == 'test':
            self.connection.set('Проверено · ' + time.strftime('%H:%M:%S'))
            self.status.set('FTP доступен · проверка завершена')
            self.detail.set('Подключение проверено и закрыто.')
            self.state_dot.configure(fg='#107c41')
        else:
            result = event[2] or {}
            self.connection.set('Соединение закрыто')
            if result.get('skipped_run'):
                self.status.set('Запуск пропущен: другой экземпляр уже работает')
                self.detail.set('Подробности — в журнале.')
                self.state_dot.configure(fg='#b36b00')
                return
            self.last.set(time.strftime('%d.%m.%Y %H:%M:%S'))
            failed = result.get('failed', 0)
            self.result.set(f"↑ {result.get('uploaded', 0)}  ·  = {result.get('skipped', 0)}  ·  − {result.get('deleted', 0)}")
            self.status.set('Завершено с ошибками' if failed else 'Синхронизация завершена')
            self.detail.set(f"Загружено: {result.get('uploaded', 0)} · Без изменений: {result.get('skipped', 0)} · Удалено: {result.get('deleted', 0)} · Ошибок загрузки: {failed}")
            self.state_dot.configure(fg='#d13438' if failed else '#107c41')
            self.progress['value'] = 100

    def log_path(self, cfg=None):
        name = (cfg or self.cfg).get('Logging', 'log_file')
        return name if os.path.isabs(name) else os.path.join(core.get_base_dir(), name)

    def open_logs(self):
        if self.logs_window and self.logs_window.winfo_exists():
            self.logs_window.lift()
            return
        win = self.logs_window = tk.Toplevel(self.root)
        win.title('FTP Sync Tool — Журнал событий')
        win.geometry('960x600')
        win.minsize(680, 400)
        win.configure(bg=BG)
        win.iconphoto(True, self.icons['app'])
        top = ttk.Frame(win, padding=18)
        top.pack(fill='x')
        ttk.Label(top, image=self.icons['logs']).pack(side='left', padx=(0, 10))
        ttk.Label(top, text='Журнал событий', font=('Segoe UI', 17, 'bold')).pack(side='left')
        auto = tk.BooleanVar(value=True)
        tail = tk.BooleanVar(value=True)
        bar = ttk.Frame(win, padding=(18, 0, 18, 10))
        bar.pack(fill='x')
        ttk.Checkbutton(bar, text='Автообновление', variable=auto).pack(side='left')
        ttk.Checkbutton(bar, text='Прокрутка вниз', variable=tail).pack(side='left', padx=12)
        area = ttk.Frame(win, padding=(18, 0))
        area.pack(fill='both', expand=True)
        text = tk.Text(area, wrap='none', bg='white', fg=INK, insertbackground=INK,
                       relief='flat', padx=12, pady=12, font=('Consolas', 10), state='disabled')
        ys = ttk.Scrollbar(area, command=text.yview)
        xs = ttk.Scrollbar(area, orient='horizontal', command=text.xview)
        ys.pack(side='right', fill='y')
        xs.pack(side='bottom', fill='x')
        text.pack(fill='both', expand=True)
        text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        text.tag_configure('error', foreground='#c42b1c')
        text.tag_configure('warning', foreground='#996300')
        path_var = tk.StringVar()
        ttk.Label(win, textvariable=path_var, style='Muted.TLabel', wraplength=880).pack(anchor='w', padx=18, pady=8)
        bottom = ttk.Frame(win, padding=(18, 0, 18, 18))
        bottom.pack(fill='x')
        cache = [None]
        def refresh(force=False):
            path = self.active_log_path or self.log_path()
            path_var.set(path)
            try:
                # Bounded tail reading keeps a large log responsive; reopening handles rotation.
                with open(path, 'rb') as f:
                    f.seek(0, 2)
                    size = f.tell()
                    offset = max(0, size - 262144)
                    f.seek(offset)
                    raw = f.read()
                if offset:
                    raw = raw.partition(b'\n')[2]
                content = raw.decode('utf-8', errors='replace')
                if offset:
                    content = '[Показан конец журнала: до 256 КБ]\n' + content
            except FileNotFoundError:
                content = 'Журнал ещё не создан. Запустите проверку FTP или синхронизацию.'
            except OSError as e:
                content = 'Не удалось прочитать журнал: ' + str(e)
            if force or cache[0] != (path, content):
                view = text.yview()
                text.configure(state='normal')
                text.delete('1.0', 'end')
                for line in content.splitlines(keepends=True):
                    tag = 'error' if '[ERROR]' in line or '[CRITICAL]' in line else 'warning' if '[WARNING]' in line else ''
                    text.insert('end', line, (tag,) if tag else ())
                text.configure(state='disabled')
                text.see('end') if tail.get() else text.yview_moveto(view[0])
                cache[0] = (path, content)
        def tick():
            if win.winfo_exists():
                if auto.get():
                    refresh()
                win.after(1000, tick)
        def copy_logs():
            win.clipboard_clear()
            win.clipboard_append(text.get('1.0', 'end-1c'))
        self.button(bottom, 'Обновить', lambda: refresh(True)).pack(side='left')
        self.button(bottom, 'Копировать', copy_logs).pack(side='left', padx=8)
        def open_folder():
            path = os.path.dirname(self.active_log_path or self.log_path())
            if hasattr(os, 'startfile'):
                try:
                    os.startfile(path)
                except OSError as e:
                    messagebox.showerror('Журнал', str(e), parent=win)
            else:
                messagebox.showinfo('Папка журнала', path, parent=win)
        self.button(bottom, 'Открыть папку', open_folder).pack(side='left')
        self.button(bottom, 'Закрыть', win.destroy).pack(side='right')
        refresh(True)
        tick()

    def close(self):
        if self.busy:
            messagebox.showinfo('Синхронизация', 'Дождитесь завершения операции перед закрытием.', parent=self.root)
            return
        self.root.destroy()

    def run(self):
        self.root.mainloop()
