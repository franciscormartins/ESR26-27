"""Testes automáticos dos cenários e demonstrações guiadas.

Cada teste faz uma ação (matar o ``S1``, congelar um nó, atrasar uma ligação…), espera pela reação do overlay e compara com o
esperado, medindo tempos a partir dos instantes exatos dos eventos de cada nó (não da hora a que o painel olhou). Os testes
repõem o cenário no fim (nós relançados, rede sem atrasos), por isso podem correr em sequência e repetir-se.

    ottctl test cenario4            # todos os que o modo permite
    ottctl test cenario4 A1 A3 --repeat 10 --md

Os que precisam de ``tc``/``ip link`` só correm com ``--backend core``.
"""
import statistics
import time

from .backend import BackendError

SETTLE_TIMEOUT = 75


class TestFailure(Exception):
    """Uma condição esperada não aconteceu a tempo."""


# ---------------------------------------------------------------- leitura do estado

def status(state, node):
    return state["nodes"][node]["status"] or {}


def health(state, node):
    return state["nodes"][node]["health"]


def flow(state, client, key):
    for f in status(state, client).get("flows", []):
        if f"{f['origin']}:{f['flow']}" == key:
            return f
    return None


def receiving(state, client, key, minimum=5):
    f = flow(state, client, key)
    return bool(f) and f["rate"] >= minimum and not f["stalled"]


def route(state, node, key):
    for r in status(state, node).get("routes", []):
        if f"{r['origin']}:{r['flow']}" == key:
            return r
    return None


def upstream(state, node, key):
    r = route(state, node, key)
    return r["upstream"] if r else None


def neighbors(state, node):
    return {n["id"] for n in status(state, node).get("neighbors", [])}


def rtt(state, node, peer):
    for n in status(state, node).get("neighbors", []):
        if n["id"] == peer:
            return n["rtt_ms"]
    return None


def event_times(state, node, kind, route=None, peer=None, after=0.0):
    """Instantes (dos próprios nós) dos eventos de um tipo, depois de ``after``."""
    out = []
    for e in status(state, node).get("events", []):
        if e["kind"] == kind and e["t"] >= after and (route is None or e.get("route") == route) \
                and (peer is None or e.get("peer") == peer):
            out.append(e["t"])
    return out


def switched_to(state, node, key, new, after=0.0):
    """Instante (do próprio nó) em que a rota ``key`` passou a ter ``new`` como montante, depois de ``after``."""
    found = []
    for e in status(state, node).get("events", []):
        if e["t"] < after or e.get("route") != key or e["kind"] not in ("montante", "rota_recuperada", "rota_nova"):
            continue
        target = e["text"].rsplit("→", 1)[-1].strip() if e["kind"] == "montante" else e["text"].split("via ")[-1].split()[0]
        if target == new:
            found.append(e["t"])
    return min(found) if found else None


def interruption(state, node, key, after=0.0):
    """Maior interrupção (s) do fluxo ``key`` em ``node`` depois de ``after`` (0 se não houve)."""
    gaps = [e.get("gap", 0) for e in status(state, node).get("events", [])
            if e["t"] >= after and e.get("route") == key and e["kind"] in ("fluxo_voltou", "fluxo_lacuna")]
    return max(gaps) if gaps else 0.0


def all_receiving(state, plan):
    for spec in plan.nodes.values():
        if spec.spare:
            continue
        if health(state, spec.id) != "up":
            return False
        if not all(receiving(state, spec.id, k) for k in spec.subscribe):
            return False
    return True


# ---------------------------------------------------------------- contexto e resultados

