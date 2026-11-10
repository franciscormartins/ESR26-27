"""Onde e como arrancam os nós: no CORE (``vcmd``) ou todos nesta máquina (IPs de loopback).

Os dois modos montam a mesma linha de comandos do ``ott``; só mudam os endereços e a forma de lançar e de sinalizar
um processo. Os valores que podem vir do painel web (interfaces, atrasos, perdas) são validados com expressões
estritas antes de chegarem a um comando, porque no CORE correm como root dentro do nó.
"""
import glob
import os
import re
import shlex
import signal
import subprocess
import sys

from .plan import DEFAULT_DATA_PORT, DEFAULT_PORT, ROOT

SIGNALS = {"TERM": signal.SIGTERM, "KILL": signal.SIGKILL, "STOP": signal.SIGSTOP, "CONT": signal.SIGCONT}
IFACE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,14}$")
TIME_RE = re.compile(r"^\d{1,5}(\.\d{1,3})?(us|ms|s)$")
RATE_RE = re.compile(r"^\d{1,7}(kbit|mbit|gbit|bit)$")
PERCENT_RE = re.compile(r"^\d{1,3}(\.\d{1,2})?%?$")
OTT = os.path.join(ROOT, "bin", "ott")


class BackendError(RuntimeError):
    pass


def default_runner(argv, timeout=20):
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr).strip()


def netem_command(iface, delay=None, jitter=None, loss=None, rate=None):
    """Comando ``tc`` (como lista) para atraso, jitter, perdas e largura de banda numa interface."""
    if not IFACE_RE.match(iface or ""):
        raise BackendError(f"interface inválida: {iface!r}")
    cmd = ["tc", "qdisc", "replace", "dev", iface, "root", "netem"]
    if delay:
        if not TIME_RE.match(delay):
            raise BackendError(f"atraso inválido: {delay!r} (ex.: 30ms)")
        cmd += ["delay", delay]
        if jitter:
            if not TIME_RE.match(jitter):
                raise BackendError(f"jitter inválido: {jitter!r} (ex.: 10ms)")
            cmd += [jitter, "distribution", "normal"]
    elif jitter:
        raise BackendError("o jitter precisa de um atraso")
    if loss:
        if not PERCENT_RE.match(str(loss)):
            raise BackendError(f"perda inválida: {loss!r} (ex.: 5%)")
        cmd += ["loss", str(loss) if str(loss).endswith("%") else f"{loss}%"]
    if rate:
        if not RATE_RE.match(rate):
            raise BackendError(f"débito inválido: {rate!r} (ex.: 500kbit)")
        cmd += ["rate", rate]
    if len(cmd) == 7:
        raise BackendError("indica pelo menos um de: atraso, perdas, débito")
    return cmd


class Backend:
    name = "?"

    def __init__(self, plan):
        self.plan = plan

    # -- endereços e linha de comandos --------------------------------

    def address(self, node_id):
        """(host, porta TCP) onde o nó escuta, como o veem os outros nós."""
        raise NotImplementedError

    def net_args(self, node_id):
        return []

    def overlay_for_serve(self):
        return self.plan.overlay_path

    def argv(self, spec, status_path, verbose=False, interval=0.5, extra=(), bootstrap=None):
        """Argumentos do ``ott`` para este nó (sem o executável).

        ``extra`` acrescenta argumentos (ex.: ``--min-neighbors 4``); ``bootstrap`` troca o bootstrapper por outro nó.
        """
        a = ["-i", spec.id, *self.net_args(spec.id), "--status-file", status_path, "--status-interval", str(interval)]
        if spec.serve:
            a += ["--serve", self.overlay_for_serve()]
        boot = bootstrap or self.plan.bootstrap_of(spec)
        if not spec.serve:
            override = None if bootstrap else self.bootstrap_override(spec)
            if override:
                a += ["-b", override]
            elif boot:
                host, port = self.address(boot)
                a += ["-b", f"{host}:{port}"]
        for flow in spec.publish:
            a += ["--publish-flow", flow]
        for sub in spec.subscribe:
            a += ["--subscribe", sub]
        a += self.neighbor_args(spec)
        a += spec.extra + list(extra)
        if verbose:
            a.append("-v")
        return a

    def bootstrap_override(self, spec):
        return None

    def neighbor_args(self, spec):
        out = []
        for peer in spec.neighbors:
            host, port = self.address(peer)
            out.append(f"{host}:{port}")
        return out

    # -- ciclo de vida -------------------------------------------------

    def prepare(self, rundir):
        pass

    def start(self, spec, argv, log_path):
        raise NotImplementedError

    def signal(self, spec, name):
        raise NotImplementedError

    def alive(self, spec):
        return None

    def execute(self, core_name, argv):
        raise BackendError(f"o modo {self.name} não executa comandos de rede (só no CORE)")

    def probe(self, core_name, argv, timeout=30):
        """Corre o ``ott`` com estes argumentos num host livre e devolve (código, texto de saída)."""
        raise NotImplementedError


