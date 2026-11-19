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
        if f"{f['origin']}:{f['flow']}" == key or (f.get("subscription") and f["subscription"] == key):
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


# ---------------------------------------------------------------- ajudas dos testes genéricos (qualquer cenário)

def _split(sub):
    """``"S1:filme_a"`` -> ``("S1", "filme_a")``; ``"filme_c"`` -> ``(None, "filme_c")``."""
    server, sep, name = sub.partition(":")
    return (server, name) if sep else (None, sub)


def _servers(plan):
    return [s for s in plan.nodes.values() if s.serve]


def _clients(plan):
    return [s for s in plan.nodes.values() if s.subscribe and not s.spare]


def _mids(plan):
    return [s for s in plan.nodes.values() if not s.serve and not s.spare and not s.subscribe]


def client_paths(state, client):
    """``{servidor:fluxo: [nós]}``: o caminho que o último ROUTE_TEST fez até ao cliente (só os recentes)."""
    return {p["key"]: p["path"] for p in state.get("paths", []) if p["node"] == client and p["fresh"]}


def tree_chain(state, key, client):
    """Caminho do servidor ao cliente, seguindo os montantes de cada tabela de rotas (a árvore do painel)."""
    up = {e["to"]: e["from"] for e in state["tree"].get(key, [])}
    chain, cur = [client], client
    while cur in up and len(chain) < 40:
        cur = up[cur]
        chain.append(cur)
    return list(reversed(chain))


def _all_paths_known(state, plan):
    """Cada subscrição de cada cliente tem um caminho recente (ROUTE_TEST) e só falta saber se é o certo."""
    for c in _clients(plan):
        if len(client_paths(state, c.id)) < len(c.subscribe):
            return False
    return True


def _depends_on(plan, server_id):
    """(afetadas, com alternativa) pela queda de um servidor: subscrições (cliente, subscrição) que perdem o fluxo
    e as que passam para outro servidor (subscrição sem servidor fixo com uma réplica)."""
    lost, failover, kept = [], [], []
    for c in _clients(plan):
        for sub in c.subscribe:
            server, name = _split(sub)
            if server == server_id:
                lost.append((c.id, sub))
            elif server is None and server_id in plan.publishers_of(name):
                (failover if len(plan.publishers_of(name)) > 1 else lost).append((c.id, sub))
            else:
                kept.append((c.id, sub))
    return lost, failover, kept


def _hops_of(path):
    return list(zip(path, path[1:]))


def _connected(plan, src, dst, dead):
    """Há caminho overlay de ``src`` a ``dst`` sem passar pelos nós ``dead`` nem pelos suplentes?"""
    adj = {}
    for a, b in plan.edges():
        if a in dead or b in dead or plan.nodes[a].spare or plan.nodes[b].spare:
            continue
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    seen, todo = {src}, [src]
    while todo:
        for nxt in adj.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return dst in seen


def _pick_failure(plan, state):
    """``(cliente, chave, caminho, nó, há_alternativa)``: um nó intermédio de um caminho ativo para matar, de preferência
    um que deixe o cliente com outro caminho (senão, o fluxo pára e só se vê isso)."""
    best = None
    for c in _clients(plan):
        for key, path in client_paths(state, c.id).items():
            for mid in path[1:-1]:
                alt = _connected(plan, key.split(":")[0], c.id, {mid})
                rank = (alt, len(path))
                if best is None or rank > best[0]:
                    best = (rank, (c.id, key, path, mid, alt))
    return best[1] if best else None


