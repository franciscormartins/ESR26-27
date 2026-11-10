#!/usr/bin/env python3
"""Gera o cenário 4 (dois servidores, rede em teia): topologies/cenario4.xml (CORE) e topologies/cenario4.json (overlay).

Underlay em teia (13 routers, OSPF/FRR): um router central (r1), um anel interior (r2..r7), um anel exterior (r8..r13),
6 raios (centro -> anel interior) e 6 ligações radiais (anel interior -> anel exterior). Os routers do mesmo ângulo
(r2/r8, r3/r9, ...) estão ligados. Cada host liga-se por uma sub-rede /24 própria ao seu router (router .1, host .20).

Overlay só em hosts (como o enunciado aconselha), com o id igual ao nome do nó no CORE. Os servidores S1 e S2 estão
ligados entre si, por isso cada um é caminho de passagem dos fluxos do outro: se um cai, as rotas do outro são redirecionadas.

    python3 topologies/gerar_cenario4.py          # reescreve o XML e o JSON (e aplica o FRR aos routers)

Para mudar a topologia, edita as tabelas ANGLES, HOSTS, OVERLAY e DELAY_US e volta a correr o programa.
"""
import json
import math
import os
import sys
import xml.etree.ElementTree as ET

import aplicar_frr

HERE = os.path.dirname(os.path.abspath(__file__))
CANVAS = (1000, 750)           # dimensões do canvas do CORE: tudo tem de caber aqui
CENTER = (500.0, 375.0)

# routers: r1 centro; r2..r7 anel interior; r8..r13 anel exterior (mesmo ângulo que o do interior com índice - 6)
ANGLES = [0, 60, 120, 180, 240, 300]
INNER = [f"r{i}" for i in range(2, 8)]
OUTER = [f"r{i}" for i in range(8, 14)]

# atraso base (µs) de cada tipo de ligação do underlay. Está a zero de propósito: os atrasos, o jitter e as perdas criam-se
# nos testes com `tc netem`, ligação a ligação. Se puseres valores aqui, repara que o OSPF reparte o tráfego por caminhos de
# custo igual, e o RTT passa a depender do caminho que cada ligação TCP apanha.
DELAY_US = {"spoke": 0, "inner": 0, "radial": 0, "outer": 0, "host": 0}

# router -> hosts ligados a ele (por ordem). Os hosts L1 e L2 não fazem parte do overlay (testes e sondas).
HOSTS = {
    "r8": ["S2"], "r11": ["S1"],                      # servidores em lados opostos da teia
    "r4": ["O1"], "r6": ["O2"], "r3": ["O3"], "r7": ["O4"],   # intermédios no anel interior
    "r10": ["C1"], "r9": ["C2"], "r5": ["C3"], "r2": ["C4"],  # clientes ao lado dos intermédios a que se ligam
    "r1": ["O5"],                                     # nó suplente no centro
    "r12": ["L1"], "r13": ["L2"],
}

# arestas do overlay. S1 e S2 estão ligados: cada um é caminho de passagem dos fluxos do outro.
# C1 e C2 ficam junto de um servidor; C3 e C4 têm dois vizinhos intermédios (empates de saltos).
# O5 é o nó suplente: só entra quando o arrancas.
OVERLAY = [
    ("S1", "S2"), ("S1", "O1"), ("S1", "O2"), ("S2", "O3"), ("S2", "O4"),
    ("O1", "O2"), ("O3", "O4"), ("O1", "O3"), ("O2", "O4"),
    ("C1", "S1"), ("C1", "O1"), ("C2", "S2"), ("C2", "O3"),
    ("C3", "O1"), ("C3", "O2"), ("C4", "O3"), ("C4", "O4"),
    ("O5", "O1"), ("O5", "O4"),
]

# calibração aproximada ecrã -> lat/lon (ponto tirado de cenario3.xml)
REF = (451.0, 572.0, 47.5739668688703, -122.1262471158529)


def latlon(x, y):
    return (REF[2] - (y - REF[1]) * 9.0905e-6, REF[3] + (x - REF[0]) * 1.34753e-5)


def polar(radius, degrees):
    a = math.radians(degrees)
    return (CENTER[0] + radius * math.cos(a), CENTER[1] - radius * math.sin(a))


def router_links():
    links = [("r1", r, "spoke") for r in INNER]
    links += [(INNER[i], INNER[(i + 1) % 6], "inner") for i in range(6)]
    links += [(OUTER[i], OUTER[(i + 1) % 6], "outer") for i in range(6)]
    links += [(INNER[i], OUTER[i], "radial") for i in range(6)]
    return links