class Ctx:
    """O que um teste usa: orquestrador, esperas, verificações e medições."""

    def __init__(self, orch, poll=0.25):
        self.orch = orch
        self.plan = orch.plan
        self.poll = poll
        self.checks, self.measures, self.notes = [], [], []
        self._deferred = []

    # -- estado e esperas
    def state(self):
        return self.orch.state()

    def wait(self, cond, timeout, what):
        """Espera até ``cond(estado)`` ser verdadeira; devolve (segundos esperados, estado)."""
        t0 = time.time()
        while True:
            st = self.state()
            if cond(st):
                return time.time() - t0, st
            if time.time() - t0 > timeout:
                raise TestFailure(f"não aconteceu em {timeout:.0f} s: {what}")
            time.sleep(self.poll)

    def sleep(self, seconds):
        time.sleep(seconds)
        return self.state()

    def act(self, fn):
        """Executa uma ação e devolve a hora a que começou."""
        t0 = time.time()
        fn()
        return t0

    # -- precondição e reposição
    def settle(self, timeout=SETTLE_TIMEOUT):
        """Põe o cenário no estado normal: nós lançados e todos os clientes a receber."""
        st = self.state()
        if all(health(st, s.id) == "off" for s in self.plan.nodes.values()):
            self.orch.up()
        else:
            self.repair(st)
        _dt, st = self.wait(lambda s: all_receiving(s, self.plan), timeout, "todos os clientes a receber os seus fluxos")
        return st

    def repair(self, st=None):
        st = st or self.state()
        for spec in self.plan.nodes.values():
            h = health(st, spec.id)
            if h in ("frozen", "stale"):
                try:
                    self.orch.thaw([spec.id])
                except BackendError:
                    pass
            elif spec.spare:
                if h == "up":
                    self.orch.stop([spec.id])
            elif h in ("dead", "stopped", "off") and self.orch.backend.alive(spec) is not True:
                try:
                    self.orch.start([spec.id])
                except BackendError:
                    pass

    def require_route(self, node, key, expected, timeout=70):
        """Precondição: espera que ``node`` receba ``key`` pelo montante ``expected`` (as rotas só mudam nos anúncios)."""
        _dt, st = self.wait(lambda s: upstream(s, node, key) == expected and receiving(s, node, key), timeout,
                            f"{node} receber {key} por {expected}")
        return st

    # -- resultados
    def check(self, name, ok, detail=""):
        self.checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})
        return bool(ok)

    def measure(self, name, value, unit="s", expect=None):
        """Regista uma medição; ``expect=(mín, máx)`` torna-a uma verificação."""
        ok = None if expect is None else (value is not None and expect[0] <= value <= expect[1])
        self.measures.append({"name": name, "value": None if value is None else round(value, 2), "unit": unit,
                              "expect": list(expect) if expect else None, "ok": ok})
        return ok is not False

    def note(self, text):
        self.notes.append(text)

    def defer(self, fn):
        self._deferred.append(fn)

    def finish(self):
        for fn in reversed(self._deferred):
            try:
                fn()
            except BackendError as exc:
                self.notes.append(f"limpeza: {exc}")
        self._deferred = []
        try:
            if self.orch.net_effects:
                self.orch.clear_network()
            time.sleep(0.6)    # dá tempo aos nós acabados de lançar para escreverem o primeiro estado
            self.settle()
        except (TestFailure, BackendError) as exc:
            self.notes.append(f"o cenário não voltou ao normal: {exc}")

    # -- ajudas de rede
    def need_core(self):
        if self.orch.backend.name != "core":
            raise Skip("precisa do CORE (--backend core)")

    def underlay_link(self, a, b):
        """Ligação direta do underlay entre os routers dos hosts ``a`` e ``b`` (ids overlay): (ra, ia, rb, ib)."""
        und = self.orch.underlay()
        ca, cb = self.plan.nodes[a].core, self.plan.nodes[b].core

        def router_of(core):
            for l in und["links"]:
                if l["a"] == core:
                    return l["b"], l["ib"], l["ia"]
                if l["b"] == core:
                    return l["a"], l["ia"], l["ib"]
            return None, None, None
        ra, rb = router_of(ca)[0], router_of(cb)[0]
        for l in und["links"]:
            if (l["a"], l["b"]) == (ra, rb):
                return l["a"], l["ia"], l["b"], l["ib"]
            if (l["a"], l["b"]) == (rb, ra):
                return l["b"], l["ib"], l["a"], l["ia"]
        raise TestFailure(f"não há ligação direta no underlay entre {ra} e {rb}")

    def host_link(self, node):
        """(router, iface do router, host, iface do host) da ligação de um host ao seu router."""
        und = self.orch.underlay()
        core = self.plan.nodes[node].core
        for l in und["links"]:
            if l["a"] == core:
                return l["b"], l["ib"], l["a"], l["ia"]
            if l["b"] == core:
                return l["a"], l["ia"], l["b"], l["ib"]
        raise TestFailure(f"{node} não tem ligação no underlay")


class Skip(Exception):
    pass


TESTS = []


def test(test_id, title, scenarios=None, core=False, slow=False):
    def wrap(fn):
        TESTS.append({"id": test_id, "title": title, "scenarios": scenarios, "core": core, "slow": slow, "fn": fn,
                      "doc": (fn.__doc__ or "").strip()})
        return fn
    return wrap


def tests_for(plan, backend="local"):
    out = []
    for t in TESTS:
        if t["scenarios"] is None or plan.name in t["scenarios"]:
            out.append({k: v for k, v in t.items() if k != "fn"} | {"runs_here": backend == "core" or not t["core"]})
    return out


# ---------------------------------------------------------------- testes genéricos

@test("G1", "Arranque: vizinhos esperados e todos os clientes a receber")
def g1(ctx):
    """Para e relança o cenário; cada nó deve ficar com os vizinhos do ficheiro overlay."""
    ctx.orch.down()
    ctx.sleep(1.5)
    t0 = ctx.act(ctx.orch.up)
    dt, st = ctx.wait(lambda s: all_receiving(s, ctx.plan), 70, "todos os clientes a receber")
    ctx.measure("do arranque até todos os clientes receberem", dt, expect=(0, 45))
    running = {s.id for s in ctx.plan.nodes.values() if not s.spare}
    wrong = []
    for spec in ctx.plan.nodes.values():
        if spec.spare:
            continue
        expect = {b for a, b in _directed(ctx.plan) if a == spec.id and b in running}
        if neighbors(st, spec.id) != expect:
            wrong.append(f"{spec.id}: {sorted(neighbors(st, spec.id))} em vez de {sorted(expect)}")
    ctx.check("cada nó tem exatamente os vizinhos do ficheiro overlay", not wrong, "; ".join(wrong))


def _directed(plan):
    for a, b in plan.edges():
        yield a, b
        yield b, a


