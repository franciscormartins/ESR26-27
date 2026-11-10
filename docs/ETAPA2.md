# Etapa 2 - Roadmap de implementação das rotas

Este documento acompanha o trabalho da Etapa 2 do enunciado: construir e manter
rotas por fluxo no overlay, ativá-las quando há clientes interessados e encaminhar
os dados pelos caminhos escolhidos.

## Estratégia escolhida

Está decidido implementar a **Estratégia 1 do enunciado**: cada servidor anuncia periodicamente
os seus fluxos; os nós propagam os anúncios por inundação controlada e guardam o melhor
caminho conhecido. Quando um cliente pede um fluxo, a ativação segue o caminho inverso
até ao servidor.

Esta escolha é compatível com as primitivas de overlay existentes (`send` e `broadcast`).

**Ponto da situação (2026-11-09).** Os passos 0 a 9 do plano (ver "Revisão de 2026-11-09")
estão feitos e cobertos por testes automáticos, incluindo ensaios com nós reais em localhost
(111 testes na suite). Falta o segundo servidor nas topologias (passo 10) e a maior parte dos
ensaios no CORE (passo 11): em 2026-11-09 passaram os ensaios 1 e 2 (cenário 1) e 4 (cenário 2);
em 2026-11-10 passaram os ensaios 6, 8 e 9 (cenário 3; o 9 depois da correção D27 do L9). O
ensaio 9 repetido mostrou um defeito novo (L10, ramo órfão por reordenação de
`ACTIVATE`/`DEACTIVATE`), corrigido pela D28 e confirmado com três reinícios do servidor;
em 2026-11-10 passaram também os ensaios 3 (cenário 1) e 5 (cenário 2). Falta o ensaio 7
(dois servidores), que depende do passo 10.

## Estado e tarefas

### 1. Modelo do estado de rota - concluído

- [x] Definir uma entrada por `(servidor, fluxo)`.
- [x] Guardar origem, identificador de fluxo, saltos, atraso acumulado, época, vizinho de
  montante, candidatos por vizinho, destinos a jusante, subscritores locais e estado.
- [x] Implementar operações locais `get`, `put` e `remove` em `RoutingTable`.
- [x] Testar valores por omissão, chaves, operações e validação em `tests/test_routing.py`.
- [x] Documentar que `flow` identifica o conteúdo/stream multimédia transmitido.

Implementação: `src/ott/routing.py`. Testes: `tests/test_routing.py`.

### 2. Fechar o desenho do protocolo - concluído

- [x] Confirmar a Estratégia 1 como abordagem a implementar.
- [x] Usar o ID overlay do nó como identificador de servidor/cliente e um nome estável
  configurado como identificador de fluxo.
- [x] Definir e validar campos de `ANNOUNCE`, `ACTIVATE`, `DEACTIVATE` e `WITHDRAW` em
  `src/ott/protocol.py` e `docs/protocolo.md`.
- [x] Definir `(server_id, flow, epoch)` como identidade de anúncio; épocas crescentes por
  fluxo distinguem rondas, e o montante é escolhido entre os candidatos (secção 3).
- [x] Definir custo inicial 0 no servidor e incremento de 1 por salto (número de saltos).
- [x] Definir que cada fluxo é anunciado independentemente; `server_id` permite dois ou mais
  servidores anunciarem fluxos sem colisão.

### 3. Anúncio e aprendizagem de rotas - implementado

- [x] Permitir ao servidor criar anúncios periódicos para cada fluxo.
- [x] Ao iniciar o anúncio, criar a rota local do servidor com custo 0 e sem vizinho de montante.
- [x] Ao receber um anúncio, rejeitar mensagens inválidas (validadas pelo protocolo) e detetar duplicados/ciclos.
- [x] Calcular o custo via cada vizinho (saltos e atraso) e escolher o montante entre os candidatos.
- [x] Reenviar anúncios aos restantes vizinhos sem os devolver ao emissor nem ao montante.
- [x] Métrica: número de saltos; em empate, atraso acumulado (RTT/2 por ligação, campo `delay`)
  com histerese; sem RTT, menor ID (D21, 2026-11-09).
- [x] Tabela de candidatos por vizinho: a primeira cópia de uma época nova já não derruba o
  montante atual (resolve L4).
- [x] Evitar que um anúncio mais antigo substitua uma rota com caminho. Desde 2026-11-10
  (D27): uma rota retirada só aceita épocas maiores durante a retenção (`route_ttl`), e o
  servidor começa as épocas na hora do arranque, por isso um servidor reiniciado é aceite
  logo (resolve L2 e L9).

A publicação está implementada em `AnnouncementPublisher` (`src/ott/routing.py`) e ligada à
CLI. `--publish-flow ID` pode repetir-se para vários fluxos; `--announce-interval S` define
o intervalo (por omissão, 5 s). O publicador emite imediatamente a primeira ronda e depois
incrementa `epoch` por fluxo a cada ronda. Cada anúncio usa `server_id=node.id`, `metric=0` e
`delay=0`.
O teste unitário cobre múltiplos fluxos, épocas, periodicidade, paragem e configuração inválida.

