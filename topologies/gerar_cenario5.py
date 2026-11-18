#!/usr/bin/env python3
"""Gera o cenário 5 (dois servidores, anel com acessos): topologies/cenario5.{xml,json,plano.json}.

Desenho (ver docs/CENARIO5.md):

            C1   O5   C2            C5 junto do O1
              \\  |  /
          O1 ---- r7 ---- O4         O5 = acesso de cima  (C1, C2)
        /  |      |       |  \\       O6 = acesso de baixo (C3, C4)
      S1   |      O7      |   S2     O7 = suplente (liga O5 a O6)
        \\  |      |       |  /
          O2 ---- r8 ---- O3
              /  |  \\
            C3   O6   C4

Overlay: S1-{O1,O2}, S2-{O3,O4}, anel O1-O2-O3-O4-O1, O5-{O1,O4}, O6-{O2,O3}, O7-{O5,O6} (suplente), clientes C1,C2-O5,
C3,C4-O6 e C5-O1. O underlay tem um router por nó do overlay (r1..r9) e uma ligação entre routers por cada ligação do
overlay entre nós de infraestrutura: o caminho IP de cada ligação overlay é único (host - router - router - host) e o
OSPF nunca reparte o tráfego por caminhos de custo igual. Os clientes ficam em sub-redes próprias no router do seu nó de
acesso. A ligação r3-r6 (O1-O4) tem 10 ms de atraso de base, para que a métrica (latência) escolha logo à partida um
caminho com mais saltos mas mais rápido (filme_c para o C5: S2-O3-O2-O1 em vez de S2-O4-O1).

    python3 topologies/gerar_cenario5.py          # reescreve o XML, o JSON e o plano (e aplica o FRR aos routers)
"""
import json
import os
import sys
import xml.etree.ElementTree as ET

import aplicar_frr
from gerar_cenario4 import ip6, latlon

HERE = os.path.dirname(os.path.abspath(__file__))
CANVAS = (1000, 750)

# router de cada nó de infraestrutura (o host com o mesmo nome liga-se a ele) e posição do router no canvas
ROUTERS = {
    "r1": ("S1", (170, 375)), "r2": ("S2", (830, 375)),
    "r3": ("O1", (330, 230)), "r4": ("O2", (330, 520)),
    "r5": ("O3", (670, 520)), "r6": ("O4", (670, 230)),
    "r7": ("O5", (500, 180)), "r8": ("O6", (500, 570)),
    "r9": ("O7", (500, 375)),
}
# hosts: (router, posição). A ordem define as sub-redes (10.0.N.0/24) depois das ligações entre routers.
HOSTS = {
    "S1": ("r1", (70, 375)), "S2": ("r2", (930, 375)),
    "O1": ("r3", (330, 140)), "O2": ("r4", (330, 610)),
    "O3": ("r5", (670, 610)), "O4": ("r6", (670, 140)),
    "O5": ("r7", (500, 95)), "O6": ("r8", (500, 655)), "O7": ("r9", (590, 375)),
    "C1": ("r7", (360, 70)), "C2": ("r7", (640, 70)),
    "C3": ("r8", (400, 690)), "C4": ("r8", (600, 690)),
    "C5": ("r3", (210, 150)),
}
# ligações entre routers (as do overlay entre nós de infraestrutura) e atraso de base em µs
ROUTER_LINKS = [
    ("r1", "r3", 0), ("r1", "r4", 0),               # S1-O1, S1-O2
    ("r2", "r5", 0), ("r2", "r6", 0),               # S2-O3, S2-O4
    ("r3", "r4", 0), ("r4", "r5", 0), ("r5", "r6", 0),
    ("r6", "r3", 10000),                            # O4-O1: 10 ms (ligação lenta de propósito)
    ("r7", "r3", 0), ("r7", "r6", 0),               # O5-O1, O5-O4
    ("r8", "r4", 0), ("r8", "r5", 0),               # O6-O2, O6-O3
    ("r9", "r7", 0), ("r9", "r8", 0),               # O7-O5, O7-O6 (suplente)
]
OVERLAY = [
    ("S1", "O1"), ("S1", "O2"), ("S2", "O3"), ("S2", "O4"),
    ("O1", "O2"), ("O2", "O3"), ("O3", "O4"), ("O4", "O1"),
    ("O5", "O1"), ("O5", "O4"), ("O6", "O2"), ("O6", "O3"),
    ("O7", "O5"), ("O7", "O6"),
    ("C1", "O5"), ("C2", "O5"), ("C3", "O6"), ("C4", "O6"), ("C5", "O1"),
]
# o que corre em cada nó: dois servidores com pelo menos dois fluxos distintos cada; o filme_c é publicado pelos dois
# (réplica) e o C1 e o C5 subscrevem-no sem servidor fixo (D38): cada um usa o servidor mais perto e passa para o
# outro se esse cair. Os clientes partilham ramos (C1 e C2 o filme_a, C3 e C4 o filme_d).
PLAN = {
    "S1": {"serve": True, "publish": ["filme_a", "filme_b", "filme_c"]},
    "S2": {"serve": True, "publish": ["filme_c", "filme_d"]},
    "O1": {}, "O2": {}, "O3": {}, "O4": {}, "O5": {}, "O6": {}, "O7": {"spare": True},
    "C1": {"subscribe": ["S1:filme_a", "filme_c"]},
    "C2": {"subscribe": ["S1:filme_a"]},
    "C3": {"subscribe": ["S1:filme_b", "S2:filme_d"]},
    "C4": {"subscribe": ["S2:filme_d"]},
    "C5": {"subscribe": ["filme_c"]},
}
ORDER = ["S1", "S2", "O1", "O2", "O3", "O4", "O5", "O6", "O7", "C1", "C2", "C3", "C4", "C5"]


