# Auditoria técnica — ESR TP2 (OTT): requisitos mínimos, Etapa 2 e Etapa 3

> Auditoria só de leitura, feita a 2026-11-18 sobre o `main` em `fd5d9ba`. Não alterei nem criei ficheiros no repositório durante a auditoria (este ficheiro foi escrito depois, a pedido). Corri a suite (`PYTHONPATH=src python3 -m unittest discover -s tests -t .`): **245 testes, todos passam, nenhum foi saltado** (≈ 48 s).
>
> Legenda de evidência: **[C]** implementado no código · **[T]** coberto por testes automáticos · **[L]** validado em integração ou localhost (nós reais) · **[CORE]** validação no CORE *só documentada* (os logs estão fora do repositório, em `~/uni/esr/ott-logs` e `~/uni/esr/tp2/ott-logs`; confirmei que as pastas existem, mas não reanalisei o conteúdo) · **[D]** só documentado ou planeado.

## Requisitos do enunciado (transcrição fiel)

Fonte: `docs/enunciado.pdf`, "Descrição detalhada", a seguir a *"A lista mínima de requisitos a considerar é a seguinte:"*

1. Um nó overlay é um programa que se executa manualmente numa máquina da rede de suporte.
2. Os nós podem ser criados e destruídos dinamicamente (início e fim de execução).
3. Ao juntar-se, o nó estabelece e mantém conexões de transporte (TCP ou UDP) com X nós, que serão os seus vizinhos.
4. Ao arrancar, o nó tem de conhecer pelo menos um outro nó (pode ser o servidor), que o ajuda a obter a lista de X vizinhos.
5. Os nós constroem e mantêm uma tabela de rotas por fluxo (Fluxo, Custo, Origem, Destinos, Estado).
6. A topologia deve suportar rotas alternativas e vários clientes em simultâneo.
7. A rede deve ser monitorizada e as rotas mantidas e atualizadas dinamicamente, conforme a entrada e saída de nós e as alterações nas condições da rede.
8. Os nós reenviam todos os dados de acordo com essa tabela.
9. Pelo menos dois servidores, cada um com pelo menos dois fluxos de vídeo distintos.
10. Uso de métricas da rede na construção das rotas.

**Nota importante:** as Etapas 0 a 4 são *"sugeridas"* e o enunciado diz que *"não precisam ser seguidas obrigatoriamente de forma sequencial"*. A Etapa 3 **não faz parte** dos 10 requisitos mínimos, mas conta para a avaliação ("Gestão dos fluxos", 25 %, e "Protocolo aplicacional de controlo", 15 %).

---

# 1. Resumo executivo

- **Requisitos mínimos: 10 de 10 cumpridos no código.** Todos têm implementação [C]. Nove têm testes automáticos diretos [T]. O requisito 9 (dois servidores × dois fluxos distintos) tem testes estruturais da topologia e um teste de integração com dois servidores **no mesmo fluxo**, mas nenhum teste automático com 2 × 2 fluxos distintos a circular ao mesmo tempo. O que falta é sobretudo documentação e validação, não funcionalidade.
- **Etapa 2: consolidada.** Segue a Estratégia 1 do enunciado e vai além dela:
  - métrica de latência acumulada (RTT/2) com histerese;
  - condição de viabilidade ao estilo do Babel;
  - `WITHDRAW` com reparação rápida e tempo de vida;
  - subscrições agregadas e RPF estrito.

  Tem um teste de ausência de ciclos com rede simulada (40 sementes × 600 passos). Os pontos fracos são a **documentação desatualizada e contraditória** (`ESTADO.md` e `ETAPA2.md` ainda dizem "falta CORE" e "falta S2") e algumas limitações conhecidas da métrica.
- **Etapa 3: só planeada.** `ROUTE_TEST` existe apenas como constante em `src/ott/protocol.py:41`. Não tem formato validado, handler, CLI, testes nem documentação de interações. `docs/protocolo.md:43` diz "a definir (Etapa 3); ainda não implementado". Existem ferramentas de observação que cobrem parte do objetivo (`--live`, `--status-file`, painel), mas não são uma mensagem de teste gerada pelo servidor.

---

# 2. Matriz dos 10 requisitos mínimos

