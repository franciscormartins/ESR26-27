"""Vista compacta do tráfego na consola (``ott --live``).

Um pequeno bloco, sempre redesenhado no mesmo sítio, com cada fluxo que passa
pelo nó:

    ── O5 · 16:37:04 ─────────────────────────────
    O1:filme_a   1840 pacotes   20/s   0 perdidos
       vem de O3 (3 saltos desde O1, 1.2 ms) → [O5] → O10, O11
       mudou às 16:37:02 (antes vinha de O7, 3 saltos)

As mensagens de log aparecem por cima do bloco, que é redesenhado a seguir.
Sem terminal (p.ex. com ``| tee``) o bloco é escrito normalmente, uma vez por
intervalo.

Só lê: os contadores do ``OverlayForwarder`` (``traffic``) e a interface
pública da ``RoutingTable`` (``path_info``, ``active_destinations``,
``has_local_subscription``). Cada nó só conhece o vizinho de onde recebe e a
distância até à origem, não o caminho completo.
"""
import logging
import shutil
import sys
import threading
import time

log = logging.getLogger("ott.live")


class LiveConsole(logging.Handler):
    """Handler de logging que partilha a consola com um bloco de estado."""

    def __init__(self, stream=None):
        super().__init__()
        self.stream = stream or sys.stderr
        try:
            self.tty = self.stream.isatty()
        except (AttributeError, ValueError):
            self.tty = False
        self._lines = []
        self._console_lock = threading.Lock()

    def _clear(self):
        """Volta ao início do bloco e apaga-o (só no terminal)."""
        if self.tty and self._lines:
            if len(self._lines) > 1:
                self.stream.write(f"\x1b[{len(self._lines) - 1}A")
            self.stream.write("\r\x1b[J")

    def _draw(self):
        if self.tty and self._lines:
            self.stream.write("\n".join(self._lines))

    def emit(self, record):
        try:
            text = self.format(record)
            with self._console_lock:
                self._clear()
                self.stream.write(text + "\n")
                self._draw()
                self.stream.flush()
        except Exception:
            self.handleError(record)

    def show(self, lines):
        """Substitui o bloco de estado (no terminal, redesenhado no mesmo sítio)."""
        lines = list(lines) or [""]
        with self._console_lock:
            if self.tty:
                width = shutil.get_terminal_size((120, 24)).columns
                # uma linha que quebra ocupa duas e o bloco deixava de se apagar bem
                lines = [line if len(line) < width else line[:max(width - 2, 1)] + "…"
                         for line in lines]
                self._clear()
                self._lines = lines
                self._draw()
            else:
                self.stream.write("\n".join(lines) + "\n")
            self.stream.flush()

    def finish(self):
        """Deixa o último bloco no ecrã e passa à linha seguinte."""
        with self._console_lock:
            if self.tty and self._lines:
                self.stream.write("\n")
                self.stream.flush()
            self._lines = []


def _hhmmss(wall):
    return time.strftime("%H:%M:%S", time.localtime(wall))


def _hops(n):
    return "1 salto" if n == 1 else f"{n} saltos"


