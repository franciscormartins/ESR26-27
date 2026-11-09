# ESTADO DO PROJETO – Elemento 1 (Overlay e protocolo de controlo)

> Ficheiro vivo: é atualizado sempre que algo é feito. Serve para saber o ponto da situação,
> relembrar as decisões e preparar as conversas com os docentes.
> **Última atualização: 2026-11-09.**

## 1. Resumo em 30 segundos

- Overlay aplicacional em **Python 3 (só biblioteca standard)**: cada nó é o programa `ott`, liga-se
  por **TCP** aos vizinhos, mantém-nos com PING/PONG, deteta quedas, mede o RTT de cada vizinho e,
  opcionalmente, pede vizinhos substitutos ao bootstrapper.
- O overlay constrói-se de duas formas: **estratégia 2** (`ott -b <bootstrapper>`: o servidor lê um
  ficheiro de topologia e devolve os vizinhos) e **estratégia 1** (`ott viz1 viz2`: vizinhos à mão).
- **Estado:** a Etapa 2 origina anúncios periódicos, aprende e propaga rotas por fluxo,
  encaminha ativações/desativações e já tem encaminhamento RTP/JPEG por UDP pelos ramos ativos.
  Até 2026-11-09 isto só funcionava nos testes com nós falsos: os handlers recebiam o objeto
  `Neighbor` em vez do ID (corrigido no commit `e83e37b`). Com nós reais em localhost, o fluxo
  ponta a ponta está agora validado (`tests/test_integration.py`). Falta o CORE e as melhorias
  decididas na revisão de 2026-11-09 (ver `docs/ETAPA2.md`, "Revisão de 2026-11-09").
  **Elemento 1 concluído:** todas as funcionalidades estão implementadas e foram ensaiadas no CORE (cenário 2 e
  cenário 3 com 11 nós) e medidas com 10 repetições (secção 7). Os ensaios acharam e corrigiram 4 defeitos
  (D17–D19 e o contador de défice); ver secção 7 e o registo de alterações.
- Falta ainda, fora do E1: validação ponta a ponta da Etapa 2 no CORE e a interface de streaming/apresentação da Etapa 4.
  Do E1 só dependem de ti a confirmação da atribuição overlay→CORE do cenário 3 e a revisão do texto do relatório.

## 2. O que está feito (elemento 1)

| Tarefa do guião | Estado | Onde |
|---|---|---|
| Biblioteca de mensagens com validação de campos | ✅ feito | `src/ott/protocol.py`, `tests/test_protocol.py` |
| Documento de especificação das mensagens | ✅ feito (a rever pelo grupo) | `docs/protocolo.md` |
| Aplicação `ott`: servidor TCP + ligações de saída, full-duplex, thread por ligação, HELLO | ✅ feito, ensaiado no CORE | `src/ott/node.py` |
| Overlay com `ott -b <bootstrapper>` e ficheiro de topologia escrito à mão | ✅ feito, ensaiado no CORE (cen. 1 e 2) | `bootstrapper.py`, `topologies/*.json` |
| Estratégia 1 (`ott viz1 viz2`) | ✅ feito, ensaiado no CORE | `cli.py` |
| KEEPALIVE periódico e deteção de vizinho caído (fecho da ligação ou timeout) | ✅ feito; ensaiado no CORE (fecho do TCP, link em baixo) e em localhost (`SIGSTOP`) | `node.py` |
| Pedido de substituto ao bootstrapper | ✅ feito (opção `--min-neighbors`); ensaiado no CORE (cenário 3) | `node.py`, `bootstrapper.py` |
| RTT por vizinho entregue ao E2 | ✅ feito (`node.rtt(id)`, `node.rtts()`); ensaiado no CORE, também com atraso (`tc netem`) | `node.py` |
| Eventos `vizinho_up/down` para o E2 | ✅ feito (`on_neighbor_up/down`) | `node.py` |
| Testes de saída/entrada de nós com anotação de resultados | ✅ feito: cenários 2 e 3 no CORE e localhost, 10 repetições (secção 7) | `tests/medicoes.py`, `docs/testes-core.md` |
| Overlay completo no cenário 3 (11 nós) | ✅ ensaiado no CORE (atribuição overlay→CORE ainda por confirmar pelo grupo) | `topologies/cenario3.*` |
| Relatório: secção do E1 | ✅ escrita (`e1_overlay.tex`, incluída em `main.tex`); rever com o grupo | `docs/relatorio/` |