@test("G2", "Cai e volta um nó intermédio (deteção e reentrada)")
def g2(ctx):
    """Mata o intermédio com mais vizinhos: os vizinhos veem-no cair logo (o TCP fecha) e voltam a ligar-se quando regressa."""
    st = ctx.settle()
    cand = [s for s in ctx.plan.nodes.values() if not s.serve and not s.spare and not s.subscribe]
    if not cand:
        raise Skip("o cenário não tem nós intermédios")
    mid = max(cand, key=lambda s: len(neighbors(st, s.id))).id
    peers = neighbors(st, mid)
    ctx.note(f"nó escolhido: {mid} (vizinhos {sorted(peers)})")
    t0 = ctx.act(lambda: ctx.orch.kill([mid]))
    ctx.wait(lambda s: all(event_times(s, p, "vizinho_down", peer=mid, after=t0) for p in peers), 15,
             f"os vizinhos de {mid} verem-no cair")
    st = ctx.state()
    detect = max(min(event_times(st, p, "vizinho_down", peer=mid, after=t0)) for p in peers) - t0
    ctx.measure(f"deteção da queda de {mid} (último vizinho)", detect, expect=(0, 3))
    t1 = ctx.act(lambda: ctx.orch.start([mid]))
    ctx.wait(lambda s: all(event_times(s, p, "vizinho_up", peer=mid, after=t1) for p in peers), 40,
             f"os vizinhos de {mid} voltarem a ligá-lo")
    st = ctx.state()
    back = max(min(event_times(st, p, "vizinho_up", peer=mid, after=t1)) for p in peers) - t1
    ctx.measure(f"reentrada de {mid} (último vizinho)", back, expect=(0, 20))


# ---------------------------------------------------------------- cenário 4: falha de servidores

C4 = ("cenario4",)


@test("A1", "Cai o S1, que é caminho de passagem do filme_c do C1", C4)
def a1(ctx):
    """O filme_c do C1 vinha por S2–S1–C1: sem o S1 tem de passar a S2–O3–O1–C1. Depois o S1 volta."""
    ctx.settle()
    ctx.require_route("C1", "S2:filme_c", "S1")      # o caminho curto (2 saltos), pelo S1
    t0 = ctx.act(lambda: ctx.orch.kill(["S1"]))
    dt, st = ctx.wait(lambda s: upstream(s, "C1", "S2:filme_c") == "O1" and receiving(s, "C1", "S2:filme_c"), 45,
                      "o C1 voltar a receber o S2:filme_c por O1")
    when = switched_to(st, "C1", "S2:filme_c", "O1", t0)
    ctx.measure("C1 passa a receber o filme_c por S2–O3–O1–C1 (rota nova)", (when - t0) if when else dt, expect=(0, 20))
    st = ctx.sleep(1.2)
    ctx.measure("interrupção do filme_c no C1 (0 = a reparação rápida foi mais depressa que 1 s)",
                interruption(st, "C1", "S2:filme_c", t0), expect=(0, 15))
    ctx.check("o S1:filme_a deixa de chegar ao C1", not receiving(st, "C1", "S1:filme_a"))
    ctx.check("os clientes registam o WITHDRAW do S1:filme_a",
              sum(1 for n in ("C1", "C2", "C3", "C4") if event_times(st, n, "withdraw", route="S1:filme_a", after=t0)) >= 3)
    for c in ("C2", "C4"):
        ctx.check(f"{c} não perde o S2:filme_c", not event_times(st, c, "fluxo_parou", route="S2:filme_c", after=t0))
    t1 = ctx.act(lambda: ctx.orch.start(["S1"]))
    ctx.wait(lambda s: receiving(s, "C1", "S1:filme_a") and upstream(s, "C1", "S2:filme_c") == "S1", 60,
             "o S1 voltar e o C1 regressar ao caminho mais curto")
    st = ctx.state()
    back = switched_to(st, "C1", "S2:filme_c", "S1", t1)
    ctx.measure("S1 volta: o filme_c do C1 regressa ao caminho mais curto (pelo S1)", (back - t1) if back else None,
                expect=(0, 40))
    swaps = event_times(st, "C1", "montante", route="S2:filme_c", after=t1)
    ctx.check("o filme_c do C1 troca de montante uma só vez ao regressar ao S1", len(swaps) <= 2, f"{len(swaps)} trocas")


@test("A2", "Cai o S2, que é caminho de passagem do filme_a do C2", C4)
def a2(ctx):
    """Espelho do A1: o filme_a do C2 vinha por S1–S2–C2 e passa a S1–O1–O3–C2."""
    ctx.settle()
    ctx.require_route("C2", "S1:filme_a", "S2")
    t0 = ctx.act(lambda: ctx.orch.kill(["S2"]))
    dt, st = ctx.wait(lambda s: upstream(s, "C2", "S1:filme_a") == "O3" and receiving(s, "C2", "S1:filme_a"), 45,
                      "o C2 voltar a receber o S1:filme_a por O3")
    when = switched_to(st, "C2", "S1:filme_a", "O3", t0)
    ctx.measure("C2 passa a receber o filme_a por S1–O1–O3–C2 (rota nova)", (when - t0) if when else dt, expect=(0, 20))
    st = ctx.sleep(1.2)
    ctx.measure("interrupção do filme_a no C2", interruption(st, "C2", "S1:filme_a", t0), expect=(0, 15))
    ctx.check("o S2:filme_c deixa de chegar ao C2", not receiving(st, "C2", "S2:filme_c"))


@test("A3", "Réplica: o C3 sobrevive à queda do S1 com o filme_b do S2", C4)
def a3(ctx):
    """O C3 subscreve o filme_b aos dois servidores; sem o S1 continua a receber o do S2."""
    st = ctx.settle()
    before = upstream(st, "C3", "S2:filme_b")
    t0 = ctx.act(lambda: ctx.orch.kill(["S1"]))
    st = ctx.sleep(14)
    ctx.note(f"caminho do S2:filme_b até ao C3 antes da falha: via {before}")
    ctx.measure("interrupção do S2:filme_b no C3 (0 se o caminho não passava pelo S1)",
                interruption(st, "C3", "S2:filme_b", t0), expect=(0, 12))
    ctx.check("o C3 continua a receber o S2:filme_b", receiving(st, "C3", "S2:filme_b"))
    ctx.check("o S1:filme_b deixou de chegar", not receiving(st, "C3", "S1:filme_b"))


