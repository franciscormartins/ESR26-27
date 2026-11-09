# Notas sobre `src/ott/routing.py`

Este documento explica o que cada classe e função de `routing.py` faz, com um
exemplo de percurso e subscrição.

## Exemplo usado

Considera esta topologia overlay para o fluxo `filme_a`:

```text
S1 → O2 → O3 → O4 → C1
                  └→ C2
```

`S1` é o servidor. Em `O4`, o vizinho de montante é `O3`; `C1` e `C2` são
destinos a jusante. A rota local de `O4` pode ter este aspeto:

```python
origin = "S1"
flow = "filme_a"
upstream = "O3"
destinations = {"C1", "C2"}
local_subscribers = set()
state = "ativa"
```

As subscrições são **agregadas por ramo**: `destinations` guarda os vizinhos de
jusante que pediram o fluxo, não os clientes. Se `C1` e `C2` estivessem ambos
atrás de `O5`, `destinations` em `O4` seria apenas `{"O5"}`, e `O4` nem saberia
quantos clientes há atrás de `O5`.

## Constantes e estados

- `INACTIVE` é o texto `"inativa"`.
- `ACTIVE` é o texto `"ativa"`.
- `log` é o logger do módulo, usado para registar erros no processamento dos
  eventos de vizinhança.

## `RouteEntry`

`RouteEntry` representa o estado local de uma rota para um par servidor/fluxo.
Cada nó mantém a sua própria entrada; as entradas não são partilhadas entre nós.

- `flow`: nome estável do conteúdo ou stream, por exemplo `"filme_a"`.
- `origin`: ID overlay do servidor que origina o fluxo, por exemplo `"S1"`.
- `metric`: custo conhecido desde o servidor, inicialmente contado em saltos.
  `math.inf` indica que o nó perdeu o caminho e ainda não aprendeu uma alternativa.
- `upstream`: vizinho que leva na direção do servidor. É `None` no servidor ou
  enquanto a rota estiver invalidada.
- `destinations`: vizinhos a jusante que pediram o fluxo (cada um conta uma vez).
- `local_subscribers`: subscrições que terminam neste nó, como no próprio cliente.
- `state`: fica `"ativa"` quando há procura (subscritores locais ou destinos) e
  um caminho válido até ao servidor; caso contrário, fica `"inativa"`.
- `epoch`: época mais recente conhecida para a rota.
- `delay`: atraso acumulado estimado até ao servidor, em segundos (soma de
  metade do RTT de cada ligação); `math.inf` se desconhecido.
- `candidates`: mapa `vizinho -> Candidate(epoch, metric, delay)` com o último
  anúncio de cada vizinho. `metric` e `delay` são os valores anunciados, antes
  de somar o salto e a ligação até ao vizinho. O montante é escolhido entre eles.

### `RouteEntry.key`

Devolve `(origin, flow)`, a chave que identifica a rota na tabela local. Por
exemplo, para `S1` e `filme_a`, devolve `("S1", "filme_a")`.

## `RoutingTable`

`RoutingTable` armazena as entradas locais num dicionário indexado por
`(origin, flow)`. Um bloqueio protege as alterações concorrentes feitas por
threads diferentes.

### `__init__`

Cria o dicionário de rotas, o bloqueio, a fila de eventos de vizinhança e o
estado da thread que processa esses eventos.

### `get(origin, flow)`

Procura a rota local. Devolve a `RouteEntry` correspondente ou `None` se ainda
não existir.

```python
route = table.get("S1", "filme_a")
```

### `put(entry)`

Verifica que recebeu uma `RouteEntry`, que origem e fluxo não estão vazios e que
a métrica não é negativa. Depois guarda ou substitui a entrada com a mesma chave.

### `remove(origin, flow)`

Remove e devolve a rota indicada. Se não existir, devolve `None`.

### `active_destinations(origin, flow, exclude=())`

Devolve os vizinhos a jusante de uma rota ativa, excluindo os IDs indicados.
O encaminhador UDP usa isto para não devolver um pacote à ligação de entrada;
como o resultado é por vizinho, vários clientes no mesmo ramo recebem uma só
cópia nesse troço.

### `upstream_of(origin, flow)`