@test("G3", "Cai cada servidor e volta: os fluxos dos outros não param")
def g3(ctx):
    """Mata um servidor de cada vez. Os clientes que o usam deixam de receber (ou passam para a réplica); os outros fluxos
    não podem parar. Volta a lançá-lo e mede quanto demora o cenário a normalizar."""
    plan = ctx.plan
    tested = 0
    for server in _servers(plan):
        lost, failover, kept = _depends_on(plan, server.id)
        if not lost and not failover:
            continue
        tested += 1
        st = ctx.settle()
        # uma subscrição com réplica só depende deste servidor se for ele que a está a servir agora
        failover, kept = ([x for x in failover if (flow(st, *x) or {}).get("origin") == server.id],
                          kept + [x for x in failover if (flow(st, *x) or {}).get("origin") != server.id])
        t0 = ctx.act(lambda: ctx.orch.kill([server.id]))
        if lost:
            dt, st = ctx.wait(lambda s: not any(receiving(s, c, k) for c, k in lost), 45,
                              f"os clientes de {server.id} deixarem de receber ({', '.join(f'{c}:{k}' for c, k in lost)})")
            ctx.measure(f"{server.id} morto: os clientes que só o têm param", dt, expect=(0, 20))
        if failover:
            dt, st = ctx.wait(lambda s: all(receiving(s, c, k) and (flow(s, c, k) or {}).get("origin") != server.id
                                            for c, k in failover), 60,
                              f"os clientes com réplica passarem para outro servidor ({failover})")
            gap = max(interruption(st, c, k, t0) for c, k in failover)
            ctx.measure(f"{server.id} morto: tempo até a réplica entregar ({failover[0][0]})", dt, expect=(0, 30))
            ctx.measure(f"{server.id} morto: interrupção máxima do fluxo com réplica", gap, expect=(0, 30))
        st = ctx.sleep(1.5)
        stopped = [f"{c}:{k}" for c, k in kept if not receiving(st, c, k)
                   and event_times(st, c, "fluxo_parou", route=k if ":" in k else None, after=t0)]
        ctx.check(f"os fluxos que não dependem de {server.id} continuam a chegar", not stopped, ", ".join(stopped))
        t1 = ctx.act(lambda: ctx.orch.start([server.id]))
        dt, st = ctx.wait(lambda s: all_receiving(s, plan), 90, "todos os clientes a receber outra vez")
        ctx.measure(f"{server.id} relançado: o cenário normaliza", dt, expect=(0, 70))
    if not tested:
        raise Skip("nenhum servidor tem clientes")


@test("G4", "Falha silenciosa: um nó intermédio congelado (só o timeout o deteta)")
def g4(ctx):
    """SIGSTOP num intermédio: o TCP não fecha, por isso os vizinhos só o dão como caído ao fim do timeout do keepalive."""
    st = ctx.settle()
    cand = _mids(ctx.plan)
    if not cand:
        raise Skip("o cenário não tem nós intermédios")
    mid = max(cand, key=lambda s: len(neighbors(st, s.id))).id
    peers = neighbors(st, mid)
    ctx.note(f"nó escolhido: {mid} (vizinhos {sorted(peers)})")
    t0 = ctx.act(lambda: ctx.orch.freeze([mid]))
    ctx.defer(lambda: ctx.orch.thaw([mid]))
    ctx.wait(lambda s: any(event_times(s, p, "vizinho_down", peer=mid, after=t0) for p in peers), 25,
             "algum vizinho dar o nó congelado como caído")
    st = ctx.state()
    first = min(min(event_times(st, p, "vizinho_down", peer=mid, after=t0)) for p in peers
                if event_times(st, p, "vizinho_down", peer=mid, after=t0)) - t0
    ctx.measure(f"primeiro vizinho dá {mid} como caído (por timeout)", first, expect=(3, 12))
    ctx.orch.thaw([mid])
    ctx.wait(lambda s: all(event_times(s, p, "vizinho_up", peer=mid, after=t0) for p in peers
                           if event_times(s, p, "vizinho_down", peer=mid, after=t0)), 45, "os vizinhos religarem-se")
    ctx.note("depois de descongelado, o nó regressa sozinho")


@test("G5", "Um cliente sai e volta a entrar sem afetar os outros")
def g5(ctx):
    """Pára um cliente (BYE): os outros não perdem imagens e, se era o único a ver um fluxo, o servidor deixa de o emitir."""
    plan = ctx.plan
    st = ctx.settle()
    clients = _clients(plan)
    if not clients:
        raise Skip("o cenário não tem clientes")
    target = max(clients, key=lambda c: len(c.subscribe))
    mine = client_paths(st, target.id)
    others = [c.id for c in clients if c.id != target.id]
    alone = [k for k in mine if not any(k in client_paths(st, o) for o in others)]
    t0 = ctx.act(lambda: ctx.orch.stop([target.id]))
    if alone:
        dt, st = ctx.wait(lambda s: all((route(s, k.split(":")[0], k) or {}).get("state") == "inativa" for k in alone), 40,
                          f"os servidores deixarem de emitir {alone}")
        ctx.measure("fluxos que só ele via ficam inativos no servidor", dt, expect=(0, 30))
    st = ctx.sleep(4)
    ctx.check("os outros clientes não perdem imagens",
              not any(event_times(st, o, "fluxo_parou", after=t0) for o in others))
    t1 = ctx.act(lambda: ctx.orch.start([target.id]))
    dt, st = ctx.wait(lambda s: all(receiving(s, target.id, k) for k in target.subscribe), 60, f"{target.id} a receber")
    ctx.measure(f"{target.id} volta a receber os seus fluxos", dt, expect=(0, 45))


