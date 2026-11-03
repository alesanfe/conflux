#!/usr/bin/env python3
"""installer.py — instalador gráfico de conflux (wizard tkinter, sin deps extra).

Pasos: entorno Python -> Prowlarr+indexers -> .env/config -> proxy+autostart ->
addon en Stremio -> login Trakt (muestra el código). Re-ejecutable.
Lanzar con:  python scripts/installer.py   (o conflux-setup.bat -> pythonw)
"""
# pylint: disable=broad-exception-caught,too-many-locals,too-many-statements
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROXY = 'http://127.0.0.1:9700'

DEFAULT_ENV = """\
# conflux .env — generado por el instalador
PROWLARR=1
TORZNAB_URL=http://127.0.0.1:9696/0/api
TRAKT_CLIENT_ID=bfdd2e032c30c35b368f97ef4ec81587b899bcb028b91a1d4ba5589a4b6a7267
TRAKT_CLIENT_SECRET=bf9fce37cf45c1de91da009e7ac6fca905a35d7a718bf65a52f92199073a2503
OPENSUBTITLES_KEY=gUCLWGoAg2PmyseoTM0INFFVPcDCeDlT
M3U_URL=https://iptv-org.github.io/iptv/countries/es.m3u
"""

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext
except ImportError:
    sys.exit('tkinter no disponible — instala Python completo o usa scripts/setup_win.ps1')


