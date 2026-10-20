# ESR TP2 – Serviço Over the Top para entrega de multimédia

Overlay aplicacional em Python 3 (só biblioteca standard). Enunciado em `docs/enunciado.pdf`,
relatório em `docs/relatorio/`.

## Estrutura

| Caminho | Conteúdo | Etapa |
|---|---|---|
| `src/ott/protocol.py` | formato das mensagens (JSON com prefixo de tamanho, TCP) e tipos | todas |
| `src/ott/node.py` | `OverlayNode`: ligações aos vizinhos, HELLO, keepalive, reconexão, handlers | 1 |
| `src/ott/bootstrapper.py` | serviço que devolve os vizinhos de cada nó a partir de um ficheiro | 1 |
| `src/ott/cli.py` | comando `ott` | 1 |
| `src/ott/routing.py` | tabela de rotas por fluxo, anúncios, ativação | 2 (por fazer) |
| `src/ott/streaming/` | servidor, cliente e pacotes de vídeo (UDP) | 4 (por fazer) |
| `topologies/` | cenários 1–3 (ajustar `host` aos IPs do CORE) | – |
| `tests/` | testes automáticos | – |

Etapa 3 (PING de teste de caminhos): mensagem `ROUTE_TEST`, a tratar com um handler.

## Como as outras etapas se ligam ao nó

```python
node.register_handler("ANNOUNCE", fn)      # fn(node, neighbor, msg)
node.send(neighbor_id, make_msg(...))      # unicast a um vizinho
node.broadcast(msg, exclude={origem})      # inundação controlada
node.neighbor_ids()                        # vizinhos ativos
```

## Utilização

```sh
./bin/ott --serve topologies/cenario1.json -i S1      # servidor + bootstrapper
./bin/ott -i O2 -b 10.0.0.10                          # nó que se regista no bootstrapper
./bin/ott -i C1 10.0.0.11 10.0.0.12                   # estratégia 1: vizinhos manuais
```

Os ids dos nós têm de existir no ficheiro de topologia (estratégia 2).

## Testes

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```
