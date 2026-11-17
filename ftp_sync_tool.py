#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
FTP Sync Tool
=============
Утилита синхронизации локального каталога (в т.ч. сетевого SMB-ресурса)
с каталогом на FTP-сервере (пассивный режим, опционально FTPS).

Логика синхронизации — зеркалирование:
  - новые/изменённые локальные файлы загружаются на сервер;
  - файлы, удалённые из локального источника, удаляются и на сервере;
  - неизменные файлы не трогаются (сравнение по размеру и времени изменения).

Работает только на стандартной библиотеке Python — это упрощает сборку
в единый exe-файл (PyInstaller) и делает утилиту пригодной для
Windows Server 2016 и новее.

Запуск:
  ftp_sync_tool.exe               -> открыть окно настроек (GUI)
  ftp_sync_tool.exe --gui         -> то же самое явно
  ftp_sync_tool.exe --once        -> выполнить одну синхронизацию и выйти
  ftp_sync_tool.exe               (в режиме "loop" из конфига) -> работает
                                    циклично с указанным интервалом
  ftp_sync_tool.exe --config C:\path\my.ini  -> использовать другой конфиг
"""

import argparse
import calendar
import configparser
import ftplib
import json
import logging
import logging.handlers
import os
import subprocess
import sys
import time

APP_NAME = "FTPSyncTool"
DEFAULT_CONFIG_NAME = "ftp_sync_config.ini"


# --------------------------------------------------------------------------
# Пути и конфигурация
# --------------------------------------------------------------------------

def get_base_dir():
    """Каталог, где лежит exe (или скрипт) — туда же кладём конфиг и лог по умолчанию."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def default_config_path():
    return os.path.join(get_base_dir(), DEFAULT_CONFIG_NAME)


DEFAULTS = {
    "Source": {
        "path": r"C:\Data\Export",
        "smb_unc_path": "",
        "smb_username": "",
        "smb_password": "",
    },
    "FTP": {
        "host": "",
        "port": "21",
        "username": "",
        "password": "",
        "remote_dir": "/",
        "passive": "true",
        "tls": "false",
        "timeout": "30",
    },
    "Sync": {
        "delete_orphaned": "true",
        "interval_minutes": "15",
        "run_mode": "loop",
        "exclude_ext": "",
        "mtime_tolerance_sec": "5",
        "state_file": "ftp_sync_state.json",
        "lock_file": "ftp_sync.lock",
        "max_run_minutes": "30",
    },
    "Logging": {
        "log_file": "ftp_sync.log",
        "log_level": "INFO",
        "max_bytes": "5242880",
        "backup_count": "5",
    },
}


def load_config(path):
    cfg = configparser.ConfigParser()
    cfg.read_dict(DEFAULTS)
    if os.path.exists(path):
        cfg.read(path, encoding="utf-8-sig")
    else:
        save_config(cfg, path)
    return cfg


def save_config(cfg, path):
    with open(path, "w", encoding="utf-8") as f:
        cfg.write(f)


def setup_logging(cfg, base_dir):
    log_file = cfg.get("Logging", "log_file")
    if not os.path.isabs(log_file):
        log_file = os.path.join(base_dir, log_file)
    level = getattr(logging, cfg.get("Logging", "log_level").upper(), logging.INFO)
    max_bytes = cfg.getint("Logging", "max_bytes")
    backup_count = cfg.getint("Logging", "backup_count")

    logger = logging.getLogger(APP_NAME)
    logger.setLevel(level)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()

    fh = logging.handlers.RotatingFileHandler(
        log_file, maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
    )
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    if not getattr(sys, "frozen", False):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        logger.addHandler(sh)

    return logger


# --------------------------------------------------------------------------
# SMB (сетевой каталог-источник)
# --------------------------------------------------------------------------

