"""Medições de tempos do overlay em localhost, com os parâmetros por omissão.

    PYTHONPATH=src python3 tests/medicoes.py [REPETICOES] [morte|substituto|simultaneo|bootstrapper ...]

Não é um teste unitário (o nome não é apanhado pelo ``unittest discover``).
Usa a topologia do cenário 2 com portas locais. O nó "vítima" corre como
processo real (``bin/ott``) para poder ser morto ou congelado com sinais; os
restantes nós correm neste processo e registam o instante dos eventos
up/down. Parâmetros: keepalive 2 s, dead_after 6 s, retry 2 s, replace 5 s.
Escreve no fim uma tabela em Markdown (mínimo, média e máximo em segundos).
"""
import logging
import os
import signal
import statistics
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from ott.bootstrapper import BootstrapService  # noqa: E402
from ott.node import OverlayNode  # noqa: E402

logging.disable(logging.CRITICAL)

HOST = "127.0.0.1"
ORDER = ("S1", "O1", "O2", "C1", "C2")
EDGES = {"S1": ["O1", "O2"], "O1": ["O2", "C2"], "O2": ["C1"], "C1": [], "C2": []}


def topo(base):
    return {"nodes": {n: {"host": HOST, "port": base + i, "neighbors": EDGES[n]}
                      for i, n in enumerate(ORDER)}}