`RoutingTable.attach(node)` instala o handler de `ANNOUNCE`. Desde 2026-11-09, cada anúncio
atualiza o **candidato** do vizinho emissor (época, saltos e atraso anunciados) e o montante
é reavaliado: menos saltos ganha; com os mesmos saltos, só se troca se o atraso melhorar mais
de 20 % e 1 ms; sem RTT medido, ganha o menor ID. O montante atual continua válido com a
época anterior até enviar a nova, por isso a ordem de chegada das cópias não provoca trocas.
O nó propaga o anúncio quando a época avança, o montante muda ou os saltos mudam (normalmente
uma vez por ronda), a todos menos o emissor e o montante. Cópias antigas, repetidas e
anúncios que regressam ao servidor de origem não são propagados. A validação dos campos é
feita pelo protocolo antes do handler. O `AnnouncementPublisher` cria também a rota local
(métrica 0, atraso 0, sem montante) a cada ronda. A CLI liga a tabela ao nó mesmo quando não
há publicador local.

Exemplo para o nó servidor/bootstrapper `S1`:

```sh
./bin/ott --serve topologies/cenario1.json -i S1 \
  --publish-flow filme_a --publish-flow filme_b --announce-interval 5
```

O publicador emite a ronda inicial imediatamente e a tabela local fica com a rota do
servidor. Os nós vizinhos aprendem e propagam as rotas. A CLI também permite subscrever e
encaminhar os dados; os ensaios de integração no CORE estão listados abaixo.

### 4. Ativação e desativação - implementado (subscrições agregadas por ramo)

- [x] Definir como o cliente seleciona servidor/fluxo e inicia a subscrição.
- [x] Encaminhar `ACTIVATE` pelo vizinho de montante até ao servidor.
- [x] Em cada salto, registar o vizinho emissor como destino a jusante.
- [x] Encaminhar `DEACTIVATE` pelo mesmo caminho e remover esse ramo.
- [x] Agregar por ramo: só a primeira procura sobe (`ACTIVATE`) e só a última sai (`DEACTIVATE`).
- [x] Manter a rota ativa enquanto existir pelo menos uma subscrição local ou a jusante; caso contrário,
  marcá-la inativa.
- [x] Tornar operações repetidas seguras (ativação/desativação duplicada não corrompe o estado).

`RoutingTable.attach(node)` regista também os handlers de `ACTIVATE`, `DEACTIVATE` e `WITHDRAW`.
O cliente inicia/termina a sua subscrição com `table.activate(node, server_id, flow)` e
`table.deactivate(node, server_id, flow)`; o `subscriber_id` local é por omissão o ID do nó.
Desde 2026-11-09 as subscrições são **agregadas por ramo** (D24): cada nó guarda em
`destinations` os vizinhos a jusante que pediram o fluxo e só envia `ACTIVATE` ao
`upstream` quando a procura (subscritores locais ou destinos) passa de zero para um, e
`DEACTIVATE` quando volta a zero. Um segundo cliente atrás de um nó já ativo não gera
mensagens a montante, e a saída de um cliente não interrompe os restantes. Uma troca de
montante gera um só `DEACTIVATE` e um só `ACTIVATE`. `subscriber_id` passou a opcional nas
mensagens e só serve de informação. O servidor deixa de conhecer os clientes individuais.
O estado `ativa` reflete a existência de procura e de um caminho válido para o servidor.

Isto atualiza o estado de controlo. A CLI inicia a subscrição com `--subscribe` e o
encaminhador de dados usa esse estado para abrir/fechar os ramos UDP.

### 5. Reação a alterações do overlay - implementado (reparação rápida e `WITHDRAW`)

- [x] Usar `on_neighbor_up` e `on_neighbor_down` para invalidar ou reaprender rotas.
- [x] Enviar ao vizinho novo os anúncios das rotas válidas já conhecidas; o servidor mantém anúncios periódicos.
- [x] Limpar destinos a jusante associados a vizinhos que caíram e desativar o ramo se já não tiver clientes.
- [x] Marcar sem caminho como inativa; após aprender um montante alternativo, reativar as subscrições e propagar o anúncio atualizado.
- [x] Reparação rápida com condição de viabilidade e `WITHDRAW` quando não há alternativa (D22, 2026-11-09).
- [x] Tempo de vida das rotas como rede de segurança (passo 7, `--route-ttl`, 2026-11-09).

`RoutingTable.attach(node)` liga os eventos de vizinhança a uma fila processada por uma thread
de fundo, para os callbacks do overlay apenas enfileirarem o evento. Quando cai o montante
(ou ele envia `WITHDRAW`), o nó tenta primeiro uma **reparação rápida**: um candidato que
tenha anunciado menos saltos do que a rota perdida (condição de viabilidade; um vizinho que
use este nó como caminho nunca a cumpre). Se existir, o ramo passa para ele sem esperar pela
ronda seguinte. Se não existir, a rota fica sem caminho, os candidatos e os destinos a jusante
são apagados (os subscritores locais ficam pendentes) e o nó envia `WITHDRAW` aos vizinhos;
cada vizinho que o tinha como montante repete o procedimento. Quando o caminho volta a ser
aprendido (numa época maior do que a retirada, D27), os nós a jusante voltam a
pedir o fluxo. Se cair um destino, esse ramo é removido e, se ficar sem procura, sobe
`DEACTIVATE`. A proteção contra ciclos usa a condição de viabilidade, split horizon (não
reenviar ao emissor nem ao montante) e rejeita um destino a jusante como montante.

Esta reação é coberta por testes unitários e pelo reinício do servidor com nós reais
(`tests/test_integration.py`); falta ensaiar quedas, recuperações e caminhos alternativos no
CORE.

### 6. Integração do encaminhamento dos dados - implementado; validado em localhost, CORE pendente

- [x] Expor `RoutingTable.active_destinations(origin, flow, exclude=())`, que devolve uma
  cópia dos vizinhos a jusante ativos, sem duplicar um vizinho que tenha vários subscritores.
