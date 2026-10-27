# ESR TP2 – Serviço Over the Top para entrega de multimédia

Overlay aplicacional em Python 3 (só biblioteca standard). Enunciado em `docs/enunciado.pdf`,
relatório em `docs/relatorio/`.

## Estrutura

| Caminho | Conteúdo | Etapa |
|---|---|---|
| `src/ott/protocol.py` | formato das mensagens (JSON com prefixo de tamanho, TCP) e tipos | todas |
| `src/ott/node.py` | `OverlayNode`: ligações aos vizinhos, HELLO, keepalive, RTT, eventos up/down, substitutos, handlers | 1 |
| `src/ott/bootstrapper.py` | serviço que devolve os vizinhos (e substitutos) de cada nó a partir de um ficheiro | 1 |
| `src/ott/cli.py` | comando `ott` | 1 |
| `src/ott/routing.py` | tabela de rotas por fluxo, anúncios, ativação | 2 (por fazer) |
| `src/ott/streaming/` | formato dos pacotes de vídeo (cabeçalho origem/fluxo + RTP), servidor e cliente UDP; ainda sem ligação ao overlay | 4 |
| `progex/` | código de apoio do livro (Kurose) e `movie.Mjpeg`, só como referência | 4 |
| `topologies/` | cenários 1–3 (ajustar `host` aos IPs do CORE) | – |
| `tests/` | testes automáticos | – |
| `docs/ESTADO.md` | **ponto da situação**: o que está feito, decisões, como testar, pendentes, perguntas ao docente | – |
| `docs/protocolo.md` | especificação das mensagens e interações | 1 |
| `docs/testes-core.md` | comandos por nó e testes a fazer nos cenários 1–3 no CORE | – |

Etapa 3 (PING de teste de caminhos): mensagem `ROUTE_TEST`, a tratar com um handler.

## Como as outras etapas se ligam ao nó

```python
node.register_handler("ANNOUNCE", fn)      # fn(node, neighbor, msg)
node.send(neighbor_id, make_msg(...))      # unicast a um vizinho
node.broadcast(msg, exclude={origem})      # inundação controlada
node.neighbor_ids()                        # vizinhos ativos
node.on_neighbor_up(fn)                    # fn(node, id)
node.on_neighbor_down(fn)                  # fn(node, id)
node.rtt(id)                               # RTT em segundos (média móvel) ou None
```

## Utilização

```sh
./bin/ott --serve topologies/cenario1.json -i S1      # servidor + bootstrapper
./bin/ott -i O2 -b 10.0.0.10                          # nó que se regista no bootstrapper
./bin/ott -i C1 10.0.0.11 10.0.0.12                   # estratégia 1: vizinhos manuais
```

Opções úteis: `--min-neighbors X` (pede substitutos se ficar com menos de X vizinhos),
`--stats S` (escreve vizinhos e RTT a cada S segundos).

Os ids dos nós têm de existir no ficheiro de topologia (estratégia 2).

## Streaming isolado (sem overlay)

Servidor e cliente de vídeo diretamente por UDP, para testar o plano de dados.
O cliente pode arrancar antes ou depois do servidor; o vídeo repete em ciclo
(`--once` para parar no fim).

```sh
PYTHONPATH=src python3 -m ott.streaming.client --origin S1 --flow filme_a --port 6000   # janela
PYTHONPATH=src python3 -m ott.streaming.server --origin S1 --flow filme_a \
    --video progex/movie.Mjpeg --to 127.0.0.1:6000
```

`--no-gui` no cliente mostra só as estatísticas (recebidos, perdidos, descartados, jitter).
Formato dos pacotes: ver `src/ott/streaming/rtp.py`.

## Testes

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```