def positions():
    pos = {"r1": CENTER}
    for i, ang in enumerate(ANGLES):
        pos[INNER[i]] = polar(120, ang)
        pos[OUTER[i]] = polar(240, ang)
    for router, hosts in HOSTS.items():
        if router == "r1":
            for h in hosts:
                pos[h] = polar(70, 270)                       # no intervalo entre dois raios
        elif router in INNER:
            ang = ANGLES[INNER.index(router)]
            for h in hosts:
                pos[h] = polar(170, ang + 20)                 # entre os dois anéis, ao lado da ligação radial
        else:
            ang = ANGLES[OUTER.index(router)]
            offsets = [0] if len(hosts) == 1 else [-7, 7]
            for h, off in zip(hosts, offsets):
                pos[h] = polar(318, ang + off)
    return pos


def ip6(subnet, host):
    return f"2001::{host:x}" if subnet == 0 else f"2001:0:0:{subnet:x}::{host:x}"


def build():
    pos = positions()
    routers = ["r1"] + INNER + OUTER
    hosts = [h for r in routers for h in HOSTS.get(r, [])]
    nodes = [(n, "router") for n in routers] + [(n, "PC") for n in hosts]
    for n, _kind in nodes:
        x, y = pos[n]
        assert 0 < x < CANVAS[0] and 0 < y < CANVAS[1], f"{n} fora do canvas {CANVAS}: ({x:.0f}, {y:.0f})"
    ids = {n: i + 1 for i, (n, _k) in enumerate(nodes)}

    ifaces = {n: [] for n, _k in nodes}   # nome -> [(nome da iface, ip, vizinho)]
    links = []                            # (nó1, iface1, ip1, nó2, iface2, ip2, sub-rede, atraso µs)

    def new_link(a, ip_a, b, ip_b, kind):
        ia, ib = f"eth{len(ifaces[a])}", f"eth{len(ifaces[b])}"
        ifaces[a].append((ia, ip_a, b))
        ifaces[b].append((ib, ip_b, a))
        links.append((a, ia, ip_a, b, ib, ip_b, len(links), DELAY_US[kind]))

    for a, b, kind in router_links():
        n = len(links)
        new_link(a, f"10.0.{n}.1", b, f"10.0.{n}.2", kind)
    for router in routers:
        for host in HOSTS.get(router, []):
            n = len(links)
            new_link(router, f"10.0.{n}.1", host, f"10.0.{n}.20", "host")

    root = ET.Element("scenario")
    ET.SubElement(root, "networks")
    devices = ET.SubElement(root, "devices")
    for name, kind in nodes:
        x, y = pos[name]
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
            ET.SubElement(link, tag, id=iface[3:], name=iface, mac=f"00:00:00:aa:00:{mac:02x}", ip4=ip,
                          ip4_mask="24", ip6=ip6(sub, int(ip.rsplit(".", 1)[1])), ip6_mask="64")
            mac += 1
        ET.SubElement(link, "options", delay=str(delay), bandwidth="0", loss="0.0", dup="0", jitter="0",
                      unidirectional="0", buffer="0")
    # cauda (origem, opções e metadados da sessão) copiada de um cenário que o CORE já abriu
    tail = ET.parse(os.path.join(HERE, "cenario3.xml")).getroot()
    for tag in ("session_origin", "session_options", "session_metadata"):
        root.append(tail.find(tag))
    return root, ifaces, links


def overlay_json(ifaces):
    neighbors = {}
    for a, b in OVERLAY:
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)
    order = ["S1", "S2"] + [f"O{i}" for i in range(1, 6)] + [f"C{i}" for i in range(1, 5)]
    assert sorted(order) == sorted(neighbors), "OVERLAY e a lista de nós não coincidem"
    return {
        "_comment": ("Cenário 4 (dois servidores, rede em teia): S1 publica filme_a e filme_b; S2 publica filme_c e filme_b "
                     "(filme_b é uma réplica). S1 e S2 estão ligados entre si: cada um é caminho de passagem dos fluxos do outro. "
                     "O id overlay é igual ao nome do nó no CORE ('core'); 'host' = IP do host. O5 é um nó suplente (arrancar só "
                     "nos testes de entrada de nós). Gerado por topologies/gerar_cenario4.py."),
        "nodes": {n: {"host": ifaces[n][0][1], "core": n, "neighbors": sorted(neighbors[n])} for n in order},
    }


def main():
    root, ifaces, _links = build()
    xml_path = os.path.join(HERE, "cenario4.xml")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(xml_path, encoding="UTF-8", xml_declaration=True)
    aplicar_frr.main(xml_path)
    json_path = os.path.join(HERE, "cenario4.json")
    with open(json_path, "w") as f:
        json.dump(overlay_json(ifaces), f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"escrito {xml_path} e {json_path}")


if __name__ == "__main__":
    sys.exit(main())