Também feito (apoio ao grupo): topologias CORE dos cenários 1, 2 e 3 (`topologies/*.xml`), com o
underlay do cenário 3 a funcionar (routers FRR, OSPF; topologia redesenhada em 2026-11-03, validada); comandos de arranque por nó (`docs/testes-core.md`); `topologies/aplicar_frr.py` repõe a config do FRR num XML redesenhado.

## 3. Decisões tomadas e porquê

| # | Decisão | Porquê / alternativa |
|---|---|---|
| D1 | Python 3, só biblioteca standard | O código de apoio do livro está em Python; sem dependências para correr em qualquer nó do CORE. |
| D2 | Controlo em TCP, vídeo em UDP (E3) | TCP dá fiabilidade ao registo/vizinhos e a queda da ligação deteta a saída de um nó. |
| D3 | Mensagens = JSON com **prefixo de 4 bytes de tamanho** (não `\n`) | Delimitação robusta e rejeição de mensagens gigantes antes de as ler. **Desvio ao guião** (que sugeria uma linha JSON): levar ao grupo/docente. |
| D4 | Envelope fixo `{type, src, data}` com campos obrigatórios por tipo, validados ao enviar e ao receber | Detetar erros cedo; um nó com bug não deixa o vizinho num estado incoerente (ligação inválida é fechada). |
| D5 | Uma só ligação TCP por par de nós; se ambos ligam ao mesmo tempo, **fica a do nó de menor id** | Evita ligações duplicadas e eventos `up` repetidos; ambos aplicam a mesma regra. |
| D6 | Topologia **simétrica**: uma aresta no ficheiro vale nos dois sentidos e os dois lados tentam ligar-se | Funciona com ligações diretas no CORE, onde só um lado alcança o outro (sem rotas), e dá resiliência à ordem de arranque. |
| D7 | O servidor é o bootstrapper (mesmo executável: `--serve`) | Pedido no enunciado ("usar o servidor como bootstrapper permanente"). |
| D8 | Vivacidade: PING a cada 2 s, morto após 6 s sem nada; fecho da ligação deteta-se logo | Equilíbrio entre rapidez e falsos positivos; valores configuráveis. |
| D9 | PING/PONG levam `ts` do emissor e o PONG devolve-o; RTT = média móvel (α = 0,3) | Mede RTT sem relógios sincronizados; a média suaviza o ruído para a métrica do E2. |
| D10 | Substitutos só com `--min-neighbors X` (por omissão desligado) | Os clientes são folhas (1 vizinho) e não devem ser forçados a ter X. O bootstrapper propõe nós já registados, perto dos vizinhos atuais, e esquece os que não ligam em 10 s. |
| D11 | Uma só tentativa de ligação em curso por alvo | Evitava acumular threads quando um vizinho está lento ou inalcançável. |
| D12 | Um só endereço (`host`) por nó no ficheiro de topologia | Simples; basta um dos lados alcançar o outro. **Limitação** para nós com várias interfaces (ver 8). |
| D13 | Cenário 3: underlay com 10 routers FRR/OSPF (`r1`–`r10`), 5 switches e `S1` + `C1`–`C9`; nós overlay também nos routers | A Figura 1 põe nós overlay em routers. Atribuição overlay→CORE **por confirmar**, escolhida pelas etiquetas da figura (O1=S1, O4=C1, O8=C2, O9=C3, O10=C4, O11=C5) e de modo a que as 6 arestas overlay entre routers (O2–O3, O2–O7, O3–O5, O5–O6, O5–O7, O6–O7) sejam links diretos do underlay: O2=r1, O3=r3, O5=r4, O6=r5, O7=r2. Ver `docs/testes-core.md`. |
| D14 | Routers com FRR (não Quagga) e `frr.conf`/`frrboot.sh` próprios no XML, **reaplicáveis com `topologies/aplicar_frr.py`**; o `frrboot.sh` manda o stdio dos daemons para `/dev/null` | O Quagga instalado não tem `vtysh`, e o CORE 9.2.1 gera config que o FRR 10 rejeita (ver `docs/testes-core.md`). Redesenhar o cenário na GUI repõe os serviços do Quagga e apaga a config (aconteceu em 2026-11-03), daí o programa. Sem o redirecionamento, o `zebra`/`ospfd` herdavam os pipes do comando de arranque e o CORE ficava preso em `INSTANTIATION` (os hosts nunca recebiam rota por defeito). |
| D15 | Decidido implementar a Estratégia 1 do enunciado. Estado de rota indexado por `(servidor, fluxo)`, com vizinho de montante, destinos a jusante, subscritores, custo em saltos, época e anúncios vistos. `fluxo` identifica o conteúdo/stream multimédia (ex.: `filme_a`). | Anúncios do servidor constroem as rotas; pedidos dos clientes abrem e fecham ramos partilhados. Roadmap em `docs/ETAPA2.md`; modelo em `docs/protocolo.md`. |
| D16 | Protocolo E2 definido: `ANNOUNCE(server_id, flow, epoch, metric)`; `ACTIVATE`/`DEACTIVATE(server_id, flow, subscriber_id)`. Custo em saltos, época crescente por fluxo, IDs overlay para servidor/cliente e nome estável para fluxo. | Suporta vários fluxos e servidores; handlers dos três tipos implementados. |
| D17 | Quem fecha uma ligação duplicada envia `BYE` com `reason: "duplicate"`; quem o recebe retira a ligação **sem `down`** e espera até 3 s pela vencedora (`DUPLICATE_GRACE`), que é adotada sem novo `up` | Num arranque simultâneo o outro lado fechava, depois de nós a darmos como `up`, a ligação perdedora: o E2 via `down`+`up` falsos (apagaria rotas à toa). Alternativas rejeitadas: só o nó de menor id liga (quebra o D6) ou adiar sempre todos os `down` (esconde quedas reais). |
| D18 | `TCP_NODELAY` em todas as ligações entre vizinhos | O algoritmo de Nagle retinha as mensagens pequenas (PING/PONG cruzados) à espera do ACK: RTT de 37 ms inicial nos clientes e 60–80 ms com atraso de 20 ms/lado (esperado 41 ms). Com NODELAY: 41,1–41,6 ms. O RTT é a métrica do E2, por isso tem de ser fiável. |
| D19 | Ligar a um substituto logo que é proposto, e repor o contador de défice ao atingir X vizinhos | Poupava-se o ciclo de 2 s; e o contador antigo podia contar défice já resolvido (artefacto visto nas medições). |
| D20 | Os handlers registados com `register_handler` recebem o **ID** do vizinho (`fn(node, neighbor_id, msg)`) | Era o contrato documentado e o que `routing.py` usa; o `_dispatch` passava o objeto `Neighbor` e partia a Etapa 2 com nós reais. |
| D21 | Métrica: saltos; em empate, RTT acumulado com histerese (> 20 % e > 1 ms), depois o ID do vizinho. Escolha a partir de uma tabela de candidatos por vizinho (**por implementar**) | Cumpre "métricas da rede" sem oscilações; a regra atual "primeira cópia da época ganha" pode alternar entre caminhos de custo igual. |
| D22 | Falhas: `WITHDRAW` explícito com reparação rápida (condição de viabilidade), rota sem caminho aceita qualquer época, tempo de vida de 6 × o intervalo como rede de segurança (**por implementar**) | Um servidor reiniciado (época 1) deixa de ficar bloqueado; um servidor morto deixa de manter rotas ativas para sempre. |
| D23 | Plano de dados com RPF estrito: só se aceitam datagramas do montante (**por implementar**) | Elimina duplicados e ciclos de dados; aceita-se uma falha curta ao trocar de montante. |
| D24 | Subscrições agregadas por ramo: `ACTIVATE` na transição 0→1, `DEACTIVATE` na 1→0 (**por implementar**) | Fiel à tabela do enunciado ("Destinos: O7, O3") e escala com o número de clientes; o servidor deixa de conhecer cada cliente. |
| D25 | Dois servidores com fluxos distintos; chave `(server_id, flow)` mantém-se | Requisito mínimo do enunciado (pág. 3); falta acrescentar `S2` às topologias. |

