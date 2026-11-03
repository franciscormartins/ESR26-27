# ESTADO DO PROJETO – Elemento 1 (Overlay e protocolo de controlo)

> Ficheiro vivo: é atualizado sempre que algo é feito. Serve para saber o ponto da situação,
> relembrar as decisões e preparar as conversas com os docentes.
> **Última atualização: 2026-11-02 (checkpoint 1: ver `docs/CHECKPOINT1.md`).**

## 1. Resumo em 30 segundos

- Overlay aplicacional em **Python 3 (só biblioteca standard)**: cada nó é o programa `ott`, liga-se
  por **TCP** aos vizinhos, mantém-nos com PING/PONG, deteta quedas, mede o RTT de cada vizinho e,
  opcionalmente, pede vizinhos substitutos ao bootstrapper.
- O overlay constrói-se de duas formas: **estratégia 2** (`ott -b <bootstrapper>`: o servidor lê um
  ficheiro de topologia e devolve os vizinhos) e **estratégia 1** (`ott viz1 viz2`: vizinhos à mão).
- **Estado:** a Etapa 2 já origina anúncios periódicos, aprende e propaga rotas por fluxo,
  e encaminha ativações/desativações idempotentes pelo caminho de montante. Os testes cobrem
  estes comportamentos; ainda falta validar a reação a falhas no CORE e implementar o plano de dados.
  **Elemento 1 concluído:** todas as funcionalidades estão implementadas e foram ensaiadas no CORE (cenário 2 e
  cenário 3 com 11 nós) e medidas com 10 repetições (secção 7). Os ensaios acharam e corrigiram 4 defeitos
  (D17–D19 e o contador de défice); ver secção 7 e o registo de alterações.
- Falta ainda, fora do E1: validação da adaptação a falhas das rotas no CORE, encaminhamento dos dados (E2) e streaming (E3).
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
underlay do cenário 3 a funcionar (routers FRR, OSPF); comandos de arranque por nó (`docs/testes-core.md`).

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
| D13 | Cenário 3: underlay com routers FRR/OSPF e nós overlay também nos routers | A Figura 1 põe nós overlay em routers; só há 10 hosts para 11 nós overlay. Atribuição overlay→CORE **por confirmar**. |
| D14 | Routers com FRR (não Quagga) e `frr.conf`/`frrboot.sh` próprios no XML | O Quagga instalado não tem `vtysh`, e o CORE 9.2.1 gera config que o FRR 10 rejeita (ver `docs/testes-core.md`). |
| D15 | Decidido implementar a Estratégia 1 do enunciado. Estado de rota indexado por `(servidor, fluxo)`, com vizinho de montante, destinos a jusante, subscritores, custo em saltos, época e anúncios vistos. `fluxo` identifica o conteúdo/stream multimédia (ex.: `filme_a`). | Anúncios do servidor constroem as rotas; pedidos dos clientes abrem e fecham ramos partilhados. Roadmap em `docs/ETAPA2.md`; modelo em `docs/protocolo.md`. |
| D16 | Protocolo E2 definido: `ANNOUNCE(server_id, flow, epoch, metric)`; `ACTIVATE`/`DEACTIVATE(server_id, flow, subscriber_id)`. Custo em saltos, época crescente por fluxo, IDs overlay para servidor/cliente e nome estável para fluxo. | Suporta vários fluxos e servidores; handlers dos três tipos implementados. |
| D17 | Quem fecha uma ligação duplicada envia `BYE` com `reason: "duplicate"`; quem o recebe retira a ligação **sem `down`** e espera até 3 s pela vencedora (`DUPLICATE_GRACE`), que é adotada sem novo `up` | Num arranque simultâneo o outro lado fechava, depois de nós a darmos como `up`, a ligação perdedora: o E2 via `down`+`up` falsos (apagaria rotas à toa). Alternativas rejeitadas: só o nó de menor id liga (quebra o D6) ou adiar sempre todos os `down` (esconde quedas reais). |
| D18 | `TCP_NODELAY` em todas as ligações entre vizinhos | O algoritmo de Nagle retinha as mensagens pequenas (PING/PONG cruzados) à espera do ACK: RTT de 37 ms inicial nos clientes e 60–80 ms com atraso de 20 ms/lado (esperado 41 ms). Com NODELAY: 41,1–41,6 ms. O RTT é a métrica do E2, por isso tem de ser fiável. |
| D19 | Ligar a um substituto logo que é proposto, e repor o contador de défice ao atingir X vizinhos | Poupava-se o ciclo de 2 s; e o contador antigo podia contar défice já resolvido (artefacto visto nas medições). |

## 4. Arquitetura (resumo)

```
src/ott/protocol.py     formato, tipos e validação das mensagens
src/ott/node.py         OverlayNode: ligações, HELLO, PING/PONG, RTT, eventos, substitutos
src/ott/bootstrapper.py BootstrapService: vizinhos e substitutos a partir do ficheiro JSON
src/ott/cli.py          comando `ott` (-b, --serve, --min-neighbors, --stats ...)
src/ott/routing.py      modelo de rotas, publicador e aprendizagem/propagação de ANNOUNCE
src/ott/streaming/      (vazio) E3
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
Continuam pendentes o formato de `ROUTE_TEST`, a validação no CORE da adaptação a falhas e o
encaminhamento dos dados.

## 6. Como testar

**Suite completa (60 testes, ~16 s):**

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

**Suite completa (46 testes, cerca de 7 s):**

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

Cobrem: validação do protocolo, overlay por bootstrapper e recuperação, eventos up/down, RTT, ligação
duplicada, vizinho calado (timeout), mensagem inválida, substituto, alvos expirados e tentativas em paralelo.
`tests/test_routing.py` cobre o modelo, publicação, aprendizagem por saltos, supressão de
duplicados/anúncios antigos, melhoria de caminho, subscrições partilhadas e reação local a
falhas e vizinhos novos. A integração ponta a ponta no overlay e o encaminhamento dos dados
continuam por testar.

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
| 11 | Overlay completo no cenário 3 (11 nós, ping underlay 0 % perda) | 1 | os 11 nós com exatamente os vizinhos do JSON, RTT 1,1–2,4 ms, 0 `desligado` |

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

## 9. Por fazer

**Elemento 1**
- [x] Ensaiar no CORE: timeout/link em baixo, RTT com atraso, substituto, arranque simultâneo, bootstrapper tardio (2026-11-02, 10 repetições).
- [x] Ensaiar o overlay no cenário 3 (11 nós) com a atribuição proposta (2026-11-02).
- [x] Passar a especificação e os resultados para o relatório (`docs/relatorio/e1_overlay.tex`, incluída em `main.tex`).
- [ ] **Tu:** confirmar a atribuição overlay→CORE do cenário 3 (se mudar, só se altera o `cenario3.json` e o `testes-core.md`; os resultados mantêm-se válidos para a estrutura).
- [ ] **Grupo:** rever `docs/protocolo.md` (inclui o `BYE reason=duplicate`) e o texto `e1_overlay.tex`; o `babel` em português do `main.tex` não está instalado nesta máquina (compila noutra com `texlive-babel-portuguese`).
- [ ] Decidir se refazemos o cenário 2 com 3 intermédios, como no enunciado.

**Outros elementos (para saber o que depende de nós)**
- [x] E2 (controlo): tabela de rotas, anúncios, ativação/desativação e reação local a alterações de vizinhos.
- [ ] E2: ensaios de falhas/recuperação no CORE e encaminhamento dos dados.
- [ ] E3: servidor/cliente de vídeo, plano de dados, cenários e medições.

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
