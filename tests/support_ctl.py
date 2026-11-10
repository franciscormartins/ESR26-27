"""Apoio aos testes do ottctl: um cenário pequeno (S - A - C) em pastas temporárias."""
import json
import os
import tempfile

PLAN = {"overlay": "mini.json", "bootstrap": "S",
        "nodes": {"S": {"serve": True, "publish": ["filme_a"]}, "A": {}, "C": {"subscribe": ["S:filme_a"]}}}
OVERLAY = {"nodes": {"S": {"host": "10.9.0.1", "neighbors": ["A"]}, "A": {"host": "10.9.0.2", "neighbors": ["C"]},
                     "C": {"host": "10.9.0.3", "neighbors": []}}}


def write_mini(directory=None):
    """Escreve mini.plano.json e mini.json; devolve o caminho do plano."""
    directory = directory or tempfile.mkdtemp(prefix="ottctl-test-")
    with open(os.path.join(directory, "mini.json"), "w") as f:
        json.dump(OVERLAY, f)
    path = os.path.join(directory, "mini.plano.json")
    with open(path, "w") as f:
        json.dump(PLAN, f)
    return path
