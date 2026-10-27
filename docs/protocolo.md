# Protocolo de controlo do overlay – especificação

Implementação de referência: `src/ott/protocol.py` (formato e validação) e `src/ott/node.py` (comportamento).
Esta especificação cobre as mensagens do elemento 1 (overlay) e o esquema das mensagens de
controlo de rotas da Etapa 2. `ANNOUNCE`, `ACTIVATE` e `DEACTIVATE` têm handlers em
`src/ott/routing.py`; o encaminhamento dos pacotes de vídeo ainda não está implementado.

## 1. Transporte e formato

- Controlo sobre **TCP**, porta por defeito **5000**. Uma só ligação por par de nós, full-duplex.
- Cada mensagem é um objeto JSON em UTF-8 **precedido por 4 bytes (big-endian) com o tamanho do JSON**.
  Tamanho máximo: 1 MiB.
- Envelope (igual para todas as mensagens):

```json
{"type": "<TIPO>", "src": "<id do nó emissor>", "data": { ... }}
```

- `type` tem de ser um dos tipos abaixo; `src` é uma string não vazia; `data` é sempre um objeto
  (vazio se o tipo não tiver campos).
- Uma mensagem que não cumpra o formato é **rejeitada**: ao enviar levanta `ProtocolError`;
  ao receber numa ligação entre vizinhos fecha essa ligação (e regista um aviso);
  ao receber no serviço de bootstrap fecha a ligação sem resposta.

Nota (decisão): o guião sugeria uma linha JSON terminada em `\n`. Usámos prefixo de tamanho porque
não depende de o JSON conter ou não quebras de linha e permite rejeitar mensagens gigantes antes de as ler.

## 2. Mensagens

| Tipo | Sentido | Campos em `data` (obrigatórios) | Quando |
|---|---|---|---|
| `HELLO` | nó ↔ nó | – | 1.ª mensagem de cada ligação (o que liga envia, o que aceita responde) |
| `PING` | nó → vizinho | `ts` (número) | de `keepalive` em `keepalive` segundos, a todos os vizinhos |
| `PONG` | vizinho → nó | `ts` (número, **o mesmo do PING**) | em resposta imediata a cada PING |
| `BYE` | nó → vizinho | – | saída limpa; o vizinho fecha a ligação sem esperar timeout |
| `REGISTER` | nó → bootstrapper | – | ao entrar no overlay (`ott -b`) |
| `REPLACE` | nó → bootstrapper | `have` (lista de ids), `lost` (lista de ids), `need` (inteiro > 0) | nó com menos de X vizinhos há mais de `replace_interval` s |
| `NEIGHBORS` | bootstrapper → nó | `neighbors`: lista de `{id, host, port}` | resposta a `REGISTER` ou `REPLACE` (pode ser lista vazia) |
| `ERROR` | bootstrapper → nó | `reason` (string) | nó desconhecido do ficheiro de topologia |
| `ANNOUNCE` | servidor → overlay | `server_id`, `flow`, `epoch`, `metric` | anúncio periódico de um fluxo; reencaminhado pelos nós |
| `ACTIVATE`, `DEACTIVATE` | cliente ↔ servidor via overlay | `server_id`, `flow`, `subscriber_id` | subscrever/remover um cliente de um fluxo |
| `ROUTE_TEST` | – | a definir (E2) | teste de caminhos (Etapa 3) |

`PING`/`PONG` cumprem o papel do "HELLO/KEEPALIVE (nó, timestamp)" do guião. O `ts` é o relógio
**monotónico do emissor do PING**; só ele o interpreta (o outro lado limita-se a devolvê-lo),
por isso não é preciso sincronizar relógios.

Exemplos:

```json
{"type":"HELLO","src":"O2","data":{}}
{"type":"PING","src":"O2","data":{"ts":12345.678}}
{"type":"PONG","src":"S1","data":{"ts":12345.678}}
{"type":"REGISTER","src":"O2","data":{}}
{"type":"NEIGHBORS","src":"S1","data":{"neighbors":[{"id":"C1","host":"10.0.1.21","port":5000}]}}
{"type":"REPLACE","src":"C1","data":{"have":[],"lost":["O2"],"need":1}}
{"type":"ERROR","src":"S1","data":{"reason":"nó desconhecido"}}
{"type":"ANNOUNCE","src":"S1","data":{"server_id":"S1","flow":"filme_a","epoch":1,"metric":0}}
{"type":"ANNOUNCE","src":"O2","data":{"server_id":"S1","flow":"filme_a","epoch":1,"metric":1}}
{"type":"ACTIVATE","src":"C1","data":{"server_id":"S1","flow":"filme_a","subscriber_id":"C1"}}
{"type":"ACTIVATE","src":"O2","data":{"server_id":"S1","flow":"filme_a","subscriber_id":"C1"}}
{"type":"DEACTIVATE","src":"C1","data":{"server_id":"S1","flow":"filme_a","subscriber_id":"C1"}}
```

Nos exemplos de rota, `src` muda quando a mensagem é reenviada por um nó intermédio;
`server_id`, `flow` e `subscriber_id` mantêm a identidade original.

## 3. Interações

### 3.1 Entrada no overlay (estratégia 2, `ott -b`)

```
nó                      bootstrapper (servidor)
 |--- REGISTER ------------->|   lê o ficheiro de topologia
 |<-- NEIGHBORS [v1,v2,...] -|   (ou ERROR se o id não existe)
 |
 |--- HELLO ---------------> v1      (TCP novo para cada vizinho)
 |<-- HELLO ---------------- v1
 |   ... PING/PONG periódicos ...
```

A ligação ao bootstrapper é de curta duração (um pedido, uma resposta). O ficheiro de topologia é
escrito à mão (`topologies/*.json`); as ligações são bidirecionais e o nó volta a tentar ligar-se
aos vizinhos em baixo de `retry_interval` em `retry_interval` segundos.

Estratégia 1 (`ott viz1 viz2`): igual, sem REGISTER; os vizinhos são os endereços dados na linha de comandos.

### 3.2 Ligação duplicada

Se dois nós se ligarem um ao outro ao mesmo tempo existem duas ligações TCP. Ambos aplicam a mesma
regra: **mantém-se a ligação iniciada pelo nó de menor id** e a outra é fechada. O módulo de rotas
só vê um evento `up` por vizinho.

### 3.3 Vivacidade e RTT

- Cada nó envia `PING` a todos os vizinhos de 2 s em 2 s (`keepalive`).
- Qualquer mensagem recebida atualiza o "visto pela última vez". Se passarem mais de 6 s (`dead_after`)
  sem nada, o vizinho é dado como morto e a ligação fechada.
- O fecho da ligação TCP (processo morto, `BYE`) é detetado imediatamente, sem esperar pelo timeout.
- RTT = `monotonic() - ts` quando chega o `PONG`. Guarda-se uma **média móvel** (`0.3·amostra + 0.7·anterior`);
  amostras fora de [0, 60] s são descartadas.

### 3.4 Substituição de vizinhos

Opção `--min-neighbors X` (por omissão 0 = desligada):

```
nó fica com < X vizinhos durante replace_interval (5 s)
 |--- REPLACE {have, lost, need} --->|
 |<-- NEIGHBORS [candidatos] --------|
 |--- HELLO ---> candidato ...         (tem substitute_wait = 10 s para ligar)
```

O bootstrapper escolhe candidatos entre os nós que já se registaram (mais o seu próprio nó), excluindo
o pedinte, os vizinhos atuais (`have`) e os perdidos (`lost`); prefere os que já são vizinhos de algum
dos vizinhos atuais. Um candidato que não ligue a tempo é esquecido e passa a `lost`, de modo a
propor-se outro na tentativa seguinte. Se não houver candidatos, `lost` é limpo e o ciclo recomeça.

### 3.5 Ativação e desativação de um fluxo (Etapa 2)

