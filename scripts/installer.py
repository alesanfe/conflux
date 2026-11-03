#!/usr/bin/env python3
"""installer.py — instalador gráfico de conflux (wizard tkinter, sin deps extra).

Pasos: entorno Python -> Prowlarr+indexers -> .env/config -> proxy+autostart ->
addon en Stremio -> login Trakt (muestra el código). También DESINSTALAR.
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
EPG_URL=https://epgshare01.online/epgshare01/epg_ripper_ES1.xml.gz
"""

try:
    import tkinter as tk
    from tkinter import ttk, scrolledtext, font as tkfont
except ImportError:
    sys.exit('tkinter no disponible — instala Python completo o usa scripts/setup_win.ps1')

# paleta (misma accent de las páginas web del proxy)
BG, BG2, BG3 = '#141416', '#1e1e23', '#26262b'
FG, DIM, ACC, OK, BAD = '#e8e8ea', '#8a8a93', '#7c5ce0', '#4caf50', '#e05555'
BORD = '#33333c'


def _stremio_exe():
    """Ruta al ejecutable de Stremio en Windows, o None."""
    import glob as _g
    la = os.environ.get('LOCALAPPDATA', '')
    pats = [
        os.path.join(la, 'Programs', 'LNV', 'Stremio-4', 'stremio.exe'),
        os.path.join(la, 'Programs', 'Stremio*', '*tremio*.exe'),
        r'C:\Program Files\Stremio*\*.exe',
        r'C:\Program Files (x86)\Stremio*\*.exe',
        os.path.join(os.environ.get('APPDATA', ''), 'stremio*', '*.exe'),
    ]
    for p in pats:
        hits = _g.glob(p)
        if hits:
            return hits[0]
    return None