@test("G6", "ROUTE_TEST: o caminho que cada cliente recebe é o da árvore e muda quando um nó cai")
def g6(ctx):
    """Etapa 3: cada servidor envia um ROUTE_TEST pela árvore ativa; cada cliente vê o caminho acumulado. Verifica que
    começa no servidor, acaba no cliente, só usa ligações overlay e coincide com a árvore de rotas; depois mata um nó
    do caminho e espera que o caminho mude."""
    plan = ctx.plan
    if not plan.route_test:
        raise Skip("este cenário não gera ROUTE_TEST (route_test = 0)")
    ctx.settle()
    dt, st = ctx.wait(lambda s: _all_paths_known(s, plan), 40, "todos os clientes receberem um ROUTE_TEST por fluxo")
    ctx.measure("até todos os clientes terem um caminho de teste", dt, expect=(0, 30))
    edges = {frozenset(e) for e in plan.edges()}
    bad = []
    for c in _clients(plan):
        for key, path in client_paths(st, c.id).items():
            server = key.split(":")[0]
            tree = tree_chain(st, key, c.id)
            if path[0] != server or path[-1] != c.id:
                bad.append(f"{c.id} {key}: {'>'.join(path)} não vai de {server} a {c.id}")
            elif not all(frozenset(h) in edges for h in _hops_of(path)):
                bad.append(f"{c.id} {key}: {'>'.join(path)} usa uma ligação que não é overlay")
            elif path != tree and tree[0] == server:
                bad.append(f"{c.id} {key}: ROUTE_TEST {'>'.join(path)} ≠ árvore {'>'.join(tree)}")
    ctx.check("cada caminho vai do servidor ao cliente, só por ligações overlay, e coincide com a árvore", not bad, "; ".join(bad))
    pick = _pick_failure(plan, st)
    if pick is None:
        raise Skip("nenhum caminho passa por um nó intermédio")
    client, key, path, mid, alt = pick
    ctx.note(f"caminho escolhido: {client} {key} = {'>'.join(path)}; mata-se {mid}"
             + ("" if alt else " (sem alternativa neste cenário)"))
    t0 = ctx.act(lambda: ctx.orch.kill([mid]))
    try:
        dt, st = ctx.wait(lambda s: (lambda p: bool(p) and mid not in p and receiving(s, client, key))(
            client_paths(s, client).get(key)), 60 if alt else 15, f"{client} receber {key} por um caminho sem {mid}")
        ctx.measure(f"o ROUTE_TEST do {client} passa a mostrar outro caminho", dt, expect=(0, 45))
        ctx.note(f"novo caminho: {'>'.join(client_paths(st, client)[key])}")
    except TestFailure:
        if alt:
            raise
        ctx.note(f"{mid} é a única ligação deste cliente a {key}: sem alternativa, o fluxo pára (esperado nesta topologia)")


@test("G7", "Entra um nó suplente e liga-se aos vizinhos do ficheiro overlay")
def g7(ctx):
    spares = [s for s in ctx.plan.nodes.values() if s.spare]
    if not spares:
        raise Skip("o cenário não tem nós suplentes")
    ctx.settle()
    for spec in spares:
        expect = {b for a, b in _directed(ctx.plan) if a == spec.id and not ctx.plan.nodes[b].spare}
        t0 = ctx.act(lambda s=spec.id: ctx.orch.start([s]))
        ctx.defer(lambda s=spec.id: ctx.orch.stop([s]))
        dt, st = ctx.wait(lambda s: neighbors(s, spec.id) == expect and all(spec.id in neighbors(s, p) for p in expect), 40,
                          f"{spec.id} ligar-se a {sorted(expect)}")
        ctx.measure(f"{spec.id} liga-se a {', '.join(sorted(expect))}", dt, expect=(0, 25))
        st = ctx.sleep(4)
        ctx.check(f"nenhum cliente perde imagens com a entrada de {spec.id}",
                  not any(event_times(st, c.id, "fluxo_parou", after=t0) for c in _clients(ctx.plan)))