- [x] No servidor, emitir pacotes do fluxo apenas quando a rota tem destinos ativos.
- [x] Nos nós intermédios, receber UDP, identificar o vizinho de entrada e replicar cada
  pacote pelos destinos ativos, excluindo a ligação de entrada.
- [x] Manter os dados em UDP, separados das mensagens de controlo TCP.
- [x] Integrar a interface RTP já existente: `--publish-flow` inicia o MJPEG/RTP e
  `--subscribe SERVIDOR:FLUXO` ativa a subscrição e recolhe estatísticas locais.
- [x] Ensaiar a distribuição ponta a ponta em localhost com nós reais
  (`tests/test_integration.py`, 2026-11-09).
- [ ] Ensaiar a distribuição ponta a ponta no CORE, incluindo falhas e recuperação.

**Defeito corrigido em 2026-11-09 (commit `e83e37b`).** `OverlayNode._dispatch` passava aos
handlers o objeto `Neighbor` em vez do ID do vizinho. Com nós reais, `upstream` e os destinos
ficavam com objetos, o `ACTIVATE` nunca era enviado, o anúncio voltava ao emissor e as quedas
não invalidavam rotas. Os testes de `tests/test_routing.py` usam nós falsos que passam strings,
por isso não o detetavam. Até esta correção, as secções 3 a 6 só funcionavam nos testes
unitários. Os testes de integração com nós reais confirmam agora o comportamento.

`OverlayForwarder` (`src/ott/forwarding.py`) escuta por omissão na porta UDP 6000. O nó de
origem envia uma cópia para cada destino a jusante ativo; cada intermédio repete o processo,
sem devolver o datagrama ao vizinho de entrada. Vários subscritores atrás do mesmo vizinho
partilham uma cópia nesse ramo. Os nós de destino entregam o RTP ao `StreamReceiver` local.

Desde 2026-11-09 o forwarder aplica **RPF estrito** (D23): um datagrama só é entregue e
reencaminhado se vier do montante atual (`RoutingTable.upstream_of`). Os restantes são
contados em `rpf_dropped`, visível em `--stats`. Ao trocar de montante perde-se o que chegar
do antigo até o novo começar a enviar.

O encaminhamento reutiliza os troços comuns da árvore construída pelos `upstream` escolhidos
pelos anúncios (métrica: número de saltos, com o atraso como desempate). A implementação não escolhe deliberadamente
um caminho alternativo com custo superior só por esse ramo já estar ativo; essa otimização
global da árvore não faz parte da métrica de menor caminho implementada.

Exemplo: no servidor, acrescentar `--publish-flow filme_a`; no cliente, usar
`--subscribe S1:filme_a`. Todos os nós intermédios têm de correr `ott` para encaminhar UDP.
`--stats 2` mostra os vizinhos, as estatísticas RTP do cliente e os contadores do plano de dados.

## Revisão de 2026-11-09: decisões e próximos passos

Uma revisão do código face ao enunciado (pág. 2-4) encontrou o defeito acima e as lacunas
seguintes. Estado atual de cada uma:

| # | Lacuna | Impacto |
|---|---|---|
| L2 | ~~O servidor reinicia com `epoch=1` e os nós rejeitam épocas inferiores à guardada~~ — **resolvido** (passo 6) | Um servidor reiniciado nunca volta a ser aprendido |
| L3 | ~~Não há retirada nem expiração de rotas~~ — **resolvido**: retirada (passo 6) e tempo de vida (passo 7) | Um servidor morto deixa rotas `ativa` para sempre |
| L4 | ~~Numa época nova, a primeira cópia ganha; empates não têm critério determinista~~ — **resolvido** (passo 5) | O montante pode oscilar a cada ronda (ex.: O5 via O3/O7 no cenário 3) |
| L5 | ~~O forwarder aceita datagramas de qualquer vizinho~~ — **resolvido** pelo RPF estrito (passo 4) | Duplicados ao trocar de montante; ciclos de dados transitórios |
| L7 | A origem UDP era identificada pelo IP do *peer* TCP — confirmada no CORE em 2026-11-10 (cenário 3, O7→O5 depois da reparação rápida; ver "Resultados no CORE"). **Corrigida em 2026-11-10 (D26):** o datagrama leva o id do nó que o envia (campo `salto`); teste de regressão em `tests/test_forwarding.py`; validada no CORE no ensaio 6 (2026-11-10) | Nós com várias interfaces viam datagramas descartados como "origem não vizinha" (`descartados` sobe, `RPF` não) |
| L8 | ~~`protocolo.md` §7 diz que só a transição 0→1 propaga `ACTIVATE`; o código propaga cada subscritor; `seen_announcements` só é escrito e cresce sem limite~~ — **resolvido** (passos 3, 8 e 9) | Documentação incoerente |
| L9 | ~~Rota fantasma em ciclo depois da queda do servidor~~ — **resolvido em 2026-11-10 (D27)** e validado no CORE (ensaio 9 repetido). (CORE, cenário 3, ensaio 9, 2026-11-10). Depois do `WITHDRAW`, anúncios da época antiga ainda em trânsito (incluindo o da reparação rápida transitória do O5 via O3, que logo a seguir também retirou) são aceites por nós sem caminho, porque uma rota sem caminho aceita qualquer época (D22). Formou-se o ciclo O6 ↔ O7 para o `filme_a`, com O2, O3 e O5 pendurados nele, que só desapareceu quando o tempo de vida expirou (30 s). Enquanto existiu, estes nós julgavam ter caminho e rejeitaram a época 1 do servidor reiniciado. Correção: uma rota retirada só aceita épocas maiores durante `route_ttl`, e o servidor começa as épocas na hora do arranque. | Depois de uma queda do servidor, um fluxo pode demorar até `--route-ttl` (30 s) a voltar, mesmo com o servidor de volta. |
| L10 | ~~Ramo órfão por reordenação de `ACTIVATE`/`DEACTIVATE`~~ — **resolvido em 2026-11-10 (D28)**; no CORE, três reinícios do servidor sem cópias a mais nem `RPF`. (CORE, cenário 3, ensaio 9 repetido, 2026-11-10). A tabela decide os envios sob o bloqueio mas envia-os depois de o libertar; quando dois anúncios chegam quase juntos por threads diferentes, o `DEACTIVATE` ao montante antigo pode sair antes do `ACTIVATE` que lhe tinha sido decidido antes. O montante antigo fica com o nó como destino e continua a enviar-lhe o fluxo, que é descartado por RPF. Reproduzido com duas threads e um envio atrasado. Correção: os envios entram numa fila única ainda sob o bloqueio e saem por ordem, uma thread de cada vez; teste de regressão em `tests/test_routing.py` (`SendOrderTest`). | Cópias a mais numa ligação, descartadas por RPF, até a rota mudar (no ensaio, ≈ 20 imagens/s durante 1,5 min). |