## 4. Arquitetura (resumo)

```
src/ott/protocol.py     formato, tipos e validação das mensagens
src/ott/node.py         OverlayNode: ligações, HELLO, PING/PONG, RTT, eventos, substitutos
src/ott/bootstrapper.py BootstrapService: vizinhos e substitutos a partir do ficheiro JSON
src/ott/cli.py          comando `ott` (-b, --serve, publish/subscribe, --stats ...)
src/ott/routing.py      modelo de rotas, anúncios, subscrições e destinos ativos
src/ott/forwarding.py   encaminhamento dos pacotes RTP/JPEG por UDP
src/ott/streaming/      empacotamento RTP, emissor MJPEG e estatísticas do recetor
topologies/             cenarios 1-3: .json (overlay) e .xml (CORE)
docs/protocolo.md       especificação das mensagens e interações
docs/ETAPA2.md          roadmap e checklist da implementação das rotas
docs/testes-core.md     comandos por nó e testes a fazer no CORE
```

Cada nó tem threads para: aceitar ligações, uma por vizinho (leitura) e uma de manutenção
(ligar em falta, PING, timeouts, substitutos).

## 5. Interface para o Elemento 2 e 3

```python
node.register_handler("ANNOUNCE", fn)   # routing_table.attach regista os três handlers E2
node.send(id, make_msg(...)) ; node.broadcast(msg, exclude={origem})
node.neighbor_ids()
node.on_neighbor_up(fn) ; node.on_neighbor_down(fn)   # fn(node, id)
node.rtt(id) ; node.rtts()                            # segundos (média móvel) ou None
```

