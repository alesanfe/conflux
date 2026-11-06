"""AppContext: agrupa el estado mutable de ejecución en una instancia
inyectable (CONFLUX-001).

`conflux.core.state` expone los contenedores del contexto por defecto
(composition root) con la misma semántica de siempre — los nombres de módulo
son referencias a los objetos del contexto, así la mutación in-situ sigue
funcionando (`manifests[:] =`, `CFG.clear()`). Tests y futuros servicios
pueden crear contextos aislados que no comparten estado entre sí.
"""
import threading


class SourceRegistry:
    """Fuentes declaradas + manifests resueltos + deshabilitadas."""
    def __init__(self):
        self.sources = []          # [Source]
        self.manifests = []        # [dict | None] — paralelo a sources
        self.disabled = set()


class FailureRegistry:
    """Salud por fuente: aciertos, fallos y rachas (decisiones de cooldown)."""
    def __init__(self):
        self.health = {}           # fuente -> {'ok','fail','last','err','streak',...}


class CircuitBreakerRegistry:
    """Disyuntor por host + cooldowns por fuente + opciones muertas."""
    def __init__(self):
        self.fails = {}            # host -> (strikes_consecutivos, último_ts)
        self.cooldown = {}         # fuente -> (ts_hasta, motivo)
        self.dead_opt = set()      # (cid, genre) declarada pero siempre vacía


class RuntimeMetrics:
    """Latencias, throughput por fuente y contadores del servidor."""
    def __init__(self):
        self.rtime = {}            # ruta -> (n, total_ms, max_ms)
        self.rts = {}              # ruta -> deque(últimas 512 latencias)
        self.served = {}           # fuente -> {'n', 'res': {res: n}}
        self.login_tries = {}      # ip -> deque(intentos /login)


class AppContext:
    """Todo el estado mutable de ejecución, inyectable y destruible.

    Dos instancias no comparten contenedores: mutar `ctx1.sources.sources`
    no afecta a `ctx2`. `shutdown_pools()` es idempotente y un contexto
    puede re-registrar pools tras cerrarse (recovery)."""
    def __init__(self):
        self.sources = SourceRegistry()
        self.failures = FailureRegistry()
        self.breakers = CircuitBreakerRegistry()
        self.metrics = RuntimeMetrics()
        self.cfg = {}              # config efectiva
        self.caches: dict = {'stream': {}, 'meta': {}, 'catalog': {}}
        self.sessions = {}         # host -> requests.Session
        self.sessions_lock = threading.Lock()
        self._pools = []
        self._closed = False

    # ---- pools (propiedad del contexto, no singletons) ----
    def register_pool(self, pool):
        """Registra un executor para que shutdown_pools lo cierre."""
        if self._closed:
            self._closed = False            # recovery: contexto reusado
        self._pools.append(pool)
        return pool

    def shutdown_pools(self):
        """Cierra los pools registrados — idempotente."""
        if self._closed:
            return
        self._closed = True
        for p in self._pools:
            try:
                p.shutdown(wait=False, cancel_futures=True)
            except Exception:               # noqa: BLE001 — cierre best-effort
                pass

    @property
    def closed(self):
        return self._closed


def default_context():
    """Composition root: envuelve los contenedores REALES de
    `conflux.core.state` en un AppContext — los objetos compartidos por el
    runtime no dejan de ser globales de módulo, pero ahora tienen dueño:
    el contexto por defecto. Cualquier consumidor puede pedir
    `AppContext()` aislado (tests) o `default_context()` (producción)
    sin re-escribir imports."""
    from conflux.core import state as st      # import tardío: ctx -> state -> (sin ctx)
    return st.CTX