def mount_smb(cfg, logger):
    """Если указан UNC-путь с учётными данными — аутентифицируемся через `net use`.
    Если ресурс уже доступен под текущей учётной записью (или диск уже подключён
    вручную), это поле можно оставить пустым."""
    unc = cfg.get("Source", "smb_unc_path").strip()
    if not unc:
        return
    user = cfg.get("Source", "smb_username").strip()
    pwd = cfg.get("Source", "smb_password")
    cmd = ["net", "use", unc]
    if pwd:
        cmd.append(pwd)
    if user:
        cmd.append("/user:" + user)
    cmd.append("/persistent:no")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.warning("net use для %s вернул код %s: %s",
                            unc, result.returncode, (result.stdout + result.stderr).strip())
        else:
            logger.info("SMB-подключение установлено: %s", unc)
    except Exception as e:
        logger.warning("Не удалось выполнить net use для %s: %s", unc, e)


def unmount_smb(cfg, logger):
    unc = cfg.get("Source", "smb_unc_path").strip()
    if not unc:
        return
    try:
        subprocess.run(["net", "use", unc, "/delete", "/y"],
                        capture_output=True, text=True, timeout=30)
        logger.info("SMB-подключение отключено: %s", unc)
    except Exception as e:
        logger.debug("Не удалось отключить SMB %s: %s", unc, e)


# --------------------------------------------------------------------------
# Локальный каталог
# --------------------------------------------------------------------------

def list_local_files(root, exclude_ext):
    result = {}
    if not os.path.isdir(root):
        return result
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if exclude_ext and os.path.splitext(name)[1].lower() in exclude_ext:
                continue
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root).replace("\\", "/")
            try:
                st = os.stat(full)
                result[rel] = (st.st_size, st.st_mtime)
            except OSError:
                continue
    return result


def list_local_dirs(root):
    dirs = set()
    if not os.path.isdir(root):
        return dirs
    for dirpath, _dirnames, _filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root).replace("\\", "/")
        if rel != ".":
            dirs.add(rel)
    return dirs


# --------------------------------------------------------------------------
# FTP
# --------------------------------------------------------------------------

def connect_ftp(cfg, logger):
    host = cfg.get("FTP", "host")
    port = cfg.getint("FTP", "port")
    user = cfg.get("FTP", "username")
    pwd = cfg.get("FTP", "password")
    timeout = cfg.getint("FTP", "timeout")
    use_tls = cfg.getboolean("FTP", "tls")
    passive = cfg.getboolean("FTP", "passive")

    ftp_cls = ftplib.FTP_TLS if use_tls else ftplib.FTP
    ftp = ftp_cls(timeout=timeout)
    ftp.connect(host, port)
    ftp.login(user, pwd)
    if use_tls:
        ftp.prot_p()
    ftp.set_pasv(passive)
    logger.info("Подключено к FTP %s:%s (passive=%s, tls=%s)", host, port, passive, use_tls)
    return ftp


def ensure_remote_dir(ftp, remote_dir_path, cache):
    if remote_dir_path in cache:
        return
    parts = remote_dir_path.strip("/").split("/")
    current = ""
    for part in parts:
        if not part:
            continue
        current = current + "/" + part
        if current in cache:
            continue
        try:
            ftp.mkd(current)
        except ftplib.error_perm:
            pass  # каталог уже существует
        cache.add(current)
    cache.add(remote_dir_path)


def list_remote_files(ftp, root, logger):
    """Рекурсивный обход через MLSD. Бросает исключение наружу, если сервер
    не поддерживает MLSD (тогда вызывающий код переходит в упрощённый режим)."""
    files = {}
    dirs = set()

    def walk(path):
        dirs.add(path)
        try:
            entries = list(ftp.mlsd(path))
        except ftplib.error_perm:
            return  # каталог пуст или недоступен - не считаем это неподдержкой MLSD

        for name, facts in entries:
            if name in (".", ".."):
                continue
            full = path.rstrip("/") + "/" + name
            rel = full[len(root):].lstrip("/")
            ftype = facts.get("type", "file")
            if ftype == "dir":
                walk(full)
            elif ftype == "file":
                size = int(facts["size"]) if facts.get("size") else None
                modify = facts.get("modify")
                mtime = None
                if modify:
                    try:
                        mtime = calendar.timegm(time.strptime(modify[:14], "%Y%m%d%H%M%S"))
                    except ValueError:
                        mtime = None
                files[rel] = (size, mtime, full)

    walk(root.rstrip("/") or "/")
    return files, dirs