Definidos e validados os campos de `ANNOUNCE`, `ACTIVATE` e `DEACTIVATE` (ver
`docs/protocolo.md` §7.1). `RoutingTable.attach(node)` regista os três handlers.
Continuam pendentes o formato de `ROUTE_TEST` e os ensaios de integração e falhas da Etapa 2
no CORE. O encaminhamento UDP está implementado em `src/ott/forwarding.py` e integrado na CLI.

Os handlers recebem `fn(node, neighbor_id, msg)`, em que `neighbor_id` é uma string (D20).

## 6. Como testar

**Suite completa (66 testes, ~20 s):**

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

**Medições de tempos em localhost** (processos reais, parâmetros por omissão, ~6 min; não é apanhado pelo `discover`):

```sh
PYTHONPATH=src python3 tests/medicoes.py 10            # todas, 10 repetições
PYTHONPATH=src python3 tests/medicoes.py 10 substituto # só uma (morte|substituto|simultaneo|bootstrapper)
```

**Tabela de rotas e anúncios:**

```sh
PYTHONPATH=src python3 -m unittest tests.test_routing -v
```

**Protocolo e tabela de rotas:**

```sh
PYTHONPATH=src python3 -m unittest tests.test_protocol tests.test_routing -v
```

**Integração da Etapa 2 com nós reais em localhost** (6 testes, ~4 s):

```sh
PYTHONPATH=src python3 -m unittest tests.test_integration -v
```

Cobrem: validação do protocolo, overlay por bootstrapper e recuperação, eventos up/down, RTT, ligação
duplicada, vizinho calado (timeout), mensagem inválida, substituto, alvos expirados e tentativas em paralelo.
`tests/test_routing.py` cobre o modelo, publicação, aprendizagem por saltos, supressão de
duplicados/anúncios antigos, melhoria de caminho, subscrições partilhadas e reação local a
falhas e vizinhos novos, mas com nós falsos. `tests/test_integration.py` usa `OverlayNode`
reais: anúncios, ativação/desativação, quedas do cliente e do montante (S1–A–C) e o plano de
dados UDP/RTP com dois clientes num ramo partilhado (S1–A–{C1, C2}). Falta ensaiar a
distribuição e a recuperação de falhas no CORE.

**No CORE:** `docs/testes-core.md` tem os comandos de cada nó para os cenários 1, 2 e 3 e a lista de testes
(arranque, saída limpa, queda abrupta, nó congelado `kill -STOP`, link em baixo, RTT com atraso, substituto).
Usar `--stats 5` para ver os vizinhos e o RTT; guardar logs com `tee`.

## 7. O que esperar dos resultados e como avaliar

| Teste | Esperado | Como medir |
|---|---|---|
| Arranque | todos os vizinhos do JSON ligados em ≤ ~4 s, uma ligação por par | logs `HELLO: vizinho X ligado`; `--stats` |
| Saída limpa (Ctrl-C) / processo morto | vizinhos detetam em milissegundos (BYE / fecho TCP) | diferença entre a hora da ação (`date`) e o log `desligado` |
| Nó congelado (`kill -STOP`) | deteção por timeout em ≈ 5–8 s (o último sinal pode ter chegado até 2 s antes do STOP) | idem |
| Reentrada | volta a ligar em ≤ ~2 s | logs |
| Link em baixo (`ip link set ... down`) | ≈ 6–8 s (dead_after 6 s + ciclo de 2 s) | `date` antes da ação e hora do log |
| RTT | em localhost/CORE sem atraso ≈ 0,3–1 ms; com atraso D no link ≈ 2·D | `--stats` |
| Substituto | ≈ 7 s (5 s de espera + ciclo de 2 s), `substitutos propostos` e novo HELLO | logs |

