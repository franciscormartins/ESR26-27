# Protocolo de controlo do overlay – especificação

Implementação de referência: `src/ott/protocol.py` (formato e validação) e `src/ott/node.py` (comportamento).
Esta especificação cobre as mensagens do elemento 1 (overlay), o controlo de rotas e o
encaminhamento de dados da Etapa 2. `ANNOUNCE`, `ACTIVATE`, `DEACTIVATE` e `WITHDRAW` são tratados por
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
| `ANNOUNCE` | servidor → overlay | `server_id`, `flow`, `epoch`, `metric` (`delay` opcional) | anúncio periódico de um fluxo; reencaminhado pelos nós |
| `WITHDRAW` | nó → vizinhos | `server_id`, `flow`, `epoch` | o nó perdeu o caminho para o fluxo e não tem alternativa viável |
| `ACTIVATE`, `DEACTIVATE` | nó → vizinho de montante | `server_id`, `flow` (`subscriber_id` opcional, informativo) | abrir/fechar o ramo deste nó para um fluxo (subscrições agregadas) |
| `ROUTE_TEST` | – | a definir (Etapa 3) | teste de caminhos (Etapa 3); ainda não implementado |

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
{"type":"ANNOUNCE","src":"S1","data":{"server_id":"S1","flow":"filme_a","epoch":1,"metric":0,"delay":0.0}}
{"type":"ANNOUNCE","src":"O2","data":{"server_id":"S1","flow":"filme_a","epoch":1,"metric":1,"delay":0.0007}}
{"type":"WITHDRAW","src":"O2","data":{"server_id":"S1","flow":"filme_a","epoch":7}}
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
épocas de anúncio próprias. A escolha do caminho e a reação a falhas estão em §3.7.

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
novo montante começar a enviar. O servidor só emite datagramas quando existem destinos
ativos; no cliente, os pacotes são entregues ao recetor RTP local.

**Vizinho de entrada (D26).** Cada datagrama leva no cabeçalho o id overlay do nó que o
enviou (campo `salto`); quem reencaminha substitui-o pelo seu. O recetor usa esse id, e não o
IP de origem, para saber de que vizinho veio o datagrama: um id que não é vizinho ativo é
descartado (`descartados`, sem contar como RPF). Antes (até 2026-11-10) usava-se o IP de
origem comparado com o IP da ligação TCP, o que falhou no CORE quando o emissor tinha várias
interfaces e a rota para o recetor saía por uma interface diferente da da ligação (limitação
L7). O pacote envia-se para o IP remoto da ligação TCP com o vizinho.

Formato do datagrama (versão 2, definido em `src/ott/streaming/rtp.py`):

```
"OT" (2 B) | versão = 2 (1 B) | len + origem | len + fluxo | len + salto | RTP (12 B + JPEG)
```

Cada identificador ocupa 1 byte de comprimento (1–255) seguido do texto UTF-8. Pacotes da
versão 1 (sem `salto`), truncados ou com identificadores vazios são rejeitados. Todos os nós
têm de usar a mesma versão.

Na CLI, `--publish-flow ID` anuncia e publica o MJPEG desse fluxo; `--subscribe
SERVIDOR:FLUXO` ativa a subscrição. Todos os nós do caminho têm de executar `ott` para
receber e encaminhar os datagramas. `--stats S` mostra estatísticas de receção e encaminhamento.

### 3.7 Escolha do caminho e reação a falhas (Etapa 2)

**Candidatos.** Cada nó guarda, por rota, o último `ANNOUNCE` de cada vizinho (época,
saltos e atraso anunciados). O custo via um vizinho é `(metric + 1, delay + RTT/2)`, em que
o RTT é a média móvel medida pelo PING/PONG; sem RTT medido, o atraso é infinito. São
candidatos válidos os vizinhos com a época atual e, para o montante atual, também a época
anterior: numa ronda nova, a primeira cópia a chegar não derruba o montante antes de este
enviar a sua. Um vizinho que está nos destinos a jusante nunca é candidato.

**Seleção** (sempre que chega um anúncio):

1. menos saltos ganha sempre;
2. com os mesmos saltos, troca-se de montante só se o atraso melhorar mais de 20 % e mais de
   1 ms (histerese, parâmetros de `RoutingTable`);
3. se ambos os atrasos forem desconhecidos, ganha o vizinho de menor ID (desempate
   determinista, independente da ordem de chegada).

O nó propaga o anúncio (a todos menos o emissor e o seu montante) quando a época avança,
quando o montante muda ou quando muda o número de saltos; ou seja, normalmente uma vez por
ronda. O campo `delay` só é incluído quando é conhecido.

