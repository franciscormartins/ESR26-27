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

## Estado e tarefas

### 1. Modelo do estado de rota - concluído

- [x] Definir uma entrada por `(servidor, fluxo)`.
- [x] Guardar origem, identificador de fluxo, custo, vizinho de montante, destinos a
  jusante, estado ativo/inativo e anúncios já vistos.
- [x] Implementar operações locais `get`, `put` e `remove` em `RoutingTable`.
- [x] Testar valores por omissão, chaves, operações e validação em `tests/test_routing.py`.
- [x] Documentar que `flow` identifica o conteúdo/stream multimédia transmitido.

Implementação: `src/ott/routing.py`. Testes: `tests/test_routing.py`.

### 2. Fechar o desenho do protocolo - concluído

- [x] Confirmar a Estratégia 1 como abordagem a implementar.
- [x] Usar o ID overlay do nó como identificador de servidor/cliente e um nome estável
  configurado como identificador de fluxo.
- [x] Definir e validar campos de `ANNOUNCE`, `ACTIVATE` e `DEACTIVATE` em
  `src/ott/protocol.py` e `docs/protocolo.md`.
- [x] Definir `(server_id, flow, epoch)` como identidade de anúncio; épocas crescentes por
  fluxo distinguem rondas, e a métrica menor dentro da mesma época melhora a rota.
- [x] Definir custo inicial 0 no servidor e incremento de 1 por salto (número de saltos).
- [x] Definir que cada fluxo é anunciado independentemente; `server_id` permite dois ou mais
  servidores anunciarem fluxos sem colisão.

### 3. Anúncio e aprendizagem de rotas - implementado

- [x] Permitir ao servidor criar anúncios periódicos para cada fluxo.
- [x] Ao iniciar o anúncio, criar a rota local do servidor com custo 0 e sem vizinho de montante.
- [x] Ao receber um anúncio, rejeitar mensagens inválidas (validadas pelo protocolo) e detetar duplicados/ciclos.
- [x] Calcular o custo via vizinho emissor e comparar com a rota guardada.
- [x] Guardar o vizinho de montante quando se aprende uma rota melhor.
- [x] Reenviar anúncios aos restantes vizinhos sem os devolver ao emissor.
- [x] Métrica: número de saltos; em empate, atraso acumulado (RTT/2 por ligação, campo `delay`)
  com histerese; sem RTT, menor ID (D21, 2026-11-09).
- [x] Tabela de candidatos por vizinho: a primeira cópia de uma época nova já não derruba o
  montante atual (resolve L4).
- [x] Evitar que um anúncio mais antigo substitua uma rota com caminho; uma rota sem caminho
  aceita qualquer época (servidor reiniciado, resolve L2).

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
anúncios que regressam ao servidor de origem não são propagados. A validação dos campos é feita pelo protocolo antes do handler. O
`AnnouncementPublisher` cria também a rota local (métrica 0, sem montante) a cada
ronda. A CLI liga a tabela ao nó mesmo quando não há publicador local.

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

`RoutingTable.attach(node)` regista também os handlers de `ACTIVATE` e `DEACTIVATE`.
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
- [ ] Tempo de vida das rotas como rede de segurança (passo 7).

`RoutingTable.attach(node)` liga os eventos de vizinhança a uma fila processada por uma thread
de fundo, para os callbacks do overlay apenas enfileirarem o evento. Quando cai o montante
(ou ele envia `WITHDRAW`), o nó tenta primeiro uma **reparação rápida**: um candidato que
tenha anunciado menos saltos do que a rota perdida (condição de viabilidade; um vizinho que
use este nó como caminho nunca a cumpre). Se existir, o ramo passa para ele sem esperar pela
ronda seguinte. Se não existir, a rota fica sem caminho, os candidatos e os destinos a jusante
são apagados (os subscritores locais ficam pendentes) e o nó envia `WITHDRAW` aos vizinhos;
cada vizinho que o tinha como montante repete o procedimento. Quando o caminho volta a ser
aprendido (mesmo com uma época inferior, se o servidor reiniciou), os nós a jusante voltam a
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
pelos anúncios (métrica atual: número de saltos). A implementação não escolhe deliberadamente
um caminho alternativo com custo superior só por esse ramo já estar ativo; essa otimização
global da árvore não faz parte da métrica de menor caminho implementada.

Exemplo: no servidor, acrescentar `--publish-flow filme_a`; no cliente, usar
`--subscribe S1:filme_a`. Todos os nós intermédios têm de correr `ott` para encaminhar UDP.
`--stats 2` mostra os vizinhos, as estatísticas RTP do cliente e os contadores do plano de dados.

## Revisão de 2026-11-09: decisões e próximos passos

Uma revisão do código face ao enunciado (pág. 2-4) encontrou o defeito acima e as lacunas
seguintes, ainda **por resolver** no código:

| # | Lacuna | Impacto |
|---|---|---|
| L2 | ~~O servidor reinicia com `epoch=1` e os nós rejeitam épocas inferiores à guardada~~ — **resolvido** (passo 6) | Um servidor reiniciado nunca volta a ser aprendido |
| L3 | ~~Não há retirada~~ nem expiração de rotas — retirada **resolvida** (passo 6); expiração no passo 7 | Um servidor morto deixa rotas `ativa` para sempre |
| L4 | ~~Numa época nova, a primeira cópia ganha; empates não têm critério determinista~~ — **resolvido** (passo 5) | O montante pode oscilar a cada ronda (ex.: O5 via O3/O7 no cenário 3) |
| L5 | ~~O forwarder aceita datagramas de qualquer vizinho~~ — **resolvido** pelo RPF estrito (passo 4) | Duplicados ao trocar de montante; ciclos de dados transitórios |
| L7 | A origem UDP é identificada pelo IP do *peer* TCP | Validar no CORE em nós com várias interfaces |
| L8 | ~~`protocolo.md` §7 diz que só a transição 0→1 propaga `ACTIVATE`; o código propaga cada subscritor~~ — **resolvido** com a agregação (passo 3). Falta: `seen_announcements` só é escrito e cresce sem limite (passo 8) | Documentação incoerente |

**Decisões tomadas pelo grupo:**

| Tema | Decisão |
|---|---|
| Métrica | Número de saltos; em empate, RTT acumulado (`delay` no `ANNOUNCE`), com histerese (> 20 % e > 1 ms); empate final pelo ID do vizinho. RTT desconhecido conta como infinito |
| Escolha da rota | Tabela de candidatos por vizinho (última época, saltos, atraso), em vez de "a primeira cópia ganha" |
| Identidade dos anúncios | `(server_id, flow, epoch)`; uma rota sem caminho aceita qualquer época (resolve o reinício do servidor) |
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
- [x] 6. `WITHDRAW`, reparação rápida e aceitação de qualquer época numa rota sem caminho.
- [ ] 7. Tempo de vida de rotas e candidatos.
- [ ] 8. Retirar `seen_announcements`.
- [ ] 9. Atualizar `protocolo.md`, este ficheiro e `ESTADO.md` com o comportamento final.
- [ ] 10. Segundo servidor (`S2`, 2 fluxos cada) em `cenario2.json` e `cenario3.json`.
- [ ] 11. Ensaios no CORE (lista abaixo).

## Plano de testes

- [x] Unidade: atualização da rota quando chega um caminho de menor custo.
- [x] Unidade: duplicados e anúncios antigos não alteram incorretamente a tabela.
- [x] Unidade: inundação não envia anúncio de volta ao vizinho de entrada.
- [ ] Integração CORE: servidor → nó intermédio → cliente.
- [ ] Integração CORE: dois clientes partilham o troço comum sem encaminhamento duplicado nesse troço.
- [ ] Integração CORE: caminhos alternativos escolhem o custo menor.
- [x] Unidade: queda do vizinho de montante invalida a rota e anúncio alternativo reativa subscrições.
- [x] Unidade: queda de um destino remove o ramo e desativa a subscrição a montante.
- [x] Unidade: caminho melhor troca o montante e move subscrições ativas.
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
  época 1 de um servidor reiniciado; rota com caminho rejeita épocas antigas.
- [x] Integração localhost: servidor morto e reiniciado (época 1) é reaprendido e o ramo
  S1–A–C volta a abrir.
- [x] RPF (`tests/test_forwarding.py`, UDP real em loopback): só o montante é entregue e
  reencaminhado; após troca de montante, o antigo é descartado; sem rota ou sem caminho tudo é
  descartado; o próprio fluxo que volta ao nó de origem não é reencaminhado.
- [ ] CORE: ativar dois clientes e desativar um mantém o outro a receber; desativar o
  último deixa a rota sem tráfego.
- [ ] CORE: exercitar as topologias 1 e 2 antes do cenário 3.

Ensaios no CORE necessários para dar a etapa como validada:

1. Cenário 1: S1 → O2 → C1 a reproduzir.
2. Dois clientes: um só fluxo no troço comum (contadores `--stats` e `tcpdump`).
3. Desativar um cliente mantém o outro; desativar o último deixa a rota sem tráfego.
4. Cenário 2: o caminho alternativo escolhe o custo menor.
5. Queda do montante: tempo até o cliente voltar a receber.
6. Reinício do servidor: o fluxo regressa (depende do passo 6).
7. Cenário 3 com 2 servidores, 2 fluxos cada e clientes em ramos partilhados.
8. `tc netem` em O3–O5 (cenário 3): O5 troca para O7 uma só vez, sem oscilar (passo 5).
9. Matar e reiniciar o servidor: o `WITHDRAW` chega aos clientes e o fluxo regressa com a época 1.

Comandos:

```sh
PYTHONPATH=src python3 -m unittest tests.test_routing -v        # modelo (nós falsos)
PYTHONPATH=src python3 -m unittest tests.test_integration -v    # nós reais em localhost
PYTHONPATH=src python3 -m unittest tests.test_forwarding -v     # RPF no plano de dados
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