Critérios: correção (vizinhos certos, sem duplicados), **tempo de deteção e de recuperação** (média e máximo
de ~10 repetições), robustez (sem threads penduradas nem exceções nos logs).

**Resultados anteriores (históricos, antes das correções D17–D19; os valores finais estão abaixo)**

- Cenário 2 no CORE (2026-10-22): Ctrl-C num nó → vizinhos detetam em ≤ 2 ms; reentrada do nó → overlay
  completo em ≈ 2,0 s (primeiros vizinhos em 0,9 s). Sem vizinhos "fantasma" após ligações duplicadas.
- Cenário 3, underlay (2026-10-22): `ping` do `C6` ao `Server` 3/3, 0 % de perda; OSPF convergiu em < 30 s.
- Cenário 3 redesenhado (2026-11-03): OSPF completo (nº de vizinhos de cada router = nº dos seus links), os 9 clientes fazem `ping` ao `S1` com 0 % de perda (3/3) e os 11 nós overlay arrancam com exatamente os vizinhos do `cenario3.json` e 0 `desligado`, RTT 1,1–2,0 ms. As medições do cenário 3 da tabela abaixo (testes 3, 4, 8, 11) foram feitas na versão anterior do underlay; a estrutura overlay é a mesma.
- Cenário 2 no CORE (2026-10-27, medido antes do `TCP_NODELAY`, com RTT inflacionado nalguns pares): RTT com `--stats 5` (sem atraso configurado), `S1`: O1 1,3–1,6 ms, O2 1,2–1,8 ms; O1: C2 1,5–1,9 ms, O2 1,8–2,2 ms, S1 1,6–2,1 ms; O2 e C1/C2 com valores semelhantes (1,2–2,3 ms). Os valores são estáveis (média móvel). Reinícios manuais de nós produziram `WARNING vizinho X desligado` e religação automática nos vizinhos, sem erros nem duplicados.
- Localhost (2026-10-26): RTT 0,4–0,7 ms; 21/21 testes automáticos, estáveis em 4 execuções seguidas.

**Resultados finais (2026-11-02; mín / média / máx em segundos; scripts em `tests/medicoes.py`)**

*Localhost, processos reais, parâmetros por omissão:*

| Medida | n | mín / média / máx |
|---|---|---|
| Processo morto (`SIGKILL`): deteção nos vizinhos | 30 | 0,00 / 0,01 / 0,01 |
| Reentrada do nó morto (todos os vizinhos ligados) | 10 | 0,97 / 0,99 / 0,99 |
| Nó congelado (`SIGSTOP`): deteção por timeout | 30 | 5,00 / 6,34 / 7,00 |
| Nó descongelado (`SIGCONT`): religação | 10 | 2,00 / 2,00 / 2,01 |
| Substituto (`--min-neighbors 1`) desde a perda do vizinho | 10 | 7,01 / 7,01 / 7,02 |
| Arranque simultâneo dos 5 nós: overlay completo | 10 | 2,05 / 2,06 / 2,06 (0 duplicados/quedas espúrias) |
| Bootstrapper tardio (sobe 3 s depois) | 5 | 2,01 / 2,01 / 2,02 |

*CORE (cenários 2 e 3; ações com `ip link`, `tc netem` e `pkill` dentro dos nós com `vcmd`):*