def wait_until(cond, timeout=30.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class Watcher:
    """Regista (vizinho, instante) de cada evento up/down de um nó."""

    def __init__(self, node):
        self.ups, self.downs = [], []
        node.on_neighbor_up(lambda n, i: self.ups.append((i, time.monotonic())))
        node.on_neighbor_down(lambda n, i: self.downs.append((i, time.monotonic())))

    @staticmethod
    def first(events, nid, after):
        return min((t for i, t in events if i == nid and t >= after), default=None)


class Overlay:
    """Nós observadores neste processo + uma vítima como processo real."""

    def __init__(self, base, observers, min_neighbors=None):
        self.base = base
        self.config = topo(base)
        self.service = BootstrapService(self.config)
        self.nodes, self.watch, self.victims = {}, {}, {}
        for name in observers:
            self.add(name, (min_neighbors or {}).get(name, 0))

    def port(self, name):
        return self.config["nodes"][name]["port"]

    def add(self, name, min_neighbors=0):
        node = OverlayNode(name, HOST, self.port(name), min_neighbors=min_neighbors,
                           bootstrap_service=self.service if name == "S1" else None)
        self.watch[name] = Watcher(node)
        node.start()
        if name == "S1":
            self.service.self_id = "S1"
            node.add_targets({self.service.address_of(n["id"]): n["id"]
                              for n in self.service.neighbors_of("S1")})
        else:
            node.bootstrap(HOST, self.port("S1"), attempts=5)
        self.nodes[name] = node
        return node

    def remove(self, name):
        self.nodes.pop(name).stop()
        self.watch.pop(name)

    def spawn(self, name):
        p = subprocess.Popen(
            [os.path.join(ROOT, "bin", "ott"), "-i", name, "-p", str(self.port(name)),
             "--bind", HOST, "-b", f"{HOST}:{self.port('S1')}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.victims[name] = p
        return p

    def signal(self, name, sig):
        os.kill(self.victims[name].pid, sig)

    def kill(self, name):
        p = self.victims.pop(name)
        p.kill()
        p.wait()

    def close(self):
        for name in list(self.victims):
            self.kill(name)
        for node in self.nodes.values():
            node.stop()


def stat(values):
    values = [v for v in values if v is not None]
    if not values:
        return "n/d"
    return f"{min(values):.2f} / {statistics.mean(values):.2f} / {max(values):.2f}"


def victim_up(ov, victim, observers):
    return wait_until(lambda: all(victim in ov.nodes[o].neighbor_ids() for o in observers), 20)


def measure_kill_freeze(reps, results):
    """Vítima O1 (vizinhos S1, O2, C2). Morte (TCP fecha), reentrada e congelamento."""
    ov = Overlay(48100, ("S1", "O2", "C2"))
    obs = ("S1", "O2", "C2")
    try:
        ov.spawn("O1")
        for _ in range(reps):
            assert victim_up(ov, "O1", obs), "O1 não ligou"
            time.sleep(1)
            t0 = time.monotonic()
            ov.signal("O1", signal.SIGKILL)
            ov.victims.pop("O1").wait()
            wait_until(lambda: all(Watcher.first(ov.watch[o].downs, "O1", t0) for o in obs), 20)
            results["kill"] += [Watcher.first(ov.watch[o].downs, "O1", t0) - t0 for o in obs
                                if Watcher.first(ov.watch[o].downs, "O1", t0)]
            t1 = time.monotonic()
            ov.spawn("O1")
            wait_until(lambda: all(Watcher.first(ov.watch[o].ups, "O1", t1) for o in obs), 20)
            ups = [Watcher.first(ov.watch[o].ups, "O1", t1) for o in obs]
            results["reentrada"].append(max(ups) - t1 if all(ups) else None)
        for _ in range(reps):
            assert victim_up(ov, "O1", obs), "O1 não ligou"
            time.sleep(1)
            t0 = time.monotonic()
            ov.signal("O1", signal.SIGSTOP)
            wait_until(lambda: all(Watcher.first(ov.watch[o].downs, "O1", t0) for o in obs), 30)
            results["timeout"] += [Watcher.first(ov.watch[o].downs, "O1", t0) - t0 for o in obs
                                   if Watcher.first(ov.watch[o].downs, "O1", t0)]
            t1 = time.monotonic()
            ov.signal("O1", signal.SIGCONT)
            wait_until(lambda: all(Watcher.first(ov.watch[o].ups, "O1", t1) for o in obs), 30)
            ups = [Watcher.first(ov.watch[o].ups, "O1", t1) for o in obs]
            results["descongelar"].append(max(ups) - t1 if all(ups) else None)
    finally:
        ov.close()


def measure_substitute(reps, results):
    """C1 (min_neighbors=1) só tem O2; mata-se O2 e mede-se até ter outro vizinho."""
    ov = Overlay(48200, ("S1", "O1"))
    try:
        for _ in range(reps):
            ov.spawn("O2")
            c1 = ov.add("C1", min_neighbors=1)
            if not wait_until(lambda: c1.neighbor_ids() == ["O2"], 20):
                results["substituto"].append(None)
            else:
                time.sleep(3)  # deixa o ciclo de manutenção repor o contador de défice de vizinhos
                t0 = time.monotonic()
                ov.kill("O2")
                ok = wait_until(lambda: any(n != "O2" for n in c1.neighbor_ids()), 40)
                results["substituto"].append(time.monotonic() - t0 if ok else None)
            ov.remove("C1")
            time.sleep(0.5)
    finally:
        ov.close()


def measure_simultaneous(reps, results):
    """Arranque simultâneo dos 5 nós: tempo até convergir e ligações duplicadas."""
    expected = {"S1": ["O1", "O2"], "O1": ["C2", "O2", "S1"], "O2": ["C1", "O1", "S1"],
                "C1": ["O2"], "C2": ["O1"]}
    for r in range(reps):
        base = 48300 + 10 * r
        config = topo(base)
        service = BootstrapService(config)
        service.self_id = "S1"
        nodes = {n: OverlayNode(n, HOST, base + i, keepalive=2.0,
                                bootstrap_service=service if n == "S1" else None)
                 for i, n in enumerate(ORDER)}
        watchers = {n: Watcher(nodes[n]) for n in ORDER}
        barrier = threading.Barrier(len(ORDER) + 1)

        def run(name):
            barrier.wait()
            nodes[name].start()
            if name == "S1":
                nodes[name].add_targets({service.address_of(x["id"]): x["id"]
                                         for x in service.neighbors_of("S1")})
            else:
                nodes[name].bootstrap(HOST, base, attempts=10)

        threads = [threading.Thread(target=run, args=(n,), daemon=True) for n in ORDER]
        for t in threads:
            t.start()
        barrier.wait()
        t0 = time.monotonic()
        ok = wait_until(lambda: all(nodes[n].neighbor_ids() == expected[n] for n in ORDER), 30)
        results["simultaneo"].append(time.monotonic() - t0 if ok else None)
        time.sleep(3)  # janela para ver duplicados ou quedas espúrias
        ups = [(n, i) for n in ORDER for i, _ in watchers[n].ups]
        extra = len(ups) - len(set(ups))
        extra += sum(len(w.downs) for w in watchers.values())
        results["duplicados"].append(extra)
        for node in nodes.values():
            node.stop()
        time.sleep(0.3)


def measure_bootstrapper_late(reps, results):
    """O2 arranca com o S1 em baixo; o S1 sobe 3 s depois."""
    for r in range(reps):
        base = 48500 + 10 * r
        config = topo(base)
        service = BootstrapService(config)
        service.self_id = "S1"
        o2 = OverlayNode("O2", HOST, base + 2, retry_interval=2.0)
        o2.start()
        done = {}

        def go():
            done["ok"] = o2.bootstrap(HOST, base, attempts=10)

        t = threading.Thread(target=go, daemon=True)
        t.start()
        time.sleep(3)
        s1 = OverlayNode("S1", HOST, base, bootstrap_service=service)
        s1.start()
        s1.add_targets({service.address_of(x["id"]): x["id"]
                        for x in service.neighbors_of("S1")})
        t0 = time.monotonic()
        ok = wait_until(lambda: "S1" in o2.neighbor_ids(), 30)
        results["bootstrapper"].append(time.monotonic() - t0 if ok and done.get("ok", True) else None)
        o2.stop()
        s1.stop()
        time.sleep(0.3)


def main():
    reps = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    results = {k: [] for k in ("kill", "reentrada", "timeout", "descongelar", "substituto",
                               "simultaneo", "duplicados", "bootstrapper")}
    steps = [("morte, reentrada, congelamento", measure_kill_freeze),
             ("substituto", measure_substitute),
             ("arranque simultâneo", measure_simultaneous),
             ("bootstrapper tardio", measure_bootstrapper_late)]
    wanted = sys.argv[2:]
    keys = {"morte": measure_kill_freeze, "substituto": measure_substitute,
            "simultaneo": measure_simultaneous, "bootstrapper": measure_bootstrapper_late}
    for label, fn in steps:
        if wanted and fn not in [keys[w] for w in wanted]:
            continue
        print(f"a medir: {label} ...", flush=True)
        fn(reps if fn is not measure_bootstrapper_late else max(3, reps // 2), results)
    rows = [
        ("Processo morto (SIGKILL): deteção nos vizinhos", "kill"),
        ("Reentrada do nó morto (até todos os vizinhos ligados)", "reentrada"),
        ("Nó congelado (SIGSTOP): deteção por timeout", "timeout"),
        ("Nó descongelado (SIGCONT): religação", "descongelar"),
        ("Substituto: do fim do vizinho até novo vizinho (`--min-neighbors 1`)", "substituto"),
        ("Arranque simultâneo dos 5 nós: tempo até overlay completo", "simultaneo"),
        ("Bootstrapper tardio (sobe 3 s depois): tempo até ligar", "bootstrapper"),
    ]
    print(f"\n| Medida (s) | n | mín / média / máx |\n|---|---|---|")
    for text, key in rows:
        vals = [v for v in results[key] if v is not None]
        print(f"| {text} | {len(vals)} | {stat(vals)} |")
    dup = results["duplicados"]
    print(f"\nLigações duplicadas ou quedas espúrias no arranque simultâneo: {sum(dup)} "
          f"em {len(dup)} execuções.")
    failed = {k: results[k].count(None) for k in results if None in results[k]}
    if failed:
        print(f"Falhas (timeouts de espera): {failed}")


if __name__ == "__main__":
    main()