Devolve o vizinho de montante atual, ou `None` se a rota não existir, se este
nó for a origem ou se a rota estiver sem caminho. O encaminhador usa-o para o
RPF estrito: só aceita datagramas vindos deste vizinho.

### `publish_local(origin, flow, epoch)` e `has_local_subscription(origin, flow)`

O publicador atualiza a rota local de origem com o bloqueio da tabela. O
encaminhador usa `has_local_subscription` para entregar o RTP ao recetor local
quando o fluxo está ativo.

### `attach(node)`

Liga uma tabela a um `OverlayNode`:

1. Regista handlers para `ANNOUNCE`, `ACTIVATE` e `DEACTIVATE`.
2. Regista callbacks para os eventos de vizinho ligado e desligado.
3. Evita registar tudo outra vez se a mesma tabela já estiver associada ao mesmo
   objeto `node`.

É a chamada feita pela CLI durante o arranque do nó.

### `_queue_neighbor_event(event, node, neighbor)`

Coloca um evento de vizinhança na fila. Se ainda não existir uma thread a
consumi-la, inicia uma thread daemon. O callback do overlay pode assim regressar
sem executar todo o trabalho de atualização da tabela.

### `_event_loop()`

Consome eventos da fila pela ordem em que chegaram. Para `"up"`, chama
`handle_neighbor_up`; para `"down"`, chama `handle_neighbor_down`. Se ocorrer
uma exceção, regista-a no log e continua a processar eventos seguintes.

## Funções auxiliares de subscrições

### `_has_demand(route)`

Indica se a rota tem procura: pelo menos um subscritor local ou um vizinho em
`destinations`. As transições de "sem procura" para "com procura" (e vice-versa)
são as únicas que geram mensagens para o montante.

### `_refresh_state(route, node_id=None)`

Define `state` como `"ativa"` se houver procura e um caminho válido. O caminho é
válido se este nó for o servidor da rota (`route.origin == node_id`) ou se tiver
um vizinho `upstream`.

### `_route_message(node, msg_type, route, subscriber_id=None)`

Constrói um `ACTIVATE` ou `DEACTIVATE`. `src` passa a ser o ID do nó que a
envia; `server_id` e `flow` identificam a rota. `subscriber_id` só é incluído se
for indicado e serve apenas de informação nos logs.

Se um envio falhar, o nó descarta a ligação e dispara `neighbor_down`: a rota
fica sem caminho mas mantém a procura, e o `ACTIVATE` é reenviado ao montante
aprendido a seguir. Por isso as funções abaixo não desfazem o registo quando o
envio falha.

## Subscrições locais

### `activate(node, origin, flow, subscriber_id=None)`

Chamado pelo cliente quando quer receber um fluxo.

1. Usa `node.id` como `subscriber_id` se não for indicado outro.
2. Valida o ID e procura a rota. Se não houver rota, devolve `False`.
3. Se esse subscritor já estiver registado localmente, devolve `True`.
4. Regista o subscritor e atualiza o estado.
5. Se a rota não tinha procura e há `upstream`, envia-lhe `ACTIVATE`.

Se a rota estiver temporariamente sem montante, a procura fica pendente e o
handler do anúncio abre o ramo quando surgir uma rota alternativa.

### `deactivate(node, origin, flow, subscriber_id=None)`

Remove o subscritor local. Se a rota ficar sem procura, envia `DEACTIVATE` pelo
`upstream`, quando houver um. Se não existir rota ou subscrição local, devolve
`False`.

## Handlers de mensagens

### `handle_activate(node, neighbor, msg)`

Trata um `ACTIVATE` recebido de um vizinho a jusante.

1. Procura a rota. Recusa a mensagem se não conhecer a rota ou se ela veio do
   `upstream` — uma ativação deve chegar pelo lado dos clientes.
2. Se o vizinho já estiver em `destinations`, é uma repetição: devolve `True`.
3. Acrescenta o vizinho a `destinations` e atualiza o estado.
4. Só se a rota não tinha procura antes envia `ACTIVATE` ao `upstream`.

No servidor, `upstream` é `None`: o pedido fica registado como destino e não é
reencaminhado mais longe.