| # | Requisito | Estado | Evidência concreta | Lacunas, limitações, contradições | Prioridade |
|---|---|---|---|---|---|
| 1 | Nó = programa executado manualmente | **Cumprido** | [C] `bin/ott`, `src/ott/cli.py:main`. [T] `tests/test_cli.py` (15 testes). [CORE] `ESTADO.md` §7 (cenários 1–3) | — | Baixa |
| 2 | Nós criados e destruídos dinamicamente | **Cumprido** | [C] `OverlayNode.stop` envia `BYE`; `SIGTERM` tratado como Ctrl+C (`cli.py:_install_signal_handlers`); reconexão em `_maintain_loop`/`_connect_missing`; reação das rotas em `handle_neighbor_down`/`up`. [T] `test_overlay.test_bootstrap_builds_overlay_and_recovers`, `test_integration.test_restarted_server_is_relearned_and_branch_reopens`, `DuplicateCloseTest`. [L] `tests/medicoes.py` (10 repetições). [CORE] ensaios 4, 6, 7, 8 e 9 do cenário 5 | Um nó novo **tem de constar** do ficheiro de topologia do bootstrapper (`BootstrapService.neighbors_of` devolve `None` → `ERROR`). O bootstrapper nunca expira nós registados (`ESTADO.md` §8) | Baixa |
| 3 | Conexões TCP mantidas com X vizinhos | **Cumprido (com nuance)** | [C] TCP full-duplex, `HELLO`, `PING`/`PONG` a cada 2 s, morte após 6 s (`node.py`); `--min-neighbors X` com `REPLACE`. [T] `test_overlay` (eventos, timeout, substitutos, duplicados). [CORE] substituto em ≈ 7,2 s (`ESTADO.md` §7) | X é definido **pelo ficheiro de topologia**, não por um parâmetro global. `--min-neighbors` está a 0 por omissão (D10), por isso X só é imposto se for pedido. Os clientes ficam com 1 vizinho | Baixa |
| 4 | Conhecer ≥ 1 nó para obter os vizinhos | **Cumprido** | [C] `OverlayNode.bootstrap` (`REGISTER` → `NEIGHBORS`), `--serve`, estratégia 1 manual. [T] `test_overlay.test_unknown_node_refused`, `BootstrapServiceTest`, `test_register_to_non_bootstrapper_says_so`. [CORE] cenários 2, 3 e 5 | Há um só bootstrapper por nó e não há redundância (no cenário 5, o S2 é bootstrapper de si próprio). Topologia estática | Baixa |
| 5 | Tabela de rotas por fluxo (Fluxo, Custo, Origem, Destinos, Estado) | **Cumprido** | [C] `RouteEntry` (`routing.py:41`) indexada por `(origin, flow)`; estados `inativa`/`ativa`; exposta em `status.py:99` (JSON) e no separador "Rotas" do painel. [T] `RouteEntryTest`, `RoutingTableTest`, `test_integration.test_routes_store_neighbor_ids_and_hop_metric` | **Terminologia:** a "Origem" do enunciado (vizinho de entrada, ex.: O1 para O2) corresponde ao campo `upstream`; no código, `origin` é o **servidor**. O "Custo" está dividido em `metric` (saltos) e `delay` (latência, que é a métrica principal). O enunciado fala de "uma rota" porque assume um só fluxo; aqui há uma por `(servidor, fluxo)`, o que vai além do pedido. Sem `--status-file`/`--live`, a tabela não aparece em nenhum comando simples | Média (explicar no relatório) |
| 6 | Rotas alternativas e vários clientes | **Cumprido** | [C] Candidatos por vizinho (`RouteEntry.candidates`), `_select`, reparação rápida em `_lose_upstream`, ramos agregados. [T] `WithdrawRepairTest`, `RouteSelectionTest`, `DataPlaneIntegrationTest` (2 clientes). [CORE] cenário 2 ensaios 4 e 5, cenário 3 ensaio 6, cenário 5 ensaios 3–5 | — | Baixa |
| 7 | Monitorização e atualização dinâmica | **Cumprido** | [C] RTT por média móvel (α = 0,3), keepalive, `route_ttl` (6 × intervalo), `WITHDRAW`, reavaliação a cada anúncio. [T] `RouteTtlTest`, `test_first_rtt_sample_moves_route_once`, `test_rtt_oscillating_around_hysteresis_switches_at_most_once`. [CORE] ensaios 8 e 10 (`netem`) | A métrica só sente latência: perdas, jitter e largura de banda não contam. O RTT é medido sobre TCP de controlo, onde as perdas aparecem apenas como RTT maior. Uma ligação em baixo demora ≈ 6 s a ser detetada. Depois de um `WITHDRAW`, a rota só volta na ronda seguinte (até 5 s) | Média |
| 8 | Reenvio dos dados segundo a tabela | **Cumprido** | [C] `OverlayForwarder._receive_loop`/`_forward`: usa `active_destinations` e RPF estrito por `upstream_of`. [T] `StrictRpfTest` (7 testes, UDP real); `DataPlaneIntegrationTest` (uma cópia no troço S1–A e duas a partir de A). [CORE] cenário 1 ensaio 2, cenário 5 ensaio 1 | O id `salto` do datagrama não é autenticado (D26). Cada JPEG vai num só datagrama e sofre fragmentação IP: 10 % de perdas de pacotes dão ≈ 44 % de imagens perdidas (achado B3 em `ESTADO.md` §11) | Baixa (E2) / Média (E4) |
| 9 | ≥ 2 servidores × ≥ 2 fluxos distintos | **Cumprido (validação automática parcial)** | [C] A chave `(origin, flow)` é genérica; `--publish-flow` pode repetir-se; `AnnouncementPublisher` gere várias épocas. [T] `test_publish_once_announces_each_flow_with_independent_epoch`, `Scenario5Test.test_two_servers_with_two_distinct_flows_each_all_subscribed` (estrutural), `FailoverIntegrationTest` (2 servidores, **o mesmo fluxo**). [CORE] cenário 5, ensaios 1, 7 e 8 | **Nenhum teste automático** põe 2 servidores × 2 fluxos distintos a circular ao mesmo tempo com nós reais. Há contradições na documentação: `ETAPA2.md` passo 10 e `ESTADO.md` §9 ("segundo servidor S2", "ensaio 7") continuam por marcar, embora o cenário 5 os dê como feitos | **Alta** (documentação) / Média (teste) |
| 10 | Métricas da rede nas rotas | **Cumprido** | [C] `_link_delay` = max(RTT/2, 0,1 ms), ou 1 s sem RTT; latência acumulada no campo `delay` do `ANNOUNCE`; histerese de 20 % / 1 ms; saltos só desempatam. [T] `RouteSelectionTest` (13 testes), `FeasibilityTest`. [CORE] cenário 2 ensaio 10, cenário 3 ensaio 8, cenário 5 ensaios 3 e 8 | Só latência. O RTT inclui o processamento em Python (≈ 1–2 ms contra 0,1 ms no `ping`). No arranque, ligações ainda sem RTT valem 1 s, o que pode causar uma troca de montante assim que o RTT aparece | Média |