def build():
    routers = sorted(ROUTERS, key=lambda r: int(r[1:]))
    nodes = [(r, "router", ROUTERS[r][1]) for r in routers] + [(h, "PC", HOSTS[h][1]) for h in ORDER]
    for name, _kind, (x, y) in nodes:
        assert 0 < x < CANVAS[0] and 0 < y < CANVAS[1], f"{name} fora do canvas {CANVAS}"
    ids = {name: i + 1 for i, (name, _k, _p) in enumerate(nodes)}
    ifaces = {name: [] for name, _k, _p in nodes}   # nome -> [(iface, ip, vizinho)]
    links = []

    def new_link(a, ip_a, b, ip_b, delay):
        ia, ib = f"eth{len(ifaces[a])}", f"eth{len(ifaces[b])}"
        ifaces[a].append((ia, ip_a, b))
        ifaces[b].append((ib, ip_b, a))
        links.append((a, ia, ip_a, b, ib, ip_b, len(links), delay))

    for a, b, delay in ROUTER_LINKS:
        n = len(links)
        new_link(a, f"10.0.{n}.1", b, f"10.0.{n}.2", delay)
    for host in ORDER:
        n = len(links)
        new_link(HOSTS[host][0], f"10.0.{n}.1", host, f"10.0.{n}.20", 0)

    root = ET.Element("scenario")
    ET.SubElement(root, "networks")
    devices = ET.SubElement(root, "devices")
    for name, kind, (x, y) in nodes:
        lat, lon = latlon(x, y)
        d = ET.SubElement(devices, "device", id=str(ids[name]), name=name, icon="", canvas="1", type=kind,
                          **{"class": "", "image": "", "compose": "", "compose_name": ""})
        ET.SubElement(d, "position", x=f"{x:.1f}", y=f"{y:.1f}", lat=str(lat), lon=str(lon), alt="2.0")
        services = ET.SubElement(d, "services")
        ET.SubElement(services, "service", name="DefaultRoute" if kind == "PC" else "IPForward")
    links_el = ET.SubElement(root, "links")
    mac = 0
    for a, ia, ip_a, b, ib, ip_b, sub, delay in links:
        link = ET.SubElement(links_el, "link", node1=str(ids[a]), node2=str(ids[b]))
        for tag, iface, ip in (("iface1", ia, ip_a), ("iface2", ib, ip_b)):
            ET.SubElement(link, tag, id=iface[3:], name=iface, mac=f"00:00:00:aa:05:{mac:02x}", ip4=ip,
                          ip4_mask="24", ip6=ip6(sub, int(ip.rsplit(".", 1)[1])), ip6_mask="64")
            mac += 1
        ET.SubElement(link, "options", delay=str(delay), bandwidth="0", loss="0.0", dup="0", jitter="0",
                      unidirectional="0", buffer="0")
    tail = ET.parse(os.path.join(HERE, "cenario3.xml")).getroot()
    for tag in ("session_origin", "session_options", "session_metadata"):
        root.append(tail.find(tag))
    return root, ifaces