**Decisões tomadas pelo grupo:**

| Tema | Decisão |
|---|---|
| Métrica | Número de saltos; em empate, RTT acumulado (`delay` no `ANNOUNCE`), com histerese (> 20 % e > 1 ms); empate final pelo ID do vizinho. RTT desconhecido conta como infinito |
| Escolha da rota | Tabela de candidatos por vizinho (última época, saltos, atraso), em vez de "a primeira cópia ganha" |
| Identidade dos anúncios | `(server_id, flow, epoch)`; épocas a partir da hora do arranque do servidor (ms); uma rota retirada só aceita épocas maiores durante `route_ttl` (D27) |
| Falhas | `WITHDRAW(server_id, flow, epoch)` explícito, só processado se vier do montante; antes de retirar, reparação rápida com condição de viabilidade (só candidatos com saltos anunciados < saltos da rota perdida). Quem envia `WITHDRAW` limpa os destinos; a jusante volta a pedir quando reaprende caminho |
| Rede de segurança | Tempo de vida de rotas e candidatos: 6 × o intervalo de anúncio (30 s), configurável (`--route-ttl`) |
| Plano de dados | RPF estrito: só se aceitam (e entregam) datagramas vindos do montante atual; aceita-se a falha curta ao trocar de montante |
| Subscrições | Agregadas por ramo: `ACTIVATE` só na transição 0→1, `DEACTIVATE` na 1→0; `subscriber_id` passa a opcional e informativo |
| Servidores | Dois servidores com fluxos distintos; a chave `(server_id, flow)` mantém-se |

**Plano (por ordem):**

- [x] 0. Teste de integração com `OverlayNode` reais (S1–A–C).
- [x] 1. Handlers recebem o ID do vizinho (`node.py`).
- [x] 2. Teste do plano de dados S1–A–{C1, C2} em localhost.
- [x] 3. Subscrições agregadas por ramo (`routing.py`, `protocol.py`).
- [x] 4. RPF estrito no `OverlayForwarder` (`RoutingTable.upstream_of`).
- [x] 5. Tabela de candidatos e desempate por RTT (campo `delay`).
- [x] 6. `WITHDRAW`, reparação rápida e aceitação de qualquer época numa rota sem caminho
  (substituída em 2026-11-10 pela retenção da D27).
- [x] 7. Tempo de vida de rotas e candidatos.
- [x] 8. Retirar `seen_announcements`.
- [x] 9. Atualizar `protocolo.md`, este ficheiro e `ESTADO.md` com o comportamento final.
- [ ] 10. Segundo servidor (`S2`, 2 fluxos cada) em `cenario2.json` e `cenario3.json`.
- [ ] 11. Ensaios no CORE (lista abaixo).

## Plano de testes

- [x] Unidade: atualização da rota quando chega um caminho de menor custo.
- [x] Unidade: duplicados e anúncios antigos não alteram incorretamente a tabela.
- [x] Unidade: inundação não envia anúncio de volta ao vizinho de entrada.
- [x] Integração CORE: servidor → nó intermédio → cliente (cenário 1, 2026-11-09).
- [x] Integração CORE: dois clientes partilham o troço comum sem encaminhamento duplicado nesse troço
  (cenário 1, 2026-11-09; confirmado pelos contadores, `tcpdump` não registado).
- [x] Integração CORE: caminhos alternativos escolhem o custo menor (cenário 2, 2026-11-09).
- [x] Integração CORE: queda de um nó intermédio com alternativa de custo igual (cenário 3,
  O3 cai com o O5 a receber): reparação rápida via O7 sem `WITHDRAW`, O10 sem perdas e O11
  com 1 imagem perdida (2026-11-10, depois da correção D26).
- [x] Unidade: queda do vizinho de montante invalida a rota e anúncio alternativo reativa subscrições.
- [x] Unidade: queda de um destino remove o ramo e desativa a subscrição a montante.
- [x] Unidade: caminho melhor troca o montante e move o ramo ativo.
- [x] Integração localhost (nós reais, S1–A–C): rotas com IDs e métrica em saltos; anúncio
  não volta ao emissor; ativação e desativação até ao servidor; queda do cliente remove o
  ramo até ao servidor; queda do montante deixa a rota sem caminho com o pedido pendente.
- [x] Integração localhost (S1–A–{C1, C2}, forwarder UDP e RTP): uma cópia no troço S1–A e
  duas a partir de A, sem perdas; C1 sai e C2 continua; sem clientes, S1 não emite.