---

# 3. Auditoria da Etapa 2 — Construção das rotas para fluxos

## 3.1 Ponto a ponto face ao enunciado (Estratégia 1)

| Ponto do enunciado | Implementação | Testes | Avaliação |
|---|---|---|---|
| **Métrica de seleção** | Latência acumulada (D34); saltos e depois ID como desempate (`_select`, `routing.py:526`) | `test_lower_delay_wins_even_with_more_hops_whatever_the_order`, `test_delay_tie_is_broken_by_fewer_hops`, `test_total_tie_is_broken_by_lowest_id_whatever_the_order` | ✅ Vai além dos "saltos" sugeridos |
| **Anúncios periódicos do servidor** | `AnnouncementPublisher` (`routing.py:810`): primeira ronda imediata, depois a cada `--announce-interval` (5 s) | `test_start_publishes_periodically_and_stop_stops_it` | ✅ |
| **Conteúdo do anúncio** | `server_id`, `flow`, `epoch`, `metric` (obrigatórios) e `delay` (opcional), validados em `protocol.py:81` | `test_protocol.test_valid_messages`/`test_invalid_messages` | ✅ Corresponde a "servidor, fluxo, métrica, etc." |
| **Propagação controlada** | `broadcast(..., exclude={neighbor, new_upstream})`. Só propaga quando há época nova, troca de montante ou mudança de saltos (`routing.py:716`). Ignora cópias repetidas (candidato igual), épocas antigas e o próprio anúncio quando volta ao servidor | `test_learns_and_propagates_with_incremented_cost_excluding_sender`, `test_duplicate_old_and_non_improving_paths_are_not_forwarded`, `test_own_announcement_is_ignored`, `test_integration.test_flood_does_not_return_announcement_to_sender` | ✅ Não há campo TTL/hop-limit na mensagem: a terminação depende da época, da propagação só quando algo muda e da viabilidade. Isto está correto, mas não está explicado em `protocolo.md` |
| **Métrica atualizada por salto** | Quem recebe soma +1 salto e RTT/2 da ligação (`_cost`); quem reenvia anuncia o seu próprio custo | `test_delay_is_accumulated_and_announced` | ✅ Equivale ao enunciado: o incremento é feito por quem recebe, não "antes de reenviar". Convém dizê-lo no relatório |
| **Tabela por servidor/fluxo** | `RouteEntry`: `flow`, `origin` (servidor), `upstream` (a "Origem" do enunciado), `metric`/`delay`, `destinations`, `state`, mais `epoch`, `candidates`, `feasibility` | `RouteEntryTest` | ✅ (ver a nota de terminologia no requisito 5) |
| **Prevenção de ciclos e repetições** | Split horizon; um destino a jusante nunca é montante (`_valid_candidate`, `handle_announce:661`); condição de viabilidade `(época, atraso, saltos)`; retenção após `WITHDRAW` (D27); RPF no plano de dados | `LoopFreedomTest.test_upstream_graph_never_has_cycles` (que, segundo `ESTADO.md`, encontra um ciclo se a viabilidade for desligada), `test_withdrawn_route_rejects_stale_announcements` | ✅ É o ponto mais forte do trabalho |
| **Estado inativo até ser ativado** | `_refresh_state`: `ativa` só com procura **e** caminho válido | `test_local_activate_and_deactivate_follow_upstream` | ✅ |
| **Ativação reversa pela "Origem"** | `activate` e `handle_activate` enviam `ACTIVATE` ao `upstream` e só na transição 0→1; `DEACTIVATE` na transição 1→0 | `test_branches_are_aggregated_only_first_and_last_go_upstream`, `test_activation_from_upstream_is_rejected`, `test_integration.test_activation_builds_branch_and_deactivation_removes_it` | ✅ |
| **Sem clientes, sem tráfego** | `OverlayForwarder._forward` com `incoming=None` e sem destinos devolve 0 | `DataPlaneIntegrationTest`, passo 3 (`source.sent == 0`, `server.forwarded` inalterado). [CORE] "S1 deixa de emitir em < 0,2 s" | ✅ Os anúncios de controlo continuam a circular, como é esperado |