| # | Teste | n | Resultado |
|---|---|---|---|
| 1 | Arranque (cenário 2) | – | vizinhos iguais ao JSON, uma ligação por par, sem `desligado` |
| 2 | RTT sem atraso | – | 1,0–2,0 ms em todos os pares (o `ping` dá 0,1 ms: o resto é Python) |
| 3 | Processo morto, O5 do cenário 3 (5 vizinhos) | 10 | deteção 0,01 / 0,02 / 0,03 s |
| 4 | Reentrada do O5 (todos os 5 ligados) | 10 | 1,86 / 1,89 / 1,92 s |
| 5 | Nó congelado (`kill -STOP`, cenário 2) | 1 + localhost 10× | ≈ 6 s após o último sinal (`sem resposta`); em CORE o caminho do timeout foi medido 10× com o teste 6 |
| 6 | Link O1–C2 em baixo (`ip link set eth0 down`) | 10 | deteção em O1 5,55 / 5,96 / 6,03 s; em C2 5,99 / 7,54 / 8,00 s; religação ao repor 0,02 / 1,59 / 2,00 s |
| 7 | RTT com atraso 20 ms em cada lado (`tc netem`) | – | 41,1–41,6 ms no link com atraso (esperado 2·20 + ~1,3); outros links 1,5 ms; ao remover volta a 2 ms |
| 8 | Substituto no cenário 3 (O9 com `--min-neighbors 1` perde o O6) | 10 | liga ao O1 em 7,18 / 7,23 / 7,29 s |
| 9 | Arranque simultâneo (cenário 2) | 10 | overlay completo em 2,24 / 2,45 / 4,20 s, 0 quedas espúrias |
| 10 | Bootstrapper em baixo ao arrancar (S1 sobe 3 s depois, cenário 2) | 10 | liga 3,15 / 3,18 / 3,20 s após o S1 subir, 0 quedas |
| 11 | Overlay completo no cenário 3 (11 nós, ping underlay 0 % perda; repetido em 2026-11-03 no XML redesenhado) | 2 | os 11 nós com exatamente os vizinhos do JSON, RTT 1,1–2,4 ms, 0 `desligado` |

Notas: o cenário 2 só tem ligações diretas; um nó não alcança um substituto que não seja vizinho, por isso o teste 8
foi feito no cenário 3 (com routers). Em localhost o tempo de substituto coincide com o do CORE (≈ 7 s).

**Defeitos encontrados pelos ensaios (todos corrigidos, com teste automático):**
1. Arranque simultâneo: `down`+`up` falsos por ligação duplicada (D17). Detetado por `tests/medicoes.py` (6 eventos extra numa execução), depois reproduzido por teste (2 de 25 execuções antes da correção).
2. RTT inflacionado e instável por Nagle (D18). Detetado no CORE com `tc netem` (60–80 ms em vez de 41 ms).
3. Substituto só ligava no ciclo seguinte (D19) → de 9,2 s para 7,2 s no CORE.
4. Contador de défice não era reposto entre ciclos (D19).

**Como foi feito (útil para a defesa):** `core-cli xml -f topologies/cenarioN.xml -s` abre a sessão; as ações correm com
`vcmd -c /tmp/pycore.1/<nó> -- <comando>`; o `ott` arranca com `setsid` e escreve para ficheiro; os tempos saem das horas dos logs
(ms) menos a hora da ação. Para parar o `ott` num nó: `vcmd -c /tmp/pycore.1/<nó> -- pkill -f '^python3 -m ott'`
(o `pkill` do anfitrião não chega: os processos correm como root noutro espaço de PIDs).

## 8. Limitações e riscos conhecidos

- Um só `host` por nó no ficheiro (nós com várias interfaces só são alcançáveis por um IP).
- O bootstrapper não expira nós: só sabe que estão vivos por terem pedido registo/substitutos.
- Ficheiro de topologia estático (um nó novo tem de constar do ficheiro).
- Na entrada, quando os dois lados tentam ligar-se em simultâneo, abre-se e fecha-se uma ligação à toa; desde o D17 isto já não produz eventos `down`/`up` falsos (só há uma ligação duplicada transitória).
- O substituto demora ≈ 7 s (espera de 5 s + ciclo de manutenção de 2 s): é o preço de não reagir a quedas curtas.
- Em ligações diretas (cenários 1 e 2) os substitutos só podem ser vizinhos já alcançáveis; faz sentido testá-los com routers (cenário 3).
- RTT ≈ 1–2 ms em CORE sem atraso: inclui processamento em Python (o `ping` dá 0,1 ms); não afeta a comparação entre caminhos.
- Sem autenticação nem cifra no controlo.
- O cenário 2 desenhado tem 2 nós intermédios; o enunciado mostra 3 (S1 liga a um só, que se liga aos outros dois).
- Etapa 2 (revisão de 2026-11-09, ver `docs/ETAPA2.md`): um servidor reiniciado não volta a ser aprendido (época 1 rejeitada); sem retirada nem expiração de rotas; o montante pode oscilar entre caminhos de custo igual; o forwarder aceita dados de qualquer vizinho; a métrica ignora o RTT. Todas têm decisão tomada (D21–D24) e estão por implementar.
- A origem dos datagramas UDP identifica-se pelo IP do *peer* TCP (`neighbor_for_address`); validar no CORE em nós com várias interfaces.
- `docs/testes-core.md` e `docs/CHECKPOINT1.md` são citados neste ficheiro mas não estão no repositório.

## 9. Por fazer

