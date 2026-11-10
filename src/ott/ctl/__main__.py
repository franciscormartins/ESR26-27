"""``ottctl``: lança e controla um cenário (nós, falhas, atrasos) e abre o painel web.

    ottctl list
    ottctl up cenario4 --backend local          # todos os nós nesta máquina
    ottctl up cenario4 --backend core           # nós de uma sessão do CORE já aberta (Start)
    ottctl status cenario4 | routes cenario4 C1 | events cenario4
    ottctl kill|stop|freeze|thaw|start cenario4 S1
    ottctl netem cenario4 r4 eth1 --delay 30ms --jitter 10ms --loss 2     # só no CORE
    ottctl dashboard cenario4
"""
import argparse
import sys
import time

from .backend import BackendError
from .control import Orchestrator, build
from .plan import PlanError, available
from .report import render_events, render_routes, render_status


def parser():
    p = argparse.ArgumentParser(prog="ottctl", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", help="pasta de trabalho (por omissão /tmp/ottctl/<cenário>)")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="cenários disponíveis")
    up = sub.add_parser("up", help="lança os nós do cenário")
    up.add_argument("scenario")
    up.add_argument("--backend", choices=["local", "core"], default="local")
    up.add_argument("--session", type=int, help="sessão do CORE (por omissão a mais recente com os nós do cenário)")
    up.add_argument("--spare", action="store_true", help="lança também os nós suplentes")
    up.add_argument("--only", help="só estes nós (separados por vírgulas)")
    up.add_argument("--verbose", action="store_true", help="logs detalhados nos nós (-v)")
    up.add_argument("--port", type=int, default=5000, help="porta TCP (modo local)")
    up.add_argument("--data-port", type=int, default=6000, help="porta UDP (modo local)")
    for name, helptext in (("down", "pára todos os nós"), ("status", "estado de todos os nós"),
                           ("events", "eventos relevantes de todos os nós")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("scenario")
        if name == "events":
            sp.add_argument("-n", type=int, default=40)
            sp.add_argument("-f", "--follow", action="store_true")
        if name == "status":
            sp.add_argument("-w", "--watch", action="store_true", help="atualiza de 2 em 2 s")
    rt = sub.add_parser("routes", help="tabela de rotas de um nó")
    rt.add_argument("scenario")
    rt.add_argument("node")
    for name, helptext in (("start", "(re)lança nós"), ("stop", "SIGTERM: saída limpa"), ("kill", "SIGKILL: morte abrupta"),
                           ("freeze", "SIGSTOP: nó calado"), ("thaw", "SIGCONT")):
        sp = sub.add_parser(name, help=helptext)
        sp.add_argument("scenario")
        sp.add_argument("nodes", nargs="+")
    ne = sub.add_parser("netem", help="atraso/jitter/perdas/débito numa interface (só CORE)")
    ne.add_argument("scenario")
    ne.add_argument("node", help="nó overlay ou nó do CORE (ex.: r4)")
    ne.add_argument("iface")
    ne.add_argument("--delay")
    ne.add_argument("--jitter")
    ne.add_argument("--loss")
    ne.add_argument("--rate")
    nc = sub.add_parser("netem-clear", help="remove o netem de uma interface (só CORE)")
    nc.add_argument("scenario")
    nc.add_argument("node")
    nc.add_argument("iface")
    ln = sub.add_parser("link", help="liga ou desliga uma interface (só CORE)")
    ln.add_argument("scenario")
    ln.add_argument("node")
    ln.add_argument("iface")
    ln.add_argument("state", choices=["up", "down"])
    db = sub.add_parser("dashboard", help="painel web (http://127.0.0.1:PORTA)")
    db.add_argument("scenario")
    db.add_argument("--port", type=int, default=8080)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return run(args)
    except (BackendError, PlanError) as exc:
        print(f"ottctl: {exc}", file=sys.stderr)
        return 1


def run(args):
    if args.cmd == "list":
        print("\n".join(available()))
        return 0
    if args.cmd == "up":
        orch = build(args.scenario, args.backend, args.session, args.port, args.data_port, args.dir)
        only = set(args.only.split(",")) if args.only else None
        ids = orch.up(only=only, spare=args.spare, verbose=args.verbose)
        print(f"{orch.plan.name}: {len(ids)} nós lançados ({orch.backend.name}); estado e logs em {orch.rundir}")
        time.sleep(2.5)
        print(render_status(orch.state()))
        return 0
    orch = Orchestrator.attach(args.scenario, args.dir)
    if args.cmd == "down":
        orch.down()
        print(f"{orch.plan.name}: nós parados")
    elif args.cmd == "status":
        while True:
            print(render_status(orch.state()))
            if not args.watch:
                break
            time.sleep(2)
            print()
    elif args.cmd == "events":
        shown = 0
        while True:
            events = orch.state()["events"]
            new = events[shown:] if args.follow and shown else events[-args.n:]
            if new:
                print(render_events(new, len(new)))
            shown = len(events)
            if not args.follow:
                break
            time.sleep(1)
    elif args.cmd == "routes":
        print(render_routes(args.node, orch.read_status(args.node)))
    elif args.cmd in ("start", "stop", "kill", "freeze", "thaw"):
        getattr(orch, args.cmd)(args.nodes)
    elif args.cmd == "netem":
        orch.netem(args.node, args.iface, args.delay, args.jitter, args.loss, args.rate)
    elif args.cmd == "netem-clear":
        orch.netem_clear(args.node, args.iface)
    elif args.cmd == "link":
        orch.link_state(args.node, args.iface, args.state == "up")
    elif args.cmd == "dashboard":
        from .web import serve
        serve(orch, args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
