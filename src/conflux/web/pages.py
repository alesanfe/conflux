"""HTML embebido: /configure, /admin, /health, auth Trakt, POSTs."""
# pylint: disable=broad-exception-caught,line-too-long
# broad-exception: resiliencia del agregador; line-too-long: regex/URLs/HTML embebido
import threading
import time

from conflux.app.bg import _prefetch_catalogs
from conflux.core.cfg import _PROF, _save_cfg, load_config
from conflux.core.state import (
    _DISABLED,
    _SERVED,
    CFG,
    COOLDOWN,
    HEALTH,
    SOURCES,
    _cache,
    _cat_c,
    _fails,
    _meta_c,
    _pct,
    _rtime,
    _rts,
    manifests,
)
from conflux.domain.manifest import build_manifest
from conflux.infra.trakt import _TRAKT_DEV, _trakt_device_start


# ---- servidor ----
def _health_page():
    """Estado por fuente: contrib, cooldowns, fallos, latencias (observabilidad)."""
    now = time.time()
    return {'sources': {n: dict(h, cd_min=round((COOLDOWN[n][0]-now)/60), cd_motivo=COOLDOWN[n][1])
                        if n in COOLDOWN and COOLDOWN[n][0] > now else dict(h)
                        for n, h in HEALTH.items()},
            'fetch_fails': {k: v[0] for k, v in _fails.items()},
            'streams_served': _SERVED,
            'latency_ms': {k: {'n': n, 'avg': round(t/n, 1), 'max': round(x, 1),
                               'p50': _pct(list(_rts.get(k, ())), .5),
                               'p95': _pct(list(_rts.get(k, ())), .95),
                               'p99': _pct(list(_rts.get(k, ())), .99)}
                           for k, (n, t, x) in _rtime.items()}}


def _manifest_reply():
    """Manifest base + prefetch de catálogos (Discover pega caché) + id por perfil."""
    threading.Thread(target=_prefetch_catalogs, daemon=True).start()
    mf = build_manifest()
    if _PROF.name:                                             # identidad por perfil
        mf = dict(mf, id=f"local.uniform.streams.{_PROF.name}",
                  name=f"Streams Unificados · {_PROF.name}")
    return mf


def _csv_list(form, k):
    """Campo CSV del form -> [items] limpios."""
    return [s.strip() for s in form.get(k, [''])[0].split(',') if s.strip()]


def _cfg_form_fields(form, cfg):
    """Campos restantes del form -> cfg. Solo campos PRESENTES pisan (un POST
    parcial no debe borrar el resto — el checkbox-group 'src' ausente no
    significa 'desactiva todo')."""
    if 'calendar_months_ahead' in form:
        try:
            cfg['calendar_months_ahead'] = max(1, min(int(form['calendar_months_ahead'][0]), 24))
        except (TypeError, ValueError):              # 'abc', '', None -> sin tocar
            pass
    if 'stream_max_gb' in form:
        try:
            cfg['stream_max_gb'] = max(0, int(form['stream_max_gb'][0]))
        except (TypeError, ValueError):
            pass
    if 'stream_prefetch' in form:                    # hidden 0 + checkbox 1 -> último gana
        cfg['stream_prefetch'] = form['stream_prefetch'][-1] == '1'
    if 'debrid_proxy' in form:
        cfg['debrid_proxy'] = form['debrid_proxy'][-1] == '1'
    if 'parental' in form:
        cfg['parental'] = form['parental'][-1] == '1'
    if 'mediafusion' in form:
        cfg['mediafusion'] = form['mediafusion'][-1] == '1'
    if 'stream_langs' in form:
        cfg['stream_langs'] = _csv_list(form, 'stream_langs') or CFG['stream_langs']
    for k in ('torznab_url', 'zilean_url', 'newznab_url', 'catalog_lists',
              'opensubtitles_key', 'opensubtitles_user', 'epg_url', 'stream_format',
              'easynews_user'):
        if k in form:
            cfg[k] = form[k][0].strip()
    for k in ('torznab_key', 'newznab_key', 'mdblist_key', 'rpdb_key', 'opensubtitles_pass',
              'easynews_pass'):
        if k in form and form[k][0].strip():
            cfg[k] = form[k][0].strip()
    for k in ('source_order', 'catalogs_disabled', 'stream_exclude', 'stream_required',
              'stream_sort', 'stream_preferred', 'extra_sources'):
        if k in form:
            cfg[k] = _csv_list(form, k)
    if 'src' in form:
        enabled = set(form.get('src', []))
        cfg['sources_disabled'] = [s.name for s in SOURCES if s.name not in enabled]


