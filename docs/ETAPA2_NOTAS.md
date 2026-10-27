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
subscriptions = {
    "C1": {"C1"},
    "C2": {"C2"},
}
destinations = {"C1", "C2"}
state = "ativa"
```

As chaves de `subscriptions` são os vizinhos de jusante. Os valores são os IDs
dos clientes cujos pedidos chegaram por cada vizinho. Se ambos os clientes
estivessem atrás de `O5`, a entrada em `O4` seria `{"O5": {"C1", "C2"}}` e
`destinations` seria `{"O5"}`.

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
- `destinations`: vizinhos a jusante que têm pelo menos um subscritor.
- `subscriptions`: mapa de vizinho a jusante para os IDs dos clientes que pediram
  o fluxo através desse vizinho.
- `local_subscribers`: subscrições que terminam neste nó, como no próprio cliente.
- `state`: fica `"ativa"` quando há subscritores e um caminho válido até ao
  servidor; caso contrário, fica `"inativa"`.
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

### `_subscriber_ids(route)`

Devolve um conjunto com os IDs registados localmente e em todos os ramos a
jusante. É usado quando uma rota muda de montante, para mover os pedidos ativos
para o novo caminho.

### `_has_subscriber(route, subscriber)`

Verifica se um cliente continua subscrito localmente ou através de algum vizinho
a jusante. Se o mesmo cliente existir por mais de um ramo, remover um dos ramos
não deve cancelar a subscrição restante.

### `_subscriber_count(route)`

Conta os registos de subscrição locais e a jusante. É usado para determinar se
a rota tem procura ativa.

### `_refresh_state(route, node_id=None)`

Atualiza duas partes da entrada:

1. Reconstrói `destinations` com os vizinhos cujos conjuntos de subscritores não
   estão vazios.
2. Define `state` como `"ativa"` se houver subscrições e um caminho válido.

O caminho é válido se este nó for o servidor da rota (`route.origin == node_id`)
ou se tiver um vizinho `upstream`.

### `_route_message(node, msg_type, route, subscriber_id)`

Constrói uma mensagem de controlo de subscrição. `src` passa a ser o ID do nó
que a envia; `server_id`, `flow` e `subscriber_id` identificam a subscrição
original.

## Subscrições locais

### `activate(node, origin, flow, subscriber_id=None)`

Chamado pelo cliente quando quer receber um fluxo.

1. Usa `node.id` como `subscriber_id` se não for indicado outro.
2. Valida o ID e procura a rota.
3. Se não houver rota, devolve `False`.
4. Se esse subscritor já estiver registado localmente, devolve `True` sem duplicar
   a ativação.
5. Regista a subscrição e atualiza o estado.
6. Se houver `upstream`, envia-lhe `ACTIVATE`.

Se a rota existir mas estiver temporariamente sem montante, guarda a subscrição
local como pendente. Quando surgir uma rota alternativa, o handler do anúncio
reativa as subscrições.

Exemplo: se o cliente `C1` tiver a rota `S1/filme_a` com `upstream="O4"`, a
chamada envia `ACTIVATE` a `O4`.

### `deactivate(node, origin, flow, subscriber_id=None)`

Remove uma subscrição local. Se ela existia, atualiza o estado e envia
`DEACTIVATE` pelo `upstream`, quando houver um. Se não existir rota ou subscrição
local, devolve `False`.

## Handlers de mensagens

### `handle_activate(node, neighbor, msg)`

Trata um `ACTIVATE` recebido de um vizinho a jusante.

1. Extrai servidor, fluxo e `subscriber_id` da mensagem.
2. Procura a rota. Recusa a mensagem se não conhecer a rota ou se ela veio do
   `upstream` — uma ativação deve chegar pelo lado dos clientes.
3. Regista o subscritor sob o vizinho emissor.
4. Se o subscritor já estiver registado noutro ramo deste nó, guarda também este
   ramo, mas não cria um segundo registo a montante.
5. Caso seja uma nova subscrição, envia `ACTIVATE` ao `upstream`, se existir.
6. Se o envio falhar, remove o registo acabado de inserir e atualiza o estado.

No servidor, `upstream` é `None`: o pedido fica registado como destino e não é
reencaminhado mais longe.

### `handle_deactivate(node, neighbor, msg)`

Remove do ramo indicado o subscritor que pediu para sair.

1. Se a rota não existir, devolve `False`.
2. Se esse subscritor não estiver registado pelo vizinho, trata a repetição como
   inofensiva e devolve `True`.
3. Remove o cliente do conjunto desse vizinho e apaga a chave se o conjunto ficar
   vazio.
4. Atualiza `destinations` e `state`.
5. Se o cliente ainda existir localmente ou por outro ramo, não propaga a
   desativação.
6. Caso contrário, envia `DEACTIVATE` ao `upstream`, se existir.

Assim, se `C1` cancelar em `O4`, o ramo de `C2` continua ativo. A desativação de
`C1` percorre os montantes até ao servidor para remover esse cliente específico.

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
8. Se o montante mudou, envia `DEACTIVATE` pelo caminho antigo e `ACTIVATE` pelo
   novo para cada subscritor ainda ativo.
9. Reenvia o anúncio atualizado aos outros vizinhos, excluindo o emissor.

Exemplo: `O4` tem custo 3 via `O3` e recebe de `O5` um anúncio cujo custo é 1.
O custo candidato de `O4` passa a 2 (`1 + 1`). Como é melhor, `upstream` muda
para `O5`; as subscrições são movidas e o anúncio com métrica 2 é reenviado.

## Reação a alterações de vizinhos

### `handle_neighbor_down(node, neighbor)`

Atualiza todas as rotas quando um vizinho cai.

- Se o vizinho era `upstream`, define `upstream=None`, coloca `metric=math.inf` e
  deixa a rota inativa. Conserva as subscrições para reaplicá-las quando aprender
  um caminho alternativo.
- Se era destino a jusante, remove as subscrições desse ramo. Para clientes que
  deixaram de existir noutro sítio, envia `DEACTIVATE` ao montante ainda ligado.
- Recalcula `destinations` e `state`.

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

## Limites atuais

- `ACTIVATE` e `DEACTIVATE` atualizam o plano de controlo; ainda não encaminham
  pacotes de vídeo.
- As quedas e recuperações de vizinhos têm testes unitários, mas ainda precisam
  de ensaios de integração no CORE.
- As mensagens de subscrição não têm confirmação de entrega.
- As épocas do publicador são mantidas em memória e começam novamente quando o
  processo do servidor é reiniciado.
