# Protocolo de controlo do overlay – especificação

Implementação de referência: `src/ott/protocol.py` (formato e validação) e `src/ott/node.py` (comportamento).
Esta especificação cobre as mensagens do elemento 1 (overlay), o controlo de rotas e o
encaminhamento de dados da Etapa 2. `ANNOUNCE`, `ACTIVATE` e `DEACTIVATE` são tratados por
`src/ott/routing.py`; o RTP/JPEG segue por UDP através de `src/ott/forwarding.py`.

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
| `BYE` | nó → vizinho | `reason` (opcional; `"duplicate"` ao fechar uma ligação duplicada) | saída limpa; o vizinho fecha a ligação sem esperar timeout |
| `REGISTER` | nó → bootstrapper | – | ao entrar no overlay (`ott -b`) |
| `REPLACE` | nó → bootstrapper | `have` (lista de ids), `lost` (lista de ids), `need` (inteiro > 0) | nó com menos de X vizinhos há mais de `replace_interval` s |
| `NEIGHBORS` | bootstrapper → nó | `neighbors`: lista de `{id, host, port}` | resposta a `REGISTER` ou `REPLACE` (pode ser lista vazia) |
| `ERROR` | bootstrapper → nó | `reason` (string) | nó desconhecido do ficheiro de topologia |
| `ANNOUNCE` | servidor → overlay | `server_id`, `flow`, `epoch`, `metric` | anúncio periódico de um fluxo; reencaminhado pelos nós |
| `ACTIVATE`, `DEACTIVATE` | nó → vizinho de montante | `server_id`, `flow` (`subscriber_id` opcional, informativo) | abrir/fechar o ramo deste nó para um fluxo (subscrições agregadas) |
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
{"type":"ACTIVATE","src":"O5","data":{"server_id":"S1","flow":"filme_a"}}
{"type":"DEACTIVATE","src":"C1","data":{"server_id":"S1","flow":"filme_a","subscriber_id":"C1"}}
```

Nos exemplos de rota, `src` muda quando a mensagem é reenviada por um nó intermédio;
`server_id` e `flow` mantêm a identidade original. `subscriber_id`, quando existe, indica
apenas quem abriu ou fechou o ramo (útil nos logs); a subscrição é identificada pelo par
(vizinho emissor, rota).

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

Como cada lado só vê as ligações à medida que chegam, um nó pode já ter adotado (e dado como `up`) a
ligação que o outro lado vai fechar. Por isso quem fecha uma duplicada envia antes `BYE` com
`reason: "duplicate"`. Quem o recebe numa ligação que tinha como ativa retira-a **sem disparar `down`** e
espera até `DUPLICATE_GRACE` (3 s) pela ligação vencedora, que então é adotada **sem novo `up`**. Se a
vencedora não chegar a tempo, dispara-se o `down`. Um `BYE` sem `reason` é uma saída normal (`down` imediato).

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
As subscrições são **agregadas por ramo**: cada nó guarda os vizinhos a jusante que pediram o
fluxo (`destinations`) e os subscritores locais, e só envia `ACTIVATE` ao seu `upstream`
quando a procura passa de zero para um. Os pedidos seguintes que chegam a um nó já ativo
ficam nesse nó. O servidor não reencaminha o pedido.

```
C4 (O10) --ACTIVATE--> O5 --ACTIVATE--> O3 --ACTIVATE--> O2 --ACTIVATE--> O1
C5 (O11) --ACTIVATE--> O5          (O5 já tem procura: não envia nada)
C4 sai   --DEACTIVATE-> O5         (ainda há O11: não envia nada)
C5 sai   --DEACTIVATE-> O5 --DEACTIVATE--> O3 --> O2 --> O1   (procura chegou a zero)
```

Ao terminar, o cliente chama `RoutingTable.deactivate(...)`. Cada nó retira o vizinho dos
destinos e só envia `DEACTIVATE` ao montante quando a procura volta a zero. Pedidos repetidos
são idempotentes; um `ACTIVATE` vindo do próprio montante é rejeitado. Quando um anúncio muda
o montante de uma rota com procura, o nó envia um só `DEACTIVATE` ao antigo e um só
`ACTIVATE` ao novo. Se cair o montante, a procura fica pendente e a rota passa a inativa;
quando é aprendida uma alternativa, o ramo é aberto pelo novo montante. Se o envio de um
pedido falhar, a queda da ligação tem o mesmo efeito. Ainda não existe confirmação de
subscrição.

### 3.6 Encaminhamento dos dados (Etapa 2)

O controlo continua em TCP. Os pacotes identificados por `(server_id, flow)` são enviados
por UDP na porta 6000. `OverlayForwarder` obtém os destinos com
`RoutingTable.active_destinations(...)`, encaminha uma cópia por vizinho a jusante e exclui
o vizinho de entrada. Vários subscritores no mesmo ramo partilham uma única cópia nesse
troço.

**RPF estrito.** Um datagrama recebido só é entregue ao recetor local e reencaminhado se vier
do vizinho de montante atual da rota (`RoutingTable.upstream_of`). Datagramas de outros
vizinhos, de fluxos sem rota, de rotas sem caminho ou do próprio fluxo do nó são descartados
e contados (`rpf_dropped`). Isto evita cópias duplicadas quando o montante muda e quebra
ciclos de dados transitórios; o custo é uma falha curta durante a troca de montante, até o
novo montante começar a enviar. O vizinho de entrada é identificado pelo IP de origem do
datagrama, comparado com o IP da ligação TCP a esse vizinho. O servidor só emite datagramas quando existem destinos ativos; no cliente, os pacotes
são entregues ao recetor RTP local. O formato do datagrama está definido em
`src/ott/streaming/rtp.py`.

Na CLI, `--publish-flow ID` anuncia e publica o MJPEG desse fluxo; `--subscribe
SERVIDOR:FLUXO` ativa a subscrição. Todos os nós do caminho têm de executar `ott` para
receber e encaminhar os datagramas. `--stats S` mostra estatísticas de receção e encaminhamento.

## 4. Interface para os outros módulos (`OverlayNode`)

| Chamada | Efeito |
|---|---|
| `register_handler(tipo, fn)` | `fn(node, vizinho_id, msg)` para mensagens desse tipo (rotas, ROUTE_TEST, ...); `vizinho_id` é o ID (string) do vizinho emissor |
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
| `DUPLICATE_GRACE` | 3 s | tempo para a ligação vencedora chegar depois de o vizinho fechar uma duplicada |

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
propagado, excluindo o vizinho emissor. `ACTIVATE`/`DEACTIVATE` acrescentam ou retiram o
vizinho emissor de `destinations` e são encaminhadas unicast pelo `upstream`, com o nó que
reencaminha como `src`. Cada nó propaga `ACTIVATE` apenas quando a procura (subscritores
locais ou destinos) passa de zero para um e `DEACTIVATE` quando volta a zero. O cliente usa
`RoutingTable.activate(node, server_id, flow)` e `deactivate(...)`; por omissão, o ID overlay
local identifica o subscritor local. Estas mensagens não têm confirmação de entrega.

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
| `ACTIVATE` | `server_id`, `flow` | identificam a rota pedida; o ramo é o do vizinho emissor |
|  | `subscriber_id` (opcional) | string não vazia; só informativo (quem abriu o ramo) |
| `DEACTIVATE` | `server_id`, `flow` | identificam o ramo que o vizinho emissor está a fechar |
|  | `subscriber_id` (opcional) | string não vazia; só informativo |

Um anúncio é identificado por `(server_id, flow, epoch)`. Vários anúncios com a mesma
identidade podem chegar por caminhos diferentes; o nó pode substituir o custo e o vizinho
de montante quando recebe um custo menor na mesma época, e deve propagar essa melhoria.
Uma época inferior à já guardada para a mesma rota é antiga e não deve substituir o estado
mais recente. `epoch` é iniciado em 1 e incrementado pelo servidor por fluxo; os nós não
comparam relógios entre si.

Cada vizinho conta no máximo uma vez nos destinos de uma rota. Repetir `ACTIVATE` não cria
um ramo adicional; repetir `DEACTIVATE` é inofensivo. Localmente, cada `subscriber_id` conta
uma vez em `local_subscribers`. O servidor não conhece os clientes individuais, apenas os
ramos que lhe pedem o fluxo.

Os validadores de `src/ott/protocol.py` aplicam estes campos tanto no envio como na receção.
Os testes de esquema estão em `tests/test_protocol.py`. O tratamento das mensagens está em
`src/ott/routing.py`; a integração de dados e os ensaios pendentes estão descritos em
`docs/ETAPA2.md`.
