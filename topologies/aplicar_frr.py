#!/usr/bin/env python3
"""Reaplica a configuração do FRR aos routers de um cenário CORE (XML).

Quando o cenário é redesenhado no CORE, os routers voltam a ter os serviços do Quagga
(`zebra`, `OSPFv2`), que não funcionam nesta máquina (sem `vtysh`), e perdem-se as
configurações próprias. Este programa, a partir das interfaces e IPs que estão no XML:

  - põe nos routers os serviços IPForward, FRRzebra e FRROSPFv2;
  - gera para cada router o `frr.conf` (OSPF ponto-a-ponto, hello 2 s / dead 6 s, uma
    linha `network` por sub-rede ligada) e o `frrboot.sh` (modelo em frrboot.sh.tmpl).

Uso:  python3 topologies/aplicar_frr.py topologies/cenario3.xml
O ficheiro é reescrito no próprio sítio; correr outra vez dá o mesmo resultado.
"""
import ipaddress
import os
import sys
import xml.etree.ElementTree as ET

SERVICES = ("IPForward", "FRRzebra", "FRROSPFv2")
IFACE = """interface {name}
    ip ospf network point-to-point
    ip ospf hello-interval 2
    ip ospf dead-interval 6
    ip ospf retransmit-interval 5
!
"""


def router_ifaces(root):
    """{id do router: [(nome da interface, ip, máscara)]} lido das ligações do XML."""
    routers = {d.get("id") for d in root.find("devices") if d.get("type") == "router"}
    found = {r: [] for r in routers}
    for link in root.find("links"):
        for node_attr, iface_tag in (("node1", "iface1"), ("node2", "iface2")):
            node, iface = link.get(node_attr), link.find(iface_tag)
            if node in routers and iface is not None and iface.get("ip4"):
                found[node].append((iface.get("name"), iface.get("ip4"), iface.get("ip4_mask")))
    return {r: sorted(v, key=lambda i: int(i[0].removeprefix("eth"))) for r, v in found.items()}


def frr_conf(ifaces):
    parts = [IFACE.format(name=name) for name, _, _ in ifaces]
    parts.append("router ospf\n  ospf router-id %s\n" % ifaces[0][1])
    for _, ip, mask in ifaces:
        net = ipaddress.ip_interface(f"{ip}/{mask}").network
        parts.append(f"  network {net} area 0\n")
    parts.append("!\n")
    return "".join(parts)


def main(path):
    here = os.path.dirname(os.path.abspath(__file__))
    boot = open(os.path.join(here, "frrboot.sh.tmpl")).read()
    tree = ET.parse(path)
    root = tree.getroot()
    ifaces = router_ifaces(root)
    for dev in root.find("devices"):
        if dev.get("type") == "router":
            services = dev.find("services")
            for s in list(services):
                services.remove(s)
            for name in SERVICES:
                ET.SubElement(services, "service", name=name)
    old = root.find("service_configurations")
    if old is not None:
        root.remove(old)
    configs = ET.SubElement(root, "service_configurations")
    for dev_id in sorted(ifaces, key=int):
        node = ET.SubElement(configs, "service", name="FRRzebra", node=dev_id)
        templates = ET.SubElement(node, "templates")
        ET.SubElement(templates, "template", name="/usr/local/etc/frr/frr.conf").text = \
            "\n" + frr_conf(ifaces[dev_id])
        ET.SubElement(templates, "template", name="frrboot.sh").text = boot
    ET.indent(tree, space="  ")
    tree.write(path, encoding="UTF-8", xml_declaration=True)
    print(f"{len(ifaces)} routers configurados em {path}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
