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
- `seen_announcements`: identidades de anúncios processados, no formato
  `servidor:fluxo:epoch`.
- `epoch`: época mais recente do anúncio que atualizou a rota.

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

Aprende uma rota anunciada pelo servidor ou por um nó intermédio.

1. Lê `server_id`, `flow`, `epoch` e `metric`. O overlay já validou os campos da
   mensagem antes de chamar o handler.
2. Ignora anúncios que regressaram ao próprio servidor.
3. Soma `1` à métrica para contabilizar o salto até ao nó atual.
4. Ignora uma época mais antiga que a guardada.
5. Na mesma época, só aceita uma métrica menor. Uma época mais recente pode
   atualizar a rota mesmo que o custo seja maior.
6. Recusa escolher como montante um vizinho que já seja destino a jusante.
7. Guarda a métrica, época e novo `upstream`; atualiza o estado.
8. Se o montante mudou e a rota tem procura, envia um só `DEACTIVATE` pelo
   caminho antigo e um só `ACTIVATE` pelo novo (o ramo é agregado).
9. Reenvia o anúncio atualizado aos outros vizinhos, excluindo o emissor.

Exemplo: `O4` tem custo 3 via `O3` e recebe de `O5` um anúncio cujo custo é 1.
O custo candidato de `O4` passa a 2 (`1 + 1`). Como é melhor, `upstream` muda
para `O5`; o ramo é movido e o anúncio com métrica 2 é reenviado.

## Reação a alterações de vizinhos

### `handle_neighbor_down(node, neighbor)`

Atualiza todas as rotas quando um vizinho cai.

- Se o vizinho era `upstream`, define `upstream=None`, coloca `metric=math.inf` e
  deixa a rota inativa. Conserva a procura para abrir o ramo quando aprender um
  caminho alternativo.
- Se era destino a jusante, retira-o de `destinations`. Se a rota ficar sem
  procura, envia `DEACTIVATE` ao montante ainda ligado.
- Recalcula `state`.

### `handle_neighbor_up(node, neighbor)`

Envia ao novo vizinho um `ANNOUNCE` para cada rota válida e com época conhecida.
Não envia uma rota ao próprio `upstream`, para evitar anunciar o caminho de
volta por onde a aprendeu. O servidor pode também voltar a anunciá-la na ronda
periódica seguinte.

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
3. Cria uma mensagem `ANNOUNCE` com `src` e `server_id` iguais ao ID do servidor.
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
- A rota usa o menor número de saltos aprendido pelos anúncios. Não se escolhe
  intencionalmente um percurso mais longo apenas por já estar ativo.
- As mensagens de subscrição não têm confirmação de entrega.
- As épocas do publicador são mantidas em memória e começam novamente quando o
  processo do servidor é reiniciado; os nós rejeitam essas épocas antigas até à
  implementação do `WITHDRAW` (passo 6 em `ETAPA2.md`).