- [x] Unidade: subscrições agregadas (só a primeira e a última procura sobem; pedidos
  repetidos idempotentes; `ACTIVATE` sem `subscriber_id`; ACTIVATE do montante rejeitado;
  queda de um destino com outros ativos não sobe nada; troca de montante com vários ramos
  gera um só par `DEACTIVATE`/`ACTIVATE`).
- [x] Unidade, seleção (passo 5): época nova pelo caminho pior não causa trocas; empate sem
  RTT decidido pelo menor ID em qualquer ordem; empate decidido pelo atraso em qualquer ordem;
  ganho abaixo da histerese não troca, ganho grande troca e fica estável; menos saltos ganha
  mesmo com pior atraso; `delay` acumulado e só anunciado quando conhecido.
- [x] Unidade, falhas (passo 6): reparação rápida sem `WITHDRAW`; alternativa não viável leva a
  `WITHDRAW`; `WITHDRAW` de outro vizinho só remove o candidato; `WITHDRAW` do montante move o
  ramo para a alternativa; retirada em cascata limpa os destinos; rota sem caminho aceita a
  época 1 de um servidor reiniciado; rota com caminho rejeita épocas antigas. Desde 2026-11-10
  (D27, regressão L9): rota retirada rejeita a época retirada e anteriores, aceita épocas
  maiores e, ao fim da retenção, qualquer época; o publicador começa na hora do arranque.
- [x] Integração localhost: servidor morto e reiniciado (épocas novas maiores) é reaprendido e o ramo
  S1–A–C volta a abrir.
- [x] Unidade, tempo de vida (passo 7): sem `route_ttl` nada expira; valores inválidos são
  recusados; candidatos recentes ficam; uma alternativa expirada é esquecida sem mensagens; o
  montante expirado é substituído pela alternativa recente (`DEACTIVATE` ao antigo,
  `ACTIVATE` ao novo) ou, sem alternativa, a rota é retirada; uma cópia repetida renova o
  tempo de vida; a rota de origem nunca expira; a thread de fundo expira um montante calado.
  `tests/test_cli.py` valida `--route-ttl` e `--announce-interval`.
- [x] RPF (`tests/test_forwarding.py`, UDP real em loopback): só o montante é entregue e
  reencaminhado; após troca de montante, o antigo é descartado; sem rota ou sem caminho tudo é
  descartado; o próprio fluxo que volta ao nó de origem não é reencaminhado.
- [x] CORE: ativar dois clientes e desativar um mantém o outro a receber; desativar o
  último deixa a rota sem tráfego (cenário 1, ensaio 3, 2026-11-10).
- [x] CORE: exercitar as topologias 1 e 2 antes do cenário 3 (ensaios 1–5).

Cenário 3: nó do CORE onde corre cada nó overlay (fiel à Figura 1, D13). Todos com
`-b 10.0.18.20`; o servidor é o `O1` (subscrições `O1:filme_a`).

| Overlay | O1 | O2 | O3 | O4 | O5 | O6 | O7 | O8 | O9 | O10 | O11 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Nó do CORE | S1 | r1 | r3 | C8 | r9 | r10 | r5 | C2 | C4 | C6 | C7 |

A O5 (r9) tem dois caminhos com o mesmo número de saltos até ao servidor: via O3 (overlay
O3–O5 = r3>r6>r9; no r9, `eth2` liga ao r6) e via O7 (O5–O7 = r9>r7>r5; no r9, `eth1` liga
ao r7). Os ensaios 6 e 8 usam estes dois caminhos (`netem` no r9 na interface do caminho
ativo). Nós do CORE livres para um segundo servidor: C1, C3, C5 e C9.

Ensaios no CORE necessários para dar a etapa como validada (comandos de cada nó e ação de
cada ensaio em `docs/testes-core.md`, local e fora do repositório):

1. Cenário 1: S1 → O2 → C1 a reproduzir.
2. Dois clientes: um só fluxo no troço comum (contadores `--stats` e `tcpdump`).
3. Desativar um cliente mantém o outro; desativar o último deixa a rota sem tráfego.
4. Cenário 2: o caminho alternativo escolhe o custo menor.
5. Queda do montante: tempo até o cliente voltar a receber.
6. Queda de um nó intermédio com alternativa de custo igual (cenário 3, cair O3 com O5 a
   receber): reparação rápida via O7, sem `WITHDRAW`; medir a falha no vídeo (RPF).
7. Cenário 3 com 2 servidores, 2 fluxos cada e clientes em ramos partilhados.
8. `tc netem` em O3–O5 (cenário 3): O5 troca para O7 uma só vez, sem oscilar (passo 5).
9. Matar e reiniciar o servidor: o `WITHDRAW` chega aos clientes e o fluxo regressa com as
   épocas novas (D27: maiores do que as retiradas).

### Resultados no CORE

| Ensaio | Data | Resultado |
|---|---|---|
| 1 e 2 (cenário 1, S1 – O2 – {C1, C2}, 20 imagens/s) | 2026-11-09 | **Passou.** Janela de 23:34:02 a 23:34:28. S1: `encaminhados` +520 em 26 s (20/s, uma cópia por pacote no troço S1–O2). O2: `recebidos` 531 → 1051 (+520) e `encaminhados` 1057 → 2097 (+1040, exatamente 2×). C1 e C2: +240 recebidos em 12 s cada (20/s), `perdidos` constantes nessa janela (434 e 439), jitter 0,06–0,12 ms, `descartados=0` e `RPF=0` em todos os nós (sem sinais da limitação L7). RTT entre vizinhos 0,3–0,6 ms. |