@test("A5", "Falha silenciosa: S1 congelado (só o timeout o deteta)", C4)
def a5(ctx):
    """Com kill -STOP o TCP não fecha: o redirecionamento demora o tempo do keepalive (5 a 8 s) a mais."""
    ctx.settle()
    ctx.require_route("C1", "S2:filme_c", "S1")
    t0 = ctx.act(lambda: ctx.orch.freeze(["S1"]))
    dt, st = ctx.wait(lambda s: upstream(s, "C1", "S2:filme_c") == "O1" and receiving(s, "C1", "S2:filme_c"), 60,
                      "o C1 voltar a receber o S2:filme_c por O1")
    when = switched_to(st, "C1", "S2:filme_c", "O1", t0)
    ctx.measure("C1 passa a receber o filme_c por O1 (S1 congelado)", (when - t0) if when else dt, expect=(3, 30))
    ctx.note("a rota mudou com o anúncio seguinte, antes de o timeout do keepalive dar o S1 como caído")
    ctx.wait(lambda s: any(event_times(s, p, "vizinho_down", peer="S1", after=t0) for p in ("C1", "O1", "O2", "S2")), 25,
             "algum vizinho dar o S1 como caído pelo timeout")
    st = ctx.sleep(1.2)
    downs = [min(event_times(st, p, "vizinho_down", peer="S1", after=t0) or [0]) for p in ("C1", "O1", "O2", "S2")]
    seen = [d - t0 for d in downs if d]
    ctx.measure("deteção do S1 congelado pelo primeiro vizinho (timeout do keepalive)", min(seen) if seen else None,
                expect=(3, 12))
    ctx.measure("interrupção do filme_c no C1 (S1 congelado)", interruption(st, "C1", "S2:filme_c", t0), expect=(0, 25))
    ctx.orch.thaw(["S1"])


# ---------------------------------------------------------------- cenário 4: entradas, saídas e bootstrap

@test("C1", "Entrada do nó suplente O5 com o overlay a funcionar", C4)
def c1(ctx):
    ctx.settle()
    t0 = ctx.act(lambda: ctx.orch.start(["O5"]))
    ctx.defer(lambda: ctx.orch.stop(["O5"]))
    dt, st = ctx.wait(lambda s: neighbors(s, "O5") == {"O1", "O4"} and "O5" in neighbors(s, "O1")
                      and "O5" in neighbors(s, "O4"), 30, "O5 ligar-se a O1 e O4 (e eles verem-no)")
    ctx.measure("O5 liga-se a O1 e O4", dt, expect=(0, 20))
    st = ctx.sleep(4)
    ctx.check("nenhum cliente perde imagens com a entrada",
              not any(event_times(st, c, "fluxo_parou", after=t0) for c in ("C1", "C2", "C3", "C4")))


@test("C2", "Um cliente sai e volta a entrar sem afetar os outros", C4)
def c2(ctx):
    ctx.settle()
    t0 = ctx.act(lambda: ctx.orch.stop(["C4"]))
    st = ctx.sleep(7)
    ctx.check("os outros clientes não perdem imagens",
              not any(event_times(st, c, "fluxo_parou", after=t0) for c in ("C1", "C2", "C3")))
    t1 = ctx.act(lambda: ctx.orch.start(["C4"]))
    dt, st = ctx.wait(lambda s: receiving(s, "C4", "S1:filme_a") and receiving(s, "C4", "S2:filme_c"), 40, "C4 a receber")
    ctx.measure("C4 volta a receber os dois fluxos", dt, expect=(0, 30))


@test("C3", "O servidor só emite os fluxos que alguém subscreve", C4)
def c3(ctx):
    ctx.settle()
    t0 = ctx.act(lambda: ctx.orch.stop(["C1", "C2", "C4"]))
    dt, st = ctx.wait(lambda s: (route(s, "S1", "S1:filme_a") or {}).get("state") == "inativa"
                      and (route(s, "S2", "S2:filme_c") or {}).get("state") == "inativa", 40,
                      "S1:filme_a e S2:filme_c ficarem sem procura")
    done = [min(event_times(st, srv, "rota_estado", route=key, after=t0) or [t0 + dt])
            for srv, key in (("S1", "S1:filme_a"), ("S2", "S2:filme_c"))]
    ctx.measure("os fluxos sem subscritores ficam inativos nos servidores", max(done) - t0, expect=(0, 30))
    ctx.check("o filme_b continua ativo (o C3 subscreve-o)", (route(st, "S1", "S1:filme_b") or {}).get("state") == "ativa")


@test("C4", "Ordem trocada: os clientes arrancam antes dos servidores", C4, slow=True)
def c4(ctx):
    ctx.orch.down()
    ctx.sleep(1.5)
    others = [s.id for s in ctx.plan.nodes.values() if not s.serve and not s.spare]
    servers = [s.id for s in ctx.plan.nodes.values() if s.serve]
    ctx.orch.up(only=others)
    ctx.wait(lambda s: all(health(s, n) == "waiting" for n in others), 20,
             "os nós ficarem à espera do bootstrapper")
    st = ctx.sleep(3)
    ctx.check("os nós ficam à espera do bootstrapper (ainda não há servidores)", all(health(st, n) == "waiting" for n in others))
    t0 = ctx.act(lambda: ctx.orch.up(only=servers))
    dt, st = ctx.wait(lambda s: all_receiving(s, ctx.plan), 70, "todos a receber")
    ctx.measure("dos servidores arrancarem até todos receberem", dt, expect=(0, 45))