class LocalBackend(Backend):
    """Todos os nós nesta máquina: cada um com o seu IP de loopback (127.0.0.N) e as mesmas portas."""
    name = "local"

    def __init__(self, plan, port=DEFAULT_PORT, data_port=DEFAULT_DATA_PORT):
        super().__init__(plan)
        self.port, self.data_port = port, data_port
        ids = sorted(plan.nodes)
        self.ips = {n: f"127.0.0.{11 + i}" for i, n in enumerate(ids)}
        self.procs = {}
        self.pids = {}
        self.rundir = None

    def address(self, node_id):
        return self.ips[node_id], self.port

    def net_args(self, node_id):
        return ["--bind", self.ips[node_id], "-p", str(self.port), "--data-port", str(self.data_port)]

    def overlay_for_serve(self):
        return os.path.join(self.rundir, "overlay.json")

    def prepare(self, rundir):
        """Escreve o ficheiro overlay com os endereços locais (o bootstrapper devolve estes)."""
        import json
        self.rundir = rundir
        nodes = {}
        for node_id, info in self.plan.overlay.items():
            entry = dict(info)
            if node_id in self.ips:
                entry["host"], entry["port"] = self.ips[node_id], self.port
            nodes[node_id] = entry
        with open(self.overlay_for_serve(), "w") as f:
            json.dump({"nodes": nodes}, f, indent=1)

    def start(self, spec, argv, log_path):
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
        log = open(log_path, "ab")
        proc = subprocess.Popen([sys.executable, "-m", "ott", *argv], stdout=log, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, env=env, start_new_session=True, cwd=ROOT)
        log.close()
        self.procs[spec.id] = proc
        self.pids[spec.id] = proc.pid
        return proc.pid

    def probe(self, core_name, argv, timeout=30):
        env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "src"))
        try:
            p = subprocess.run([sys.executable, "-m", "ott", "--bind", "127.0.0.250", "-p", str(self.port + 40),
                                "--data-port", str(self.data_port + 40), *argv], capture_output=True, text=True,
                               timeout=timeout, env=env, cwd=ROOT)
        except subprocess.TimeoutExpired as exc:
            return 124, f"(excedeu {timeout} s) {exc.stdout or ''}"
        return p.returncode, (p.stdout + p.stderr).strip()

    def adopt(self, pids):
        """Retoma pids de uma execução anterior (de outro processo ``ottctl``)."""
        self.pids.update({k: v for k, v in pids.items() if v})

    def signal(self, spec, name):
        pid = self.pids.get(spec.id)
        if not pid:
            raise BackendError(f"{spec.id} não foi lançado")
        try:
            os.kill(pid, SIGNALS[name])
        except ProcessLookupError:
            raise BackendError(f"{spec.id} já não está a correr") from None
        if name in ("TERM", "KILL") and spec.id in self.procs:
            try:
                self.procs[spec.id].wait(timeout=3)   # recolhe o processo (sem zombies)
            except subprocess.TimeoutExpired:
                pass

    def alive(self, spec):
        proc = self.procs.get(spec.id)
        if proc is not None:
            return proc.poll() is None
        pid = self.pids.get(spec.id)
        if not pid:
            return False
        try:
            with open(f"/proc/{pid}/stat") as f:
                return f.read().rsplit(")", 1)[1].split()[0] != "Z"
        except OSError:
            return False


class CoreBackend(Backend):
    """Nós de uma sessão do CORE já aberta: os comandos correm dentro de cada nó com ``vcmd``."""
    name = "core"

    def __init__(self, plan, session=None, runner=None, base="/tmp"):
        super().__init__(plan)
        self.run = runner or default_runner
        self.base = base
        self.session_dir = self.find_session(session)

    def find_session(self, session=None):
        if session is not None:
            path = os.path.join(self.base, f"pycore.{int(session)}")
            if not os.path.isdir(path):
                raise BackendError(f"a sessão {session} do CORE não existe ({path})")
            return path
        needed = self.plan.core_names()
        found = []
        for path in glob.glob(os.path.join(self.base, "pycore.*")):
            tail = path.rsplit(".", 1)[1]
            if tail.isdigit() and all(os.path.exists(os.path.join(path, n)) for n in needed):
                found.append((int(tail), path))
        if not found:
            raise BackendError(f"não há nenhuma sessão do CORE com os nós de {self.plan.name}: abre o cenário "
                               f"no CORE e faz Start (ou usa --backend local)")
        return max(found)[1]

    @property
    def session(self):
        return int(self.session_dir.rsplit(".", 1)[1])

    def address(self, node_id):
        info = self.plan.overlay[node_id]
        return info["host"], info.get("port", DEFAULT_PORT)

    def bootstrap_override(self, spec):
        return spec.core_bootstrap

    def neighbor_args(self, spec):
        return list(spec.core_neighbors) if spec.core_neighbors else super().neighbor_args(spec)

    def vcmd(self, core_name, argv, timeout=20):
        return self.run(["vcmd", "-c", os.path.join(self.session_dir, core_name), "--", *argv], timeout)

    def start(self, spec, argv, log_path):
        command = (f"cd {shlex.quote(ROOT)} && exec setsid ./bin/ott {shlex.join(argv)} "
                   f">> {shlex.quote(log_path)} 2>&1 < /dev/null &")
        rc, out = self.vcmd(spec.core, ["sh", "-c", command])
        if rc != 0:
            raise BackendError(f"não consegui lançar {spec.id} em {spec.core}: {out}")

    def signal(self, spec, name):
        if name not in SIGNALS:
            raise BackendError(f"sinal desconhecido: {name}")
        rc, out = self.vcmd(spec.core, ["pkill", f"-{name}", "-f", "^python3 -m ott"])
        if rc == 1:
            raise BackendError(f"{spec.id} não está a correr em {spec.core}")
        if rc != 0:
            raise BackendError(f"falhou o sinal {name} a {spec.id}: {out}")

    def execute(self, core_name, argv):
        return self.vcmd(core_name, argv)

    def probe(self, core_name, argv, timeout=30):
        command = f"cd {shlex.quote(ROOT)} && timeout {int(timeout)} ./bin/ott {shlex.join(argv)} 2>&1"
        return self.vcmd(core_name, ["sh", "-c", command], timeout=timeout + 10)