## 3.2 Aspetos avaliados com evidência

**Latência como métrica principal, saltos como desempate.** ✅ [C][T]. Os saltos também servem de componente da condição de viabilidade.

**RTT/2, histerese e RTT desconhecido.** ✅ [C][T].
- Sem amostra de RTT, a ligação vale `unknown_link_delay = 1 s` (finita), por isso no arranque a escolha comporta-se como contagem de saltos (`test_link_without_rtt_costs_unknown_link_delay`).
- Um anúncio sem `delay` tem custo infinito (`test_announcement_without_delay_is_learned_and_forwarded_without_it`).
- A histerese tem duas zonas (`_tie_band`, metade da histerese, mais a histerese completa), testada em `test_gain_between_tie_and_hysteresis_keeps_current_upstream`.
- **Risco residual:** quando o RTT de uma ligação muda sem chegar nenhum anúncio novo, a reavaliação só acontece no próximo `ANNOUNCE` de qualquer vizinho. Na prática, isso é no máximo um intervalo de 5 s.

**Vários fluxos e vários servidores.** ✅ [C][T] parcial. Ver o requisito 9: falta um teste de integração 2 × 2 com fluxos distintos. O cenário 4 (D30) aparece em `ESTADO.md` como "ainda não aberto no CORE", mas o registo de 2026-11-10 diz que o modo CORE foi validado com o cenário 4 (`G1, A1–A3…`). É mais uma contradição a resolver.