@test("C5", "Bootstrapper principal em baixo: um nó volta a entrar pelo S2", C4)
def c5(ctx):
    ctx.settle()
    expect = neighbors(ctx.state(), "O3")
    ctx.orch.kill(["S1"])
    ctx.orch.stop(["O3"])
    ctx.sleep(2)
    t0 = ctx.act(lambda: ctx.orch.start(["O3"], bootstrap="S2"))
    dt, st = ctx.wait(lambda s: neighbors(s, "O3") >= (expect - {"S1"}) and len(neighbors(s, "O3")) >= 4, 40,
                      "O3 voltar a ter os vizinhos (registado no S2)")
    ctx.measure("O3 volta a entrar registando-se no S2", dt, expect=(0, 30))
    ctx.check("o S2 registou o O3", "O3" in (status(st, "S2").get("bootstrapper") or []))


@test("C6", "Substitutos: o O4 pede vizinhos novos ao bootstrapper quando perde o O3", C4, slow=True)
def c6(ctx):
    ctx.settle()
    ctx.orch.stop(["O4"])
    ctx.sleep(2)
    ctx.orch.start(["O4"], extra=["--min-neighbors", "4"])
    original = {"C4", "O2", "O3", "S2"}
    ctx.wait(lambda s: neighbors(s, "O4") == original, 40, "O4 com os 4 vizinhos")
    t0 = ctx.act(lambda: ctx.orch.kill(["O3"]))
    dt, st = ctx.wait(lambda s: len(neighbors(s, "O4") - original) >= 1 and len(neighbors(s, "O4")) >= 4, 40,
                      "O4 ligar-se a um vizinho substituto")
    new = sorted(neighbors(st, "O4") - original)
    ctx.note(f"substituto escolhido pelo bootstrapper: {new}")
    ctx.measure("O4 liga-se a um substituto depois de perder o O3", dt, expect=(4, 20))
    ctx.orch.stop(["O4"])
    ctx.sleep(1)
    ctx.orch.start(["O4"])


@test("C7", "Bootstrap: id desconhecido e nó que não é bootstrapper", C4)
def c7(ctx):
    ctx.settle()
    free = ctx.plan.free[0]
    boot = "%s:%s" % ctx.orch.backend.address(ctx.plan.bootstrap)
    rc, out = ctx.orch.probe(free, ["-i", "O99", "-b", boot], timeout=30)
    ctx.check("um id fora do ficheiro é recusado", rc != 0 and "nó desconhecido" in out, out[-160:])
    other = "%s:%s" % ctx.orch.backend.address("O3")
    rc, out = ctx.orch.probe(free, ["-i", "O99", "-b", other], timeout=30)
    ctx.check("um nó que não é bootstrapper diz que não é", rc != 0 and "não é bootstrapper" in out, out[-160:])


# ---------------------------------------------------------------- cenário 4: rede (só no CORE)

@test("B1", "Atraso de 30 ms numa ligação direta (O1–O3)", C4, core=True)
def b1(ctx):
    ctx.need_core()
    st = ctx.settle()
    a, ia, b, ib = ctx.underlay_link("O1", "O3")
    base = rtt(st, "O1", "O3")
    ctx.note(f"ligação do underlay: {a}:{ia} ↔ {b}:{ib}; RTT inicial {base} ms")
    ctx.defer(lambda: ctx.orch.link_clear(a, ia, b, ib))
    ctx.orch.link_netem(a, ia, b, ib, delay="30ms")
    dt, st = ctx.wait(lambda s: (rtt(s, "O1", "O3") or 0) >= 58, 45, "o RTT O1–O3 estabilizar perto de 61 ms")
    ctx.measure("RTT O1–O3 com 30 ms em cada sentido (média móvel já estabilizada)", rtt(st, "O1", "O3"), "ms", expect=(57, 80))
    ctx.check("as outras ligações do O1 não mudam", (rtt(st, "O1", "O2") or 99) < 15)
    ctx.orch.link_clear(a, ia, b, ib)
    dt, st = ctx.wait(lambda s: (rtt(s, "O1", "O3") or 99) < 15, 40, "o RTT O1–O3 voltar ao normal")
    ctx.measure("tempo até o RTT voltar ao normal", dt, expect=(0, 30))


@test("B3", "Perdas de 10 % na ligação do C1 ao S1", C4, core=True)
def b3(ctx):
    ctx.need_core()
    ctx.settle()
    st = ctx.require_route("C1", "S1:filme_a", "S1")
    a, ia, b, ib = ctx.underlay_link("C1", "S1")
    f0 = flow(st, "C1", "S1:filme_a")
    ctx.defer(lambda: ctx.orch.link_clear(a, ia, b, ib))
    ctx.orch.link_netem(a, ia, b, ib, loss="10")
    st = ctx.sleep(25)
    f1 = flow(st, "C1", "S1:filme_a")
    got, lost = f1["received"] - f0["received"], f1["lost"] - f0["lost"]
    pct = 100 * lost / max(1, got + lost)
    ctx.measure("imagens perdidas no C1 (S1:filme_a) com 10 % de perdas de pacotes", pct, "%", expect=(15, 80))
    ctx.note("as perdas de imagens superam as de pacotes porque cada imagem (≈ 8 KiB) vai num só datagrama UDP que o IP parte em "
             "≈ 6 fragmentos: basta perder um para perder a imagem (esperado ≈ 1 − 0,9^6 ≈ 47 %)")
    ctx.check("o overlay não dá a ligação por caída (o controlo TCP aguenta)", "S1" in neighbors(st, "C1"))