def load_manifest(path):
    """Локальный манифест: что реально было загружено в прошлый раз
    (размер + время изменения ИСХОДНОГО файла). Используется вместо
    сравнения с датой на FTP-сервере, т.к. STOR не переносит originalьное
    время изменения — сервер проставляет время самой загрузки, и сравнение
    с ним приводит к бесконечной перезаливке (дата "съезжает" на каждый
    прогон)."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("files", {})
        except Exception:
            return {}
    return {}


def save_manifest(path, files):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"files": files}, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def set_remote_mtime(ftp, remote_path, mtime, logger):
    """Best-effort: проставить на сервере время модификации, совпадающее
    с исходным файлом (команда MFMT, RFC 3659). Поддерживается не всеми
    серверами — если нет, просто отдаём предупреждение в лог; на логику
    сравнения (см. манифест выше) это уже не влияет."""
    try:
        ts = time.strftime("%Y%m%d%H%M%S", time.gmtime(mtime))
        ftp.sendcmd("MFMT " + ts + " " + remote_path)
    except Exception as e:
        logger.debug("Сервер не поддерживает MFMT для %s: %s", remote_path, e)


def prune_empty_remote_dirs(ftp, remote_dirs, local_dirs, root, logger):
    candidates = sorted((d for d in remote_dirs if d != root), key=len, reverse=True)
    for d in candidates:
        rel = d[len(root):].lstrip("/")
        if rel in local_dirs:
            continue
        try:
            if not list(ftp.mlsd(d)):
                ftp.rmd(d)
                logger.info("Удалён пустой каталог на сервере: %s", rel)
        except Exception:
            pass


# --------------------------------------------------------------------------
# Основная синхронизация
# --------------------------------------------------------------------------

def lock_file_path(cfg):
    name = cfg.get("Sync", "lock_file")
    if not os.path.isabs(name):
        name = os.path.join(get_base_dir(), name)
    return name


def is_pid_running(pid):
    """Проверка через tasklist (без сторонних библиотек). Если проверить
    не удалось - считаем, что процесс работает, чтобы случайно не убить
    чужой/наш же процесс по ошибке."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
            capture_output=True, text=True, timeout=10
        )
        return str(pid) in result.stdout
    except Exception:
        return True


def kill_pid(pid, logger):
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True, text=True, timeout=10)
        logger.warning("Принудительно завершён зависший процесс PID %s", pid)
    except Exception as e:
        logger.error("Не удалось завершить зависший процесс PID %s: %s", pid, e)


def acquire_lock(cfg, logger):
    """True - можно продолжать (лок захвачен этим процессом).
    False - другой активный (не зависший) экземпляр уже работает, нужно
    пропустить этот запуск."""
    path = lock_file_path(cfg)
    max_run_minutes = cfg.getint("Sync", "max_run_minutes")

    if os.path.exists(path):
        old_pid, old_started = None, None
        try:
            with open(path, "r", encoding="utf-8") as f:
                parts = f.read().strip().split(",")
            old_pid = int(parts[0])
            old_started = float(parts[1])
        except Exception:
            pass

        if old_pid is not None:
            running = is_pid_running(old_pid)
            age_minutes = (time.time() - old_started) / 60.0 if old_started else 0.0
            if running and age_minutes < max_run_minutes:
                logger.info(
                    "Предыдущий экземпляр ещё выполняется (PID %s, работает %.1f мин) — "
                    "пропускаем этот запуск.", old_pid, age_minutes
                )
                return False
            if running and age_minutes >= max_run_minutes:
                logger.warning(
                    "Предыдущий экземпляр (PID %s) выполняется уже %.1f мин (порог %d мин) — "
                    "считаем его зависшим и завершаем принудительно.",
                    old_pid, age_minutes, max_run_minutes
                )
                kill_pid(old_pid, logger)
            else:
                logger.info(
                    "Найден устаревший lock-файл от процесса PID %s (уже не выполняется) — удаляем.",
                    old_pid
                )
        else:
            logger.warning("Lock-файл %s повреждён, игнорируем и перезаписываем.", path)

    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("%d,%f" % (os.getpid(), time.time()))
    except Exception as e:
        logger.warning("Не удалось создать lock-файл %s: %s", path, e)
    return True