**Subscrições agregadas, sem duplicação de tráfego.** ✅ [C][T][L][CORE]. `destinations` é um conjunto por vizinho, e `active_destinations` deduplica e exclui a ligação de entrada. `test_two_clients_share_common_link_and_branches_close_independently` mede `server.forwarded == FRAMES` e `relay.forwarded == 2*FRAMES`.

**RPF, `WITHDRAW`, TTL, falhas, recuperação e risco de ciclos.**
- RPF estrito: ✅ [T] `StrictRpfTest`. Uma troca de montante provoca uma falha curta (documentada).
- `WITHDRAW`: ✅ [T] `WithdrawRepairTest` (9 testes). Em cascata, limpa os ramos a jusante.
- "TTL": no código é o **tempo de vida dos candidatos** (`route_ttl`), não um TTL de mensagem. ✅ [T] `RouteTtlTest` (8 testes). Também serve de prazo de retenção depois do `WITHDRAW`.
- Ciclos: ✅ [T] propriedade verificada com rede simulada.
- **D36** (`_only_neighbor`): é uma exceção heurística à viabilidade, justificada porque uma folha não pode fechar um ciclo. Avalia `neighbor_ids()` no instante em que o anúncio chega. Não encontrei um contra-exemplo, mas a exceção não é coberta pelo `LoopFreedomTest`, cuja topologia não tem folhas com um só vizinho. Fica como risco **plausível e não demonstrado**.
- **Observações de código, de baixo impacto:**
  - As entradas de rota **nunca são removidas**: `RoutingTable.remove` não é chamado fora dos testes. Fluxos de servidores que desapareceram ficam na tabela sem caminho. `sources_of` filtra-os, mas a tabela e o `status.json` crescem.
  - Um anúncio vindo de um vizinho que está em `destinations` é descartado antes de atualizar o candidato (`routing.py:661`). Se esse vizinho sair dos destinos, o seu candidato só reaparece na época seguinte.
  - A propagação não reage a mudanças **só de atraso** com o mesmo montante e os mesmos saltos (`routing.py:716`). Os vizinhos só veem a latência nova na ronda seguinte.
  - `handle_activate` sobre uma rota desconhecida devolve `False` sem aviso a quem pediu. É improvável, porque quem pede recebeu antes o anúncio desse vizinho.

**Coerência entre código, testes e documentação.** ⚠️ Aqui está o principal problema:

| Documento | Afirmação | Realidade |
|---|---|---|
| `ESTADO.md` §1 | "188 testes automáticos"; "**Falta:** segundo servidor nas topologias e todos os ensaios no CORE" | 245 testes; os cenários 1, 2, 3 e 5 estão dados como ensaiados no CORE |
| `ESTADO.md` §6 | "Suite completa (188 testes)" | 245 |
| `ESTADO.md` §4 | `topologies/ cenarios 1-3` | Existem os cenários 1–5 |
| `ESTADO.md` §9 | "[ ] E2: segundo servidor S2" e "[ ] E2: ensaio 7" | A linha seguinte diz que o cenário 5 cobre o ensaio 7 |
| `ETAPA2.md` linhas 16–24 | "Ponto da situação (2026-11-09) … 111 testes … Falta o ensaio 7" | Desatualizado |
| `ETAPA2.md` §5, §6, passos 10, 11, 14 e critério de conclusão | "falta ensaiar no CORE", "falta abri-lo no CORE", "Estado face ao critério (2026-11-09)" | `ETAPA2.md:443` e `CENARIO5.md:8` dão os 9 ensaios do cenário 5 como passados a 2026-11-18 |
| `README.md` | `topologies/`: "cenários 1–4"; `testes-core.md` "não está no repositório" | Há 5 cenários; o ficheiro existe localmente, mas está no `.gitignore` |
| `ESTADO.md` §8 | Cita `docs/CHECKPOINT1.md` | Não existe no repositório |
| `ESTADO.md` D2, §9 | "E3" quer dizer *Elemento* 3 (streaming), não *Etapa* 3 | Ambíguo para quem lê o relatório |