@test("B6", "Menor latência ganha mesmo com mais saltos (S1 com 100 ms, D34)", C4, core=True)
def b6(ctx):
    ctx.need_core()
    st = ctx.settle()
    ctx.require_route("C1", "S2:filme_c", "S1")      # 2 saltos pelo S1
    ctx.defer(lambda: ctx.orch.netem_clear("S1", "eth0"))
    ctx.orch.netem("S1", "eth0", delay="100ms")
    dt, st = ctx.wait(lambda s: (rtt(s, "C1", "S1") or 0) >= 97, 60, "o RTT C1–S1 estabilizar perto de 100 ms")
    ctx.measure("RTT C1–S1 com 100 ms no S1 (média móvel já estabilizada)", rtt(st, "C1", "S1"), "ms", expect=(96, 135))
    dt, st = ctx.wait(lambda s: upstream(s, "C1", "S2:filme_c") not in (None, "S1"), 30,
                      "o C1 trocar o montante do filme_c para o caminho de menor latência")
    ctx.measure("tempo até o C1 trocar para o caminho de 3 saltos", dt, "s", expect=(0, 30))
    ctx.note("com a métrica de latência acumulada (D34) o caminho de 3 saltos por O1 ganha ao de 2 saltos por S1 quando este "
             "tem 100 ms; a documentação antiga (D21, saltos primeiro) já não vale")


# ---------------------------------------------------------------- execução

def run_test(orch, t):
    """Corre um teste e devolve o resultado (dicionário)."""
    ctx = Ctx(orch)
    t0 = time.time()
    result = {"id": t["id"], "title": t["title"], "status": "ok", "error": None}
    try:
        if t["core"] and orch.backend.name != "core":
            raise Skip("precisa do CORE (--backend core)")
        t["fn"](ctx)
    except Skip as exc:
        result.update(status="ignorado", error=str(exc))
    except TestFailure as exc:
        result.update(status="falhou", error=str(exc))
    except (BackendError, KeyError, TypeError, AttributeError) as exc:
        result.update(status="erro", error=f"{type(exc).__name__}: {exc}")
    finally:
        if result["status"] != "ignorado":
            ctx.finish()
    bad = [c for c in ctx.checks if not c["ok"]] + [m for m in ctx.measures if m["ok"] is False]
    if result["status"] == "ok" and bad:
        result["status"] = "falhou"
    result.update(checks=ctx.checks, measures=ctx.measures, notes=ctx.notes, seconds=round(time.time() - t0, 1))
    return result


def run_tests(orch, ids=None, repeat=1, include_slow=True, on_result=None, should_stop=lambda: False, on_start=None):
    """Corre os testes pedidos (ou todos os do cenário) ``repeat`` vezes. Devolve a lista de resultados."""
    chosen = [t for t in TESTS if (t["scenarios"] is None or orch.plan.name in t["scenarios"])
              and (not ids or t["id"] in ids) and (include_slow or not t["slow"] or ids)]
    out = []
    for rep in range(repeat):
        for t in chosen:
            if should_stop():
                return out
            if on_start:
                on_start(t, rep + 1)
            res = run_test(orch, t)
            res["repeat"] = rep + 1
            out.append(res)
            if on_result:
                on_result(res)
    return out


def summarize(results):
    """Por teste: execuções, aprovadas e, por medição, mínimo/média/máximo."""
    by = {}
    for r in results:
        s = by.setdefault(r["id"], {"id": r["id"], "title": r["title"], "runs": 0, "ok": 0, "ignored": 0, "measures": {}})
        if r["status"] == "ignorado":
            s["ignored"] += 1
            continue
        s["runs"] += 1
        s["ok"] += r["status"] == "ok"
        for m in r["measures"]:
            if m["value"] is not None:
                s["measures"].setdefault((m["name"], m["unit"]), []).append(m["value"])
    for s in by.values():
        s["measures"] = [{"name": n, "unit": u, "n": len(v), "min": min(v), "mean": round(statistics.mean(v), 2), "max": max(v)}
                         for (n, u), v in s["measures"].items()]
    return list(by.values())


def render_markdown(results):
    """Tabela em Markdown para o relatório."""
    lines = ["| Teste | Resultado | Medições (mín / média / máx) |", "|---|---|---|"]
    for s in summarize(results):
        if not s["runs"]:
            lines.append(f"| {s['id']} {s['title']} | ignorado | – |")
            continue
        meas = "<br>".join(f"{m['name']}: {m['min']} / {m['mean']} / {m['max']} {m['unit']} (n={m['n']})" for m in s["measures"]) or "–"
        lines.append(f"| {s['id']} {s['title']} | {s['ok']}/{s['runs']} ok | {meas} |")
    return "\n".join(lines)


def render_text(results):
    out = []
    for r in results:
        mark = {"ok": "OK ", "falhou": "FALHOU", "erro": "ERRO", "ignorado": "ignorado"}[r["status"]]
        out.append(f"[{mark}] {r['id']} {r['title']} ({r['seconds']} s)" + (f"  — {r['error']}" if r["error"] else ""))
        for m in r["measures"]:
            exp = f"  esperado {m['expect'][0]}–{m['expect'][1]}" if m["expect"] else ""
            flag = "" if m["ok"] in (None, True) else "  ✗"
            out.append(f"      medido: {m['name']} = {m['value']} {m['unit']}{exp}{flag}")
        for c in r["checks"]:
            out.append(f"      {'✓' if c['ok'] else '✗'} {c['name']}" + (f"  ({c['detail']})" if c["detail"] and not c["ok"] else ""))
        for n in r["notes"]:
            out.append(f"      nota: {n}")
    return "\n".join(out)