| 4 (cenário 2, S1–O1, S1–O2, O1–O2, O1–C2, O2–C1; C1 e C2 subscritos) | 2026-11-09 | **Passou.** Janela de 23:40:24 a 23:40:46. Cada cliente usa o caminho de 2 saltos e não o de 3 pela ligação O1–O2: O2 tem `recebidos` = `encaminhados` (+40 a cada 2 s, só para C1) e O1 tem `recebidos` = `encaminhados` (+40 a cada 2 s, só para C2), ou seja, nenhum dos dois reencaminha para o outro. S1 envia 2 cópias por pacote (+80 a cada 2 s depois de C2 entrar às 23:40:22), uma por ramo, como esperado porque os ramos divergem logo no servidor. C1 e C2: 20/s, `perdidos=0`, jitter 0,06–0,13 ms. `descartados=0` e `RPF=0` em todos os nós, incluindo O1 e O2 com 3 interfaces cada (sem sinais da limitação L7). RTT entre vizinhos 0,3–0,7 ms. |

| Arranque do cenário 3 (atribuição da Figura 1; O4, O8, O9, O10 com `filme_a`, O11 com `filme_b`) — **parcial, só O2** | 2026-11-10 | Janela de 00:02:03 a 00:02:33 no O2 (r1). Vizinhos O1, O3 e O7, como no JSON. `recebidos` +80 a cada 2 s (40/s = 2 fluxos × 20/s: cada fluxo atravessa o troço O1–O2 **uma só vez**, apesar de o `filme_a` ter 4 clientes). `encaminhados` +120 a cada 2 s (60/s = 3 cópias por imagem: `filme_a` para O3 e O7, `filme_b` só para o ramo que leva à O5). `descartados=0`, `RPF=0`. Falta ver os restantes nós para confirmar o caminho da O5 e a entrega nos clientes. |

| Cenário 3 completo (atribuição da Figura 1; O4, O8, O9, O10 com `filme_a`, O11 com `filme_b`) | 2026-11-10 | **Passou.** Logs `/tmp/ott-O*.log`, janela de 09:44:00 a 09:47:40 (218–220 s) com os 11 nós ligados e os vizinhos do JSON. Taxas (imagens/s, recebidos → encaminhados): O1 0 → 40; O2 40 → 60 (`filme_a` para O3 e O7, `filme_b` para O3); O3 40 → 60 (`filme_a` para O4 e O5, `filme_b` para O5); O5 40 → 40 (`filme_a` para O10, `filme_b` para O11), com montante O3; O7 20 → 40 (`filme_a` para O8 e O6; não envia para O5); O6 20 → 20 (para O9). Uma só cópia de cada fluxo em cada ligação overlay. Clientes O4, O8, O9, O10, O11 a 20/s, `perdidos=0`, jitter 0,15–0,31 ms. `descartados=0` e `RPF=0` em todos os nós. |
| Saída do O11 e queda do O3 (fim da sessão anterior, não foi um ensaio planeado) | 2026-11-10 | **Controlo passou, dados falharam (L7).** 09:47:46 sai o O11: o `filme_b` deixa de ter clientes e o O1 passa a emitir só o `filme_a` (20/s), como esperado. 09:47:49,095 sai o O3: o O5 deteta no mesmo milissegundo e regista `rota ... reparada via O7` para os dois fluxos, sem `WITHDRAW`; o O2 passa a enviar só para o O7 (20 → 20) e o O7 passa a 3 cópias (O8, O6 e O5). **Mas o O5 descarta tudo o que vem do O7:** `recebidos` +40 a cada 2 s, `encaminhados` +2 e depois +0, `descartados` +38, +40, com `RPF=0`, ou seja, descarte por "origem não vizinha". O O10 fica parado em 6003 imagens até à saída do O2 às 09:47:53. **Causa confirmada com `ip route get`:** o O5 ligou-se por TCP ao O7 em 10.0.4.2 a partir de 10.0.2.1 (no r9, `10.0.4.2 via 10.0.2.2 dev eth1 src 10.0.2.1`); o O7 envia o UDP para 10.0.2.1 e o r5 usa `dev eth1 src 10.0.5.1`. O O5 conhece o O7 como 10.0.4.2, por isso 10.0.5.1 não é vizinho. O problema só aparece quando o emissor do UDP é o lado que **aceitou** a ligação TCP e o seu encaminhamento para o outro lado sai por uma interface diferente da do IP a que o outro se ligou. Corrigido em 2026-11-10 (D26) e validado no CORE no ensaio 6 (linha seguinte). |

| 6 (cenário 3, queda do O3 com o O5 a receber; código com a correção D26) | 2026-11-10 | **Passou.** Logs em `~/uni/esr/ott-logs/2026-11-10-2`. Antes (10:03:40–10:04:44): a mesma árvore do ensaio anterior (O2 40 → 60, O5 40 → 40 via O3, O7 20 → 40). Ctrl-C no O3 às 10:04:46,125: O2, O4 e O5 detetam no mesmo milissegundo; o O5 regista `reparada via O7` para os dois fluxos, **sem `WITHDRAW`**. Sem O3 (10:04:50–10:05:42): O2 40 → 40 (só para O7), O7 40 → 80 (`filme_a` para O8, O6 e O5; `filme_b` para O5), O5 40 → 40, `descartados=0` e `RPF=0` em todos os nós. **Falha no vídeo:** O10 sem nenhuma imagem perdida (+40 a cada 2 s sem interrupção); O11 com 1 imagem perdida. O O4, cujo único vizinho é o O3, fica sem vídeo, como esperado. O3 reiniciado às 10:05:44: religa-se a O2, O4 e O5 em ≤ 1,3 s e o O4 volta a receber em ≈ 2 s (os 1188 `perdidos` do O4 são as imagens do período sem O3, ≈ 59 s × 20/s). O O5 **mantém o montante O7** (mesmo número de saltos; sem ganho de atraso acima da histerese, D21), por isso o O3 passa a levar só o `filme_a` para o O4 (20 → 20). |