**Conclusão da Etapa 2: CONSOLIDADA no código e nos testes, PARCIALMENTE consolidada na documentação.** A funcionalidade pedida pela Estratégia 1 está completa e coberta por testes unitários, de propriedade (ausência de ciclos) e de integração com nós reais. A validação no CORE está documentada em pormenor, mas não é reprodutível a partir do repositório, porque os logs estão fora dele. A documentação de estado contradiz-se em vários sítios, e um avaliador que leia `ETAPA2.md` primeiro conclui que a etapa não está validada.

---

# 4. Auditoria da Etapa 3 — Teste dos percursos no overlay

| Elemento pedido / verificado | Estado | Evidência |
|---|---|---|
| Tipo de mensagem | Só a constante | `MsgType.ROUTE_TEST = "ROUTE_TEST"` (`protocol.py:41`) |
| Formato validado | **Não** | Não há entrada em `REQUIRED`/`OPTIONAL`. Como está em `ALL_TYPES`, `validate()` **aceita qualquer `ROUTE_TEST`, com qualquer `data`** |
| Gerada pelo servidor | **Não** | Não existe gerador em `routing.py` nem em `cli.py` |
| Handler e reencaminhamento pela árvore ativa | **Não** | Não há `register_handler(MsgType.ROUTE_TEST, …)`. `OverlayNode._dispatch` regista-a em `debug` como "mensagem ROUTE_TEST ignorada" (`node.py:484`) |
| Controlo de duplicados e ciclos | **Não** | — |
| Log por nó / caminho acumulado | **Não** | — |
| CLI | **Não** | Não há nenhuma opção |
| Testes | **Não** | `grep ROUTE_TEST tests/` não dá resultados |
| Documentação | Só a intenção | `protocolo.md:43` ("a definir (Etapa 3); ainda não implementado"), `README.md:28`, `ESTADO.md:130` ("Continuam pendentes o formato de `ROUTE_TEST`") |

**O que existe e se aproxima do objetivo** (sem cumprir a letra do enunciado):
- `ott --live` mostra em cada nó "vem de X (N saltos desde S, D ms) → destinos".
- `--status-file` com eventos `montante`, `rota_nova`, etc.
- `TrafficStats` conta pacotes por vizinho de entrada e de saída.
- O painel do `ottctl` desenha as árvores por fluxo.

Isto é **observação passiva do estado das rotas e do tráfego**, não uma mensagem de teste gerada pelo servidor e reencaminhada pelas rotas. `ESTADO.md:417` regista que se decidiu **não** acrescentar um campo `path` ao `ANNOUNCE`; o caminho completo não é visível em nenhum nó.

**Conclusão da Etapa 3: APENAS PLANEADA.** O tipo está reservado. Não há formato, lógica, interface nem testes.

---

# 5. Melhorias recomendadas

## 5.1 Críticas para cumprir o enunciado e a avaliação

**M1. Implementar a Etapa 3 em versão mínima viável (ver 5.3).**
- *Problema:* a etapa está em falta e afeta "Gestão dos fluxos" e "Protocolo de controlo".
- *Componentes:* `protocol.py`, `routing.py` (ou um módulo novo `routetest.py`), `cli.py`, `status.py`, `tests/`, `protocolo.md`.
- *Benefício:* fecha a lacuna mais visível do trabalho.
- *Risco:* baixo, porque o tipo é novo e não toca na seleção de rotas.
- *Esforço:* médio.
- *A validar antes:* ver M8.

**M2. Uniformizar a documentação de estado.**
- *Problema:* as contradições da tabela em 3.2 (`ESTADO.md` §1, §4, §6, §9; `ETAPA2.md` ponto da situação, §5, §6, passos 10, 11 e 14, critério; `README.md`).
- *Componentes:* `ESTADO.md`, `ETAPA2.md`, `README.md`.
- *Benefício:* um avaliador vê o estado real; evita perguntas na defesa.
- *Risco:* nenhum.
- *Esforço:* baixo.
- *A validar antes:* confirmar no grupo quais ensaios do cenário 4 contam como feitos.

**M3. Criar uma matriz "requisito → evidência" no relatório.**
- *Problema:* a prova está espalhada por `CENARIO5.md` §2, `ETAPA2.md` e `ESTADO.md`. A ligação entre a "Origem" do enunciado e o campo `upstream` não está explicada.
- *Componentes:* `docs/relatorio/`, `CENARIO5.md`.
- *Benefício:* avaliação direta, e o modelo de rota fica explicado (Custo = latência + saltos, Origem = montante).
- *Risco:* nenhum.
- *Esforço:* baixo.
- *A validar antes:* nada.