# ---------------------------------------------------------------- demonstrações guiadas

def _flow_filter_step(title, text, flow=None, focus=()):
    return {"title": title, "text": text, "flow": flow, "focus": list(focus), "core": False, "run": None}


def _demo4():
    """Guião do cenário 4: dois servidores, um é caminho de passagem do outro, o que acontece quando um cai."""

    def start(ctx):
        ctx.settle()
        st = ctx.state()
        return (f"Agora: o C1 recebe o S2:filme_c por {upstream(st, 'C1', 'S2:filme_c')} e o C2 recebe o S1:filme_a por "
                f"{upstream(st, 'C2', 'S1:filme_a')}.")

    def kill_s1(ctx):
        ctx.settle()
        ctx.require_route("C1", "S2:filme_c", "S1")
        t0 = ctx.act(lambda: ctx.orch.kill(["S1"]))
        dt, st = ctx.wait(lambda s: upstream(s, "C1", "S2:filme_c") == "O1" and receiving(s, "C1", "S2:filme_c"), 45,
                          "o C1 voltar a receber o filme_c")
        when = switched_to(st, "C1", "S2:filme_c", "O1", t0)
        st = ctx.sleep(1.5)
        gap = interruption(st, "C1", "S2:filme_c", t0)
        return (f"Medido: o C1 passou a receber o filme_c por S2–O3–O1–C1 {((when - t0) if when else dt):.1f} s depois da queda, "
                f"com uma interrupção de {gap:.1f} s. O filme_b do C3 não parou: vem também do S2.")

    def back_s1(ctx):
        t1 = ctx.act(lambda: ctx.orch.start(["S1"]))
        ctx.wait(lambda s: receiving(s, "C1", "S1:filme_a") and upstream(s, "C1", "S2:filme_c") == "S1", 70,
                 "o S1 voltar e o C1 regressar ao caminho curto")
        st = ctx.state()
        back = switched_to(st, "C1", "S2:filme_c", "S1", t1)
        return f"Medido: o filme_c do C1 regressou ao caminho mais curto (pelo S1) {((back - t1) if back else 0):.1f} s depois."

    def freeze_s2(ctx):
        ctx.settle()
        ctx.require_route("C2", "S1:filme_a", "S2")
        t0 = ctx.act(lambda: ctx.orch.freeze(["S2"]))
        dt, st = ctx.wait(lambda s: upstream(s, "C2", "S1:filme_a") == "O3" and receiving(s, "C2", "S1:filme_a"), 60,
                          "o C2 voltar a receber o filme_a")
        ctx.wait(lambda s: any(event_times(s, p, "vizinho_down", peer="S2", after=t0) for p in ("C2", "O3", "O4", "S1")), 25,
                 "algum vizinho dar o S2 como caído")
        st = ctx.sleep(1.2)
        seen = [min(event_times(st, p, "vizinho_down", peer="S2", after=t0)) - t0 for p in ("C2", "O3", "O4", "S1")
                if event_times(st, p, "vizinho_down", peer="S2", after=t0)]
        when = switched_to(st, "C2", "S1:filme_a", "O3", t0)
        ctx.orch.thaw(["S2"])
        return (f"Medido: a rota do C2 mudou {((when - t0) if when else dt):.1f} s depois de o S2 congelar (com o anúncio seguinte); "
                f"o primeiro vizinho só o deu como caído ao fim de {min(seen) if seen else 0:.1f} s, por timeout.")

    def join_o5(ctx):
        ctx.settle()
        t0 = ctx.act(lambda: ctx.orch.start(["O5"]))
        dt, st = ctx.wait(lambda s: neighbors(s, "O5") == {"O1", "O4"} and "O5" in neighbors(s, "O1"), 30, "O5 ligar-se")
        return f"Medido: o O5 ligou-se a O1 e O4 em {dt:.1f} s, sem nenhum cliente perder imagens."

    def delay_link(ctx):
        ctx.need_core()
        ctx.settle()
        a, ia, b, ib = ctx.underlay_link("O1", "O3")
        ctx.orch.link_netem(a, ia, b, ib, delay="40ms")
        dt, st = ctx.wait(lambda s: (rtt(s, "O1", "O3") or 0) >= 60, 30, "o RTT subir")
        return f"Medido: o RTT O1–O3 subiu para {rtt(st, 'O1', 'O3'):.0f} ms (40 ms em cada sentido). Ligação {a}:{ia} ↔ {b}:{ib}."

    def reset(ctx):
        ctx.finish()
        return "Cenário reposto: todos os nós a correr e a rede sem atrasos."

    return [
        {"title": "Dois servidores, quatro fluxos", "flow": None, "focus": ["S1", "S2"], "core": False, "run": start,
         "text": "O S1 emite o filme_a e o filme_b; o S2 emite o filme_c e o filme_b (réplica). Os quatro clientes subscrevem "
                 "fluxos dos dois servidores. Repara na árvore de cada fluxo: o C1 recebe o filme_c através do S1 e o C2 recebe "
                 "o filme_a através do S2. Cada servidor é também caminho de passagem do outro."},
        {"title": "A rota de cada fluxo", "flow": "S2:filme_c", "focus": ["S2", "S1", "C1", "O3", "O1"], "core": False, "run": None,
         "text": "Cada nó construiu a sua tabela de rotas com os anúncios dos servidores (latência acumulada; os saltos só desempatam, D34). "
                 "Clica num nó para ver a tabela: montante, saltos, destinos e os candidatos de cada vizinho. "
                 "O filme_c chega ao C1 em 2 saltos (S2–S1–C1); a alternativa, S2–O3–O1–C1, tem 3."},
        {"title": "Cai o servidor S1", "flow": "S2:filme_c", "focus": ["S1", "C1", "O1", "O3"], "core": False, "run": kill_s1,
         "text": "Matamos o S1 (kill -9). Os vizinhos veem o TCP fechar de imediato e os nós registam o WITHDRAW do que o S1 "
                 "anunciava. O filme_a e o filme_b do S1 deixam de chegar. O filme_c do S2, que passava pelo S1, tem de ser "
                 "redirecionado pelo caminho alternativo."},
        {"title": "A réplica sobrevive", "flow": "S2:filme_b", "focus": ["C3", "S2"], "core": False, "run": None,
         "text": "O C3 subscreve o filme_b aos dois servidores. Quando o S1 cai, continua a receber o do S2: quem precisa de "
                 "disponibilidade subscreve uma réplica. Vê o gráfico de imagens por segundo do C3."},
        {"title": "O servidor S1 volta", "flow": "S2:filme_c", "focus": ["S1", "C1"], "core": False, "run": back_s1,
         "text": "Relançamos o S1. As épocas dos anúncios recomeçam a partir da hora de arranque, por isso os nós aceitam-nas e "
                 "o S1 volta a ser origem dos seus fluxos. O filme_c do C1 regressa ao caminho mais curto."},
        {"title": "Falha silenciosa: o S2 congela", "flow": "S1:filme_a", "focus": ["S2", "C2", "O3"], "core": False, "run": freeze_s2,
         "text": "Com kill -STOP o S2 fica vivo mas calado: o TCP não fecha. A rota do C2 muda com o anúncio seguinte, mas só o "
                 "timeout do keepalive (6 s) dá o S2 como caído. É a diferença entre uma falha com fecho e uma falha silenciosa."},
        {"title": "Entra um nó novo (O5)", "flow": None, "focus": ["O5", "O1", "O4"], "core": False, "run": join_o5,
         "text": "O O5 estava previsto no ficheiro de topologia mas não corria. Ao arrancar regista-se no bootstrapper, recebe os "
                 "vizinhos O1 e O4 e passa a ser um caminho alternativo, sem afetar os clientes."},
        {"title": "Atraso numa ligação do underlay", "flow": None, "focus": ["O1", "O3"], "core": True, "run": delay_link,
         "text": "Com o tc netem pomos 40 ms em cada sentido da ligação O1–O3. Os RTT que cada nó mede mudam, e a métrica usa-os "
                 "para desempatar caminhos com o mesmo número de saltos (com histerese, para não oscilar)."},
        {"title": "Repor tudo", "flow": None, "focus": [], "core": False, "run": reset,
         "text": "Voltamos ao estado inicial: nós todos a correr e rede sem atrasos."},
    ]