def release_lock(cfg, logger):
    path = lock_file_path(cfg)
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                parts = f.read().strip().split(",")
            if int(parts[0]) == os.getpid():
                os.remove(path)
    except Exception as e:
        logger.debug("Не удалось удалить lock-файл %s: %s", path, e)


def sync_once(cfg, logger, progress=None):
    if not acquire_lock(cfg, logger):
        return {"skipped_run": True}
    try:
        return _sync_once_impl(cfg, logger, progress)
    finally:
        release_lock(cfg, logger)


def _sync_once_impl(cfg, logger, progress=None):
    def report(phase, **data):
        if progress:
            progress(phase, **data)

    report("scan")
    local_root = cfg.get("Source", "path")
    remote_root = cfg.get("FTP", "remote_dir") or "/"
    if not remote_root.startswith("/"):
        remote_root = "/" + remote_root

    delete_orphaned = cfg.getboolean("Sync", "delete_orphaned")
    tolerance = cfg.getint("Sync", "mtime_tolerance_sec")
    exclude_ext = {e.strip().lower() for e in cfg.get("Sync", "exclude_ext").split(",") if e.strip()}

    mount_smb(cfg, logger)
    try:
        if not os.path.isdir(local_root):
            logger.error("Локальный каталог-источник недоступен: %s", local_root)
            raise FileNotFoundError("Каталог-источник недоступен: " + local_root)

        local_files = list_local_files(local_root, exclude_ext)
        local_dirs = list_local_dirs(local_root)
        logger.info("Найдено локальных файлов: %d (источник: %s)", len(local_files), local_root)

        report("connect")
        ftp = connect_ftp(cfg, logger)
        report("connected", total=len(local_files))
        try:
            mkd_cache = set()
            ensure_remote_dir(ftp, remote_root, mkd_cache)

            mlsd_supported = True
            try:
                remote_files, remote_dirs = list_remote_files(ftp, remote_root, logger)
            except Exception as e:
                mlsd_supported = False
                remote_files, remote_dirs = {}, {remote_root}
                logger.warning("Сервер не поддерживает MLSD (%s) — переход в упрощённый "
                                "режим: файлы будут перезаписываться без сравнения, "
                                "автоудаление отключено", e)

            state_file = cfg.get("Sync", "state_file")
            if not os.path.isabs(state_file):
                state_file = os.path.join(get_base_dir(), state_file)
            manifest = load_manifest(state_file)
            manifest_changed = False

            uploaded = skipped = deleted = failed = 0
            processed = 0

            for rel, (size, mtime) in local_files.items():
                remote_path = remote_root.rstrip("/") + "/" + rel
                remote_dir = "/".join(remote_path.split("/")[:-1]) or "/"
                ensure_remote_dir(ftp, remote_dir, mkd_cache)

                need_upload = True
                reason = "новый файл (нет в манифесте и на сервере)"
                m = manifest.get(rel)
                if m and m.get("size") == size and abs(mtime - m.get("mtime", 0)) <= tolerance:
                    # Уже заливали именно эту версию файла — сверяемся с тем,
                    # что реально отправляли, а не с датой на сервере.
                    need_upload = False
                elif m is None and mlsd_supported and rel in remote_files:
                    r_size, _r_mtime, _full = remote_files[rel]
                    if r_size == size:
                        # Файл уже есть на сервере (например, с прошлой версии
                        # утилиты, до появления манифеста) — принимаем как
                        # есть один раз, не перезаливаем.
                        need_upload = False
                elif m is not None:
                    if m.get("size") != size:
                        reason = "изменился размер файла: было %s байт, стало %s" % (m.get("size"), size)
                    else:
                        reason = "изменилось время модификации файла: было %s, стало %s (разница %.1f сек, допуск %s сек)" % (
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(m.get("mtime", 0))),
                            time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(mtime)),
                            abs(mtime - m.get("mtime", 0)), tolerance,
                        )

                report("file", current=processed, total=len(local_files), name=rel)
                if need_upload:
                    local_full = os.path.join(local_root, rel.replace("/", os.sep))
                    logger.debug("Причина заливки %s: %s", rel, reason)
                    try:
                        with open(local_full, "rb") as fh:
                            ftp.storbinary("STOR " + remote_path, fh)
                        set_remote_mtime(ftp, remote_path, mtime, logger)
                        uploaded += 1
                        logger.info("Загружен: %s (%s)", rel, reason)
                    except Exception as e:
                        logger.error("Ошибка загрузки %s: %s", rel, e)
                        failed += 1
                        processed += 1
                        report("progress", current=processed, total=len(local_files))
                        continue
                else:
                    skipped += 1

                manifest[rel] = {"size": size, "mtime": mtime}
                manifest_changed = True
                processed += 1
                report("progress", current=processed, total=len(local_files))

            report("cleanup")
            if delete_orphaned:
                known_remote_rels = set(manifest.keys())
                if mlsd_supported:
                    known_remote_rels |= set(remote_files.keys())
                for rel in known_remote_rels:
                    if rel in local_files:
                        continue
                    remote_path = remote_root.rstrip("/") + "/" + rel
                    try:
                        ftp.delete(remote_path)
                        deleted += 1
                        logger.info("Удалён на сервере (отсутствует в источнике): %s", rel)
                    except Exception as e:
                        logger.debug("Не удалось удалить %s (возможно, уже отсутствует): %s", rel, e)
                    manifest.pop(rel, None)
                    manifest_changed = True
                if mlsd_supported:
                    prune_empty_remote_dirs(ftp, remote_dirs, local_dirs, remote_root, logger)

            if manifest_changed:
                save_manifest(state_file, manifest)

            logger.info(
                "Синхронизация завершена: загружено=%d, без изменений=%d, удалено=%d",
                uploaded, skipped, deleted
            )
            return {"uploaded": uploaded, "skipped": skipped, "deleted": deleted, "failed": failed}
        finally:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
    finally:
        unmount_smb(cfg, logger)