## 5.2 Robustez e correção da Etapa 2

**M4. Teste de integração com 2 servidores × 2 fluxos distintos e nós reais.**
- *Problema:* o requisito 9 só tem prova automática estrutural.
- *Componentes:* `tests/test_integration.py` (com o mesmo padrão de IPs de loopback do `DataPlaneIntegrationTest`).
- *Benefício:* prova reprodutível sem CORE; deteta regressões no isolamento entre `(origem, fluxo)`.
- *Risco:* baixo (depende de aliases 127.0.0.x, como os testes atuais).
- *Esforço:* baixo a médio.
- *A validar antes:* tempo de execução da suite.

**M5. Ciclo de vida das entradas de rota.**
- *Problema:* `RouteEntry` nunca é removida (não há chamadas a `remove`), por isso servidores mortos ou fluxos retirados ficam na tabela para sempre.
- *Componentes:* `routing.py` (`expire`), `status.py`.
- *Benefício:* tabela e painel limpos em demonstrações longas.
- *Risco:* médio, porque interage com a retenção D27 (`withdrawn_at` tem de sobreviver pelo menos `route_ttl`).
- *Esforço:* baixo.
- *A validar antes:* apagar só depois do fim da retenção e sem subscritores locais.

**M6. Propagar mudanças significativas de latência dentro da mesma época.**
- *Problema:* `propagate` ignora mudanças só de `delay` (`routing.py:716`), por isso os nós a jusante só as veem na ronda seguinte.
- *Componentes:* `handle_announce`.
- *Benefício:* reação mais rápida a alterações das condições da rede (requisito 7).
- *Risco:* **médio-alto.** Mais anúncios, e qualquer mudança junto da viabilidade obriga a repetir o `LoopFreedomTest`.
- *Esforço:* médio.
- *A validar antes:* usar o mesmo limiar da histerese; medir o volume de controlo.

**M7. Alargar o `LoopFreedomTest` a folhas com um só vizinho (D36) e a reinícios do servidor.**
- *Problema:* a exceção da D36 e a retenção D27 não entram na rede simulada.
- *Componentes:* `tests/test_routing.py`.
- *Benefício:* reforça a prova de ausência de ciclos nos casos novos.
- *Risco:* baixo.
- *Esforço:* baixo.
- *A validar antes:* nada.

**M7b (opcional). Métrica com perdas.**
- *Problema:* perdas e jitter não contam, e o achado B3 mostra que as perdas pesam muito na qualidade.
- *Componentes:* `_link_delay`, mais sondas UDP por vizinho.
- *Benefício:* "métricas da rede" mais completas.
- *Risco:* médio (mais oscilação, interação com a histerese).
- *Esforço:* alto.
- *A validar antes:* se o docente valoriza isto acima do resto.

## 5.3 Etapa 3: implementação mínima viável

**M8. Decisões a fechar antes de implementar** (com o grupo ou o docente):
1. **Plano de controlo (TCP) ou plano de dados (UDP).** Pelo TCP é simples, fiável e reutiliza `node.send`. Pelo UDP testa o caminho real dos dados (`OverlayForwarder`), mas tem perdas e exige mudar o formato `rtp.py` v2. **Recomendo TCP**, seguindo exatamente `active_destinations` e o RPF por `upstream_of`, que são a mesma semântica do plano de dados.
2. **Só a árvore ativa, ou também as rotas inativas.** O enunciado diz "usando as rotas", o que é ambíguo. Recomendo um modo `ativa` por omissão e, opcionalmente, um modo `todas`, que inunda por quem tem esse nó como montante.
3. **Como disparar.** Um disparo periódico (`--route-test-interval S`) é simples e fácil de demonstrar. Um `SIGUSR1` ou um ficheiro de controlo para o `ottctl` dão mais controlo na demonstração.

**M9. Formato e validação.**
- `ROUTE_TEST {server_id, flow, test_id, path: [ids], ts, max_hops}` com predicados em `REQUIRED`.
- `path` limitado em tamanho; `ts` serve só para o servidor calcular o RTT, se houver resposta.
- *Problema que resolve:* hoje `validate` aceita qualquer `ROUTE_TEST`.
- *Componentes:* `protocol.py`, `test_protocol.py`, `protocolo.md` §2 e §3.
- *Esforço:* baixo.