**Perda do montante** (queda da ligação ou `WITHDRAW` vindo do montante):

1. *Reparação rápida.* Procura-se um candidato válido que tenha anunciado **menos saltos do
   que a rota perdida** (condição de viabilidade). Um vizinho cujo caminho passe por este nó
   anunciaria pelo menos os nossos saltos + 1, por isso nunca é escolhido. Se houver, o ramo
   passa para ele (`ACTIVATE`) e o novo anúncio é propagado, sem `WITHDRAW`.
2. *Retirada.* Sem alternativa viável, a rota fica sem caminho: os candidatos e os destinos a
   jusante são apagados (os subscritores locais ficam pendentes) e o nó envia `WITHDRAW` a
   todos os vizinhos. Quem o recebe retira esse vizinho dos candidatos; só se o emissor for o
   seu montante é que repete o procedimento, pelo que a retirada desce pela árvore.
3. Se o `WITHDRAW` veio do montante (ainda ligado) e havia procura, o nó envia-lhe também
   `DEACTIVATE`, para não ficarem destinos órfãos.

**Épocas depois de uma retirada (D27).** Uma rota que este nó retirou (`WITHDRAW`) guarda a
época em que a retirou e, durante uma retenção de `route_ttl` segundos, só aceita anúncios de
uma época **maior**. Os anúncios da época retirada ou anteriores que ainda estejam em trânsito
são ignorados: aceitá-los podia formar um ciclo fantasma entre nós sem caminho, que só o tempo
de vida desfazia (L9, visto no CORE). O servidor começa as épocas na hora do arranque em
milissegundos, por isso um servidor reiniciado anuncia épocas maiores do que as retiradas e é
aceite logo. Ao fim da retenção a rota volta a aceitar qualquer época (cobre um servidor
reiniciado com o relógio atrasado). Uma rota com caminho continua a rejeitar épocas antigas.
Depois de uma retirada, o caminho é reaprendido na ronda de anúncio seguinte (até
`--announce-interval`). Os nós a jusante voltam a pedir o fluxo (`ACTIVATE`) quando
reaprendem o caminho.

**Tempo de vida (rede de segurança).** Cada candidato guarda a hora da última receção; uma
cópia repetida também a renova. Um candidato sem anúncios há mais de `route_ttl` segundos
(`--route-ttl`, por omissão 6 × `--announce-interval` = 30 s) é esquecido. Se era o montante,
o nó segue o procedimento da perda do montante (reparação rápida ou `WITHDRAW`), enviando-lhe
`DEACTIVATE` se havia procura. Cobre casos em que a retirada não chegou (por exemplo, uma
ligação que caiu a meio da cascata). A verificação corre numa thread da tabela, de
`route_ttl / 4` em `route_ttl / 4` segundos (no máximo 1 s). As rotas originadas no próprio nó
nunca expiram.

```
servidor S1 morre ──> O2 (vizinho) perde o montante, sem alternativa ──WITHDRAW──> O3, O7
O3: montante era O2 ──> sem alternativa ──WITHDRAW──> O4, O5 ...   (desce pela árvore)
S1 reinicia (época = hora do arranque, > retirada) ──ANNOUNCE──> O2 (aceita) ──> O3 ... ──> clientes
clientes ──ACTIVATE──> ... ──> S1                                     (ramos reabertos)
```

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
| `--announce-interval` | 5 s | intervalo entre rondas de `ANNOUNCE`; igual em todos os nós |
| `--route-ttl` (`route_ttl`) | 6 × intervalo (30 s) | tempo sem anúncios ao fim do qual um candidato é esquecido; tem de ser maior que o intervalo |
| `delay_hysteresis` / `delay_min_gain` | 20 % / 1 ms | melhoria mínima de atraso para trocar de montante com os mesmos saltos |

## 6. Limitações conhecidas

- Cada nó tem **um só endereço** no ficheiro de topologia; um nó com várias interfaces (router) só
  é contactável pelo IP escolhido. Funciona porque basta um lado alcançar o outro.
- O bootstrapper só sabe que um nó está vivo porque se registou ou pediu substitutos; não o expira.
- O ficheiro de topologia é estático: um nó novo tem de existir no ficheiro.
- Não há autenticação nem cifra no controlo.
- Rotas (Etapa 2): o RTT só desempata caminhos com o mesmo número de saltos; uma troca de
  montante provoca uma falha curta no vídeo (RPF estrito); depois de um `WITHDRAW` sem
  alternativa, os ramos só reabrem quando o caminho é reaprendido (até um intervalo de
  anúncio); todos os nós têm de usar o mesmo `--announce-interval`; `ACTIVATE`/`DEACTIVATE`
  não têm confirmação; o servidor não conhece os clientes individuais (subscrições agregadas).