def _configure_save(form):
    """POST /configure: solo campos presentes pisan; claves vacías conservan el
    valor previo (no se muestran en el form)."""
    cfg = CFG.copy()
    if 'trakt_client_id' in form:
        cfg['trakt_client_id'] = form['trakt_client_id'][0].strip()
    if form.get('trakt_client_secret', [''])[0].strip():
        cfg['trakt_client_secret'] = form['trakt_client_secret'][0].strip()
    if 'debrid_provider' in form:
        cfg['debrid_provider'] = form['debrid_provider'][0].strip()
    if form.get('debrid_key', [''])[0].strip():
        cfg['debrid_key'] = form['debrid_key'][0].strip()
    if 'm3u_path' in form:
        cfg['m3u_path'] = form['m3u_path'][0].strip()
    _cfg_form_fields(form, cfg)
    CFG.clear()
    CFG.update(cfg)
    _save_cfg()
    load_config()


def _trakt_status():
    if CFG.get('trakt_access_token') and not _TRAKT_DEV:
        return {'state': 'ok'}
    return {'state': 'pending' if _TRAKT_DEV else 'none'}


_PAGE_CSS = ('<style>body{font:15px/1.5 system-ui;background:#0f0f14;color:#e8e8f0;max-width:880px;margin:24px auto;padding:0 16px}'
             'h1{font-size:22px}h2{font-size:16px;color:#9d9db5;margin-top:28px}'
             'table{border-collapse:collapse;width:100%}td,th{padding:6px 10px;border-bottom:1px solid #26262f;text-align:left}'
             'input,select{background:#1a1a24;border:1px solid #333;color:#e8e8f0;padding:6px 8px;border-radius:6px;width:100%}'
             'button,a.btn{background:#7c5ce0;color:#fff;border:0;padding:8px 16px;border-radius:6px;cursor:pointer;text-decoration:none;display:inline-block}'
             '.ok{color:#4ade80}.bad{color:#f87171}.dim{color:#8b8ba0;font-size:13px}</style>')


def _landing_page(host, tls):
    """/ : puerta del addon — botón stremio:// host-aware + URL manifest manual.
    host viene del header HTTP: loopback, LAN o dominio público generan el
    deep link correcto para dondequiera que se abra la página."""
    scheme = 'https' if tls else 'http'
    man = f'{scheme}://{host}/manifest.json'
    return (f"<html><head><title>conflux</title>{_PAGE_CSS}</head><body>"
            f"<h1>conflux · streams unificados</h1>"
            f"<p class='dim'>Addon agregador: catálogos, streams, metas, subtítulos, "
            f"calendario, Trakt, debrid, torznab y M3U — detrás de un solo manifest.</p>"
            f"<p><a class='btn' href='stremio://{host}/manifest.json' "
            f"style='font-size:19px;padding:12px 24px'>Instalar en Stremio</a></p>"
            f"<p class='dim'>¿No abre la app? Stremio → Addons → pega esta URL en el buscador:<br>"
            f"<code style='background:#1a1a24;padding:4px 8px;border-radius:6px'>{man}</code></p>"
            f"<p><a class='btn' href='/configure'>configurar</a> "
            f"<a class='btn' href='/admin'>admin</a> "
            f"<a class='btn' href='/health'>health</a> "
            f"<a class='btn' href='/manifest.json'>manifest</a></p>"
            f"</body></html>")