**Elemento 1**
- [x] Ensaiar no CORE: timeout/link em baixo, RTT com atraso, substituto, arranque simultâneo, bootstrapper tardio (2026-11-02, 10 repetições).
- [x] Ensaiar o overlay no cenário 3 (11 nós) com a atribuição proposta (2026-11-02).
- [x] Passar a especificação e os resultados para o relatório (`docs/relatorio/e1_overlay.tex`, incluída em `main.tex`).
- [ ] **Tu:** confirmar a atribuição overlay→CORE do cenário 3 (proposta atualizada em 2026-11-03 para o XML redesenhado; se mudar, só se altera o `cenario3.json` e o `testes-core.md`).
- [ ] **Grupo:** rever `docs/protocolo.md` (inclui o `BYE reason=duplicate`) e o texto `e1_overlay.tex`; o `babel` em português do `main.tex` não está instalado nesta máquina (compila noutra com `texlive-babel-portuguese`).
- [ ] Decidir se refazemos o cenário 2 com 3 intermédios, como no enunciado.

**Outros elementos (para saber o que depende de nós)**
- [x] E2 (controlo): tabela de rotas, anúncios, ativação/desativação e reação local a alterações de vizinhos.
- [x] E2: corrigir o argumento dos handlers e validar ponta a ponta em localhost com nós reais (2026-11-09).
- [ ] E2: passos 3–10 do plano em `docs/ETAPA2.md` (subscrições agregadas, RPF, candidatos e RTT, `WITHDRAW`, tempo de vida, `S2`).
- [ ] E2: ensaios ponta a ponta de distribuição e falhas/recuperação no CORE (ensaios 1–9 em `docs/ETAPA2.md`).
- [ ] E3: interface de apresentação do vídeo, cenários e medições de streaming.

## 10. Perguntas para os docentes

1. Podemos usar **prefixo de tamanho** em vez de uma linha JSON por mensagem? (D3)
2. Quantos vizinhos **X** por nó se espera, e quantos nós overlay no cenário 3? Os clientes (folhas) também têm de ter X?
3. Basta o servidor conhecer o overlay por **ficheiro escrito à mão**, ou querem entrada dinâmica de nós novos?
4. Podem correr nós overlay **em routers** no CORE (como na Figura 1), dado que o enunciado aconselha só sistemas terminais?
5. O cenário 2 do enunciado tem 3 intermédios; a nossa topologia de teste tem 2 com um triângulo. Serve?
6. Dois servidores: são dois nós overlay distintos com anúncios independentes?
7. Datas e conteúdos das duas avaliações intermédias (5 % + 5 %).

## 11. Registo de alterações