- O vizinho de entrada de um datagrama UDP é identificado pelo id no campo `salto` (D26),
  sem autenticação: um nó pode declarar o id de outro.

## 7. Modelo de estado das rotas (Etapa 2)

A estratégia decidida para a Etapa 2 é a **Estratégia 1 do enunciado**: anúncios periódicos
do servidor, propagados por inundação controlada; os clientes ativam a rota quando pedem o
fluxo. Esta secção descreve o estado local que cada nó mantém por rota.

Cada entrada é identificada por `(servidor, fluxo)` e contém:

| Campo | Significado |
|---|---|
| `origin` | identificador do servidor que origina o fluxo |
| `flow` | identificador do conteúdo/stream multimédia transmitido (por exemplo, `filme_a` ou `camera_sala`) |
| `metric` | número de saltos até ao servidor; `inf` quando a rota está sem caminho |
| `delay` | atraso acumulado estimado (s), soma de RTT/2 por ligação; `inf` se desconhecido |
| `upstream` | vizinho escolhido em direção ao servidor; `None` no servidor ou sem caminho |
| `epoch` | ronda de anúncio mais recente conhecida para esta rota |
| `candidates` | último anúncio (época, saltos, atraso) de cada vizinho; o montante é escolhido entre eles |
| `destinations` | conjunto de vizinhos a jusante que pediram o fluxo |
| `state` | `inativa` ou `ativa`; ativa quando há subscritores locais/a jusante e caminho válido |

O par `(origin, flow)` identifica uma transmissão: o mesmo servidor pode oferecer vários
conteúdos, e servidores diferentes podem usar o mesmo identificador de fluxo sem colisão.
O modelo está em `src/ott/routing.py` como `RouteEntry` e `RoutingTable`, indexada por
`(origin, flow)`. A tabela oferece operações `get`, `put` e `remove`; `attach(node)` instala
os handlers de `ANNOUNCE`, `ACTIVATE`, `DEACTIVATE` e `WITHDRAW`. O publicador guarda a rota
local com métrica e atraso zero. Nos recetores, o montante é escolhido entre os candidatos
como descrito em §3.7. `ACTIVATE`/`DEACTIVATE` acrescentam ou retiram o
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
|  | `delay` (opcional) | número finito ≥ 0, em segundos; o servidor anuncia 0 e cada nó soma RTT/2 da ligação por onde recebeu; ausente = desconhecido |
| `WITHDRAW` | `server_id`, `flow` | identificam a rota que o emissor deixou de conseguir alcançar |
|  | `epoch` | inteiro positivo; última época conhecida pelo emissor (informativo) |
| `ACTIVATE` | `server_id`, `flow` | identificam a rota pedida; o ramo é o do vizinho emissor |
|  | `subscriber_id` (opcional) | string não vazia; só informativo (quem abriu o ramo) |
| `DEACTIVATE` | `server_id`, `flow` | identificam o ramo que o vizinho emissor está a fechar |
|  | `subscriber_id` (opcional) | string não vazia; só informativo |

Um anúncio é identificado por `(server_id, flow, epoch)`. Vários anúncios com a mesma
identidade podem chegar por caminhos diferentes; cada um atualiza o candidato do vizinho
emissor e o montante é reavaliado (§3.7). Numa rota com caminho, uma época inferior à já
guardada é antiga e é ignorada; numa rota retirada por este nó, também a época retirada é
ignorada até ao fim da retenção (§3.7). O servidor inicia `epoch` na hora do arranque em
milissegundos e incrementa-o uma unidade por ronda e por fluxo, para que um servidor
reiniciado tenha épocas maiores do que as anteriores. Só o relógio do próprio servidor conta:
os nós não comparam relógios entre si.

Cada vizinho conta no máximo uma vez nos destinos de uma rota. Repetir `ACTIVATE` não cria
um ramo adicional; repetir `DEACTIVATE` é inofensivo. Localmente, cada `subscriber_id` conta
uma vez em `local_subscribers`. O servidor não conhece os clientes individuais, apenas os
ramos que lhe pedem o fluxo.

Os validadores de `src/ott/protocol.py` aplicam estes campos tanto no envio como na receção.
Os testes de esquema estão em `tests/test_protocol.py`. O tratamento das mensagens está em
`src/ott/routing.py`; a integração de dados e os ensaios pendentes estão descritos em
`docs/ETAPA2.md`.