def _login_page(to='/admin', err=''):
    """Form de acceso cuando admin_token está activo — el token va en una
    cookie HttpOnly+SameSite=Strict, nunca en la URL (historial/Referer)."""
    err_h = f"<p class='bad'>{err}</p>" if err else ''
    return (f"<html><head><title>conflux · acceso</title>{_PAGE_CSS}</head><body>"
            f"<h1>conflux · acceso</h1>{err_h}"
            f"<form method='post' action='/login'>"
            f"<input type='hidden' name='to' value='{to}'>"
            f"<p>Token de administrador<br>"
            f"<input type='password' name='token' autofocus style='max-width:320px'></p>"
            f"<p><button type='submit'>entrar</button></p></form>"
            f"</body></html>")


def _admin_page():
    now = time.time()
    rows = []
    for n, h in HEALTH.items():
        cd = COOLDOWN.get(n, (0, ''))
        cdtxt = f'{max(0,(cd[0]-now))/60:.0f} min · {cd[1]}' if cd[0] > now else '—'
        sv = _SERVED.get(n, {'n': 0, 'res': {}})
        res_txt = ' '.join(f"{r}:{c}" for r, c in sorted(sv['res'].items(), key=lambda kv: -kv[1])[:4])
        last = h.get('last', 0)
        last_txt = (f"{(now - last) / 60:.0f}min" if last else '—')
        if h.get('err'):
            last_txt += f" · {h['err'][:28]}"
        rows.append(f"<tr><td>{n}</td><td class='ok'>{h.get('ok',0)}</td><td class='bad'>{h.get('fail',0)}</td>"
                    f"<td>{sv['n']}</td><td class='dim'>{res_txt}</td>"
                    f"<td class='dim'>{last_txt}</td><td>{cdtxt}</td></tr>")
    lat = ''.join(f"<tr><td>{k}</td><td>{n2}</td><td>{t/n2:.0f}</td><td>{_pct(list(_rts.get(k,())),.5):.0f}</td>"
                  f"<td>{_pct(list(_rts.get(k,())),.95):.0f}</td><td>{_pct(list(_rts.get(k,())),.99):.0f}</td><td>{x:.0f}</td></tr>"
                  for k, (n2, t, x) in _rtime.items())
    return (f"<html><head><title>admin</title>{_PAGE_CSS}</head><body>"
            f"<h1>Streams Unificados · admin</h1>"
            f"<p class='dim'>sources {sum(1 for m in manifests if m)}/{len(manifests)} up · "
            f"cache streams {len(_cache)} · meta {len(_meta_c)} · cat {len(_cat_c)} · "
            f"<a class='btn' href='/configure'>configure</a></p>"
            f"<h2>Fuentes</h2><table><tr><th>fuente</th><th>ok</th><th>fail</th><th>streams</th><th>por calidad</th><th>último</th><th>circuito</th></tr>{''.join(rows)}</table>"
            f"<h2>Latencia por ruta (ms)</h2><table><tr><th>ruta</th><th>n</th><th>avg</th><th>p50</th><th>p95</th><th>p99</th><th>max</th></tr>{lat}</table>"
            f"<h2>Fails por host</h2><p class='dim'>{' · '.join(f'{k}: {v[0]}' for k, v in _fails.items()) or '—'}</p></body></html>")


def _cfg_trakt_state():
    return '<b class=ok>conectado ✓</b>' if CFG.get('trakt_access_token') else ''


def _cfg_trakt_link():
    if CFG.get('trakt_client_id') and not CFG.get('trakt_access_token'):
        return ('<p><a class="btn" href="/auth/trakt">Conectar cuenta Trakt</a> '
                '<span class=dim>device flow: autorizas una vez en trakt.tv/activate</span></p>')
    return ''