def _demo_generic(plan):
    def start(ctx):
        ctx.settle()
        return "Todos os clientes estão a receber os seus fluxos."

    def kill_mid(ctx):
        st = ctx.settle()
        cand = [s for s in plan.nodes.values() if not s.serve and not s.spare and not s.subscribe]
        if not cand:
            return "Este cenário não tem nós intermédios."
        mid = max(cand, key=lambda s: len(neighbors(st, s.id))).id
        peers = neighbors(st, mid)
        t0 = ctx.act(lambda: ctx.orch.kill([mid]))
        ctx.wait(lambda s: all(event_times(s, p, "vizinho_down", peer=mid, after=t0) for p in peers), 15, "deteção")
        st = ctx.state()
        detect = max(min(event_times(st, p, "vizinho_down", peer=mid, after=t0)) for p in peers) - t0
        return f"Medido: os vizinhos de {mid} viram-no cair em {detect:.1f} s (o TCP fechou)."

    def back(ctx):
        ctx.finish()
        return "O nó voltou e os vizinhos religaram-se sozinhos."

    return [
        {"title": "Arranque", "flow": None, "focus": [], "core": False, "run": start,
         "text": "Os nós registam-se no bootstrapper, recebem os vizinhos e ligam-se. Os servidores anunciam os fluxos e os "
                 "clientes ativam a rota pelo caminho inverso."},
        {"title": "Cai um nó intermédio", "flow": None, "focus": [], "core": False, "run": kill_mid,
         "text": "Matamos o nó intermédio com mais vizinhos e vemos como o overlay reage."},
        {"title": "O nó volta", "flow": None, "focus": [], "core": False, "run": back,
         "text": "Relançamos o nó: os vizinhos voltam a ligar-se e as rotas são reconstruídas."},
    ]


def demo_for(plan):
    """Passos da demonstração guiada do cenário."""
    return _demo4() if plan.name == "cenario4" else _demo_generic(plan)


def run_demo_step(orch, step):
    """Executa um passo da demonstração; devolve o texto extra (o que foi medido)."""
    if step["core"] and orch.backend.name != "core":
        return "(este passo só se executa com o CORE: aqui fica apenas a explicação)"
    if step["run"] is None:
        return ""
    ctx = Ctx(orch)
    try:
        return step["run"](ctx) or ""
    except Skip as exc:
        return f"({exc})"
    except TestFailure as exc:
        return f"(não aconteceu como esperado: {exc})"
