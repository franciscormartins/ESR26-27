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
| `src/ott/routing.py` | tabela de rotas: anúncios, escolha do montante (latência acumulada, D34), subscrições agregadas, `WITHDRAW`, tempo de vida | 2 |
| `src/ott/subscriptions.py` | subscrições locais: servidor fixo ou escolha do servidor mais perto com failover (D38) | 2 |
| `src/ott/routetest.py` | `ROUTE_TEST`: o servidor testa os percursos e cada nó regista o caminho acumulado (`ott --route-test S`, D39) | 3 |
| `src/ott/forwarding.py` | encaminhamento UDP pelos ramos ativos da árvore overlay (RPF estrito) | 2 |
| `src/ott/live.py` | vista compacta na consola (`ott --live`): bloco fixo com recebidos, origem (vizinho e saltos), destinos e trocas de cada fluxo | apoio aos testes |
| `src/ott/status.py` | estado de um nó em JSON (`ott --status-file`): vizinhos, tabela de rotas, fluxos e eventos relevantes | apoio aos testes |
| `src/ott/ctl/`, `bin/ottctl` | orquestração dos cenários 1–5 (lançar nós, falhas, atrasos, obstáculos nos troços, subscrições dos clientes), testes automáticos G/N (qualquer cenário) e A/B/C (cenário 4) e painel web com vídeo, caminhos (`ROUTE_TEST`), escolha de filmes e demonstração guiada; `make dashboard` (CORE); ver `docs/OTTCTL.md` e `docs/PAINEL.md` | apoio aos testes |
| `src/ott/streaming/` | pacotes de vídeo (cabeçalho origem/fluxo/salto + RTP), emissor MJPEG e recetor UDP | 2/4 |
| `videos/`, `tools/gerar_video_teste.py` | um vídeo MJPEG diferente por fluxo (`filme_b`, `filme_c`, `filme_d`; o `filme_a` é o filme real) para ver de que servidor vem cada janela do painel | apoio aos testes |
| `progex/` | código de apoio do livro (Kurose) e `movie.Mjpeg`, só como referência | 4 |
| `topologies/` | cenários 1–5 (`.json` do overlay, `.xml` do CORE e `.plano.json` do `ottctl`); `aplicar_frr.py` repõe a config do FRR; `gerar_cenario4.py` e `gerar_cenario5.py` geram os cenários 4 e 5 | – |
| `tests/` | testes automáticos (`python3 -m unittest discover -s tests -t .`) e `medicoes.py` (tempos em localhost, 10 repetições) | – |
| `docs/ESTADO.md` | **ponto da situação**: o que está feito, decisões, como testar, pendentes, perguntas ao docente | – |
| `docs/protocolo.md` | especificação das mensagens e interações | 1, 2 |
| `docs/ETAPA2.md` | plano, decisões e testes da Etapa 2 | 2 |
| `docs/AUDITORIA.md` | auditoria dos 10 requisitos mínimos e das Etapas 2 e 3, com melhorias priorizadas (2026-11-18) | 2, 3 |
| `docs/CENARIO4.md`, `docs/CENARIO5.md` | cenários 4 (teia) e 5 (dois servidores com dois fluxos distintos, o que cumpre o requisito mínimo): nós, IPs e ensaios | 2 |
| `docs/testes-core.md` | comandos por nó e testes a fazer nos cenários 1–3 no CORE (local: está no `.gitignore`, **não está no repositório**) | – |

Etapa 3 (teste dos percursos): `ott --publish-flow filme_a --route-test 5` faz o servidor enviar um `ROUTE_TEST` por
fluxo a cada 5 s pelos ramos ativos; cada nó regista no log, por exemplo,
`ROUTE_TEST S1:filme_a #4 caminho S1>O2>C1 (2 saltos): entregue ao cliente local` (ver `docs/protocolo.md` §3.8).

## Como as outras etapas se ligam ao nó

```python
node.register_handler("ANNOUNCE", fn)      # fn(node, neighbor_id, msg)
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
./bin/ott --serve topologies/cenario1.json -i S1 \
    --publish-flow filme_a --stats 2                  # anunciar e emitir um fluxo
./bin/ott -i C1 -b 10.0.0.10 --subscribe S1:filme_a --stats 2  # receber o fluxo
```

Opções úteis: `--min-neighbors X` (pede substitutos se ficar com menos de X vizinhos),
`--stats S` (vizinhos, RTT e estatísticas UDP), `--live [S]` (bloco fixo redesenhado a cada S s, 2 por omissão: por fluxo, recebidos, ritmo, perdidos, vizinho de onde vem com saltos e latência, destinos e última troca), `--log-dir PASTA` (grava o log em `PASTA/ott-<ID>.log`), `--log-traffic` (ao sair um vizinho, pacotes recebidos/perdidos por fluxo e de/para que vizinhos, em vez dos logs de rotas), `--publish-flow ID[=VIDEO]` (pode repetir-se; cada fluxo pode ter o seu ficheiro MJPEG, p.ex. `filme_b=/tmp/b.Mjpeg`; `tools/gerar_video_teste.py` gera um segundo vídeo de teste) e `--gui` (janela com o vídeo do primeiro `--subscribe`),
`--subscribe SERVIDOR:FLUXO` (pode repetir-se; só `--subscribe FLUXO` escolhe o servidor mais perto entre os que
publicam esse fluxo e passa para outro se esse deixar de ter caminho, D38), `--video FICHEIRO`, `--fps N`,
`--announce-interval S` (5 s; usar o mesmo em todos os nós) e `--route-ttl S` (esquecer
caminhos sem anúncios há S segundos; por omissão 6 × o intervalo).
Todos os nós do caminho overlay têm de executar `ott`; os dados usam UDP na porta 6000,
enquanto o controlo das ligações e subscrições usa TCP na porta 5000. `Ctrl-C` desativa as
subscrições locais antes de fechar o nó.

Os ids dos nós têm de existir no ficheiro de topologia (estratégia 2).

## Streaming isolado (sem overlay)

Servidor e cliente de vídeo diretamente por UDP, para testar o plano de dados.
O cliente pode arrancar antes ou depois do servidor; o vídeo repete em ciclo
(`--once` para parar no fim). Para enviar e receber através do overlay, usa as opções
`--publish-flow` e `--subscribe` de `ott`, não estes comandos isolados.

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