| 8 (cenário 3, `tc netem delay 20ms` no r9 `eth2`, o caminho do O5 para o O3, montante ativo) | 2026-11-10 | **Passou.** Logs em `~/uni/esr/ott-logs/2026-11-10-3`. Atraso aplicado entre 10:58:32 e 10:58:34; o RTT do O5 para o O3 sobe pela média móvel (0,5 → 6,6 → 10,9 → 17,4 ms em 8 s) e o do O7 fica em 0,6–0,8 ms. O O5 troca para o O7 **uma só vez** na janela que termina às 10:58:33–35: O3 passa de 40 → 60 para 20 → 20/s e O7 de 40 → 80 para 80 → 160 por 2 s. Sem oscilação durante cerca de 4 min com o atraso. **Nenhuma imagem perdida** em nenhum cliente e `RPF=0` em todos os nós. Removido o atraso (por volta das 11:02:3x, RTT de volta a 1,4 ms às 11:02:50), o O5 **mantém o O7** até ao fim do ensaio (histerese, D21). |
| 9 (cenário 3, Ctrl-C no servidor O1 e novo arranque) | 2026-11-10 | **Passou com um defeito (L9).** Ctrl-C por volta das 11:04:15,5: o O2 deteta às 11:04:15,515 e o `WITHDRAW` chega aos 5 clientes em 3 ms (11:04:15,518). O1 reiniciado às 11:04:40,205; o O2 religa-se em 0,7 s. O `filme_b` volta logo: O11 a receber às 11:04:41 (≈ 1 s). O `filme_a` só volta às 11:04:50–51 (≈ 10 s) nos O4, O8, O9 e O10: ficou presa uma rota fantasma em ciclo (O6 ↔ O7, com O2, O3 e O5 pendurados nela) que rejeitou a época 1 do novo servidor até o tempo de vida expirar às 11:04:45,9 (`montante O7 sem anúncios há mais de 30.0 s` no O2 e no O6); o fluxo regressou na ronda de anúncio seguinte (11:04:50,2). Pelos logs (só INFO), o ciclo começa no O5: o `WITHDRAW` do O7 chega antes do do O3, o O5 repara via O3 e anuncia; esse anúncio e outros da época antiga ainda em trânsito são aceites por nós que já tinham retirado (D22). |

| 9, repetido com a correção D27 (cenário 3) | 2026-11-10 | **Passou (L9 corrigido).** Logs em `~/uni/esr/ott-logs/2026-11-10-4`. Ctrl-C no O1 às 11:32:06,4; o `WITHDRAW` chega aos 5 clientes em 3 ms. O1 reiniciado às 11:32:26,6; o O2 religa-se em 0,7 s e **os dois fluxos voltam em ≈ 1–2 s** em todos os clientes (O4 às 11:32:27,6, O8–O11 até às 11:32:29,4; o O1 já encaminhou 51 imagens às 11:32:28,6). Nenhum `montante ... sem anúncios há mais de 30.0 s` em nenhum nó: sem rota fantasma. O O5 voltou a repetir a reparação transitória via O7 antes de retirar (a ordem dos `WITHDRAW` é a mesma), agora sem efeito. **Defeito novo (L10):** depois do reinício, o O7 ficou com o O5 como destino do `filme_a` quando o montante do O5 era o O3; o O5 recebeu ≈ 20 imagens/s a mais, descartadas por RPF (1920 até à queda do O3), durante ≈ 1,5 min. |
| 6, repetido com as correções D26 e D27 (cenário 3) | 2026-11-10 | **Passou.** Ctrl-C no O3 às 11:34:03,3: o O5 repara o `filme_a` via O7 no mesmo milissegundo, sem `WITHDRAW` (o `filme_b` já vinha pelo O7). O10 e O11 **sem nenhuma imagem perdida**; O8 e O9 sem efeito. O O4 retira (o O3 é o seu único vizinho). O3 reiniciado às 11:34:33,1: religa-se a O2, O4 e O5 em ≤ 1,2 s e o **O4 volta a receber em ≤ 2,5 s** (época nova maior do que a retirada, D27); os 606 `perdidos` do O4 são as imagens dos 30 s sem O3. O O5 mantém o montante O7. |

| 9, três reinícios do servidor com a correção D28 (cenário 3) | 2026-11-10 | **Passou (L10 não se repetiu).** Logs em `~/uni/esr/ott-logs/2026-11-10-5`. O1 reiniciado às 11:48:28,7, 11:49:38,1 e 11:50:45,9; em cada um, O4, O10 e O11 voltam a receber em ≈ 1–2 s. Em todo o período, O2, O3, O5 e O7 com `descartados=0` e `RPF=0`; o O5 recebe 40 imagens/s (sem cópias a mais). No 3.º reinício o O5 trocou o montante do `filme_b` do O3 para o O7 ≈ 6 s depois do arranque e o O3 deixou logo de lhe enviar esse fluxo (40 → 80 só com o `filme_a`), sem ramo órfão. **Erro de configuração nesta sessão:** o nó C2 arrancou com `-i O9` em vez de `-i O8` (dois nós O9; o O7 vê `O9` como vizinho e o O9 verdadeiro, no C4, perde e recupera a ligação ao O6 continuamente). Os resultados de O6, O8 e O9 não são válidos; os de O2, O3, O5, O7, O4, O10 e O11 não dependem desse ramo. |