def _sh(cmd, timeout=300, shell=False):
    """Subproceso -> (rc, salida)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           shell=shell, cwd=ROOT, encoding='utf-8', errors='replace')
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:                               # noqa: BLE001 — installer: todo se reporta en UI
        return 1, str(e)


class Installer(tk.Tk):
    """Wizard estilo instalador moderno: banner, sidebar, progreso, log plegable."""

    STEPS = ['Entorno', 'Prowlarr', 'Configuración', 'Proxy + autostart',
             'Addon Stremio', 'Trakt (opcional)', 'Fin']
    USTEPS = ['Detener procesos', 'Autostart + addon', 'Borrar runtime',
              'Prowlarr', 'Fin']
    ICONS = ['1', '2', '3', '4', '5', '6', '7']
    U_ICONS = ['1', '2', '3', '4', '5']

    def __init__(self):
        super().__init__()
        self.title('conflux — Setup')
        self.geometry('880x560')
        self.configure(bg=BG)
        self.resizable(False, False)
        self.q = queue.Queue()
        self.cur = -1                     # -1 = pantalla de modo (instalar/desinstalar)
        self.done_flags = {}
        self.mode = 'install'
        self.steps = []
        self.icons = []
        self.log_visible = False

        # fuentes
        self.f_h1 = tkfont.Font(family='Segoe UI', size=16, weight='bold')
        self.f_step = tkfont.Font(family='Segoe UI', size=10)
        self.f_step_b = tkfont.Font(family='Segoe UI', size=10, weight='bold')
        self.f_txt = tkfont.Font(family='Segoe UI', size=10)
        self.f_code = tkfont.Font(family='Consolas', size=32, weight='bold')

        self._build_chrome()
        self.after(150, self._pump)
        self._mode_screen()
        self.after(80, lambda: (self.lift(), self.focus_force(),
                                self.attributes('-topmost', True),
                                self.after(400, lambda: self.attributes('-topmost', False))))

    # ==================== CHROME (layout fijo) ====================
    def _build_chrome(self):
        # --- banner superior ---
        banner = tk.Frame(self, bg=ACC, height=64)
        banner.pack(fill='x')
        banner.pack_propagate(False)
        tk.Label(banner, text=' conflux ', bg=ACC, fg='white',
                 font=tkfont.Font(family='Segoe UI', size=18, weight='bold')).pack(
                     side='left', padx=(22, 8), pady=14)
        self.banner_sub = tk.Label(banner, text='Setup', bg=ACC, fg='#d9d2f7',
                                   font=self.f_txt)
        self.banner_sub.pack(side='left', pady=18)
        hexico = tk.Canvas(banner, width=30, height=30, bg=ACC,
                           highlightthickness=0)
        hexico.create_polygon(15, 3, 26, 9, 26, 21, 15, 27, 4, 21, 4, 9,
                              fill='', outline='#e6dffc', width=2)
        hexico.pack(side='right', padx=20, pady=17)

        mid = tk.Frame(self, bg=BG)
        mid.pack(fill='both', expand=True)

        # --- sidebar pasos ---
        self.side = tk.Frame(mid, bg=BG2, width=210)
        self.side.pack(side='left', fill='y')
        self.side.pack_propagate(False)
        tk.Frame(self.side, bg=BG2, height=10).pack()
        self.lbl_steps = []

        # --- contenido ---
        right = tk.Frame(mid, bg=BG)
        right.pack(side='right', fill='both', expand=True)
        self.title_lbl = tk.Label(right, text='', bg=BG, fg=FG, font=self.f_h1)
        self.title_lbl.pack(anchor='w', padx=26, pady=(20, 2))
        self.sub_lbl = tk.Label(right, text='', bg=BG, fg=DIM, font=self.f_txt)
        self.sub_lbl.pack(anchor='w', padx=26)
        self.body = tk.Frame(right, bg=BG)
        self.body.pack(fill='both', expand=True, padx=26, pady=10)

        # --- log plegable ---
        self.log_frame = tk.Frame(right, bg=BG)
        self.log = scrolledtext.ScrolledText(
            self.log_frame, height=8, bg='#0d0d10', fg='#9cdcfe',
            font=('Consolas', 9), state='disabled', relief='flat',
            insertbackground='#9cdcfe')
        self.log.pack(fill='both', expand=True)

        # --- footer: progreso + nav ---
        foot = tk.Frame(self, bg=BG2, height=58)
        foot.pack(fill='x', side='bottom')
        foot.pack_propagate(False)
        sty = ttk.Style(self)
        try:
            sty.theme_use('clam')
        except tk.TclError:
            pass
        sty.configure('conflux.Horizontal.TProgressbar', troughcolor=BG2,
                      background=ACC, bordercolor=BG2, lightcolor=ACC,
                      darkcolor=ACC, thickness=4)
        self.pbar = ttk.Progressbar(foot, maximum=len(self.STEPS) - 1,
                                    style='conflux.Horizontal.TProgressbar')
        self.pbar.pack(fill='x', padx=1, side='top')
        inner = tk.Frame(foot, bg=BG2)
        inner.pack(fill='x', expand=True, pady=8)
        self.btn_log = tk.Button(inner, text='Detalles ▾', command=self._toggle_log,
                                 bg=BG2, fg=DIM, relief='flat', bd=0, font=self.f_txt,
                                 activebackground=BG2, activeforeground=FG,
                                 cursor='hand2')
        self.btn_log.pack(side='left', padx=18)
        self.btn_skip = self._mkbtn(inner, 'Saltar paso', self.next_step, ghost=True)
        self.btn_skip.pack(side='right', padx=8)
        self.btn_next = self._mkbtn(inner, 'Siguiente →', self.next_step)
        self.btn_next.pack(side='right', padx=(8, 18))

        # separador fino sobre el footer
        tk.Frame(self, bg=BORD, height=1).pack(fill='x', side='bottom')

    def _mkbtn(self, parent, text, cmd, ghost=False, width=0):
        """Botón estilo moderno: sólido accent o 'ghost' oscuro."""
        b = tk.Button(parent, text=text, command=cmd, relief='flat', bd=0,
                      bg=(BG3 if ghost else ACC), fg='white', padx=16, pady=7,
                      font=self.f_step_b, activebackground=(BORD if ghost else '#8d6ff0'),
                      activeforeground='white', cursor='hand2', width=width or None)
        return b

    def _toggle_log(self):
        self.log_visible = not self.log_visible
        if self.log_visible:
            self.log_frame.pack(fill='both', expand=True, padx=26, pady=(0, 8))
            self.btn_log.config(text='Detalles ▴')
        else:
            self.log_frame.pack_forget()
            self.btn_log.config(text='Detalles ▾')

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
                elif kind == 'addon_manual':
                    self.addon_lbl.config(
                        text='Sin sesión de Stremio accesible — usa el diálogo '
                             'nativo (un clic):', fg=DIM)
                    self._mkbtn(self.body, 'Instalar addon en Stremio',
                                lambda: (webbrowser.open(
                                    'stremio://127.0.0.1:9700/manifest.json'),
                                    self.say('diálogo de instalación abierto'),
                                    self._mark_done())).pack(pady=12)
                    self.btn_skip.config(state='normal')
        except queue.Empty:
            pass
        self.after(150, self._pump)

    def say(self, msg):
        self.q.put(('log', msg))

    def _mark_done(self, note=''):
        self.done_flags[self.cur] = True
        self._paint_steps()
        self.pbar.config(value=self.cur + 1)
        self.btn_next.config(state='normal')
        self.btn_skip.config(state='normal')

    def _busy(self):
        self.btn_next.config(state='disabled')
        self.btn_skip.config(state='disabled')

    def _clear_body(self):
        for w in self.body.winfo_children():
            w.destroy()

    def _lbl(self, text, size=10, color=FG, bold=False, mono=False, **kw):
        f = self.f_code if mono else (
            tkfont.Font(family='Segoe UI', size=size, weight='bold' if bold else 'normal'))
        lb = tk.Label(self.body, text=text, bg=BG, fg=color, font=f,
                      wraplength=580, justify='left', **kw)
        lb.pack(anchor='w', pady=3)
        return lb

    def _paint_steps(self):
        for i, (lb, ic) in enumerate(self.lbl_steps):
            done = self.done_flags.get(i)
            cur = i == self.cur
            ic.config(text='✓' if done else self.icons[i],
                      fg=OK if done else (ACC if cur else DIM))
            lb.config(fg=ACC if cur else (OK if done else FG),
                      font=self.f_step_b if cur else self.f_step)

    def next_step(self):
        self.cur = min(self.cur + 1, len(self.steps) - 1)
        self._show_step()

    def _show_step(self):
        self._paint_steps()
        self._clear_body()
        self.title_lbl.config(text=self.steps[self.cur])
        self.sub_lbl.config(text='')
        prefix = '_step' if self.mode == 'install' else '_ustep'
        getattr(self, f'{prefix}{self.cur}')()

    def _run(self, fn):
        self._busy()
        threading.Thread(target=fn, daemon=True).start()

    # ---------- iconos vectoriales (Canvas — nítidos, sin depender de emojis) ----------
    def _icon_canvas(self, parent, kind, size=28, color='#a48af5', bg=BG2):
        """Dibuja un icono: 'dl' flecha-descarga | 'trash' papelera."""
        c = tk.Canvas(parent, width=size, height=size, bg=bg,
                      highlightthickness=0)
        s = size / 28.0                     # escala base 28px
        if kind == 'dl':                    # flecha hacia bandeja
            c.create_line(14*s, 4*s, 14*s, 17*s, fill=color, width=round(3*s))
            c.create_line(8*s, 12*s, 14*s, 18*s, fill=color, width=round(3*s))
            c.create_line(20*s, 12*s, 14*s, 18*s, fill=color, width=round(3*s))
            c.create_line(5*s, 24*s, 23*s, 24*s, fill=color, width=round(3*s))
        elif kind == 'trash':               # papelera
            c.create_line(8*s, 8*s, 20*s, 8*s, fill=color, width=round(2.4*s))
            c.create_line(12*s, 8*s, 12*s, 5*s, 16*s, 5*s, 16*s, 8*s,
                          fill=color, width=round(2*s))
            c.create_line(9*s, 8*s, 10.5*s, 23*s, 17.5*s, 23*s, 19*s, 8*s,
                          fill=color, width=round(2*s))
            c.create_line(13*s, 11*s, 13*s, 20*s, fill=color, width=round(1.6*s))
            c.create_line(15*s, 11*s, 15*s, 20*s, fill=color, width=round(1.6*s))
        return c

    # ---------- pantalla de modo ----------
    def _mode_screen(self):
        self.banner_sub.config(text='Setup')
        self.title_lbl.config(text='¿Qué quieres hacer?')
        self.sub_lbl.config(text='Instalación o desinstalación completa del entorno')
        self._clear_body()
        self.btn_next.pack_forget()
        self.btn_skip.pack_forget()
        # sidebar = panel de marca (estilo página bienvenida de Inno)
        for w in self.side.winfo_children()[1:]:
            w.destroy()
        self.lbl_steps = []
        tk.Label(self.side, text='conflux', bg=BG2, fg=ACC,
                 font=tkfont.Font(family='Segoe UI', size=15, weight='bold')
                 ).pack(anchor='w', padx=18, pady=(14, 2))
        tk.Label(self.side, text='Media center\nlocal para Stremio',
                 bg=BG2, fg=DIM, font=self.f_txt, justify='left'
                 ).pack(anchor='w', padx=18)

        row = tk.Frame(self.body, bg=BG)
        row.pack(expand=True)

        def card(txt, icon, sub, cmd, accent=ACC):
            c = tk.Frame(row, bg=BG2, highlightbackground=BORD,
                         highlightthickness=1, cursor='hand2')
            c.pack(side='left', padx=14, ipadx=20, ipady=16)
            ico = self._icon_canvas(c, icon)
            ico.pack(anchor='w', pady=(0, 8))
            tk.Label(c, text=txt, bg=BG2, fg=FG,
                     font=tkfont.Font(family='Segoe UI', size=13, weight='bold')
                     ).pack(anchor='w')
            tk.Label(c, text=sub, bg=BG2, fg=DIM, font=self.f_txt,
                     justify='left', wraplength=200).pack(anchor='w', pady=3)
            for w in (c, *c.winfo_children()):
                w.bind('<Button-1>', lambda e, f=cmd: f())
                w.bind('<Enter>', lambda e, fr=c: fr.config(highlightbackground=ACC))
                w.bind('<Leave>', lambda e, fr=c: fr.config(highlightbackground=BORD))
            return c

        card('Instalar', 'dl', 'Monta todo el entorno:\nvenv · Prowlarr · indexers\n'
                               'proxy · autostart · addon\nlogin Trakt opcional',
             lambda: self._set_mode('install'))
        card('Desinstalar', 'trash', 'Quita todo lo instalado:\nprocesos · autostart · runtime\n'
                                     'Prowlarr · (opcional) claves\ny la propia carpeta',
             lambda: self._set_mode('uninstall'))

    def _set_mode(self, mode):
        self.mode = mode
        self.steps = self.STEPS if mode == 'install' else self.USTEPS
        self.icons = self.ICONS if mode == 'install' else self.U_ICONS
        self.banner_sub.config(text='Setup — instalación' if mode == 'install'
                               else 'Setup — desinstalación')
        self.pbar.config(maximum=len(self.steps) - 1, value=0)
        for w in self.side.winfo_children()[1:]:
            w.destroy()
        self.lbl_steps = []
        for i, s in enumerate(self.steps):
            row = tk.Frame(self.side, bg=BG2)
            row.pack(anchor='w', fill='x', padx=(16, 8), pady=4)
            ic = tk.Label(row, text=self.icons[i], bg=BG2, fg=DIM,
                          font=self.f_step_b, width=2)
            ic.pack(side='left')
            lb = tk.Label(row, text=s, bg=BG2, fg=FG, font=self.f_step)
            lb.pack(side='left')
            self.lbl_steps.append((lb, ic))
        self.btn_next.pack(side='right', padx=(8, 18))
        self.btn_skip.pack(side='right', padx=8)
        self.cur = 0
        self._show_step()

    # ==================== INSTALAR ====================
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
            _sh([py, 'scripts/apply_env.py'], 60)
            rc, out = _sh([py, 'scripts/prowlarr_indexers.py'], 600)
            self.say(out or 'indexers listos')
            self.q.put(('done', 'OK'))
        else:
            self.say('sin ApiKey — Prowlarr no arrancó; siembra manual en :9696')
            self.q.put(('done', 'FALLO'))

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
        # arranque desacoplado: sin pipes heredados (capture_output haria que el
        # pythonw hijo retuviera el pipe y communicate() se bloqueara siempre)
        subprocess.Popen(f'cmd /c "{bat}"', shell=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
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

    def _step4(self):
        self._lbl('Registra el addon «conflux» en Stremio. Se intenta de forma '
                  'automática (colección remota, sin clics); si no hay sesión '
                  'disponible se ofrece el diálogo nativo como alternativa.')
        self.addon_lbl = self._lbl('…', color=DIM)
        self._run(self._do_addon)

    def _do_addon(self):
        if sys.platform == 'win32' and not _stremio_exe():
            self.say('stremio no detectado — instalando (winget)…')
            rc, out = _sh('winget install -e --id Stremio.Stremio '
                          '--accept-source-agreements --accept-package-agreements',
                          shell=True, timeout=900)
            self.say(out[-160:] or 'stremio instalado')
        try:
            import install_addon
            st, msg = install_addon.install(f'{PROXY}/manifest.json')
        except Exception as e:
            st, msg = 'error', str(e)
        self.say(f'addon stremio: {st} {msg}')
        if st in ('installed', 'already'):
            self.addon_lbl.config(
                text='✔ instalado — aparece al reiniciar Stremio' if st == 'installed'
                     else '✔ ya estaba instalado', fg=OK)
            self.q.put(('done', 'OK'))
        else:
            self.q.put(('addon_manual',))

    def _step5(self):
        self._lbl('Conecta tu cuenta trakt.tv (gratis): pulsa abrir, inicia sesión '
                  'y escribe el código. Se detecta solo.')
        self.code_lbl = tk.Label(self.body, text='········', bg=BG, fg=ACC,
                                 font=self.f_code)
        self.code_lbl.pack(pady=10)
        self._mkbtn(self.body, 'Abrir trakt.tv/activate',
                    lambda: webbrowser.open('https://trakt.tv/activate')).pack()
        self.status_lbl = self._lbl('…', color=DIM)
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
                    self.status_lbl.config(text='Conectado — token guardado', fg=OK)
                    self.q.put(('log', 'trakt conectado'))
                    self.q.put(('done', 'conectado'))
                    return
            except Exception:
                pass
        self.status_lbl.config(text='expirado — reintenta con '
                                    'python scripts/trakt_auth.py')
        self.q.put(('done', 'expirado'))

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
            rc, out = _sh('netstat -ano | findstr :9700 | findstr LISTENING',
                          shell=True, timeout=15)
            for ln in out.splitlines():
                pid = ln.strip().rsplit(None, 1)[-1]
                if pid.isdigit():
                    _sh(f'taskkill /PID {pid} /F', shell=True, timeout=15)
                    killed += 1
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
        self._lbl('Quita el acceso directo de autostart y elimina el addon de '
                  'la colección de Stremio (remoto — aplica al reiniciar la app).')
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
        try:
            import install_addon
            st, msg = install_addon.remove(f'{PROXY}/manifest.json')
            self.say(f'addon stremio: {st} {msg}'
                     + ('' if st != 'manual' else ' — quítalo a mano en Addons'))
        except Exception as e:
            self.say(f'addon remoto: {e} — quítalo a mano en Stremio')
        self.q.put(('done', 'OK'))

    def _ustep2(self):
        self._lbl('Borra el entorno generado: .venv, cachés, log y listas m3u.\n'
                  'Marca la casilla para conservar .env y config.json (tus claves).')
        self.keep_cfg = tk.BooleanVar(value=True)
        self._check(self.body, 'Conservar .env y config.json', self.keep_cfg)
        self._run(self._do_rm_runtime)

    def _check(self, parent, text, var):
        cb = tk.Checkbutton(parent, text=text, variable=var, bg=BG, fg=FG,
                            selectcolor=BG3, activebackground=BG,
                            activeforeground=FG, font=self.f_txt)
        cb.pack(anchor='w', pady=6)
        return cb

    def _do_rm_runtime(self):
        import shutil
        for p in ('.venv', 'm3u', '.manifests-cache.json', 'epg-cache.json',
                  'conflux.log', 'start-conflux.bat', 'post1337.json',
                  'resp1337.json', 'tz.xml', 'sc.json', 'tu.cs', 'os.json'):
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
        self._check(self.body, 'Desinstalar Prowlarr también', self.rm_prowlarr)
        self._run(self._do_prowlarr_rm)

    def _do_prowlarr_rm(self):
        if not self.rm_prowlarr.get():
            self.say('Prowlarr se conserva')
            self.q.put(('done', 'omitido'))
            return
        if sys.platform != 'win32':
            self.say('desinstalación automática solo en Windows — '
                     'quita Prowlarr con tu gestor de paquetes')
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
        self._mkbtn(self.body, 'Eliminar también la carpeta conflux',
                    self._self_destruct, ghost=True).pack(pady=12)
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
