# ESTADO DO PROJETO – Elemento 1 (Overlay e protocolo de controlo)

> Ficheiro vivo: é atualizado sempre que algo é feito. Serve para saber o ponto da situação,
> relembrar as decisões e preparar as conversas com os docentes.
> **Última atualização: 2026-11-10.**

## 1. Resumo em 30 segundos

- Overlay aplicacional em **Python 3 (só biblioteca standard)**: cada nó é o programa `ott`, liga-se
  por **TCP** aos vizinhos, mantém-nos com PING/PONG, deteta quedas, mede o RTT de cada vizinho e,
  opcionalmente, pede vizinhos substitutos ao bootstrapper.
- O overlay constrói-se de duas formas: **estratégia 2** (`ott -b <bootstrapper>`: o servidor lê um
  ficheiro de topologia e devolve os vizinhos) e **estratégia 1** (`ott viz1 viz2`: vizinhos à mão).
- **Etapa 2 (estado em 2026-11-09):** anúncios periódicos por fluxo; montante escolhido pela
  menor latência acumulada (RTT/2 por ligação, com histerese e condição de viabilidade contra
  ciclos; saltos só desempatam, D30, 2026-11-10); subscrições agregadas por ramo; dados RTP/JPEG
  por UDP pelos ramos ativos com RPF estrito; reparação rápida, `WITHDRAW` e tempo de vida das
  rotas para falhas e reinício do servidor. Tudo coberto por 118 testes automáticos, incluindo
  nós reais em localhost. Até 2026-11-09 a Etapa 2 só funcionava com nós falsos (handlers
  recebiam o objeto `Neighbor`, corrigido em `e83e37b`). **Falta:** segundo servidor nas
  topologias e todos os ensaios no CORE (ver `docs/ETAPA2.md`).
  **Elemento 1 concluído:** todas as funcionalidades estão implementadas e foram ensaiadas no CORE (cenário 2 e
  cenário 3 com 11 nós) e medidas com 10 repetições (secção 7). Os ensaios acharam e corrigiram 4 defeitos
  (D17–D19 e o contador de défice); ver secção 7 e o registo de alterações.