| 9, três reinícios do servidor com D26–D28 e a topologia completa (cenário 3) | 2026-11-10 | **Passou.** Logs em `~/uni/esr/ott-logs/2026-11-10-6`. Os 11 nós com os vizinhos do JSON (O7: O2, O5, O6, O8; O6: O5, O7, O9), sem quedas inesperadas nem expirações. O1 reiniciado às 12:02:04, 12:02:56 e 12:03:57; os 5 clientes voltam a receber em **1,2–2,9 s** (limite superior: primeira linha de `--stats`, de 2 em 2 s), o O8 sempre primeiro e o O11 sempre último. Entre reinícios, a árvore volta a ter uma cópia por ligação: O1 0 → 40, O2 40 → 60, O3 40 → 60, O5 40 → 40, O6 20 → 20, O7 20 → 40 imagens/s; depois do 3.º reinício o O5 recebe o `filme_b` pelo O7 e o `filme_a` pelo O3 (O3 20 → 40, O7 40 → 60), também sem duplicados. No fim, `descartados=0` e `RPF=0` em todos os nós e `perdidos=0` em todos os clientes. |

| 3 (cenário 1, S1 – O2 – {C1, C2}; desativar os clientes um a um) | 2026-11-10 | **Passou.** Logs em `~/uni/esr/ott-logs/2026-11-10-7`. Com os dois clientes: S1 0 → 20, O2 20 → 40 imagens/s. Ctrl-C no C1 (O2 deteta às 12:30:15,5): o O2 passa a 20 → 20 (só para o C2) e o C2 continua a 20/s, sem nenhuma imagem perdida. Ctrl-C no C2 (O2 deteta às 12:31:08,5): o S1 deixa de emitir na janela seguinte (`encaminhados` parado em 2574 até ao fim, 12:31:47) e o O2 deixa de receber. `descartados=0` e `RPF=0` em todos os nós. |
| 5 (cenário 2; link O2–S1 em baixo com `ip link set eth2 down` no O2, depois reposto) | 2026-11-10 | **Passou.** Logs em `~/uni/esr/ott-logs/2026-11-10-8`. Antes: S1 0 → 40 (uma cópia por ramo), O1 e O2 20 → 20 cada. Link em baixo por volta das 12:25:27,2 (última imagem no C1): o O2 deteta por timeout às 12:25:32,7 (`sem resposta`, ≈ 5,5 s) e o S1 às 12:25:34,2; o O2 não tem alternativa viável (o O1 anuncia 1 salto, como o próprio O2), envia `WITHDRAW` e reaprende o caminho pelo O1 na ronda seguinte; o C1 volta a receber às ≈ 12:25:34,2. **Falha no vídeo do C1: 138 imagens (≈ 6,9 s)**, quase toda devida à deteção. Pelo caminho novo: S1 0 → 20, O1 20 → 40 (para C2 e O2), O2 20 → 20. Link reposto: o S1 religa-se ao O2 às 12:27:03,3 e o O2 volta logo ao caminho direto (menos saltos): O1 de novo 20 → 20 e S1 0 → 40, **sem nenhuma imagem perdida** no C1. `descartados=0` e `RPF=0` em todos os nós. |

Nota sobre os `perdidos` do ensaio 1/2: os 434/439 acumularam-se **antes** da janela medida (não
cresceram durante ela) e os dois clientes mostram `reinicios=1`. O emissor numera todas as imagens,
mesmo quando não há destinos, por isso um período sem caminho (por exemplo, um nó reiniciado) conta
como perdas no cliente quando o fluxo regressa. Esses valores não medem perdas na rede; para medir
perdas, usar a diferença de `perdidos` entre duas linhas de `--stats`.

Comandos:

```sh
PYTHONPATH=src python3 -m unittest tests.test_routing -v        # modelo (nós falsos)
PYTHONPATH=src python3 -m unittest tests.test_integration -v    # nós reais em localhost
PYTHONPATH=src python3 -m unittest tests.test_forwarding -v     # RPF no plano de dados
PYTHONPATH=src python3 -m unittest tests.test_cli -v            # opções da CLI
PYTHONPATH=src python3 -m unittest discover -s tests -t .       # tudo (111 testes)
```

`tests/test_integration.py` dá a cada nó um IP de loopback (127.0.0.1–4), porque todos os
forwarders usam a mesma porta UDP, como no CORE. O nó A (127.0.0.1) inicia todas as ligações
TCP, para que o IP visto na ligação TCP coincida com a origem dos datagramas UDP. Se o sistema
não aceitar esses endereços, o teste do plano de dados é saltado.

## Critério de conclusão da Etapa 2

A etapa estará funcionalmente concluída quando vários clientes puderem ativar o mesmo fluxo,
os nós encaminharem os dados pela árvore de distribuição construída sobre os melhores
caminhos conhecidos, e uma falha de vizinho provocar atualização da rota sem deixar tráfego
preso em caminhos inválidos. Registar os resultados e limitações em `docs/ESTADO.md` e no
relatório.

**Estado face ao critério (2026-11-09):** os três pontos estão cobertos por testes automáticos
em localhost (dois clientes num ramo partilhado, uma cópia por troço; queda de cliente, de
montante e do servidor com recuperação). Só se dá a etapa como **validada** depois dos ensaios
1 a 9 no CORE, com os resultados registados em `docs/ESTADO.md`.