**M10. Gerar, tratar e reencaminhar.**
- O servidor cria a mensagem para cada fluxo publicado.
- Cada nó:
  1. aceita a mensagem só se vier do `upstream` da rota (RPF);
  2. descarta duplicados por `(server_id, flow, test_id)` com uma cache limitada e com prazo;
  3. descarta se `len(path) >= max_hops` ou se o seu próprio id já está em `path`;
  4. regista um log `INFO` (`route_test S1:filme_a #42 via O2 caminho=[S1,O1,O2]`);
  5. junta o seu id a `path` e reenvia para `active_destinations(exclude=incoming)`.
- Num nó com subscritor local, regista o caminho completo e o tempo decorrido.
- *Componentes:* um módulo novo ou `routing.py`, com `attach` a registar o handler.
- *Benefício:* cumpre as duas variantes do enunciado (log em cada nó **e** caminho acumulado).
- *Risco:* baixo, porque não altera o estado das rotas.
- *Esforço:* médio.
- *Dependências:* M8 e M9.

**M11. Observabilidade.** Um evento `route_test` no `status.py`, visível no painel e no `ottctl`; opcionalmente uma resposta `ROUTE_TEST_REPLY` unicast ao servidor pelo caminho inverso, para o servidor juntar todos os caminhos. *Esforço:* baixo para o evento, médio para a resposta. *A validar:* se a resposta acrescenta valor suficiente para a defesa.

**M12. Testes da Etapa 3.**
- Validação do formato.
- Unitários com `FakeNode`: RPF, duplicados, `max_hops`, ramo inativo não percorrido.
- Integração com S1–A–{C1, C2}: os dois clientes registam `[S1, A, Cx]` e A regista um só teste.
- Depois de uma reparação, o caminho registado muda.
- Um ensaio no CORE no cenário 3 ou 5.
- *Esforço:* médio.

## 5.4 Testes, observabilidade, documentação e validação no CORE

**M13. Evidência do CORE reprodutível.**
- *Problema:* os ensaios no CORE são a prova principal da integração, mas os logs estão fora do repositório e `testes-core.md` está no `.gitignore`.
- *Proposta:* versionar um resumo por ensaio (comando, data, valores-chave e excerto de log) ou um `ottctl test --md` gerado.
- *Componentes:* `docs/`, `.gitignore`.
- *Benefício:* uma afirmação documentada passa a ser evidência verificável.
- *Risco:* tamanho do repositório, se forem logs completos.
- *Esforço:* baixo.

**M14. Mostrar a tabela num formato próximo do enunciado.**
- *Proposta:* uma linha `{Fluxo, Origem(=montante), Métrica, Destinos, Estado}` no `--live`, ou um subcomando, sem precisar do painel.
- *Benefício:* ligação direta ao exemplo do enunciado para O2.
- *Risco:* nenhum.
- *Esforço:* baixo.

**M15. Registar no log as trocas de montante feitas pela seleção normal.**
- *Problema:* `ESTADO.md` (2026-11-10) diz que essas trocas "não ficam no log" e tiveram de ser deduzidas por `tcpdump`. O `live.py` e o `status.py` registam-nas, mas só quando estão ativos.
- *Componentes:* `handle_announce` (um `log.info` quando `moved`).
- *Benefício:* depuração da Etapa 2 sem ferramentas extra.
- *Risco:* nenhum.
- *Esforço:* baixo.

**M16. Resolver as pendências de documentação.** `docs/CHECKPOINT1.md` é citado mas não existe; usar "Elemento N" e "Etapa N" de forma consistente; explicar em `protocolo.md` porque o `ANNOUNCE` não precisa de TTL de mensagem. *Esforço:* baixo.

---

**Incertezas declaradas:**
- Não reanalisei os logs do CORE. Todo o estado **[CORE]** vem da documentação do próprio repositório.
- Não vi a interface do painel no browser.
- Li `ETAPA2_NOTAS.md` e `CENARIO4.md` só por amostragem (títulos e secções relevantes).