@test("G8", "Réplica: um cliente sem servidor fixo passa para outro servidor quando o seu cai")
def g8(ctx):
    """D38: subscrever só o fluxo (sem servidor) escolhe o servidor mais perto e muda se ele cair."""
    plan = ctx.plan
    pairs = [(c.id, sub) for c in _clients(plan) for sub in c.subscribe
             if ":" not in sub and len(plan.publishers_of(sub)) > 1]
    if not pairs:
        raise Skip("nenhum cliente subscreve um fluxo replicado sem servidor fixo")
    client, key = pairs[0]
    st = ctx.settle()
    f0 = flow(st, client, key)
    first = f0["origin"]
    ctx.note(f"{client} recebe {key} de {first}")
    t0 = ctx.act(lambda: ctx.orch.kill([first]))
    dt, st = ctx.wait(lambda s: receiving(s, client, key) and (flow(s, client, key) or {}).get("origin") not in (None, first),
                      60, f"{client} receber {key} de outro servidor")
    ctx.measure(f"{client} passa para a réplica ({flow(st, client, key)['origin']})", dt, expect=(0, 30))
    ctx.measure("interrupção do vídeo na troca", interruption(st, client, key, t0), expect=(0, 20))


@test("G9", "O cliente escolhe os filmes: tira e volta a pôr subscrições")
def g9(ctx):
    """Muda as subscrições de um cliente (o painel relança-o): o servidor deixa de emitir o que ninguém vê e os outros clientes
    não notam. Depois subscreve um fluxo novo e mede o tempo até à primeira imagem."""
    plan = ctx.plan
    st = ctx.settle()
    clients = _clients(plan)
    if not clients:
        raise Skip("o cenário não tem clientes")
    target = max(clients, key=lambda c: len(c.subscribe))
    others = [c.id for c in clients if c.id != target.id]
    original = list(target.subscribe)
    ctx.defer(lambda: ctx.orch.set_subscriptions(target.id, original))
    dropped = original[0]
    t0 = ctx.act(lambda: ctx.orch.set_subscriptions(target.id, original[1:]))
    st = ctx.sleep(3)
    ctx.check(f"{target.id} deixa de pedir {dropped}", not any(f.get("subscription") == dropped or
              f"{f['origin']}:{f['flow']}" == dropped for f in status(st, target.id).get("flows", []) if f["active"]))
    ctx.check("os outros clientes não perdem imagens",
              not any(event_times(st, o, "fluxo_parou", after=t0) for o in others))
    new = [o["key"] for o in ctx.orch.subscription_options() if o["server"] is not None and o["key"] not in original
           and not any(_split(x)[1] == o["flow"] for x in original[1:])]
    t1 = ctx.act(lambda: ctx.orch.set_subscriptions(target.id, original[1:] + [new[0] if new else dropped]))
    added = new[0] if new else dropped
    dt, st = ctx.wait(lambda s: receiving(s, target.id, added), 60, f"{target.id} receber {added}")
    ctx.measure(f"{target.id} começa a receber {added}", dt, expect=(0, 45))


# ---------------------------------------------------------------- testes genéricos de rede (só no CORE)