### `handle_deactivate(node, neighbor, msg)`

Fecha o ramo do vizinho que pediu para sair.

1. Se a rota não existir, devolve `False`.
2. Se o vizinho não estiver em `destinations`, trata a repetição como inofensiva.
3. Retira o vizinho e atualiza o estado.
4. Só se a rota ficar sem procura envia `DEACTIVATE` ao `upstream`.

Assim, se `C1` cancelar em `O4`, o ramo de `C2` continua ativo e nada sobe. Só
quando `C2` também sair é que `O4` fecha o seu ramo em `O3`.

### `handle_announce(node, neighbor, msg)`

Regista o anúncio como candidato do vizinho emissor e reavalia o montante.

1. Ignora anúncios do próprio nó (fecharam um ciclo) e de vizinhos que são
   destinos a jusante (nunca podem ser montante).
2. Se a rota está **sem caminho** e a época é inferior à guardada, aceita-a na
   mesma: apaga os candidatos e recomeça a contagem (servidor reiniciado).
3. Com caminho, ignora épocas antigas; ignora também uma cópia igual à última
   recebida desse vizinho.
4. Guarda `candidates[vizinho]`. Se a época avançou, atualiza `epoch` e apaga
   candidatos com mais de uma ronda de atraso.
5. Chama `_select` e aplica o resultado (`upstream`, `metric`, `delay`).
6. Se o montante mudou e há procura, envia um só `DEACTIVATE` ao antigo e um só
   `ACTIVATE` ao novo.
7. Propaga o anúncio (a todos menos o emissor e o montante) se a época avançou,
   o montante mudou ou o número de saltos mudou. Devolve `True` se propagou.

Exemplo: `O4` usa `O3` com 3 saltos. Na ronda 8 chega primeiro a cópia de `O5`
com 4 saltos: `O3` continua válido (época 7, é o montante atual) e mantém-se;
`O4` propaga a época 8 com 3 saltos. Quando chegar a cópia de `O3` da época 8,
nada muda e nada é propagado.

### `_select(node, route, feasible_below=None)` e auxiliares

- `_link_delay(node, vizinho)`: metade de `node.rtt(vizinho)`, ou infinito.
- `_cost(...)`: custo via um vizinho, `(metric + 1, delay + ligação)`.
- `_valid_candidate(...)`: candidato da época atual; o montante atual vale
  também com a época anterior. Destinos a jusante nunca são válidos.
- `_delay_better(novo, atual)`: melhoria de atraso acima da histerese
  (`delay_hysteresis`, 20 %, e `delay_min_gain`, 1 ms).
- `_select`: entre os candidatos válidos, menos saltos ganha sempre; com os
  mesmos saltos, só troca de montante se `_delay_better`; com ambos os atrasos
  desconhecidos, ganha o menor ID. Com `feasible_below`, só servem candidatos
  que anunciaram menos saltos do que esse valor (condição de viabilidade).

### `handle_withdraw(node, neighbor, msg)`

O vizinho perdeu o caminho: é retirado dos candidatos. Se era o montante,
chama `_lose_upstream` (com o antigo montante ainda ligado).

### `expire(node, now=None)`, `_expiry_loop` e `close()`

Rede de segurança do passo 7. Com `RoutingTable(route_ttl=S)`, `attach` inicia
uma thread que chama `expire` de `S/4` em `S/4` segundos (no máximo 1 s).
`expire` esquece os candidatos cujo `seen_at` tem mais de `S` segundos. Se um
deles era o montante, chama `_lose_upstream(..., still_connected=True)`:
reparação rápida com um candidato ainda recente ou `WITHDRAW`. As rotas
originadas no próprio nó são ignoradas. Devolve o número de candidatos
esquecidos. `close()` pára a thread. Sem `route_ttl`, `expire` não faz nada.

Uma cópia repetida de um anúncio não é propagada, mas substitui o candidato
para renovar o `seen_at`.

### `_lose_upstream(node, route, still_connected)`

Chamada sob o bloqueio quando o montante cai ou envia `WITHDRAW`.

1. Remove o candidato perdido e, se o antigo montante ainda está ligado e havia
   procura, envia-lhe `DEACTIVATE`.
