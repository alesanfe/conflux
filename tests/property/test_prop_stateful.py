# pylint: disable=missing-docstring,protected-access,import-outside-toplevel,multiple-statements,unused-argument,invalid-name,redefined-outer-name,expression-not-assigned,too-many-public-methods,duplicate-code,non-ascii-name,wrong-import-position,too-few-public-methods
"""Stateful testing (hypothesis RuleBasedStateMachine): el disyuntor por
host es una maquina de estados — los tests de caja inyectan estados, esta
maquina busca SECUENCIAS de operaciones que violen los invariantes.

Modelo espejo de _fails[host] = (strikes, ts):
  - fetch falla      -> strikes += 2 (dos intentos), ts = ahora
  - fetch abierto    -> NI toca la red NI cambia el estado (fail-fast)
  - fetch exito      -> entrada eliminada (strikes a cero)
  - ts > HOST_CD     -> el breaker se rearma aunque strikes >= HOST_OPEN
"""
import os
import types
import unittest
from unittest.mock import patch

import requests
from hypothesis import HealthCheck, settings
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule
from hypothesis import strategies as st

from conflux.infra import net
from tests import fake_session

_MAX = settings(
    max_examples=int(os.getenv('HYP_EXAMPLES', '40')),
    deadline=None,
    stateful_step_count=int(os.getenv('HYP_STEPS', '15')),
    suppress_health_check=list(HealthCheck),
)

_HOST = 'machine.test'


@_MAX
class BreakerMachine(RuleBasedStateMachine):
    """Modelo vs implementacion: tras CADA regla, _fails[host] real debe
    coincidir con el modelo (strikes, ts) y el observable (¿toca red?)."""

    def __init__(self):
        super().__init__()
        self._saved = dict(net._fails)
        net._fails.clear()
        # reloj controlado: net.time es el modulo time importado por net.py —
        # lo sustituimos por un namespace solo dentro de net (resto del
        # proceso sigue con el reloj real).
        self.clock = [1000.0]
        self.ok = False                     # sesion en modo fallo
        self.calls = 0                      # llamadas a _session observadas
        # modelo: lo que _fails[host] DEBERIA valer
        self.strikes = 0
        self.last_ts = 0.0
        self._p_time = patch.object(net, 'time', types.SimpleNamespace(
            time=lambda: self.clock[0], sleep=lambda _s: None))
        self._p_sess = patch.object(net, '_session', self._session)
        self._p_time.start()
        self._p_sess.start()

    def teardown(self):
        self._p_sess.stop()
        self._p_time.stop()
        net._fails.clear()
        net._fails.update(self._saved)

    # ---- infra ----
    def _session(self, host):
        self.calls += 1
        s, _ = fake_session({'json': {'ok': 1}} if self.ok
                            else requests.ConnectionError('x'))
        return s

    def _model_open(self):
        return self.strikes >= net.HOST_OPEN \
            and self.clock[0] - self.last_ts < net.HOST_CD

    # ---- reglas ----
    @rule()
    def fetch_falla(self):
        calls0 = self.calls
        self.ok = False
        assert net.fetch(f'http://{_HOST}/x', 1) is None
        if self._model_open():
            assert self.calls == calls0,                 'breaker abierto no debe tocar la red'
        else:
            assert self.calls > calls0, 'intentos reales'
            self.strikes += 2                    # 1 reintento = 2 intentos
            self.last_ts = self.clock[0]

    @rule()
    def fetch_ok(self):
        calls0 = self.calls
        self.ok = True
        r = net.fetch(f'http://{_HOST}/x', 1)
        if self._model_open():
            assert r is None
            assert self.calls == calls0
        else:
            assert r is not None
            self.strikes = 0                     # exito: entrada eliminada

    @rule(s=st.integers(min_value=1, max_value=120))
    def tick(self, s):
        self.clock[0] += s

    # ---- invariantes ----
    @invariant()
    def modelo_coincide(self):
        real = net._fails.get(_HOST)
        if self.strikes == 0:
            assert real is None, 'strikes 0 -> entrada ausente'
        else:
            assert real is not None
            assert real[0] == self.strikes, 'strikes divergen'
            assert abs(real[1] - self.last_ts) < 0.001, 'ts diverge'

    @invariant()
    def fetch_respeta_breaker(self):
        """Sonda: un fetch en modo fallo toca la red si y solo si el
        modelo dice breaker cerrado."""
        calls0 = self.calls
        self.ok = False
        net.fetch(f'http://{_HOST}/x', 1)
        if self._model_open():
            assert self.calls == calls0
        else:
            # la sonda misma acumula strikes — sincronizar el modelo
            self.strikes += 2
            self.last_ts = self.clock[0]
            assert self.calls > calls0


TestBreaker = BreakerMachine.TestCase

if __name__ == '__main__':
    unittest.main()