def _sh(cmd, timeout=300, shell=False):
    """Subproceso -> (rc, salida)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=shell, cwd=ROOT, encoding='utf-8', errors='replace')
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:                               # noqa: BLE001 — installer: todo se reporta en UI
        return 1, str(e)


class Installer(tk.Tk):
    """Wizard: panel de pasos a la izquierda, contenido + log a la derecha."""

    STEPS = ['Entorno', 'Prowlarr', 'Configuración', 'Proxy + autostart',
             'Addon Stremio', 'Trakt (opcional)', 'Fin']
    USTEPS = ['Detener procesos', 'Autostart + addon', 'Borrar runtime',
              'Prowlarr', 'Fin']

    def __init__(self):
        super().__init__()
        self.title('conflux — instalador')
        self.geometry('820x520')
        self.configure(bg='#1b1b1f')
        self.q = queue.Queue()
        self.cur = -1                     # -1 = pantalla de modo (instalar/desinstalar)
        self.done_flags = {}
        self.mode = 'install'
        self.steps = []

        fg, bg2, acc = '#e8e8ea', '#26262b', '#7c5ce0'
        style = ttk.Style(self)
        style.theme_use('clam')
        style.configure('TFrame', background=bg2)
        style.configure('TLabel', background=bg2, foreground=fg, font=('Segoe UI', 10))
        style.configure('Title.TLabel', font=('Segoe UI', 15, 'bold'))
        style.configure('Step.TLabel', font=('Segoe UI', 10), padding=6)
        style.configure('On.TLabel', foreground=acc, font=('Segoe UI', 10, 'bold'), padding=6)
        style.configure('Ok.TLabel', foreground='#4caf50', padding=6)
        style.configure('Code.TLabel', font=('Consolas', 30, 'bold'), foreground=acc)
        style.configure('TButton', padding=8, font=('Segoe UI', 10))
        style.configure('Acc.TButton', padding=10)

        left = ttk.Frame(self, width=190)
        left.pack(side='left', fill='y')
        self.left = left
        self.lbl_steps = []

        right = ttk.Frame(self)
        right.pack(side='right', fill='both', expand=True)
        self.title_lbl = ttk.Label(right, text='', style='Title.TLabel')
        self.title_lbl.pack(anchor='w', padx=20, pady=(18, 4))
        self.body = ttk.Frame(right)
        self.body.pack(fill='both', expand=True, padx=20, pady=6)
        self.log = scrolledtext.ScrolledText(right, height=9, bg='#101014', fg='#9cdcfe',
                                           font=('Consolas', 9), state='disabled',
                                           relief='flat')
        self.log.pack(fill='x', padx=20, pady=(0, 6))
        nav = ttk.Frame(right)
        nav.pack(fill='x', padx=20, pady=(0, 14))
        self.btn_next = ttk.Button(nav, text='Siguiente →', style='Acc.TButton',
                                   command=self.next_step)
        self.btn_next.pack(side='right')
        self.btn_skip = ttk.Button(nav, text='Saltar paso', command=self.next_step)
        self.btn_skip.pack(side='right', padx=8)

        self.after(150, self._pump)
        self._mode_screen()

    # ---------- pantalla de modo ----------
    def _mode_screen(self):
        self.title_lbl.config(text='conflux — ¿qué quieres hacer?')
        self._clear_body()
        self._lbl('Instalar monta todo el entorno (venv, Prowlarr, indexers,\n'
                  'proxy, autostart, addon). Desinstalar lo quita todo.')
        row = ttk.Frame(self.body)
        row.pack(pady=18)
        ttk.Button(row, text='⬇  Instalar', style='Acc.TButton',
                   command=lambda: self._set_mode('install')).pack(side='left', padx=10)
        ttk.Button(row, text='🗑  Desinstalar',
                   command=lambda: self._set_mode('uninstall')).pack(side='left', padx=10)
        self.btn_next.pack_forget()
        self.btn_skip.pack_forget()

    def _set_mode(self, mode):
        self.mode = mode
        self.steps = self.STEPS if mode == 'install' else self.USTEPS
        for w in self.left.winfo_children():
            w.destroy()
        self.lbl_steps = []
        for i, s in enumerate(self.steps):
            lb = ttk.Label(self.left, text=f'{i + 1}. {s}', style='Step.TLabel')
            lb.pack(anchor='w', padx=12, pady=2)
            self.lbl_steps.append(lb)
        self.btn_next.pack(side='right')
        self.btn_skip.pack(side='right', padx=8)
        self.cur = 0
        self._show_step()

    # ---------- utilidades UI ----------
    def _pump(self):
        try:
            while True:
                kind, msg = self.q.get_nowait()
                if kind == 'log':
                    self.log.config(state='normal')
                    self.log.insert('end', msg + '\n')
                    self.log.see('end')
                    self.log.config(state='disabled')
                elif kind == 'done':
                    self._mark_done(msg)
        except queue.Empty:
            pass
        self.after(150, self._pump)

    def say(self, msg):
        self.q.put(('log', msg))

    def _mark_done(self, note=''):
        self.done_flags[self.cur] = True
        self.lbl_steps[self.cur].config(style='Ok.TLabel',
                                        text=f'✓ {self.steps[self.cur]} {note}')
        self.btn_next.config(state='normal')
        self.btn_skip.config(state='normal')

    def _busy(self):
        self.btn_next.config(state='disabled')
        self.btn_skip.config(state='disabled')

    def _clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _lbl(self, text, style='TLabel', **kw):
        lb = ttk.Label(self.body, text=text, style=style, wraplength=560,
                       justify='left', **kw)
        lb.pack(anchor='w', pady=3)
        return lb

    def next_step(self):
        self.cur = min(self.cur + 1, len(self.steps) - 1)
        self._show_step()

    def _show_step(self):
        for i, lb in enumerate(self.lbl_steps):
            lb.config(style='On.TLabel' if i == self.cur else
                          ('Ok.TLabel' if self.done_flags.get(i) else 'Step.TLabel'),
                      text=('' if self.done_flags.get(i) else f'{i + 1}. ') +
                      ('✓ ' if self.done_flags.get(i) else '') + self.steps[i])
        self._clear_body()
        self.title_lbl.config(text=self.steps[self.cur])
        prefix = '_step' if self.mode == 'install' else '_ustep'
        getattr(self, f'{prefix}{self.cur}')()

    def _run(self, fn):
        self._busy()
        threading.Thread(target=fn, daemon=True).start()

    # ---------- paso 0: entorno ----------
    def _step0(self):
        self._lbl('Prepara el entorno Python: crea .venv e instala dependencias '
                  '(requirements.txt con hashes).')
        self._run(self._do_env)

    def _do_env(self):
        py = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
        if not os.path.exists(py):
            self.say('creando .venv…')
            for cand in ('py -3.12', 'py -3.11', 'python'):
                rc, out = _sh(f'{cand} -m venv .venv', shell=True)
                if rc == 0:
                    break
                self.say(f'{cand}: {out[:120]}')
            if not os.path.exists(py):
                self.say('no se pudo crear .venv — instala Python ≥3.10 y reintenta')
                self.q.put(('done', 'FALLO'))
                return
        self.say('instalando dependencias…')
        rc, out = _sh([py, '-m', 'pip', 'install', '-r', 'requirements.txt', '-q'], 600)
        self.say('deps: ' + ('OK' if rc == 0 else out[-200:]))
        self.q.put(('done', 'OK' if rc == 0 else 'FALLO'))

    # ---------- paso 1: prowlarr ----------
    def _step1(self):
        self._lbl('Instala Prowlarr (winget), conecta Torznab y siembra los '
                  'indexers públicos por API. Puede pedir UAC.')
        self._run(self._do_prowlarr)

    def _do_prowlarr(self):
        if sys.platform != 'win32':
            self.say('Prowlarr auto solo en Windows — instálalo a mano o con Docker')
            self.q.put(('done', 'omitido'))
            return
        rc, _ = _sh('sc query Prowlarr', shell=True, timeout=20)
        if rc != 0:
            self.say('instalando Prowlarr (winget)…')
            rc, out = _sh('winget install -e --id TeamProwlarr.Prowlarr '
                          '--accept-source-agreements --accept-package-agreements',
                          shell=True, timeout=900)
            self.say(out[-200:])
        for _ in range(30):
            try:
                urllib.request.urlopen('http://127.0.0.1:9696/ping', timeout=3)
                break
            except Exception:
                time.sleep(3)
        pcfg = os.path.join(os.environ.get('ProgramData', ''), 'Prowlarr', 'config.xml')
        key = ''
        try:
            key = ET.parse(pcfg).getroot().findtext('.//ApiKey') or ''
        except Exception:
            pass
        if key:
            env_f = os.path.join(ROOT, '.env')
            txt = open(env_f, encoding='utf-8').read() if os.path.exists(env_f) else ''
            if 'TORZNAB_KEY' not in txt:
                with open(env_f, 'a', encoding='utf-8') as f:
                    f.write(f'\nTORZNAB_KEY={key}\n')
            self.say('ApiKey detectada — sembrando indexers…')
            py = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
            if not os.path.exists(py):
                py = sys.executable
            # asegurar torznab en config antes de sembrar
            _sh([py, 'scripts/apply_env.py'], 60)
            rc, out = _sh([py, 'scripts/prowlarr_indexers.py'], 600)
            self.say(out or 'indexers listos')
            self.q.put(('done', 'OK'))
        else:
            self.say('sin ApiKey — Prowlarr no arrancó; siembra manual en :9696')
            self.q.put(('done', 'FALLO'))

    # ---------- paso 2: config ----------
    def _step2(self):
        self._lbl('Crea/actualiza .env con las integraciones gratuitas '
                  '(Trakt, OpenSubtitles, IPTV, Torznab) y lo aplica a config.json.')
        self._run(self._do_cfg)

    def _do_cfg(self):
        env_f = os.path.join(ROOT, '.env')
        if not os.path.exists(env_f):
            with open(env_f, 'w', encoding='utf-8') as f:
                f.write(DEFAULT_ENV)
            self.say('.env creado con claves públicas + IPTV ES')
        py = os.path.join(ROOT, '.venv', 'Scripts', 'python.exe')
        if not os.path.exists(py):
            py = sys.executable
        rc, out = _sh([py, 'scripts/apply_env.py'], 120)
        self.say(out or 'config aplicada')
        self.q.put(('done', 'OK' if rc == 0 else 'FALLO'))

    # ---------- paso 3: proxy + autostart ----------
    def _step3(self):
        self._lbl('Crea el lanzador, registra el autostart en la carpeta Inicio '
                  'y arranca el proxy en 127.0.0.1:9700.')
        self._run(self._do_proxy)

    def _do_proxy(self):
        bat = os.path.join(ROOT, 'start-conflux.bat')
        with open(bat, 'w', encoding='ascii') as f:
            f.write(f'@echo off\ncd /d "{ROOT}"\n'
                    f'start "" /min "{ROOT}\\.venv\\Scripts\\pythonw.exe" proxy.py '
                    '>> conflux.log 2>&1\n')
        if sys.platform == 'win32':
            startup = os.path.join(os.environ['APPDATA'],
                                   'Microsoft\\Windows\\Start Menu\\Programs\\Startup')
            _sh(['powershell', '-NoProfile', '-Command',
                 f"$w=New-Object -ComObject WScript.Shell; "
                 f"$l=$w.CreateShortcut('{startup}\\\\Conflux.lnk'); "
                 f"$l.TargetPath='{bat}'; $l.WindowStyle=7; "
                 f"$l.WorkingDirectory='{ROOT}'; $l.Save()"], 30)
            self.say('autostart registrado')
        rc, _ = _sh(f'cmd /c "{bat}"', shell=True, timeout=20)
        for _ in range(30):
            try:
                urllib.request.urlopen(f'{PROXY}/health', timeout=3)
                self.say('proxy: OK en 127.0.0.1:9700')
                self.q.put(('done', 'OK'))
                return
            except Exception:
                time.sleep(2)
        self.say('el proxy no responde — revisa conflux.log')
        self.q.put(('done', 'FALLO'))

    # ---------- paso 4: addon ----------
    def _step4(self):
        self._lbl('Abre el diálogo de instalación del addon dentro de Stremio. '
                  'Pulsa el botón y confirma «Instalar» en la app.')
        b = ttk.Button(self.body, text='Instalar addon en Stremio',
                       style='Acc.TButton',
                       command=lambda: (webbrowser.open('stremio://127.0.0.1:9700/manifest.json'),
                                        self.say('diálogo de instalación abierto'),
                                        self._mark_done()))
        b.pack(pady=16)
        self.btn_skip.config(state='normal')
        self.btn_next.config(state='normal')

    # ---------- paso 5: trakt ----------
    def _step5(self):
        self._lbl('Conecta tu cuenta trakt.tv (gratis): pulsa abrir, inicia sesión '
                  'y escribe el código. Se detecta solo.')
        self.code_lbl = ttk.Label(self.body, text='········', style='Code.TLabel')
        self.code_lbl.pack(pady=10)
        ttk.Button(self.body, text='Abrir trakt.tv/activate',
                   command=lambda: webbrowser.open('https://trakt.tv/activate')).pack()
        self.status_lbl = self._lbl('…')
        self.btn_skip.config(state='normal')
        self.btn_next.config(state='normal')
        self._run(self._do_trakt)

    def _do_trakt(self):
        cfg = json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))
        if cfg.get('trakt_access_token'):
            self.q.put(('log', 'trakt ya conectado'))
            self.q.put(('done', 'ya conectado'))
            return
        try:
            html = urllib.request.urlopen(f'{PROXY}/auth/trakt', timeout=15).read().decode()
            m = re.search(r'<b>([A-Z0-9]{6,10})</b>', html)
            if m:
                self.code_lbl.config(text=m.group(1))
        except Exception as e:
            self.q.put(('log', f'auth page: {e}'))
            self.q.put(('done', 'sin proxy'))
            return
        webbrowser.open('https://trakt.tv/activate')
        for _ in range(120):
            time.sleep(5)
            try:
                cfg = json.load(open(os.path.join(ROOT, 'config.json'), encoding='utf-8'))
                if cfg.get('trakt_access_token'):
                    self.status_lbl.config(text='Conectado — token guardado')
                    self.q.put(('log', 'trakt conectado'))
                    self.q.put(('done', 'conectado'))
                    return
            except Exception:
                pass
        self.status_lbl.config(text='expirado — puedes reintentar con '
                                  'python scripts/trakt_auth.py')
        self.q.put(('done', 'expirado'))

    # ---------- paso 6: fin ----------
    def _step6(self):
        ok = sum(self.done_flags.get(i, False) for i in range(len(self.steps) - 1))
        self._lbl(f'{ok} de {len(self.steps) - 1} pasos completados.\n\n'
                  'El addon corre en http://127.0.0.1:9700 y arranca solo al '
                  'iniciar sesión.\n\n'
                  'Útil: /configure (ajustes) · /admin (salud) · /health')
        self.btn_next.config(text='Cerrar', command=self.destroy)
        self.btn_skip.config(state='disabled')

    # ==================== DESINSTALAR ====================
    def _ustep0(self):
        self._lbl('Detiene el proxy conflux y los procesos Python asociados.')
        self._run(self._do_kill)

    def _do_kill(self):
        killed = 0
        if sys.platform == 'win32':
            # proceso(s) escuchando :9700
            rc, out = _sh('netstat -ano | findstr :9700 | findstr LISTENING',
                          shell=True, timeout=15)
            for ln in out.splitlines():
                pid = ln.strip().rsplit(None, 1)[-1]
                if pid.isdigit():
                    _sh(f'taskkill /PID {pid} /F', shell=True, timeout=15)
                    killed += 1
            # cualquier python corriendo proxy.py
            rc, out = _sh(['powershell', '-NoProfile', '-Command',
                           "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" "
                           "| Where-Object {$_.CommandLine -match 'proxy\\.py'} "
                           '| ForEach-Object {$_.ProcessId}'], 30)
            for ln in out.splitlines():
                pid = ln.strip()
                if pid.isdigit():
                    _sh(f'taskkill /PID {pid} /F', shell=True, timeout=15)
                    killed += 1
        self.say(f'{killed} proceso(s) detenidos')
        self.q.put(('done', 'OK'))

    def _ustep1(self):
        self._lbl('Quita el acceso directo de autostart y recuerda quitar el '
                  'addon dentro de Stremio (Addons → Streams Unificados → Quitar).')
        self._run(self._do_auto_rm)

    def _do_auto_rm(self):
        lnk = os.path.join(os.environ.get('APPDATA', ''),
                           'Microsoft\\Windows\\Start Menu\\Programs\\Startup',
                           'Conflux.lnk')
        try:
            os.unlink(lnk)
            self.say('autostart eliminado')
        except OSError:
            self.say('autostart no existía')
        self.q.put(('done', 'OK'))

    def _ustep2(self):
        self._lbl('Borra el entorno generado: .venv, cachés, log y listas m3u.\n'
                  'Marca la casilla para borrar TAMBIÉN .env y config.json '
                  '(tus claves).')
        self.keep_cfg = tk.BooleanVar(value=True)
        tk.Checkbutton(self.body, text='Conservar .env y config.json',
                       variable=self.keep_cfg, bg='#26262b', fg='#e8e8ea',
                       selectcolor='#26262b', activebackground='#26262b',
                       font=('Segoe UI', 10)).pack(anchor='w', pady=6)
        self._run(self._do_rm_runtime)

    def _do_rm_runtime(self):
        import shutil
        for p in ('.venv', 'm3u', '.manifests-cache.json', 'conflux.log',
                  'start-conflux.bat', 'post1337.json', 'resp1337.json',
                  'tz.xml', 'sc.json', 'tu.cs', 'os.json'):
            fp = os.path.join(ROOT, p)
            try:
                if os.path.isdir(fp):
                    shutil.rmtree(fp, ignore_errors=True)
                elif os.path.exists(fp):
                    os.unlink(fp)
                self.say(f'{p}: eliminado')
            except OSError as e:
                self.say(f'{p}: {e}')
        if not self.keep_cfg.get():
            for p in ('.env', 'config.json', 'sources.json'):
                fp = os.path.join(ROOT, p)
                try:
                    os.unlink(fp)
                    self.say(f'{p}: eliminado')
                except OSError:
                    self.say(f'{p}: no existía')
        else:
            self.say('.env/config.json: conservados')
        self.q.put(('done', 'OK'))

    def _ustep3(self):
        self._lbl('Opcional: desinstala Prowlarr (servicio + programa).')
        self.rm_prowlarr = tk.BooleanVar(value=False)
        tk.Checkbutton(self.body, text='Desinstalar Prowlarr también',
                       variable=self.rm_prowlarr, bg='#26262b', fg='#e8e8ea',
                       selectcolor='#26262b', activebackground='#26262b',
                       font=('Segoe UI', 10)).pack(anchor='w', pady=6)
        self._run(self._do_prowlarr_rm)

    def _do_prowlarr_rm(self):
        if not self.rm_prowlarr.get():
            self.say('Prowlarr se conserva')
            self.q.put(('done', 'omitido'))
            return
        self.say('deteniendo servicio Prowlarr…')
        _sh('net stop Prowlarr', shell=True, timeout=30)
        rc, out = _sh('winget uninstall -e --id TeamProwlarr.Prowlarr '
                      '--accept-source-agreements', shell=True, timeout=600)
        self.say('prowlarr: ' + ('desinstalado' if rc == 0 else out[-150:]))
        pcfg = os.path.join(os.environ.get('ProgramData', ''), 'Prowlarr')
        self.say(f'config Prowlarr queda en {pcfg} (bórralo a mano si quieres)')
        self.q.put(('done', 'OK' if rc == 0 else 'FALLO'))

    def _ustep4(self):
        self._lbl('Hecho. Opcionalmente puedes borrar la propia carpeta del '
                  'proyecto — el instalador programa su eliminación al cerrar.')
        ttk.Button(self.body, text='Eliminar también la carpeta conflux',
                   command=self._self_destruct).pack(pady=12)
        self.btn_next.config(text='Cerrar', command=self.destroy)
        self.btn_skip.config(state='disabled')

    def _self_destruct(self):
        if sys.platform == 'win32':
            subprocess.Popen(
                f'cmd /c "ping -n 3 127.0.0.1 >nul & rd /s /q "{ROOT}""',
                shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
        self.say(f'carpeta {ROOT} marcada para borrado al cerrar')
        self.destroy()


if __name__ == '__main__':
    Installer().mainloop()