2. Tenta a reparação rápida: `_select` com `feasible_below` igual aos saltos da
   rota perdida. Se houver alternativa, abre o ramo nela (`ACTIVATE`) e propaga
   o anúncio.
3. Sem alternativa: rota sem caminho (`metric` e `delay` infinitos), apaga
   candidatos e destinos (os subscritores locais ficam pendentes) e envia
   `WITHDRAW` a todos os vizinhos.

Devolve a lista de envios, executados fora do bloqueio por `_run`.

## Reação a alterações de vizinhos

### `handle_neighbor_down(node, neighbor)`

Atualiza todas as rotas quando um vizinho cai.

- Se o vizinho era `upstream`, chama `_lose_upstream` (reparação ou retirada).
- Caso contrário, retira-o dos candidatos e de `destinations`. Se era destino e
  a rota ficou sem procura, envia `DEACTIVATE` ao montante.

### `handle_neighbor_up(node, neighbor)`

Envia ao novo vizinho um `ANNOUNCE` (com `delay`, se conhecido) para cada rota
válida e com época conhecida. Não envia uma rota ao próprio `upstream`. O
servidor volta também a anunciá-la na ronda periódica seguinte.

## `AnnouncementPublisher`

Esta classe origina anúncios dos fluxos disponibilizados neste nó. Na CLI, é
criada para o servidor quando se indicam opções `--publish-flow`.

### `__init__(node, flows, interval=5.0, routing_table=None)`

Verifica os fluxos, rejeita identificadores vazios ou repetidos e valida que o
intervalo é positivo e finito. Guarda uma época independente por fluxo. Se for
passada uma tabela, atualiza nela a rota local do servidor.

### `publish_once()`

Para cada fluxo:

1. Incrementa a época desse fluxo.
2. Cria ou atualiza a rota local do servidor com custo `0` e sem `upstream`.
3. Cria uma mensagem `ANNOUNCE` com `src` e `server_id` iguais ao ID do servidor,
   `metric=0` e `delay=0`.
4. Transmite a mensagem aos vizinhos com `node.broadcast`.

Devolve a lista de mensagens publicadas.

### `start()`

Se já estiver a correr, não cria outra thread. Caso contrário, limpa o sinal de
paragem, publica imediatamente a primeira ronda e inicia a thread periódica.

### `_run()`

Espera `interval` segundos entre rondas. Termina quando o evento de paragem é
ativado.

### `stop()`

Ativa o evento de paragem e aguarda a thread terminar. Pode ser chamada durante
o encerramento do servidor.

## Encaminhamento de dados e limites atuais

- `OverlayForwarder` escuta os datagramas RTP/JPEG em UDP e consulta
  `RoutingTable.active_destinations` para replicar pelos ramos ativos. O vizinho
  de entrada é excluído e clientes no mesmo ramo partilham uma cópia.
- RPF estrito: um datagrama só é entregue e reencaminhado se vier de
  `upstream_of(origin, flow)`; os restantes são contados em `rpf_dropped`.
- A CLI inicia publicadores com `--publish-flow` e subscrições com
  `--subscribe SERVIDOR:FLUXO`; ao terminar com Ctrl-C, desativa as subscrições.
- As quedas e recuperações de vizinhos têm testes unitários e de integração em
  localhost (`tests/test_integration.py`), mas ainda precisam de ensaios no CORE,
  incluindo continuidade do tráfego após falhas.
- A rota usa o menor número de saltos; o RTT só desempata caminhos com os mesmos
  saltos. Não se escolhe intencionalmente um percurso mais longo apenas por já
  estar ativo.
- Sem RTT medido (primeiros segundos de uma ligação), o desempate é pelo ID; quando
  o RTT aparece pode haver uma troca única de montante.
- As mensagens de subscrição não têm confirmação de entrega.
- As épocas do publicador são mantidas em memória e recomeçam em 1 quando o
  servidor é reiniciado; os nós aceitam-nas porque a queda do servidor provoca
  `WITHDRAW` e deixa as rotas sem caminho.
- O tempo de vida (`--route-ttl`, por omissão 6 × o intervalo de anúncio) só
  funciona se todos os nós usarem o mesmo `--announce-interval`.