O servidor é o único que origina rondas `ANNOUNCE`. Os nós intermédios retransmitem essas
mensagens e podem retransmitir uma nova cópia quando encontram um caminho melhor; não criam
épocas de anúncio próprias.

Quando o cliente quer receber um fluxo, chama `RoutingTable.activate(node, server_id, flow)`.
O pedido sobe pelo `upstream` até ao servidor. Em cada nó, `src` passa a identificar o nó
que reencaminha, `subscriber_id` mantém o ID original do cliente e o vizinho de entrada fica
registado como destino a jusante. O servidor não reencaminha o pedido.

Ao terminar, o cliente chama `RoutingTable.deactivate(...)`. A remoção percorre o mesmo
caminho inverso. Cada identidade de subscritor é propagada uma vez e removida da mesma
forma; se um ramo ainda tiver outros subscritores, mantém-se ativo. Mensagens repetidas são
idempotentes. Se cair o montante, as subscrições ficam pendentes e a rota passa a inativa;
quando é aprendida uma alternativa, os pedidos são ativados pelo novo montante. Ainda não
existe confirmação de subscrição nem tráfego de vídeo.

## 4. Interface para os outros módulos (`OverlayNode`)

| Chamada | Efeito |
|---|---|
| `register_handler(tipo, fn)` | `fn(node, vizinho, msg)` para mensagens desse tipo (rotas, ROUTE_TEST, ...) |
| `send(id, msg)` / `broadcast(msg, exclude=())` | envio a um vizinho / a todos menos os excluídos |
| `neighbor_ids()` | vizinhos ativos (lista ordenada) |
| `on_neighbor_up(fn)` / `on_neighbor_down(fn)` | `fn(node, id)` quando um vizinho liga / cai; correm nas threads do nó, têm de ser curtos |
| `rtt(id)` / `rtts()` | RTT em **segundos** (média móvel) de um vizinho / de todos os que já têm amostra; `None` se ainda não há |

## 5. Parâmetros

| Parâmetro | Valor por omissão | Significado |
|---|---|---|
| `keepalive` | 2 s | intervalo entre PING |
| `dead_after` | 6 s | silêncio máximo antes de declarar o vizinho morto |
| `retry_interval` | 2 s | intervalo entre tentativas de ligar a vizinhos em falta |
| `CONNECT_TIMEOUT` / `HANDSHAKE_TIMEOUT` | 3 s | ligar TCP / completar o HELLO |
| `min_neighbors` (X) | 0 | mínimo de vizinhos; 0 desativa os substitutos |
| `replace_interval` | 5 s | défice de vizinhos tolerado antes (e entre) pedidos REPLACE |
| `substitute_wait` | 10 s | tempo que um substituto tem para ligar |

## 6. Limitações conhecidas

- Cada nó tem **um só endereço** no ficheiro de topologia; um nó com várias interfaces (router) só
  é contactável pelo IP escolhido. Funciona porque basta um lado alcançar o outro.
- O bootstrapper só sabe que um nó está vivo porque se registou ou pediu substitutos; não o expira.
- O ficheiro de topologia é estático: um nó novo tem de existir no ficheiro.
- Não há autenticação nem cifra no controlo.

## 7. Modelo de estado das rotas (Etapa 2)

A estratégia decidida para a Etapa 2 é a **Estratégia 1 do enunciado**: anúncios periódicos
do servidor, propagados por inundação controlada; os clientes ativam a rota quando pedem o
fluxo. Esta secção fixa o estado local necessário antes de implementar as interações.

Cada entrada é identificada por `(servidor, fluxo)` e contém:

| Campo | Significado |
|---|---|
| `origin` | identificador do servidor que origina o fluxo |
| `flow` | identificador do conteúdo/stream multimédia transmitido (por exemplo, `filme_a` ou `camera_sala`) |
| `metric` | custo acumulado desde o servidor; a primeira métrica será número de saltos |
| `upstream` | vizinho do melhor caminho conhecido em direção ao servidor; `None` no servidor |
| `epoch` | ronda de anúncio mais recente guardada para esta rota |
| `destinations` | conjunto de vizinhos a jusante que pediram o fluxo |
| `state` | `inativa` ou `ativa`; ativa quando há subscritores locais/a jusante e caminho válido |
| `seen_announcements` | identificadores de anúncios já processados, para evitar repetições e ciclos |

O par `(origin, flow)` identifica uma transmissão: o mesmo servidor pode oferecer vários
conteúdos, e servidores diferentes podem usar o mesmo identificador de fluxo sem colisão.
O modelo está em `src/ott/routing.py` como `RouteEntry` e `RoutingTable`, indexada por
`(origin, flow)`. A tabela oferece operações `get`, `put` e `remove`; `attach(node)` instala
o handler de `ANNOUNCE`. O publicador guarda a rota local com métrica zero. Nos recetores,
a métrica aumenta em um salto; apenas uma época nova ou um custo melhor na época atual é
propagado, excluindo o vizinho emissor. `ACTIVATE`/`DEACTIVATE` atualizam as subscrições e
são encaminhadas unicast pelo `upstream`. O nó que reencaminha passa a ser o `src`, mas
mantém o `subscriber_id` original. Cada nó regista os subscritores por vizinho a jusante;
propaga `ACTIVATE` apenas quando passa de zero para uma subscrição e `DEACTIVATE` quando
remove a última. O cliente usa `RoutingTable.activate(node, server_id, flow)` e
`deactivate(...)`; por omissão, o ID overlay local é o `subscriber_id`. Estas mensagens
não têm confirmação de entrega.

### 7.1 Campos das mensagens de rota

Os campos seguintes são obrigatórios em `data`; o envelope mantém o formato comum
`{type, src, data}`. `src` identifica o vizinho que enviou esta cópia da mensagem e pode
mudar a cada salto. Os identificadores estáveis do servidor, conteúdo e cliente seguem nos
campos de `data`.

| Tipo | Campo | Tipo/semântica |
|---|---|---|
| `ANNOUNCE` | `server_id` | ID do nó servidor que origina o fluxo |
|  | `flow` | identificador estável do conteúdo/stream anunciado |
|  | `epoch` | inteiro positivo, crescente por servidor e fluxo a cada ronda de anúncio |
|  | `metric` | número finito ≥ 0; o servidor anuncia 0 e cada salto acrescenta 1 |
| `ACTIVATE` | `server_id`, `flow` | identificam a rota pedida |
|  | `subscriber_id` | ID estável do nó cliente que pediu o fluxo; usado para tornar ativações e desativações repetidas idempotentes |
| `DEACTIVATE` | `server_id`, `flow`, `subscriber_id` | identificam a subscrição que o cliente está a terminar; devem corresponder ao `ACTIVATE` |

Um anúncio é identificado por `(server_id, flow, epoch)`. Vários anúncios com a mesma
identidade podem chegar por caminhos diferentes; o nó pode substituir o custo e o vizinho
de montante quando recebe um custo menor na mesma época, e deve propagar essa melhoria.
Uma época inferior à já guardada para a mesma rota é antiga e não deve substituir o estado
mais recente. `epoch` é iniciado em 1 e incrementado pelo servidor por fluxo; os nós não
comparam relógios entre si.

Cada cliente usa o seu ID overlay como `subscriber_id` e mantém no máximo uma subscrição
ativa por `(server_id, flow)`. Repetir `ACTIVATE` não cria uma subscrição adicional; repetir
`DEACTIVATE` é inofensivo. Esta primeira versão não modela várias sessões do mesmo cliente
para o mesmo fluxo.

Os validadores de `src/ott/protocol.py` aplicam estes campos tanto no envio como na receção.
Os testes de esquema estão em `tests/test_protocol.py`. O tratamento e encaminhamento das
mensagens ainda pertencem às tarefas seguintes do roadmap `docs/ETAPA2.md`.