def _configure_page():
    src_chk = ''.join(
        f"<label style='display:flex;gap:8px;padding:4px 0'><input type='checkbox' name='src' value='{s.name}'"
        f"{' checked' if s.name not in _DISABLED else ''} style='width:auto'> {s.name}</label>"
        for s in SOURCES)
    langs = ','.join(CFG.get('stream_langs') or [])
    opts = ''.join(f"<option value='{v}'{' selected' if CFG.get('debrid_provider')==v else ''}>{v or '— ninguno —'}</option>"
                   for v in ('', 'real-debrid', 'torbox', 'alldebrid', 'premiumize',
                             'easydebrid', 'debrid-link', 'offcloud', 'putio'))
    return (f"<html><head><title>configure</title>{_PAGE_CSS}</head><body>"
            f"<h1>Streams Unificados · configuración</h1>"
            f"<p class='dim'>Guarda en config.json. Tras guardar, POST /reload aplica en caliente.</p>"
            f"<form method='post' action='/configure'>"
            f"<h2>Integraciones</h2>"
            f"<p>Trakt <span class='dim'>(app en trakt.tv/oauth/applications — feed global + personal)</span>"
            f" {_cfg_trakt_state()}<br>"
            f"<input name='trakt_client_id' value='{CFG.get('trakt_client_id','')}' placeholder='client_id — vacío = Trakt off'>"
            f"<input name='trakt_client_secret' value='' placeholder='client_secret (no se muestra el guardado)'></p>"
            f"{_cfg_trakt_link()}"
            f"<p>Debrid <span class='dim'>(reproducción instantánea, sin peers)</span><br>"
            f"<select name='debrid_provider'>{opts}</select><br>"
            f"<input name='debrid_key' value='' placeholder='API key (no se muestra la guardada)'></p>"
            f"<p>M3U propio <span class='dim'>(ruta local, catálogo tv 'Mis canales')</span><br>"
            f"<input name='m3u_path' value='{CFG.get('m3u_path','')}' placeholder='/home/…/canales.m3u'></p>"
            f"<p>EPG <span class='dim'>(guía XML/XMLTV .gz para los canales IPTV — vacío = sin guía)</span><br>"
            f"<input name='epg_url' value='{CFG.get('epg_url','')}' placeholder='https://…/epg_ripper_ES1.xml.gz'></p>"
            f"<p>Torznab <span class='dim'>(Prowlarr/Jackett local — fuente extra de streams)</span><br>"
            f"<input name='torznab_url' value='{CFG.get('torznab_url','')}' placeholder='http://localhost:9696/api/v1.0/indexers/all/results/torznab'>"
            f"<input name='torznab_key' value='' placeholder='apikey (no se muestra)'></p>"
            f"<p>Zilean <span class='dim'>(self-hosted: hashlists DMM ya cacheadas en debrid — más ⚡ sin coste)</span><br>"
            f"<input name='zilean_url' value='{CFG.get('zilean_url','')}' placeholder='http://localhost:8181 — vacío = off'></p>"
            f"<p>Newznab / Usenet <span class='dim'>(NZBHydra2, Easynews vía hydra — requiere debrid TorBox)</span><br>"
            f"<input name='newznab_url' value='{CFG.get('newznab_url','')}' placeholder='https://hydra/api — vacío = off'>"
            f"<input name='newznab_key' type='password' value='' placeholder='apikey (no se muestra)'></p>"
            f"<p>Easynews <span class='dim'>(usenet directo — sin TorBox: busca por título y sirve su propio enlace)</span><br>"
            f"<input name='easynews_user' value='{CFG.get('easynews_user','')}' placeholder='usuario'>"
            f"<input name='easynews_pass' type='password' value='' placeholder='contraseña (no se muestra)'></p>"
            f"<p>OpenSubtitles <span class='dim'>(búsqueda con api key; la DESCARGA exige cuenta — "
            f"registro gratis en opensubtitles.com)</span><br>"
            f"<input name='opensubtitles_key' value='' placeholder='api key — vacío = off'>"
            f"<input name='opensubtitles_user' value='{CFG.get('opensubtitles_user','')}' placeholder='usuario (descarga)'>"
            f"<input name='opensubtitles_pass' type='password' value='' placeholder='contraseña (no se muestra)'></p>"
            f"<p>Meses de calendario hacia adelante <input name='calendar_months_ahead' value='{CFG.get('calendar_months_ahead',2)}' style='width:80px'>"
            f" · idiomas streams <input name='stream_langs' value='{langs}' style='width:200px'></p>"
            f"<p>Filtros de stream <span class='dim'>(términos a excluir, coma — p.ej. cam,screener,ts,720p)</span><br>"
            f"<input name='stream_exclude' value='{','.join(CFG.get('stream_exclude') or [])}' placeholder='cam, camrip, hdcam, hdts, ts, screener, telesync'>"
            f"<input name='rpdb_key' value='' placeholder='rpdb apikey — posters con rating (ratingposterdb.com)'></p>"
            f"<p>Términos requeridos <span class='dim'>(comma — solo pasa lo que contenga alguno: es, latino, remux…)</span><br>"
            f"<input name='stream_required' value='{','.join(CFG.get('stream_required') or [])}' placeholder='vacío = todo pasa'>"
            f"<input name='stream_sort' value='{','.join(CFG.get('stream_sort') or [])}' placeholder='orden: res,cached,quality,seeds,size'></p>"
            f"<p>Preferidos <span class='dim'>(boost +300, no filtro: remux, es, atmos…)</span><br>"
            f"<input name='stream_preferred' value='{','.join(CFG.get('stream_preferred') or [])}' placeholder='remux, es, atmos'>"
            f"timeout por fuente <input name='source_timeout' value='{CFG.get('source_timeout',0)}' style='width:60px'>s"
            f" <span class='dim'>(0 = {4}s por defecto)</span></p>"
            f"<p>Formato de stream <span class='dim'>(template con {'{res} {hdr} {codec} {audio} {lang} {rip} {title} {es}'} — vacío = normal)</span><br>"
            f"<input name='stream_format' value='{CFG.get('stream_format','')}' placeholder='&#123;res&#125; · &#123;hdr&#125; · &#123;codec&#125; · &#123;audio&#125; · &#123;lang&#125;'></p>"
            f"<p>Tamaño máx. por stream <input name='stream_max_gb' value='{CFG.get('stream_max_gb',0)}' style='width:80px'> GB"
            f" <span class='dim'>(0 = sin límite — un 4K de 80GB no siempre gana)</span> · "
            f"precargar streams al abrir ficha <input type='hidden' name='stream_prefetch' value='0'>"
            f"<input type='checkbox' name='stream_prefetch' value='1'"
            f"{' checked' if CFG.get('stream_prefetch', True) else ''} style='width:auto'> · "
            f"proxy debrid <input type='hidden' name='debrid_proxy' value='0'>"
            f"<input type='checkbox' name='debrid_proxy' value='1'"
            f"{' checked' if CFG.get('debrid_proxy') else ''} style='width:auto'>"
            f" <span class='dim'>(/dp/: tu IP no toca el debrid — estilo StremThru; consume tu banda)</span> · "
            f"parental <input type='hidden' name='parental' value='0'>"
            f"<input type='checkbox' name='parental' value='1'"
            f"{' checked' if CFG.get('parental') else ''} style='width:auto'>"
            f" <span class='dim'>(fuera contenido adulto: streams + canales M3U)</span> · "
            f"mediafusion <input type='hidden' name='mediafusion' value='0'>"
            f"<input type='checkbox' name='mediafusion' value='1'"
            f"{' checked' if CFG.get('mediafusion') else ''} style='width:auto'>"
            f" <span class='dim'>(regional India/Asia + live TV vía ElfHosted — perfil auto-provisionado)</span></p>"
            f"<h2>Catálogos</h2>"
            f"<p>Listas externas <span class='dim'>(URLs Trakt /lists/, IMDb /list/ o mdblist:ID — separadas por coma)</span><br>"
            f"<input name='catalog_lists' value='{CFG.get('catalog_lists','')}' placeholder='https://trakt.tv/users/me/lists/mis-favs, https://www.imdb.com/list/ls123456/'>"
            f"<input name='mdblist_key' value='' placeholder='mdblist apikey (solo si usas MDBList)'></p>"
            f"<p>Addons extra <span class='dim'>(URLs de addons Stremio — base o .../manifest.json, coma; marketplace-lite: cualquier addon de la comunidad entra como fuente)</span><br>"
            f"<input name='extra_sources' value='{','.join(CFG.get('extra_sources') or [])}' placeholder='https://addon.ejemplo.com, https://otro.org/manifest.json'></p>"
            f"<p>Catálogos ocultos <span class='dim'>(ids separados por coma — visibles en /health &gt; manifest)</span><br>"
            f"<input name='catalogs_disabled' value='{','.join(CFG.get('catalogs_disabled') or [])}' placeholder='sid_catalogo1, sid_catalogo2'></p>"
            f"<h2>Fuentes activas</h2>"
            f"<p class='dim'>Orden en Discover (nombres separados por coma, vacío = orden fijo):<br>"
            f"<input name='source_order' value='{','.join(CFG.get('source_order') or [])}' placeholder='Cinemeta,TMDB,Kitsu'></p>"
            f"{src_chk}"
            f"<p><button type='submit'>Guardar</button> <a class='btn' href='/admin'>dashboard</a></p>"
            f"</form>"
            f"<h2>Addon con config en URL</h2>"
            f"<p class='dim'>Estilo Torrentio: instala esta URL en otro perfil/dispositivo y lleva "
            f"idiomas/filtros/rpdb incrustados (sin tocar config.json):</p>"
            f"<p><code id='cfgurl' style='word-break:break-all;background:#1a1a24;padding:6px;border-radius:6px'></code></p>"
            f"<p><a class='btn' id='cfginstall'>instalar este addon</a></p>"
            f"<script>function _b64u(s){{return btoa(unescape(encodeURIComponent(s)))"
            f".replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');}}"
            f"function _gen(){{var o={{}};"
            f"var sl=document.querySelector('[name=stream_langs]').value;"
            f"if(sl)o.stream_langs=sl.split(',').map(x=>x.trim()).filter(Boolean);"
            f"var se=document.querySelector('[name=stream_exclude]').value;"
            f"if(se)o.stream_exclude=se.split(',').map(x=>x.trim()).filter(Boolean);"
            f"var rk=document.querySelector('[name=rpdb_key]').value;if(rk)o.rpdb_key=rk;"
            f"var dp=document.querySelector('[name=debrid_provider]').value;if(dp)o.debrid_provider=dp;"
            f"var dk=document.querySelector('[name=debrid_key]').value;if(dk)o.debrid_key=dk;"
            f"['opensubtitles_key','opensubtitles_user','opensubtitles_pass','mdblist_key']"
            f".forEach(k=>{{var e=document.querySelector('[name='+k+']');if(e&&e.value)o[k]=e.value;}});"
            f"['source_order','catalogs_disabled','stream_required','stream_sort','stream_preferred'].forEach(k=>{{var e=document.querySelector('[name='+k+']');"
            f"if(e&&e.value)o[k]=e.value.split(',').map(x=>x.trim()).filter(Boolean);}});"
            f"var cm=document.querySelector('[name=calendar_months_ahead]').value;"
            f"var ci=parseInt(cm,10);if(!isNaN(ci)&&ci>=1&&ci<=24)o.calendar_months_ahead=ci;"
            f"var mg=document.querySelector('[name=stream_max_gb]').value;"
            f"var mi=parseInt(mg,10);if(!isNaN(mi)&&mi>0)o.stream_max_gb=mi;"
            f"var st=document.querySelector('[name=source_timeout]').value;"
            f"var si=parseInt(st,10);if(!isNaN(si)&&si>0)o.source_timeout=si;"
            f"var pf=document.querySelectorAll('[name=stream_prefetch]');"
            f"if(pf.length&&pf[pf.length-1].checked===false)o.stream_prefetch=false;"
            f"var dx=document.querySelectorAll('[name=debrid_proxy]');"
            f"if(dx.length&&dx[dx.length-1].checked)o.debrid_proxy=true;"
            f"var px=document.querySelectorAll('[name=parental]');"
            f"if(px.length&&px[px.length-1].checked)o.parental=true;"
            f"var mf=document.querySelectorAll('[name=mediafusion]');"
            f"if(mf.length&&mf[mf.length-1].checked===false)o.mediafusion=false;"
            f"var sf=document.querySelector('[name=stream_format]');if(sf&&sf.value)o.stream_format=sf.value;"
            f"var sd=[];document.querySelectorAll('[name=src]').forEach(c=>{{if(!c.checked)sd.push(c.value);}});"
            f"if(sd.length)o.sources_disabled=sd;"
            f"var b=_b64u(JSON.stringify(o));"
            f"document.getElementById('cfgurl').textContent=location.origin+'/cfg/'+b+'/manifest.json';"
            f"document.getElementById('cfginstall').href='stremio://'+location.host+'/cfg/'+b+'/manifest.json';}}"
            f"document.querySelectorAll('input,select').forEach(e=>e.addEventListener('input',_gen));_gen();</script>"
            f"</body></html>")


