"""Tabelas de texto com o estado de um cenário (para o terminal)."""
import time

HEALTH = {"up": "ligado", "waiting": "à espera do bootstrapper", "off": "por lançar", "stopped": "parado", "dead": "morto", "frozen": "congelado",
          "stale": "sem resposta"}


def _fmt(rows, headers):
    widths = [max(len(str(r[i])) for r in [headers] + rows) for i in range(len(headers))]
    line = "  ".join(f"{{:<{w}}}" for w in widths)
    out = [line.format(*headers), line.format(*("-" * w for w in widths))]
    out += [line.format(*(str(c) for c in r)) for r in rows]
    return "\n".join(out)


def render_status(state):
    """Uma linha por nó: estado, vizinhos com RTT, rotas ativas e fluxos."""
    rows = []
    for nid in sorted(state["nodes"], key=lambda n: (state["nodes"][n]["role"] != "servidor", n)):
        n = state["nodes"][nid]
        st = n["status"] or {}
        neigh = " ".join(f"{x['id']}({x['rtt_ms']:.1f})" if x["rtt_ms"] is not None else f"{x['id']}(?)"
                         for x in st.get("neighbors", []))
        routes = ", ".join(f"{r['origin']}:{r['flow']}←{r['upstream'] or 'origem'}"
                           for r in st.get("routes", []) if r["state"] == "ativa")
        flows = ", ".join(f"{f['origin']}:{f['flow']} {f['rate']:.0f}/s" + (f" perd {f['loss_pct']}%" if f["lost"] else "")
                          + (" PAROU" if f["stalled"] else "") for f in st.get("flows", []) if f["active"])
        rows.append([nid, n["core"] if n["core"] != nid else "", n["role"], HEALTH.get(n["health"], n["health"]),
                     neigh or "-", routes or "-", flows or "-"])
    head = f"{state['scenario']} ({state['backend']})  {time.strftime('%H:%M:%S', time.localtime(state['time']))}"
    return head + "\n" + _fmt(rows, ["nó", "core", "papel", "estado", "vizinhos (RTT ms)", "rotas ativas", "fluxos recebidos"])


def render_routes(node_id, status):
    if not status:
        return f"{node_id}: sem estado (nó por lançar?)"
    rows = []
    for r in status["routes"]:
        cands = " ".join(f"{n}:{c['metric']:.0f}" + (f"/{c['delay_ms']:.1f}ms" if c["delay_ms"] is not None else "")
                         for n, c in r["candidates"].items() if c["metric"] is not None)
        rows.append([f"{r['origin']}:{r['flow']}", r["upstream"] or "(origem)" if not r["withdrawn"] else "(retirada)",
                     "-" if r["metric"] is None else f"{r['metric']:.0f}",
                     "-" if r["delay_ms"] is None else f"{r['delay_ms']:.1f}", r["state"], r["epoch"],
                     ",".join(r["destinations"]) or "-", ",".join(r["local_subscribers"]) or "-", cands or "-"])
    if not rows:
        return f"{node_id}: sem rotas"
    return f"Tabela de rotas de {node_id}\n" + _fmt(rows, ["fluxo", "montante", "saltos", "atraso ms", "estado", "época",
                                                          "destinos", "local", "candidatos (saltos/atraso)"])


def render_events(events, last=40):
    out = []
    for e in events[-last:]:
        out.append(f"{time.strftime('%H:%M:%S', time.localtime(e['t']))}.{int(e['t'] * 1000) % 1000:03d}  "
                   f"{e['node']:<4} {e['text']}")
    return "\n".join(out) if out else "(sem eventos)"