class TrafficView:
    """Constrói o bloco de estado e deteta trocas de montante."""

    def __init__(self, node_id, forwarder, routing_table, published=(), subscriptions=(),
                 clock=time.monotonic, wall=time.time):
        self.node_id = node_id
        self.forwarder = forwarder
        self.table = routing_table
        self.published = list(published)
        self.subscriptions = subscriptions if callable(subscriptions) else list(subscriptions)
        self.clock = clock
        self.wall = wall
        self._rate = {}        # fluxo -> (contagem, instante)
        self._upstream = {}    # fluxo -> (montante, saltos) atuais
        self._change = {}      # fluxo -> (hora, montante anterior, saltos anteriores)

    def _keys(self, counts):
        subs = self.subscriptions() if callable(self.subscriptions) else self.subscriptions
        keys = set(counts) | set(subs)
        keys |= {(self.node_id, flow) for flow in self.published}
        return sorted(keys)

    def _path(self, key):
        info = self.table.path_info(*key)
        return (None, None, None) if info is None else info

    def check_upstreams(self):
        """Regista as trocas de montante desde a última chamada; devolve-as."""
        changes = []
        for key in self._keys(self.forwarder.traffic.counts()):
            if key[0] == self.node_id:
                continue
            upstream, hops, _ = self._path(key)
            if key not in self._upstream:
                self._upstream[key] = (upstream, hops)
                continue
            before, before_hops = self._upstream[key]
            if upstream == before:
                self._upstream[key] = (upstream, hops)   # a distância pode mudar sem troca
                continue
            self._upstream[key] = (upstream, hops)
            if before is not None:
                self._change[key] = (_hhmmss(self.wall()), before, before_hops)
            changes.append((key, before, upstream))
            if upstream is None:
                log.info("%s:%s: perdeu o caminho (vinha de %s)", key[0], key[1], before)
            elif before is None:
                log.info("%s:%s: passa a vir de %s (%s desde %s)", key[0], key[1],
                         upstream, _hops(hops), key[0])
            else:
                log.info("%s:%s: passa a vir de %s (%s desde %s); antes vinha de %s",
                         key[0], key[1], upstream, _hops(hops), key[0], before)
        return changes

    def _rate_of(self, key, count, now):
        prev = self._rate.get(key)
        self._rate[key] = (count, now)
        if prev is None or now <= prev[1] or count < prev[0]:
            return None
        return (count - prev[0]) / (now - prev[1])

    def flows(self):
        """Um bloco de linhas por fluxo (a primeira é o cabeçalho do fluxo)."""
        now = self.clock()
        counts = self.forwarder.traffic.counts()
        me = f"[{self.node_id}]"
        blocks = []
        for key in self._keys(counts):
            origin, flow = key
            c = counts.get(key) or {"received": 0, "lost": 0, "sent": 0}
            dests = list(self.table.active_destinations(origin, flow))
            onward = f" → {', '.join(dests)}" if dests else ""
            if origin == self.node_id:
                rate = self._rate_of(key, c["sent"], now)
                blocks.append([
                    f"{origin}:{flow}  {c['sent']:>6} enviados  {_fmt_rate(rate)}",
                    f"   origem {me}{onward or ' (ninguém a receber)'}"])
                continue
            rate = self._rate_of(key, c["received"], now)
            lines = [f"{origin}:{flow}  {c['received']:>6} pacotes  {_fmt_rate(rate)}"
                     f"  {c['lost']} perdidos"]
            upstream, hops, delay = self._path(key)
            here = me + (" (cliente)" if self.table.has_local_subscription(origin, flow)
                         else "")
            if upstream is None:
                lines.append(f"   sem caminho até {origin} (à espera de anúncios)")
            else:
                dist = _hops(hops) + f" desde {origin}" if hops is not None else ""
                if dist and delay is not None:
                    dist += f", {delay * 1000:.1f} ms"
                lines.append(f"   vem de {upstream}" + (f" ({dist})" if dist else "")
                             + f" → {here}{onward}")
            change = self._change.get(key)
            if change:
                at, before, before_hops = change
                lines.append(f"   mudou às {at} (antes vinha de {before}"
                             + (f", {_hops(before_hops)})" if before_hops is not None else ")"))
            blocks.append(lines)
        return blocks

    def render(self):
        """Linhas do bloco da consola."""
        title = f"── {self.node_id} · {_hhmmss(self.wall())} "
        lines = [title + "─" * max(0, 46 - len(title))]
        for block in self.flows():
            lines.extend(block)
        if len(lines) == 1:
            lines.append("sem fluxos")
        return lines

    def file_lines(self, blocks=None):
        """Uma linha por fluxo, para o ficheiro de log."""
        blocks = self.flows() if blocks is None else blocks
        return [" | ".join(part.strip() for part in block) for block in blocks] or ["sem fluxos"]


def _fmt_rate(rate):
    return "  --/s" if rate is None else f"{rate:4.0f}/s"


class LiveView:
    """Thread que atualiza o bloco a cada ``interval`` e vigia o montante."""

    def __init__(self, view, console, interval=2.0, file_logger=None, poll=0.25):
        self.view = view
        self.console = console
        self.interval = interval
        self.file_logger = file_logger   # p.ex. logger só com o ficheiro de log
        self.poll = min(poll, interval)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True, name="ott-live")

    def start(self):
        self._thread.start()
        return self

    def _tick(self):
        lines = self.view.render()
        self.console.show(lines)
        if self.file_logger is not None:
            for line in self.view.file_lines(_split_blocks(lines)):
                self.file_logger.info("%s", line)

    def _run(self):
        next_draw = 0.0
        while not self._stop.is_set():
            try:
                self.view.check_upstreams()
                now = time.monotonic()
                if now >= next_draw:
                    self._tick()
                    next_draw = now + self.interval
            except Exception:
                log.exception("erro a atualizar a vista --live")
            self._stop.wait(self.poll)

    def stop(self):
        self._stop.set()
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self.console.finish()


def _split_blocks(lines):
    """Reagrupa as linhas do bloco (sem o título) por fluxo: as indentadas juntam-se à anterior."""
    blocks = []
    for line in lines[1:]:
        if line.startswith("   ") and blocks:
            blocks[-1].append(line)
        elif line != "sem fluxos":
            blocks.append([line])
    return blocks