def _active_hop(ctx, st, prefer_mid=True):
    """(cliente, chave, a, b): um troço overlay do caminho ativo de um cliente (o do meio, se houver)."""
    best = None
    for c in _clients(ctx.plan):
        for key, path in client_paths(st, c.id).items():
            hops = _hops_of(path)
            if hops and (best is None or len(hops) > len(best[2])):
                best = (c.id, key, hops)
    if best is None:
        raise TestFailure("não há nenhum caminho ativo (ROUTE_TEST) para escolher um troço")
    client, key, hops = best
    a, b = hops[len(hops) // 2] if prefer_mid else hops[0]
    return client, key, a, b


@test("N1", "Atraso de 50 ms no caminho ativo de um cliente: o RTT sobe e o caminho adapta-se", core=True)
def n1(ctx):
    ctx.need_core()
    st = ctx.settle()
    ctx.wait(lambda s: _all_paths_known(s, ctx.plan), 40, "caminhos de teste")
    st = ctx.state()
    client, key, a, b = _active_hop(ctx, st)
    before = client_paths(st, client)[key]
    base = rtt(st, a, b)
    ctx.note(f"troço escolhido: {a}→{b} no caminho {'>'.join(before)} de {client} ({key}); RTT inicial {base} ms")
    ctx.defer(lambda: ctx.orch.hop_clear(a, b))
    ctx.orch.hop_netem(a, b, delay="50ms")
    dt, st = ctx.wait(lambda s: (rtt(s, a, b) or 0) >= 90, 60, f"o RTT {a}–{b} subir")
    ctx.measure(f"RTT {a}–{b} com 50 ms em cada sentido", rtt(st, a, b), "ms", expect=(90, 140))
    st = ctx.sleep(12)
    after = client_paths(st, client).get(key)
    ctx.note(f"caminho depois: {'>'.join(after) if after else '(sem teste recente)'}"
             + (" (mudou: há uma alternativa mais rápida)" if after and after != before else " (igual: não há alternativa melhor)"))
    ctx.check(f"{client} continua a receber {key}", receiving(st, client, key))
    ctx.orch.hop_clear(a, b)
    dt, st = ctx.wait(lambda s: (rtt(s, a, b) or 99) < 30, 60, f"o RTT {a}–{b} voltar ao normal")
    ctx.measure("tempo até o RTT voltar ao normal", dt, expect=(0, 45))


@test("N2", "Corta a ligação do underlay no caminho ativo: o fluxo recupera", core=True)
def n2(ctx):
    ctx.need_core()
    st = ctx.settle()
    ctx.wait(lambda s: _all_paths_known(s, ctx.plan), 40, "caminhos de teste")
    st = ctx.state()
    client, key, a, b = _active_hop(ctx, st)
    before = client_paths(st, client)[key]
    ctx.note(f"troço cortado: {a}→{b} no caminho {'>'.join(before)} de {client} ({key})")
    ctx.defer(lambda: ctx.orch.hop_set(a, b, True))
    t0 = ctx.act(lambda: ctx.orch.hop_set(a, b, False))
    time.sleep(2)
    try:
        dt, st = ctx.wait(lambda s: receiving(s, client, key), 80, f"{client} voltar a receber {key}")
        ctx.measure(f"{client} volta a receber {key}", dt + 2, expect=(0, 60))
        ctx.measure("interrupção do vídeo", interruption(st, client, key, t0), expect=(0, 60))
        after = client_paths(st, client).get(key)
        ctx.note(f"caminho depois: {'>'.join(after) if after else '(sem teste recente)'}")
    finally:
        ctx.orch.hop_set(a, b, True)


@test("N3", "Desliga um router de passagem do caminho ativo (o OSPF recalcula)", core=True)
def n3(ctx):
    ctx.need_core()
    st = ctx.settle()
    ctx.wait(lambda s: _all_paths_known(s, ctx.plan), 40, "caminhos de teste")
    st = ctx.state()
    under = ctx.orch.underlay()
    hosts = {s.core for s in ctx.plan.nodes.values()} | set(ctx.plan.free)
    attached = {l["a"] for l in under["links"] if l["b"] in hosts} | {l["b"] for l in under["links"] if l["a"] in hosts}
    for c in _clients(ctx.plan):
        for key, path in client_paths(st, c.id).items():
            for a, b in _hops_of(path):
                ip = ctx.orch.ip_path(ctx.plan.nodes[a].core, ctx.plan.nodes[b].core) or []
                transit = [r for r in ip[1:-1] if r not in attached and r.startswith("r")]
                if transit:
                    router, client, key = transit[len(transit) // 2], c.id, key
                    break
            else:
                continue
            break
        else:
            continue
        break
    else:
        raise Skip("não há nenhum router só de passagem (todos têm um nó overlay ligado)")
    ctx.note(f"router desligado: {router} (no caminho IP de {client}:{key})")
    ctx.defer(lambda: ctx.orch.router_set(router, True))
    t0 = ctx.act(lambda: ctx.orch.router_set(router, False))
    time.sleep(2)
    try:
        dt, st = ctx.wait(lambda s: receiving(s, client, key), 90, f"{client} voltar a receber {key}")
        ctx.measure(f"{client} volta a receber {key}", dt + 2, expect=(0, 70))
        ctx.measure("interrupção do vídeo", interruption(st, client, key, t0), expect=(0, 70))
    finally:
        ctx.orch.router_set(router, True)


@test("N4", "Perdas de 10 % no último troço do caminho de um cliente", core=True)
def n4(ctx):
    ctx.need_core()
    st = ctx.settle()
    ctx.wait(lambda s: _all_paths_known(s, ctx.plan), 40, "caminhos de teste")
    st = ctx.state()
    client, key, a, b = _active_hop(ctx, st, prefer_mid=False)
    path = client_paths(st, client)[key]
    a, b = _hops_of(path)[-1]
    f0 = flow(st, client, key)
    ctx.note(f"troço com perdas: {a}→{b} (caminho {'>'.join(path)})")
    ctx.defer(lambda: ctx.orch.hop_clear(a, b))
    ctx.orch.hop_netem(a, b, loss="10")
    st = ctx.sleep(25)
    f1 = flow(st, client, key)
    got, lost = f1["received"] - f0["received"], f1["lost"] - f0["lost"]
    pct = 100 * lost / max(1, got + lost)
    ctx.measure(f"imagens perdidas no {client} ({key}) com 10 % de perdas de pacotes", pct, "%", expect=(15, 85))
    ctx.note("cada imagem vai fragmentada em vários pacotes IP: basta perder um para perder a imagem (≈ 1 − 0,9^6 ≈ 47 %)")
    ctx.check("o overlay não dá o vizinho por caído (o controlo TCP aguenta)", b in neighbors(st, a) or a in neighbors(st, b))


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


def _fmt_paths(state, plan):
    lines = []
    for c in _clients(plan):
        for key, path in sorted(client_paths(state, c.id).items()):
            lines.append(f"{c.id} ← {key}: {' → '.join(path)}")
    return "; ".join(lines) or "(ainda sem ROUTE_TEST)"


def _demo_generic(plan):
    """Guião que serve a qualquer cenário: constrói os textos a partir do plano (servidores, fluxos, clientes)."""
    servers, clients, mids = _servers(plan), _clients(plan), _mids(plan)
    flows = plan.all_flows()
    shared = [f for f in flows if len(plan.publishers_of(f)) > 1]
    content = "; ".join(f"{f} ({plan.video_label(f)}, de {'/'.join(plan.publishers_of(f))})" for f in flows)

    def start(ctx):
        st = ctx.settle()
        ctx.wait(lambda s: _all_paths_known(s, plan), 30, "os caminhos de teste")
        return "Caminhos medidos: " + _fmt_paths(ctx.state(), plan)

    def show_paths(ctx):
        return "Caminhos (último ROUTE_TEST de cada servidor): " + _fmt_paths(ctx.state(), plan)

    def pick_path(st, need_mid=True):
        best = None
        for c in clients:
            for key, path in client_paths(st, c.id).items():
                if (not need_mid or len(path) > 2) and (best is None or len(path) > len(best[2])):
                    best = (c.id, key, path)
        return best

    state = {}

    def kill_mid(ctx):
        st = ctx.settle()
        ctx.wait(lambda s: _all_paths_known(s, plan), 30, "os caminhos de teste")
        pick = _pick_failure(plan, ctx.state())
        if pick is None:
            return "Nenhum caminho passa por um nó intermédio neste cenário."
        client, key, path, mid, _alt = pick
        state["mid"] = mid
        t0 = ctx.act(lambda: ctx.orch.kill([mid]))
        try:
            dt, st = ctx.wait(lambda s: (lambda p: bool(p) and mid not in p and receiving(s, client, key))(
                client_paths(s, client).get(key)), 60 if _alt else 15, "novo caminho")
            return (f"Medido: o caminho de {client} ({key}) era {' → '.join(path)}; com {mid} morto passou a "
                    f"{' → '.join(client_paths(st, client)[key])} em {dt:.1f} s.")
        except TestFailure:
            return (f"Medido: {mid} era o único caminho de {client} para {key}: sem alternativa, o fluxo parou "
                    f"(os outros clientes não foram afetados).")

    def back(ctx):
        mid = state.get("mid")
        if mid:
            ctx.act(lambda: ctx.orch.start([mid]))
        ctx.finish()
        return "O nó voltou e os vizinhos religaram-se sozinhos; as rotas foram reconstruídas."

    def kill_server(ctx):
        ctx.settle()
        pick = None
        for srv in servers:
            lost, failover, _kept = _depends_on(plan, srv.id)
            if failover or lost:
                if pick is None or (failover and not pick[2]):
                    pick = (srv.id, lost, failover)
        if pick is None:
            return "Nenhum servidor tem clientes."
        srv, lost, failover = pick
        state["server"] = srv
        t0 = ctx.act(lambda: ctx.orch.kill([srv]))
        parts = []
        if failover:
            c, k = failover[0]
            dt, st = ctx.wait(lambda s: receiving(s, c, k) and (flow(s, c, k) or {}).get("origin") != srv, 60, "a réplica")
            parts.append(f"{c} passou a receber {k} de {flow(st, c, k)['origin']} em {dt:.1f} s "
                         f"(interrupção {interruption(st, c, k, t0):.1f} s)")
        if lost:
            ctx.wait(lambda s: not any(receiving(s, c, k) for c, k in lost), 40, "os fluxos sem réplica pararem")
            parts.append("os fluxos sem réplica pararam: " + ", ".join(f"{c}:{k}" for c, k in lost))
        return "Medido: " + "; ".join(parts) + "."

    def server_back(ctx):
        srv = state.get("server")
        if srv:
            ctx.act(lambda: ctx.orch.start([srv]))
        t0 = time.time()
        ctx.settle()
        return f"Medido: o servidor voltou e todos os clientes receberam de novo em {time.time() - t0:.1f} s."

    def delay_hop(ctx):
        ctx.need_core()
        ctx.settle()
        ctx.wait(lambda s: _all_paths_known(s, plan), 30, "os caminhos de teste")
        client, key, a, b = _active_hop(ctx, ctx.state())
        before = client_paths(ctx.state(), client)[key]
        state["hop"] = (a, b)
        ctx.orch.hop_netem(a, b, delay="80ms")
        dt, st = ctx.wait(lambda s: (rtt(s, a, b) or 0) >= 120, 40, "o RTT subir")
        ctx.sleep(8)
        after = client_paths(ctx.state(), client).get(key)
        return (f"Medido: o RTT {a}–{b} subiu para {rtt(st, a, b):.0f} ms. O caminho de {client} ({key}) era "
                f"{' → '.join(before)} e agora é {' → '.join(after) if after else '(à espera de novo teste)'}"
                + (" (mudou para fugir ao atraso)." if after and after != before else " (não há alternativa melhor)."))

    def cut_hop(ctx):
        ctx.need_core()
        hop = state.get("hop")
        st = ctx.settle()
        ctx.wait(lambda s: _all_paths_known(s, plan), 30, "os caminhos de teste")
        client, key, a, b = _active_hop(ctx, ctx.state())
        before = client_paths(ctx.state(), client)[key]
        ctx.orch.clear_network()
        t0 = ctx.act(lambda: ctx.orch.hop_set(a, b, False))
        try:
            dt, st = ctx.wait(lambda s: receiving(s, client, key), 80, "o fluxo voltar")
            after = client_paths(st, client).get(key)
            return (f"Medido: ligação {a}–{b} cortada; {client} voltou a receber {key} em {dt:.1f} s (interrupção "
                    f"{interruption(st, client, key, t0):.1f} s). Caminho: {' → '.join(before)} → "
                    f"{' → '.join(after) if after else '(à espera de novo teste)'}.")
        finally:
            ctx.orch.hop_set(a, b, True)

    def change_movie(ctx):
        ctx.settle()
        target = max(clients, key=lambda c: len(c.subscribe))
        opts = [o["key"] for o in ctx.orch.subscription_options() if o["server"] is not None
                and not any(_split(x)[1] == o["flow"] for x in target.subscribe)]
        state["client"] = target.id
        if not opts:
            ctx.orch.set_subscriptions(target.id, target.subscribe[1:])
            ctx.sleep(3)
            return f"{target.id} deixou de pedir {target.subscribe[0] if target.subscribe else ''}: o servidor só o emite se alguém o pedir."
        t0 = time.time()
        ctx.orch.set_subscriptions(target.id, target.subscribe + [opts[0]])
        dt, st = ctx.wait(lambda s: receiving(s, target.id, opts[0]), 60, "o filme novo")
        return f"Medido: {target.id} passou a pedir também {opts[0]} e a primeira imagem chegou em {time.time() - t0:.1f} s."

    def reset(ctx):
        ctx.finish()
        ctx.orch.reset_subscriptions()
        return "Cenário reposto: nós todos a correr, subscrições do plano e rede sem atrasos."

    steps = [
        {"title": "Arranque e rotas", "flow": None, "focus": [s.id for s in servers], "core": False, "run": start, "tab": "paths",
         "text": f"Cenário {plan.name}: {len(plan.nodes)} nós, servidores {', '.join(s.id for s in servers)}. Fluxos e conteúdo: {content}. "
                 "Os servidores anunciam os fluxos, cada nó escolhe o montante de menor latência acumulada e os clientes ativam a rota "
                 "pelo caminho inverso. Cada servidor envia também um ROUTE_TEST pela árvore ativa e cada cliente regista o caminho."},
        {"title": "Cada fluxo é um filme diferente", "flow": None, "focus": [c.id for c in clients], "core": False, "run": None, "tab": "video",
         "text": "Cada fluxo tem o seu vídeo (o filme_a é o filme real; os outros são vídeos com o nome do fluxo). "
                 + (f"Os fluxos {', '.join(shared)} são publicados por mais do que um servidor: é o mesmo conteúdo, e um cliente que o "
                    "subscreva sem indicar o servidor usa o mais perto e passa para o outro se esse cair. " if shared else "")
                 + "Compara as janelas dos clientes no separador Vídeo."},
        {"title": "O caminho de cada cliente", "flow": None, "focus": [], "core": False, "run": show_paths, "tab": "paths",
         "text": "O separador Caminhos mostra o último ROUTE_TEST de cada subscrição: o servidor, os nós por onde passou e o cliente. "
                 "É a prova de que os pacotes seguem as rotas da tabela. Clica numa linha para a ver desenhada no grafo."},
    ]
    if mids:
        steps += [
            {"title": "Cai um nó do caminho", "flow": None, "focus": [m.id for m in mids], "core": False, "run": kill_mid, "tab": "paths",
             "text": "Matamos um nó intermédio que faz parte de um caminho. Os vizinhos veem o TCP fechar, a rota é reparada (ou retirada com "
                     "WITHDRAW) e o ROUTE_TEST seguinte mostra o caminho novo, se houver alternativa."},
            {"title": "O nó volta", "flow": None, "focus": [], "core": False, "run": back, "tab": "node",
             "text": "Relançamos o nó: regista-se no bootstrapper, os vizinhos religam-se e as rotas são reconstruídas."},
        ]
    if servers and clients:
        steps += [
            {"title": "Cai um servidor", "flow": None, "focus": [s.id for s in servers], "core": False, "run": kill_server, "tab": "video",
             "text": "Matamos um servidor com clientes. O WITHDRAW percorre a árvore; o que tem réplica passa para o outro servidor e o resto "
                     "pára (vê o vídeo)."},
            {"title": "O servidor volta", "flow": None, "focus": [], "core": False, "run": server_back, "tab": "video",
             "text": "As épocas dos anúncios recomeçam a partir da hora de arranque, por isso os nós aceitam o servidor reiniciado."},
        ]
    steps += [
        {"title": "Atraso num troço do caminho", "flow": None, "focus": [], "core": True, "run": delay_hop, "tab": "paths",
         "text": "Com o tc netem põe-se 80 ms em cada sentido numa ligação do caminho ativo. O RTT medido sobe e, se houver um caminho mais "
                 "rápido, o cliente muda para ele (métrica de latência, com histerese para não oscilar)."},
        {"title": "Corta uma ligação do caminho", "flow": None, "focus": [], "core": True, "run": cut_hop, "tab": "paths",
         "text": "Cortamos a ligação (ip link down): o OSPF recalcula o underlay e/ou o overlay muda de caminho. Mede-se a interrupção."},
        {"title": "O cliente muda de filme", "flow": None, "focus": [], "core": False, "run": change_movie, "tab": "clients",
         "text": "O separador Clientes deixa escolher que fluxos cada cliente subscreve (o cliente é relançado). O servidor só emite o que "
                 "alguém pede, e o ramo novo parte do cliente até ao servidor pelo caminho inverso."},
        {"title": "Repor tudo", "flow": None, "focus": [], "core": False, "run": reset, "tab": "node",
         "text": "Voltamos ao estado inicial: nós todos a correr, subscrições do plano e rede sem atrasos."},
    ]
    return steps


def demo_for(plan):
    """Passos da demonstração guiada do cenário."""
    generic = _demo_generic(plan)
    if plan.name != "cenario4":
        return generic
    extra = {t: next(g for g in generic if g["title"] == t) for t in (
        "O caminho de cada cliente", "Corta uma ligação do caminho", "O cliente muda de filme", "Repor tudo")}
    steps = _demo4()[:-1]   # o guião do cenário 4 e depois os passos que servem a qualquer cenário
    steps.insert(2, extra["O caminho de cada cliente"])
    return steps + [extra["Corta uma ligação do caminho"], extra["O cliente muda de filme"], extra["Repor tudo"]]


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