def overlay_json(ifaces):
    neighbors = {}
    for a, b in OVERLAY:
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)
    assert sorted(ORDER) == sorted(neighbors), "OVERLAY e a lista de nós não coincidem"
    return {
        "_comment": ("Cenário 5 (dois servidores, anel com acessos): S1 publica filme_a, filme_b e filme_c; S2 publica "
                     "filme_c e filme_d (filme_c é uma réplica). Anel O1-O2-O3-O4 com os servidores em lados opostos, acessos O5 (C1, C2) e O6 (C3, C4), "
                     "C5 no O1 e O7 suplente entre O5 e O6. A ligação O1-O4 tem 10 ms no underlay. O id overlay é igual "
                     "ao nome do nó no CORE. Gerado por topologies/gerar_cenario5.py; ver docs/CENARIO5.md."),
        "nodes": {n: {"host": ifaces[n][0][1], "core": n, "neighbors": sorted(neighbors[n])} for n in ORDER},
    }


def iface_table(ifaces):
    """Linhas 'router ethN -> vizinho' para o guia (tc e ip link)."""
    return {r: [(i, ip, peer) for i, ip, peer in ifaces[r]] for r in sorted(ROUTERS, key=lambda r: int(r[1:]))}


SVG_STYLE = {
    "router": ("#e8eef7", "#4a6fa5"), "servidor": ("#fde2c8", "#c46a1b"), "cliente": ("#dff3e0", "#3a8d43"),
    "intermedio": ("#ece4f7", "#7a5aa6"), "suplente": ("#f2f2f2", "#888888"),
}


def _kind(name):
    if name in ROUTERS:
        return "router"
    if PLAN.get(name, {}).get("serve"):
        return "servidor"
    if PLAN.get(name, {}).get("subscribe"):
        return "cliente"
    return "suplente" if PLAN.get(name, {}).get("spare") else "intermedio"


def svg(title, nodes, edges, labels=None):
    """Desenho SVG simples nas posições do canvas do CORE: ``nodes`` = {nome: (x, y, texto extra)}, ``edges`` =
    [(a, b, rótulo, estilo)] com estilo "normal", "lento" ou "suplente"."""
    w, h = CANVAS
    total = h + 50                     # espaço para a legenda por baixo do canvas
    halo = 'stroke="#ffffff" stroke-width="4" paint-order="stroke"'
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {total}" width="{w}" height="{total}" '
           'font-family="Helvetica, Arial, sans-serif">',
           f'<rect width="{w}" height="{total}" fill="#ffffff"/>',
           f'<text x="16" y="30" font-size="20" font-weight="bold" fill="#222">{title}</text>']
    for a, b, label, style in edges:
        (x1, y1, _), (x2, y2, _) = nodes[a], nodes[b]
        attrs = {"normal": 'stroke="#555" stroke-width="2"',
                 "lento": 'stroke="#c0392b" stroke-width="4"',
                 "suplente": 'stroke="#888" stroke-width="2" stroke-dasharray="6 4"'}[style]
        out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" {attrs}/>')
        if label:
            mx, my = x1 + 0.3 * (x2 - x1), y1 + 0.3 * (y2 - y1)   # fora do meio, onde cruzam outras linhas
            out.append(f'<rect x="{mx - 30}" y="{my - 13}" width="60" height="22" rx="4" fill="#fff" stroke="#c0392b"/>')
            out.append(f'<text x="{mx}" y="{my + 3}" font-size="14" text-anchor="middle" fill="#c0392b">{label}</text>')
    for name, (x, y, extra) in nodes.items():
        fill, stroke = SVG_STYLE[_kind(name)]
        dash = ' stroke-dasharray="5 3"' if _kind(name) == "suplente" else ""
        if name in ROUTERS:
            out.append(f'<circle cx="{x}" cy="{y}" r="20" fill="{fill}" stroke="{stroke}" stroke-width="2"/>')
        else:
            out.append(f'<rect x="{x - 24}" y="{y - 15}" width="48" height="30" rx="5" fill="{fill}" '
                       f'stroke="{stroke}" stroke-width="2"{dash}/>')
        out.append(f'<text x="{x}" y="{y + 5}" font-size="15" font-weight="bold" text-anchor="middle" fill="#222">'
                   f'{name}</text>')
        if extra:
            out.append(f'<text x="{x}" y="{y + 32}" font-size="13" text-anchor="middle" fill="#333" {halo}>'
                       f'{extra}</text>')
    legend = [("servidor", "servidor"), ("intermedio", "nó overlay"), ("cliente", "cliente"),
              ("suplente", "suplente")] + ([("router", "router")] if any(n in ROUTERS for n in nodes) else [])
    for i, (kind, text) in enumerate(legend):
        fill, stroke = SVG_STYLE[kind]
        x = 16 + i * 120
        out.append(f'<rect x="{x}" y="{h + 14}" width="18" height="18" rx="3" fill="{fill}" stroke="{stroke}"/>')
        out.append(f'<text x="{x + 24}" y="{h + 28}" font-size="13" fill="#333">{text}</text>')
    for text in labels or []:
        out.append(text)
    out.append("</svg>\n")
    return "\n".join(out)