| Data | O que mudou |
|---|---|
| 2026-11-09 | **Revisão da Etapa 2** face ao enunciado. Defeito crítico corrigido (commit `e83e37b`): `OverlayNode._dispatch` passava o objeto `Neighbor` aos handlers, por isso com nós reais o `ACTIVATE` nunca saía, o anúncio voltava ao emissor e as quedas não invalidavam rotas (D20). Novo `tests/test_integration.py` (6 testes com nós reais: rotas, ativação, quedas e plano de dados S1–A–{C1, C2}); falhavam todos antes da correção. Suite: 66 testes, todos passam. Decididas as melhorias D21–D25; plano ordenado em `docs/ETAPA2.md`. Nada ensaiado no CORE. |
| 2026-11-03 | Etapa 2: integrado o plano de dados UDP à CLI. `--publish-flow` emite MJPEG/RTP só quando há destinos ativos; `--subscribe SERVIDOR:FLUXO` ativa a rota e recolhe estatísticas; nós intermédios replicam uma cópia por vizinho a jusante. Atualizados README, protocolo e roadmap. Ensaios de integração no CORE ainda por fazer; não foram executados testes nesta alteração. |
| 2026-11-03 | `docs/testes-core.md` reorganizado por **nome do nó no CORE** (`S1`, `r1`–`r10`, `C1`–`C9` no cenário 3), com uma tabela por cenário "nó do CORE → comando → vizinhos esperados" e a distinção explícita entre o nome do nó e o id overlay (`-i`); os testes T1–T10 e B1–B8 passaram a indicar o nó do CORE onde correr cada comando. |
| 2026-11-03 | `docs/testes-core.md` reescrito na parte dos testes: método (ott em segundo plano com log em ficheiro, `date` para medir), interfaces de cada nó, testes gerais T1–T10 com comandos e valores esperados/medidos, tabela de onde aplicar cada um nos cenários 1, 2 e 3, e testes do bootstrap no cenário 3 (B1–B8: registo, bootstrapper tardio, id desconhecido, bootstrapper cai e volta, substitutos, estratégia 1). O B7 (substituto `O10`) está deduzido do código e ainda não foi ensaiado; o B5 (religação ao bootstrapper reposto) também. |
| 2026-11-03 | **Cenário 3 redesenhado no CORE** (routers `r1`–`r10`, `S1`, switches com 2 hosts, como na Figura 1): o XML tinha perdido os serviços FRR e a config própria. Novo `topologies/aplicar_frr.py` (+ `frrboot.sh.tmpl`) repõe-os a partir das interfaces do XML. Corrigido o `frrboot.sh` (stdio dos daemons para `/dev/null`; sem isso o CORE ficava em `INSTANTIATION`). `cenario3.json` com nova atribuição overlay→CORE e IPs novos; `docs/testes-core.md` e `CHECKPOINT1.md` atualizados (`-b 10.0.18.20`). Validado no CORE: OSPF completo, 9/9 clientes fazem ping ao S1, 11 nós overlay com os vizinhos do JSON. |
| 2026-11-02 | Novo `docs/CHECKPOINT1.md`: guia para o checkpoint 1 (o que se pede, demonstração, como funciona o código, decisões, resultados, defeitos achados, limitações, perguntas prováveis e checklist). |
| 2026-11-02 | **Elemento 1 concluído.** Ensaios no CORE (cenários 2 e 3, abertos por `core-cli`, ações por `vcmd`) e medições em localhost com 10 repetições (`tests/medicoes.py`, novo). Corrigidos 4 defeitos achados nos ensaios: `down`/`up` falsos com ligação duplicada (`BYE reason=duplicate`, D17), RTT inflacionado por Nagle (`TCP_NODELAY`, D18), ligação imediata a substitutos e reposição do contador de défice (D19). 60 testes automáticos (+9). Relatório: nova secção `docs/relatorio/e1_overlay.tex`. `docs/protocolo.md` atualizado. |
| 2026-10-27 | Teste 5 (nó congelado) feito 1× no cenário 2: deteção por timeout (`sem resposta`) ≈ 6 s; falta repetir com hora exata. |
| 2026-10-27 | Checklist de ensaios do cenário 2 (§7) com resultados: arranque, RTT, processo morto (≤ 2 ms) e reentrada (≈ 2 s) feitos; timeout, link down, atraso, substitutos, arranque simultâneo e bootstrapper em baixo por fazer. |
| 2026-10-27 | Primeiro ensaio das funcionalidades novas no CORE (cenário 2, `--stats 5`): RTT por vizinho funciona (1,2–2,3 ms); deteção de queda e religação observadas nos logs. Falta medir timeout, link down, RTT com atraso e substitutos. |
| 2026-10-27 | Definido em `routing.py` o modelo local de rotas por `(servidor, fluxo)` e documentados os campos e a escolha da Estratégia 1 em `docs/protocolo.md` §7. Adicionados 8 testes unitários para o modelo (`tests/test_routing.py`), todos passaram isoladamente. Anúncios, ativação e encaminhamento continuam pendentes. |
| 2026-10-27 | Implementada a reação base das rotas a `neighbor_up/down`: anúncios para vizinhos novos, invalidação do montante perdido, limpeza de ramos caídos e reativação de subscrições após reaprendizagem; 46 testes passam. Ensaios de falhas no CORE ainda pendentes. |
| 2026-10-27 | Implementados handlers de `ACTIVATE`/`DEACTIVATE`, partilha de ramos por subscritores e operações locais para iniciar/terminar uma subscrição; atualizados testes e documentação. |
| 2026-10-26 | Validação de campos no protocolo (`ProtocolError`); eventos `up/down`; RTT por vizinho; pedido de substitutos (`REPLACE`, `--min-neighbors`); uma tentativa de ligação por alvo; opções `--stats`/`--replace-after`; 21 testes; `docs/protocolo.md`; `cenario3.json` com a atribuição proposta; este ficheiro. |
| 2026-10-22 | Cenários 1 e 2 ensaiados no CORE (ligar, Ctrl-C, reentrada); `cenario3.xml` validado e routers FRR a funcionar (config própria no XML); `docs/testes-core.md`. |
| 2026-10-20 | Etapas 0 e 1: protocolo, nó, bootstrapper, CLI, topologias e primeiros testes. |