def _trakt_auth_page():
    """Device Authorization: muestra user_code, el usuario lo introduce en
    trakt.tv/activate y el poll en background guarda el token."""
    if not CFG.get('trakt_client_id') or not CFG.get('trakt_client_secret'):
        return (f"<html><head><title>trakt</title>{_PAGE_CSS}</head><body>"
                f"<h1>Conectar Trakt</h1><p>Faltan credenciales de app: crea una en "
                f"<a href='https://trakt.tv/oauth/applications/new' style='color:#7c5ce0'>"
                f"trakt.tv/oauth/applications/new</a> (redirect URI: <code>urn:ietf:wg:oauth:2.0:oob</code>)"
                f" y guarda client_id + client_secret en <a class='btn' href='/configure'>/configure</a>.</p></body></html>")
    if CFG.get('trakt_access_token'):
        exp = time.strftime('%Y-%m-%d', time.localtime(CFG.get('trakt_expires', 0)))
        return (f"<html><head><title>trakt</title>{_PAGE_CSS}</head><body>"
                f"<h1>Trakt conectado</h1><p class='ok'>Token activo (expira {exp}).</p>"
                f"<p><a class='btn' href='/configure'>volver</a></p></body></html>")
    d = _trakt_device_start() if not _TRAKT_DEV else _TRAKT_DEV
    if not d:
        return (f"<html><head><title>trakt</title>{_PAGE_CSS}</head><body>"
                f"<h1>Trakt</h1><p class='bad'>No se pudo iniciar el device flow.</p></body></html>")
    # auth.trakt.tv/activate (lo que devuelve la API) redirige a app.trakt.tv
    # que da 404 — la página real es trakt.tv/activate
    url = 'https://trakt.tv/activate'
    return (f"<html><head><title>trakt</title>{_PAGE_CSS}</head><body>"
            f"<h1>Autorizar en Trakt</h1>"
            f"<p>1. Abre <a href='{url}' style='color:#7c5ce0'>{url}</a><br>"
            f"2. Introduce el código:</p>"
            f"<p style='font-size:34px;letter-spacing:4px;color:#7c5ce0'><b>{d['user_code']}</b></p>"
            f"<p id='st' class='dim'>esperando autorización…</p>"
            f"<script>setInterval(()=>fetch('/auth/trakt/status').then(r=>r.json()).then(d=>{{"
            f"if(d.state==='ok'){{document.getElementById('st').innerHTML=\"<span class=ok>Conectado — token guardado</span>\";}}"
            f"if(d.state==='none')document.getElementById('st').textContent='expirado — recarga para reintentar';"
            f"}}),4000)</script></body></html>")