def draw():
    """Escreve docs/img/cenario5-underlay.svg e docs/img/cenario5-overlay.svg."""
    img = os.path.join(os.path.dirname(HERE), "docs", "img")
    os.makedirs(img, exist_ok=True)
    flows = {}
    for n, i in PLAN.items():
        flows[n] = ", ".join(f if i.get("publish") else (f.split(":")[1] if ":" in f else f + " (qualquer)")
                             for f in (i.get("publish") or i.get("subscribe") or []))
    hosts = {h: (x, y, "") for h, (_r, (x, y)) in HOSTS.items()}
    routers = {r: (x, y, "") for r, (_n, (x, y)) in ROUTERS.items()}
    under_edges = [(a, b, "10 ms" if d else "", "lento" if d else "normal") for a, b, d in ROUTER_LINKS]
    under_edges += [(h, r, "", "suplente" if h == "O7" else "normal") for h, (r, _p) in HOSTS.items()]
    with open(os.path.join(img, "cenario5-underlay.svg"), "w") as f:
        f.write(svg("Cenário 5 — underlay (CORE): routers FRR/OSPF e hosts", {**routers, **hosts}, under_edges))
    over_nodes = {h: (x, y, flows.get(h, "")) for h, (x, y, _e) in hosts.items()}
    over_edges = [(a, b, "10 ms" if {a, b} == {"O1", "O4"} else "",
                   "lento" if {a, b} == {"O1", "O4"} else "suplente" if "O7" in (a, b) else "normal")
                  for a, b in OVERLAY]
    with open(os.path.join(img, "cenario5-overlay.svg"), "w") as f:
        f.write(svg("Cenário 5 — overlay: ligações TCP entre os nós ott", over_nodes, over_edges))


def main():
    draw()
    root, ifaces = build()
    xml_path = os.path.join(HERE, "cenario5.xml")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="UTF-8", xml_declaration=True)
    aplicar_frr.main(xml_path)
    json_path = os.path.join(HERE, "cenario5.json")
    with open(json_path, "w") as f:
        json.dump(overlay_json(ifaces), f, indent=2, ensure_ascii=False)
        f.write("\n")
    plan_path = os.path.join(HERE, "cenario5.plano.json")
    with open(plan_path, "w") as f:
        json.dump({"_comment": "Plano do cenário 5 (gerado por gerar_cenario5.py): o que corre em cada nó.",
                   "xml": "cenario5.xml", "overlay": "cenario5.json", "bootstrap": "S1", "nodes": PLAN},
                  f, indent=2, ensure_ascii=False)
        f.write("\n")
    for r, rows in iface_table(ifaces).items():
        print(r, ROUTERS[r][0], ", ".join(f"{i}->{peer}" for i, _ip, peer in rows))
    print(f"escrito {xml_path}, {json_path} e {plan_path}")


if __name__ == "__main__":
    sys.exit(main())