def run_loop(cfg, logger):
    interval = max(1, cfg.getint("Sync", "interval_minutes"))
    logger.info("Запуск в циклическом режиме, интервал = %d мин.", interval)
    while True:
        try:
            sync_once(cfg, logger)
        except Exception as e:
            logger.error("Ошибка синхронизации: %s", e, exc_info=True)
        time.sleep(interval * 60)


# --------------------------------------------------------------------------
# GUI настроек (tkinter — входит в стандартную библиотеку)
# --------------------------------------------------------------------------

def launch_gui(config_path):
    from fluent_ui import FluentApp
    FluentApp(config_path).run()


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="FTP Sync Tool")
    parser.add_argument("--config", default=None, help="Путь к файлу конфигурации")
    parser.add_argument("--gui", action="store_true", help="Открыть окно настроек")
    parser.add_argument("--once", action="store_true", help="Выполнить одну синхронизацию и выйти")
    args = parser.parse_args()

    config_path = args.config or default_config_path()
    cfg = load_config(config_path)

    if args.gui:
        launch_gui(config_path)
        return

    logger = setup_logging(cfg, get_base_dir())

    if args.once or cfg.get("Sync", "run_mode").strip().lower() == "once":
        try:
            sync_once(cfg, logger)
        except Exception as e:
            logger.error("Необработанная ошибка синхронизации: %s", e, exc_info=True)
    else:
        run_loop(cfg, logger)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        # Без аргументов (например, запуск двойным щелчком) — открыть окно настроек
        launch_gui(default_config_path())
    else:
        main()