- Falta ainda, fora do E1: validação ponta a ponta da Etapa 2 no CORE e a interface de streaming/apresentação da Etapa 4.
  Do E1 só depende de ti a revisão do texto do relatório (a atribuição overlay→CORE do cenário 3 foi decidida em 2026-11-09, D13).

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
| Overlay completo no cenário 3 (11 nós) | ✅ ensaiado no CORE com a atribuição anterior; atribuição fiel à Figura 1 decidida em 2026-11-09 (D13), ainda por ensaiar | `topologies/cenario3.*` |
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
| D13 | Cenário 3: underlay com 10 routers FRR/OSPF (`r1`–`r10`), 5 switches e `S1` + `C1`–`C9`; nós overlay também nos routers. **Atribuição overlay→CORE fiel à Figura 1 (decidida em 2026-11-09):** O1=S1, O2=r1, O3=r3, O4=C8, O5=r9, O6=r10, O7=r5, O8=C2, O9=C4, O10=C6, O11=C7 | O underlay do XML é isomorfo ao da Figura 1 (n10=r1, n9=r3, n8=r2, n7=r5, n6=r4, n5=r6, n4=r7, n3=r8, n2=r9, n1=r10; verificado por comparação dos grafos). Com esta atribuição as ligações virtuais atravessam 0 a 2 ligações físicas, como no enunciado (O3–O5 = r3>r6>r9, o "n9 → n5 → n2" do texto), e cada cliente fica no switch do router do seu nó overlay. A atribuição anterior (O4=C1, O5=r4, O6=r5, O7=r2, O9=C3, O10=C4, O11=C5) punha todas as ligações overlay entre routers sobre ligações diretas, o que copiava o underlay, e fazia o vídeo para O4 atravessar r1–r3 duas vezes. |
| D14 | Routers com FRR (não Quagga) e `frr.conf`/`frrboot.sh` próprios no XML, **reaplicáveis com `topologies/aplicar_frr.py`**; o `frrboot.sh` manda o stdio dos daemons para `/dev/null` | O Quagga instalado não tem `vtysh`, e o CORE 9.2.1 gera config que o FRR 10 rejeita (ver `docs/testes-core.md`). Redesenhar o cenário na GUI repõe os serviços do Quagga e apaga a config (aconteceu em 2026-11-03), daí o programa. Sem o redirecionamento, o `zebra`/`ospfd` herdavam os pipes do comando de arranque e o CORE ficava preso em `INSTANTIATION` (os hosts nunca recebiam rota por defeito). |
| D15 | Decidido implementar a Estratégia 1 do enunciado. Estado de rota indexado por `(servidor, fluxo)`, com vizinho de montante, candidatos por vizinho, destinos a jusante, subscritores locais, saltos, atraso acumulado e época. `fluxo` identifica o conteúdo/stream multimédia (ex.: `filme_a`). | Anúncios do servidor constroem as rotas; pedidos dos clientes abrem e fecham ramos partilhados. Roadmap em `docs/ETAPA2.md`; modelo em `docs/protocolo.md`. |
| D16 | Protocolo E2 definido: `ANNOUNCE(server_id, flow, epoch, metric)`; `ACTIVATE`/`DEACTIVATE(server_id, flow, subscriber_id)`. Custo em saltos, época crescente por fluxo, IDs overlay para servidor/cliente e nome estável para fluxo. | Suporta vários fluxos e servidores; handlers dos três tipos implementados. |
| D17 | Quem fecha uma ligação duplicada envia `BYE` com `reason: "duplicate"`; quem o recebe retira a ligação **sem `down`** e espera até 3 s pela vencedora (`DUPLICATE_GRACE`), que é adotada sem novo `up` | Num arranque simultâneo o outro lado fechava, depois de nós a darmos como `up`, a ligação perdedora: o E2 via `down`+`up` falsos (apagaria rotas à toa). Alternativas rejeitadas: só o nó de menor id liga (quebra o D6) ou adiar sempre todos os `down` (esconde quedas reais). |
| D18 | `TCP_NODELAY` em todas as ligações entre vizinhos | O algoritmo de Nagle retinha as mensagens pequenas (PING/PONG cruzados) à espera do ACK: RTT de 37 ms inicial nos clientes e 60–80 ms com atraso de 20 ms/lado (esperado 41 ms). Com NODELAY: 41,1–41,6 ms. O RTT é a métrica do E2, por isso tem de ser fiável. |
| D19 | Ligar a um substituto logo que é proposto, e repor o contador de défice ao atingir X vizinhos | Poupava-se o ciclo de 2 s; e o contador antigo podia contar défice já resolvido (artefacto visto nas medições). |
| D20 | Os handlers registados com `register_handler` recebem o **ID** do vizinho (`fn(node, neighbor_id, msg)`) | Era o contrato documentado e o que `routing.py` usa; o `_dispatch` passava o objeto `Neighbor` e partia a Etapa 2 com nós reais. |
| D21 | **Substituída pela D30 em 2026-11-10.** Métrica: saltos; em empate, RTT acumulado com histerese (> 20 % e > 1 ms), depois o ID do vizinho. Escolha a partir de uma tabela de candidatos por vizinho; o montante atual vale também com a época anterior (**implementado** em 2026-11-09) | Cumpre "métricas da rede" sem oscilações; a regra antiga "primeira cópia da época ganha" alternava entre caminhos de custo igual. |
| D22 | Falhas: `WITHDRAW` explícito com reparação rápida (condição de viabilidade), rota sem caminho aceita qualquer época (**implementado** em 2026-11-09; **a aceitação de qualquer época foi substituída pela D27 em 2026-11-10**); tempo de vida de 6 × o intervalo como rede de segurança (`--route-ttl`, **implementado** em 2026-11-09) | Um servidor reiniciado (época 1) deixa de ficar bloqueado; um servidor morto deixa de manter rotas ativas para sempre. |
| D23 | Plano de dados com RPF estrito: só se aceitam datagramas do montante (**implementado** em 2026-11-09) | Elimina duplicados e ciclos de dados; aceita-se uma falha curta ao trocar de montante. |
| D24 | Subscrições agregadas por ramo: `ACTIVATE` na transição 0→1, `DEACTIVATE` na 1→0; `subscriber_id` opcional e informativo (**implementado** em 2026-11-09) | Fiel à tabela do enunciado ("Destinos: O7, O3") e escala com o número de clientes; o servidor deixa de conhecer cada cliente. |
| D25 | Dois servidores com fluxos distintos; chave `(server_id, flow)` mantém-se | Requisito mínimo do enunciado (pág. 3); falta acrescentar `S2` às topologias. |
| D26 | Plano de dados: o datagrama leva o id do nó que o envia (campo `salto`, formato versão 2); o vizinho de entrada (RPF) é esse id e não o IP de origem (**implementado** em 2026-11-10) | Substitui a identificação pelo IP da ligação TCP (`neighbor_for_address`, que fica na interface do `OverlayNode` mas deixa de ser usada pelo forwarder). Falhou no CORE (cenário 3): o r5 envia o UDP para o r9 pela `eth1` (10.0.5.1), mas o O5 conhece o O7 pelo IP a que se ligou, 10.0.4.2, e descartou todo o vídeo após a reparação rápida. O id não depende do encaminhamento IP. Impacto: `rtp.py` passa à versão 2 e rejeita a 1, por isso todos os nós têm de ser atualizados juntos; `protocolo.md` §3.6. Sem autenticação do id, como no resto do controlo. |
| D27 | Épocas e retirada: o servidor começa as épocas na hora do arranque em ms (+1 por ronda); uma rota retirada por este nó só aceita épocas maiores do que a retirada durante `route_ttl` (retenção), e depois qualquer época (**implementado** em 2026-11-10) | Substitui a regra da D22 "rota sem caminho aceita qualquer época", que no CORE (ensaio 9) deixou anúncios antigos em trânsito formar um ciclo fantasma O6 ↔ O7 que só o tempo de vida desfez (L9). Épocas a partir do relógio mantêm o reinício do servidor imediato sem essa regra. Impacto: formato das mensagens inalterado (só o valor de `epoch` muda), por isso não há problema de compatibilidade; uma retirada só é reaprendida na ronda seguinte; um servidor com o relógio atrasado só é aceite ao fim de `--route-ttl`. |
| D28 | Envios da tabela de rotas por ordem: as mensagens decididas sob o bloqueio entram nessa altura numa fila única e são enviadas fora do bloqueio, por ordem, uma thread de cada vez (**implementado** em 2026-11-10) | Antes, cada handler enviava depois de libertar o bloqueio, e duas threads podiam trocar a ordem: no CORE (ensaio 9 repetido), um `ACTIVATE` saiu depois do `DEACTIVATE` da troca de montante seguinte e deixou um ramo órfão (L10). Formato das mensagens inalterado. Custo: um envio lento a um vizinho atrasa os envios seguintes de outras threads (já atrasava os da própria thread). |
| D29 | Uma exceção num handler (rotas, streaming) é registada no log e **não fecha a ligação** ao vizinho; um nó que não é bootstrapper responde `ERROR "este nó não é bootstrapper"` | Um erro de outro módulo não pode derrubar a ligação nem fazer o vizinho dar-nos como caídos; o texto antigo (`nó desconhecido`) enganava quem apontava `-b` a um nó errado. Cobertos por `RobustnessTest`. |
| D30 | **Métrica: latência acumulada** (substitui a D21). O custo de uma ligação é RTT/2 (média móvel do PING/PONG), no mínimo 0,1 ms, e 1 s sem RTT medido (`RoutingTable._link_delay`, o único sítio a mudar para juntar perdas ou jitter); o montante é o de menor latência, com histerese (> 20 % e > 1 ms); diferenças abaixo de metade disso são empate, decidido por menos saltos e depois pelo ID. **Condição de viabilidade** como no Babel: só servem candidatos de uma época mais recente do que a última anunciada, ou da mesma época com `(atraso, saltos)` anunciados menores do que o melhor anunciado nela (`RouteEntry.feasibility`); um anúncio não viável do montante conta como perda do montante. O nó anuncia a época do montante que usa (só propaga uma época nova quando o montante a traz) (**implementado** em 2026-11-10) | Os saltos não são uma métrica da rede; os docentes sugeriram-nos só para começar. Trocar apenas a ordem da seleção formava ciclos (o RTT para o montante sobe sem chegar anúncio e um vizinho que nos usa parece mais rápido); o teste de rede simulada encontra um ciclo sem a condição de viabilidade. Alternativa rejeitada para a viabilidade: só saltos (impedia adotar a meio da época um caminho mais longo e mais rápido). Impacto: formato das mensagens inalterado (`delay` já era opcional; passa a ir sempre que é finito e o `epoch` reencaminhado pode ser o da ronda anterior); todos os nós devem ser atualizados juntos (um nó antigo escolhe por saltos e anuncia épocas novas com o caminho antigo). Custo: mais trocas de montante possíveis, cada uma com uma falha curta no vídeo (RPF); um caminho mais rápido mas não viável só é adotado na época seguinte. Ensaios 4, 5, 6, 8 e 9 repetidos no CORE com a D30 e ensaio 10 novo, todos passaram (2026-11-10). |

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
node.register_handler("ANNOUNCE", fn)   # fn(node, vizinho_id, msg); attach regista os 4 handlers E2
node.send(id, make_msg(...)) ; node.broadcast(msg, exclude={origem})
node.neighbor_ids()
node.on_neighbor_up(fn) ; node.on_neighbor_down(fn)   # fn(node, id)
node.rtt(id) ; node.rtts()                            # segundos (média móvel) ou None
```

Definidos e validados os campos de `ANNOUNCE`, `ACTIVATE`, `DEACTIVATE` e `WITHDRAW` (ver
`docs/protocolo.md` §7.1). `RoutingTable.attach(node)` regista os quatro handlers, os eventos
de vizinhança e, com `route_ttl`, a thread do tempo de vida; `RoutingTable.close()` pára-a.
Continuam pendentes o formato de `ROUTE_TEST` e os ensaios de integração e falhas da Etapa 2
no CORE. O encaminhamento UDP está implementado em `src/ott/forwarding.py` e integrado na CLI.

Os handlers recebem `fn(node, neighbor_id, msg)`, em que `neighbor_id` é uma string (D20).

## 6. Como testar

**Suite completa (118 testes, ~26 s):**

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

**Integração da Etapa 2 com nós reais em localhost** (6 testes, ~4 s) e **RPF** (4 testes, ~2 s):

```sh
PYTHONPATH=src python3 -m unittest tests.test_integration -v
PYTHONPATH=src python3 -m unittest tests.test_forwarding -v
```

Cobrem: validação do protocolo, overlay por bootstrapper e recuperação, eventos up/down, RTT, ligação
duplicada, vizinho calado (timeout), mensagem inválida, substituto, alvos expirados e tentativas em paralelo.
`tests/test_routing.py` cobre o modelo, publicação, aprendizagem e escolha pela latência (com
a condição de viabilidade e uma rede simulada sem ciclos), supressão de
duplicados/anúncios antigos, melhoria de caminho, subscrições partilhadas e reação local a
falhas e vizinhos novos, mas com nós falsos. `tests/test_integration.py` usa `OverlayNode`
reais: anúncios, ativação/desativação, quedas do cliente e do montante (S1–A–C) e o plano de
dados UDP/RTP com dois clientes num ramo partilhado (S1–A–{C1, C2}).
`tests/test_forwarding.py` cobre o RPF estrito com UDP real em loopback. Falta ensaiar a
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
- Etapa 2: uma retirada pode perder-se se a ligação cair a meio; o tempo de vida (`--route-ttl`, 30 s por omissão) cobre esse caso, mas só ao fim desse tempo. Todos os nós têm de usar o mesmo `--announce-interval`, senão o tempo de vida pode esquecer caminhos válidos.
- A métrica é só a latência (RTT/2 medido sobre TCP): perdas, jitter e largura de banda não contam; a perda no UDP exigiria sondas UDP próprias (o TCP retransmite e a perda só aparece como RTT maior). Nos primeiros segundos de uma ligação ainda não há RTT: a ligação custa 1 s (comporta-se como saltos) e, quando o RTT aparece, pode haver uma troca única de montante. Depois de anunciar uma época, um caminho mais rápido mas não viável só é adotado na época seguinte (D30).
- Depois de um `WITHDRAW`, os ramos a jusante são desfeitos e só voltam a abrir quando o caminho é reaprendido (até um intervalo de anúncio, 5 s por omissão); a reparação rápida evita isto quando há alternativa viável.
- Com RPF estrito, uma troca de montante provoca uma falha curta no vídeo (até o novo montante começar a enviar); por medir no CORE.
- Com subscrições agregadas, o servidor não sabe quantos clientes tem: só vê os ramos que lhe pedem o fluxo.
- **Ramo órfão por reordenação de `ACTIVATE`/`DEACTIVATE` (L10), corrigido pela D28 e confirmado no CORE (três reinícios do servidor sem cópias a mais).** A `RoutingTable` decide os envios sob o bloqueio e envia-os depois de o libertar; com dois anúncios quase simultâneos (threads de ligações diferentes), o `DEACTIVATE` ao montante antigo pode chegar antes do `ACTIVATE` decidido antes. No ensaio 9 repetido (CORE), o O7 ficou a enviar o `filme_a` ao O5, cujo montante era o O3: ≈ 20 imagens/s descartadas por RPF durante 1,5 min. Reproduzido com duas threads.
- **Rota fantasma depois da queda do servidor (L9), corrigida pela D27 e validada no CORE (ensaio 9 repetido).** Uma rota sem caminho aceitava qualquer época (D22) e, por isso, também anúncios antigos ainda em trânsito depois do `WITHDRAW`. No ensaio 9 (CORE, cenário 3) formou-se um ciclo O6 ↔ O7 para o `filme_a` que só o tempo de vida (30 s) desfez; até lá, a época 1 do servidor reiniciado foi rejeitada e o fluxo voltou ≈ 10 s depois do servidor (o `filme_b`, sem ciclo, voltou em ≈ 1 s).
- Depois de uma retirada, a rota só é reaprendida na ronda de anúncio seguinte (até `--announce-interval`), mesmo que um vizinho repare o caminho antes, porque os anúncios da mesma época são recusados (D27). Se o relógio do servidor recuar entre arranques, o servidor só é aceite ao fim de `--route-ttl`.
- Até 2026-11-10 a origem dos datagramas UDP identificava-se pelo IP do *peer* TCP, o que falhou no CORE (cenário 3: o O5 descartou todo o vídeo do O7 depois da reparação rápida, porque o r5 envia o UDP com origem 10.0.5.1 e o O5 conhece o O7 como 10.0.4.2). **Corrigido (D26):** o vizinho de entrada passa a ser o id no campo `salto` do datagrama; validado no CORE no ensaio 6 (2026-11-10). O id não é autenticado.
- `docs/testes-core.md` existe localmente (recriado em 2026-11-10) mas está no `.gitignore`; `docs/CHECKPOINT1.md` é citado mas não está no repositório.

## 9. Por fazer

**Elemento 1**
- [x] Ensaiar no CORE: timeout/link em baixo, RTT com atraso, substituto, arranque simultâneo, bootstrapper tardio (2026-11-02, 10 repetições).
- [x] Ensaiar o overlay no cenário 3 (11 nós) com a atribuição proposta (2026-11-02).
- [x] Passar a especificação e os resultados para o relatório (`docs/relatorio/e1_overlay.tex`, incluída em `main.tex`).
- [x] Atribuição overlay→CORE do cenário 3: decidida em 2026-11-09, fiel à Figura 1 (D13; `cenario3.json` atualizado).
- [x] Repetir no CORE o overlay completo do cenário 3 com a nova atribuição: os 11 nós com exatamente os vizinhos do JSON (2026-11-10, logs `2026-11-10-6`). Os resultados 3, 4, 8 e 11 da secção 7 continuam a ser os da atribuição anterior.
- [x] Revisão cruzada do `routing.py` (`docs/REVISAO_E2.md`; achados todos resolvidos pelo E2) e testes de robustez do nó (D29), 2026-11-10.
- [ ] **Grupo:** rever `docs/protocolo.md` (inclui o `BYE reason=duplicate`) e o texto `e1_overlay.tex`; o `babel` em português do `main.tex` não está instalado nesta máquina (compila noutra com `texlive-babel-portuguese`).
- [ ] Decidir se refazemos o cenário 2 com 3 intermédios, como no enunciado.

**Outros elementos (para saber o que depende de nós)**
- [x] E2 (controlo): tabela de rotas, anúncios, ativação/desativação e reação local a alterações de vizinhos.
- [x] E2: corrigir o argumento dos handlers e validar ponta a ponta em localhost com nós reais (2026-11-09).
- [x] E2: subscrições agregadas por ramo e RPF estrito (passos 3 e 4, 2026-11-09).
- [x] E2: candidatos com desempate por RTT, `WITHDRAW` e reparação rápida (passos 5 e 6, 2026-11-09).
- [x] E2: tempo de vida das rotas (passo 7, 2026-11-09).
- [x] E2: retirar `seen_announcements` e revisão final da documentação (passos 8 e 9, 2026-11-09).
- [ ] E2: segundo servidor `S2` nas topologias (passo 10).
- [x] E2: ensaios 1 e 2 no CORE (cenário 1: caminho básico e troço comum partilhado), 2026-11-09.
- [x] E2: ensaio 4 no CORE (cenário 2: cada cliente usa o caminho mais curto), 2026-11-09.
- [x] E2: ensaio 6 no CORE (cenário 3: reparação rápida na queda do O3), 2026-11-10, depois da correção D26.
- [x] E2: ensaio 8 no CORE (cenário 3: atraso no caminho ativo, troca única sem perdas), 2026-11-10.
- [x] E2: ensaio 9 no CORE repetido com a correção D27: os dois fluxos voltam em ≈ 1–2 s, sem rota fantasma (2026-11-10). Ensaio 6 repetido também passou.
- [x] E2: correção do L10 (D28) confirmada no CORE: três reinícios do servidor, clientes de volta em ≈ 1–2 s, `RPF=0` e sem cópias a mais no O5 (2026-11-10).
- [x] E2: ensaios 3 (cenário 1, desativação dos clientes) e 5 (cenário 2, queda do link O2–S1: C1 sem vídeo ≈ 6,9 s, regresso ao caminho direto sem perdas) no CORE, 2026-11-10.
- [ ] E2: ensaio 7 no CORE (dois servidores; depende do `S2` nas topologias).
- [x] E2: ensaios 4, 5, 6, 8 e 9 repetidos no CORE com a D30 e ensaio 10 novo (cenário 2, `netem` no S1 para o O2: o O2 passa para o caminho de 2 saltos via O1 uma só vez), 2026-11-10; todos passaram.
- [ ] E2: perdas na métrica (sondas UDP por vizinho, custo estilo ETX em `_link_delay`); opcional.
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
| 2026-11-10 | **Ensaios da D30 no CORE: todos passaram.** Cenário 2 (logs `2026-11-10-9`): ensaio 4 com os caminhos de 2 saltos (latências empatadas, decidem os saltos); ensaio 10 novo, com `netem` de 20 ms no S1 para o O2: o O2 passa **uma só vez** para o caminho de 2 saltos via O1 (C1 com 1 imagem perdida) e volta ao direto uma só vez depois de removido o atraso; ensaio 5: o O2 **repara via O1 sem `WITHDRAW`** (com a D21 retirava), C1 com 130 imagens perdidas (≈ 6,5 s, quase só deteção; antes 138). Cenário 3 (logs `2026-11-10-10`): ensaio 8, o O5 troca do O3 para o O7 uma só vez (confirmado com `tcpdump` no r9), sem perdas, e mantém o O7 sem o atraso; ensaio 6, queda do O7: o O5 repara via O3 em 8 ms sem `WITHDRAW` (O10 0, O11 1 imagem perdida), o O8 volta ≈ 2 s depois do reinício do O7; ensaio 9, `WITHDRAW` em 3 ms e os 5 clientes de volta em ≈ 1–2 s. Detalhe em `docs/ETAPA2.md`. As trocas da seleção normal não ficam no log; foram deduzidas dos contadores e do `tcpdump`. |
| 2026-11-10 | **Latência acumulada como métrica principal (D30, substitui a D21).** `routing.py`: custo da ligação isolado em `_link_delay` (RTT/2, mínimo 0,1 ms, 1 s sem RTT; parâmetros `min_link_delay` e `unknown_link_delay`, validados); `_select` escolhe a menor latência com histerese e banda de empate (saltos e ID só desempatam); condição de viabilidade `(época, atraso, saltos)` em todas as seleções e na reparação (`RouteEntry.feasibility`, substitui `feasible_below`); anúncio não viável do montante conta como perda; o nó anuncia a época do montante usado. Testes: 4 testes do contrato antigo atualizados ("menos saltos ganha" passa a "menor latência ganha"; a época nova só é propagada com a cópia do montante) e 20 novos, incluindo uma rede simulada de 7 nós (40 sementes × 600 passos) sem ciclos, que encontra um ciclo se a condição de viabilidade for desligada. Suite: 138 testes, todos passam. Atualizados `protocolo.md` §3.7, §5, §6 e §7, `ETAPA2.md`, `ETAPA2_NOTAS.md`, README. Validado só em localhost; ensaios no CORE por repetir. |
| 2026-11-10 | **Verificação do E1 depois das alterações do E2.** 118 testes OK; `ott` ensaiado de ponta a ponta com o CLI novo (registo no bootstrapper, `--stats`, `Ctrl+C` em 0,3 s com código 0 e `desligado` imediato no vizinho); `cenario3.json` validado contra o XML (todos os IPs pertencem ao nó do CORE indicado, 12 arestas = Figura 1). Reposta a proteção contra exceções em handlers e o `ERROR` próprio de nós que não são bootstrapper, com 7 testes de robustez (D29), que tinham ficado só num `git stash`. Novo `docs/REVISAO_E2.md` com a revisão cruzada fechada. |
| 2026-11-10 | **Ensaios 3 e 5 da Etapa 2 no CORE.** Ensaio 3 (cenário 1): sair o C1 deixa o C2 a 20/s sem perdas e o O2 a enviar uma só cópia; sair o C2 faz o S1 deixar de emitir. Ensaio 5 (cenário 2): com o link O2–S1 em baixo, o O2 deteta por timeout em ≈ 5,5 s, retira e reaprende pelo O1; o C1 fica ≈ 6,9 s sem vídeo (138 imagens); ao repor o link, o O2 volta ao caminho direto sem nenhuma imagem perdida. `RPF=0` em todos os nós. Falta só o ensaio 7. Logs em `~/uni/esr/ott-logs/2026-11-10-7` e `-8`. |
| 2026-11-10 | **Ensaio 9 no CORE repetido com a topologia completa (D26–D28).** Os 11 nós com os vizinhos certos; três reinícios do servidor, com os 5 clientes de volta em 1,2–2,9 s e uma cópia por ligação depois de cada um. `descartados=0` e `RPF=0` em todos os nós e `perdidos=0` em todos os clientes. Substitui, para o ramo O6/O8/O9, a sessão anterior em que o C2 arrancou como O9. Logs em `~/uni/esr/ott-logs/2026-11-10-6`. |
| 2026-11-10 | **Ensaio 9 no CORE com a D28: três reinícios do servidor.** Em cada um, O4, O10 e O11 voltam a receber em ≈ 1–2 s; O2, O3, O5 e O7 sempre com `descartados=0` e `RPF=0`, e o O5 a 40 imagens/s, sem cópias a mais (L10 não se repetiu, incluindo uma troca de montante do `filme_b` logo a seguir a um arranque). Nesta sessão o nó C2 arrancou por engano com `-i O9`, por isso os resultados de O6, O8 e O9 não contam. Logs em `~/uni/esr/ott-logs/2026-11-10-5`. |
| 2026-11-10 | **Correção do L10 (D28).** A `RoutingTable` passa a meter os envios numa fila única ainda sob o bloqueio (`_transaction`) e a enviá-los por ordem fora dele, uma thread de cada vez (`_flush`); `activate`, `deactivate`, `handle_activate`, `handle_deactivate` e `handle_neighbor_up` deixam de chamar `node.send` diretamente. Teste de regressão `SendOrderTest` (duas threads, envio lento: o O7 recebia `DEACTIVATE` antes de `ACTIVATE`; falhava antes). Suite: 111 testes, todos passam; testes de rotas, integração e RPF repetidos 3×. Atualizados `ETAPA2.md` e `ETAPA2_NOTAS.md`. Validado só em localhost. |
| 2026-11-10 | **Ensaios 9 e 6 repetidos no CORE (cenário 3) com as correções D26 e D27.** Ensaio 9: o servidor reinicia e os dois fluxos voltam em ≈ 1–2 s em todos os clientes, sem rota fantasma (L9 validado). Ensaio 6: o O5 repara via O7 sem `WITHDRAW` e sem nenhuma imagem perdida no O10 e no O11; o O3 reiniciado religa-se em ≤ 1,2 s e o O4 volta a receber em ≤ 2,5 s. Defeito novo L10: depois do reinício do servidor, o O7 ficou com um ramo órfão para o O5 (≈ 20 imagens/s descartadas por RPF durante 1,5 min), por reordenação de `ACTIVATE`/`DEACTIVATE` entre threads; reproduzido localmente, por corrigir. Logs em `~/uni/esr/ott-logs/2026-11-10-4`. |
| 2026-11-10 | **Correção do L9 (D27).** Uma rota retirada por este nó guarda a hora da retirada (`RouteEntry.withdrawn_at`) e, durante `route_ttl`, recusa anúncios da época retirada ou anteriores; ao fim da retenção volta a aceitar qualquer época. O `AnnouncementPublisher` começa as épocas na hora do arranque em ms (`first_epoch`, por omissão), por isso um servidor reiniciado é aceite logo. Substitui a aceitação de qualquer época da D22. Testes novos: anúncio antigo depois do `WITHDRAW` é recusado (falhava antes), época maior aceite, época antiga aceite ao fim da retenção, épocas do publicador a partir do relógio e maiores num reinício, `first_epoch` inválido; teste de integração do reinício passa a exigir épocas maiores. Suite: 110 testes, todos passam; testes de rotas e integração repetidos 3×. Atualizados `protocolo.md` §3.7 e §7, `ETAPA2.md`, `ETAPA2_NOTAS.md`. Validado só em localhost; ensaio 9 por repetir no CORE. |
| 2026-11-10 | **Ensaios 8 e 9 da Etapa 2 no CORE (cenário 3).** Ensaio 8 passou: com 20 ms de atraso no caminho do O5 para o O3, o O5 troca para o O7 uma só vez, sem oscilar durante ≈ 4 min, sem nenhuma imagem perdida e com `RPF=0`; sem o atraso mantém o O7 (histerese). Ensaio 9 passou com um defeito: depois de o O1 cair, o `WITHDRAW` chega aos clientes em 3 ms; reiniciado, o `filme_b` volta em ≈ 1 s, mas o `filme_a` só ≈ 10 s depois, porque um ciclo fantasma O6 ↔ O7 (anúncios antigos aceites por rotas sem caminho, D22) rejeitou a época 1 até expirar o tempo de vida (30 s). Novo defeito L9, por corrigir. Logs em `~/uni/esr/ott-logs/2026-11-10-3`. |
| 2026-11-10 | **Ensaio 6 da Etapa 2 no CORE (cenário 3), com a correção D26: passou.** Ctrl-C no O3 com o O5 a receber: o O5 repara a rota via O7 no mesmo milissegundo, sem `WITHDRAW`; o O7 passa a 40 → 80/s e o O5 recebe pelo O7 com `descartados=0` e `RPF=0` em todos os nós. Falha no vídeo: O10 sem perdas, O11 com 1 imagem perdida. O O3 reiniciado religa-se em ≤ 1,3 s e o O4 volta a receber em ≈ 2 s; o O5 mantém o montante O7 (D21). Logs em `~/uni/esr/ott-logs/2026-11-10-2`; detalhe em `docs/ETAPA2.md`. |
| 2026-11-10 | **Correção do L7 (D26).** O datagrama de vídeo passa à versão 2, com o id do nó que o envia (campo `salto`, reescrito em cada salto); o `OverlayForwarder` identifica o vizinho de entrada por esse id em vez do IP de origem e descarta ids que não são vizinhos. Testes novos: regressão com o montante a enviar de outro IP e id não vizinho (`test_forwarding.py`, falhavam antes da correção), ida e volta do salto, versão 1 e salto truncado (`test_streaming.py`). Suite: 105 testes, todos passam. Atualizados `protocolo.md` §3.6 e §6, `ETAPA2.md` (L7), README. Validado só em localhost; ensaio 6 por repetir no CORE. |
| 2026-11-10 | **Cenário 3 completo no CORE (análise dos logs `/tmp/ott-O*.log`).** Com os 11 nós e 5 clientes (4 × `filme_a`, 1 × `filme_b`), a árvore está correta: uma cópia de cada fluxo por ligação overlay (O2 e O3 a 40 → 60/s, O5 40 → 40 via O3, O7 20 → 40, O6 20 → 20), clientes a 20/s sem perdas, `descartados=0` e `RPF=0`. Na saída dos nós, a queda do O3 mostrou a reparação rápida no controlo (O5 → O7 sem `WITHDRAW`, no mesmo ms) mas o O5 descartou todos os dados do O7 como origem não vizinha: **limitação L7 confirmada** (rotas verificadas: o UDP do O7 sai com origem 10.0.5.1, o O5 conhece-o como 10.0.4.2), por corrigir antes de repetir o ensaio 6. Detalhe em `docs/ETAPA2.md`. |
| 2026-11-10 | **`docs/testes-core.md` recriado** (não existia; continua no `.gitignore`): comandos de cada nó do CORE para os cenários 1, 2 e 3 (tabela nó do CORE → id overlay → IPs, estratégia 1 nos clientes dos cenários 1 e 2, que não alcançam o bootstrapper sem routers; `-b 10.0.18.20` no cenário 3), leitura do `--stats`, ensaios 1–9 da Etapa 2 com a ação e o que observar, e ações da Etapa 1. Comandos deduzidos do código e dos XML, ainda não corridos tal como estão escritos. |
| 2026-11-10 | **Cenário 3 no CORE com a atribuição da Figura 1 (primeira observação, só O2/r1).** O2 liga-se a O1, O3 e O7; recebe 40/s (dois fluxos, uma cópia de cada no troço O1–O2) e encaminha 60/s (`filme_a` para os dois ramos, `filme_b` só para um), sem descartes. Restantes nós por analisar. |
| 2026-11-09 | **Cenário 3 alinhado com a Figura 1.** Verificado que o `cenario3.json` é consistente com o `cenario3.xml` (nós e IPs) e que as 12 ligações overlay coincidem com a figura; o underlay do XML é isomorfo ao da figura. A atribuição overlay→CORE passou a ser a da figura (D13): O4=C8, O5=r9, O6=r10, O7=r5, O9=C4, O10=C6, O11=C7 (O1, O2, O3 e O8 mantêm-se). JSON validado contra o XML e o `BootstrapService`. Ensaios de cenário 3 da secção 7 ficam como históricos da atribuição anterior. |
| 2026-11-09 | **Ensaio 4 da Etapa 2 no CORE (cenário 2).** Com C1 (atrás de O2) e C2 (atrás de O1) subscritos, cada ramo segue o caminho de 2 saltos (S1→O2→C1 e S1→O1→C2) e a ligação O1–O2 não transporta dados (em O1 e O2, `encaminhados` = `recebidos`). S1 envia uma cópia por ramo (40/s). Clientes a 20/s sem perdas, jitter ≤ 0,13 ms; `descartados=0` e `RPF=0` em todos os nós, incluindo os que têm 3 interfaces. |
| 2026-11-09 | **Primeiros ensaios da Etapa 2 no CORE (cenário 1).** S1 – O2 – {C1, C2}, nós arrancados em terminais do `core-gui`. Ensaios 1 e 2 passaram: uma cópia por pacote no troço S1–O2 (20/s), O2 replica exatamente 2× para C1 e C2, cada cliente recebe 20/s, sem novas perdas, jitter 0,06–0,12 ms, `descartados=0` e `RPF=0` em todos os nós. Os `perdidos` acumulados (≈ 435) vêm de antes da janela medida, com `reinicios=1`: são imagens emitidas sem caminho ativo, não perdas na rede (nota em `docs/ETAPA2.md`, "Resultados no CORE"). |
| 2026-11-09 | **Etapa 2, passos 8 e 9.** Retirado `RouteEntry.seen_announcements` (só era escrito e crescia sem limite; a deteção de cópias repetidas e antigas é feita pela época e pelos candidatos); testes dos valores por omissão passam a cobrir `candidates`, `local_subscribers`, `epoch` e `delay`. Revisão final da documentação face ao código: `protocolo.md` (§2, §6, §7), `ETAPA2.md` (ponto da situação, secções 1–3, lacunas, ensaio 6 substituído pela reparação rápida no cenário 3, estado face ao critério de conclusão), `ETAPA2_NOTAS.md`, este ficheiro (resumo, D15, interface) e `AGENTS.md`. Suite: 101 testes, todos passam. Nada ensaiado no CORE. |
| 2026-11-09 | **Etapa 2, passo 7.** Tempo de vida dos candidatos: `RoutingTable(route_ttl=...)`, opção `--route-ttl` (por omissão 6 × `--announce-interval`, tem de ser maior que o intervalo); thread que esquece candidatos sem anúncios e trata o montante expirado como uma perda (reparação rápida ou `WITHDRAW`); cópias repetidas renovam o tempo de vida; `--announce-interval` passa a ser validado em todos os nós; o caminho de erro do bootstrapper também fecha a tabela. 9 testes novos em `test_routing.py` e 4 em `tests/test_cli.py` (novo). Suite: 101 testes, todos passam; testes de rede repetidos 3×. Atualizados README, `protocolo.md` (§3.7 e §5), `ETAPA2.md`, `ETAPA2_NOTAS.md`. Nada ensaiado no CORE. |
| 2026-11-09 | **Etapa 2, passos 5 e 6.** Tabela de candidatos por vizinho em `RouteEntry.candidates`; seleção por saltos, depois atraso acumulado (campo opcional `delay` no `ANNOUNCE`, RTT/2 por ligação) com histerese de 20 % e 1 ms, e menor ID sem RTT; o montante atual mantém-se numa época nova até enviar a sua cópia (D21, resolve L4). Nova mensagem `WITHDRAW`; perda do montante tenta reparação rápida com condição de viabilidade e, sem alternativa, retira a rota, desfaz os ramos e envia `WITHDRAW`; rota sem caminho aceita qualquer época (D22, resolve L2 e a retirada de L3). 13 testes de unidade novos, validação das mensagens novas e 1 ensaio de reinício do servidor com nós reais. Suite: 88 testes, todos passam; testes de rede repetidos 3×. Documentação atualizada (`protocolo.md` §3.7, `ETAPA2.md`, `ETAPA2_NOTAS.md`). Nada ensaiado no CORE. |
| 2026-11-09 | **Etapa 2, passos 3 e 4.** Subscrições agregadas por ramo (D24): `RouteEntry.subscriptions` desaparece, `destinations` passa a ser a fonte de verdade, `ACTIVATE`/`DEACTIVATE` só nas transições 0↔1, um só par numa troca de montante; `subscriber_id` opcional no protocolo (`OPTIONAL` em `protocol.py`). RPF estrito no `OverlayForwarder` (D23) com `RoutingTable.upstream_of` e contador `rpf_dropped` em `--stats`. Novo `tests/test_forwarding.py` (4 testes) e testes de rotas reescritos para a agregação. Atualizados `protocolo.md`, `ETAPA2.md` e `ETAPA2_NOTAS.md`. Suite: 74 testes, todos passam; testes de rede repetidos 3×. Nada ensaiado no CORE. |
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
