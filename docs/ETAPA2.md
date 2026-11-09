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
- [x] Começar por usar número de saltos como métrica; deixar RTT como extensão posterior.
- [x] Evitar que um anúncio mais antigo substitua uma rota aprendida numa época mais nova.

A publicação está implementada em `AnnouncementPublisher` (`src/ott/routing.py`) e ligada à
CLI. `--publish-flow ID` pode repetir-se para vários fluxos; `--announce-interval S` define
o intervalo (por omissão, 5 s). O publicador emite imediatamente a primeira ronda e depois
incrementa `epoch` por fluxo a cada ronda. Cada anúncio usa `server_id=node.id` e `metric=0`.
O teste unitário cobre múltiplos fluxos, épocas, periodicidade, paragem e configuração inválida.

`RoutingTable.attach(node)` instala o handler de `ANNOUNCE`. O handler soma um salto à
métrica recebida, guarda a rota e o vizinho emissor, e inunda o anúncio apenas quando
aprende uma época nova ou encontra um custo menor na época atual. Cópias antigas,
duplicadas, não melhoradas e anúncios que regressam ao servidor de origem não são
propagados. A validação dos campos é feita pelo protocolo antes do handler. O
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

### 4. Ativação e desativação - implementado (controlo)

- [x] Definir como o cliente seleciona servidor/fluxo e inicia a subscrição.
- [x] Encaminhar `ACTIVATE` pelo vizinho de montante até ao servidor.
- [x] Em cada salto, registar o vizinho emissor como destino a jusante.
- [x] Encaminhar `DEACTIVATE` pelo mesmo caminho e remover essa subscrição.
- [x] Manter a rota ativa enquanto existir pelo menos uma subscrição local ou a jusante; caso contrário,
  marcá-la inativa.
- [x] Tornar operações repetidas seguras (ativação/desativação duplicada não corrompe o estado).

`RoutingTable.attach(node)` regista também os handlers de `ACTIVATE` e `DEACTIVATE`.
O cliente inicia/termina a sua subscrição com `table.activate(node, server_id, flow)` e
`table.deactivate(node, server_id, flow)`; o `subscriber_id` é por omissão o ID do nó.
Cada nó encaminha o pedido unicast para o seu `upstream`, mudando `src` para o seu próprio
ID e mantendo o `subscriber_id` original. Os nós intermédios guardam os clientes por vizinho
de jusante. Cada subscrição distinta sobe ao servidor e a respetiva remoção propaga
`DEACTIVATE`; o estado do ramo mantém-se ativo enquanto pelo menos um cliente o usar.
Assim, vários clientes atrás do mesmo nó partilham o ramo e a remoção de um não interrompe
os restantes. O estado `ativa` reflete a existência de pelo menos uma subscrição local ou a
jusante e um caminho válido para o servidor.

Isto atualiza o estado de controlo. A CLI inicia a subscrição com `--subscribe` e o
encaminhador de dados usa esse estado para abrir/fechar os ramos UDP.

### 5. Reação a alterações do overlay - implementado (base)

- [x] Usar `on_neighbor_up` e `on_neighbor_down` para invalidar ou reaprender rotas.
- [x] Enviar ao vizinho novo os anúncios das rotas válidas já conhecidas; o servidor mantém anúncios periódicos.
- [x] Limpar destinos a jusante associados a vizinhos que caíram e desativar o ramo se já não tiver clientes.
- [x] Marcar sem caminho como inativa; após aprender um montante alternativo, reativar as subscrições e propagar o anúncio atualizado.

`RoutingTable.attach(node)` liga os eventos de vizinhança a uma fila processada por uma thread
de fundo, para os callbacks do overlay apenas enfileirarem o evento. Quando cai o montante,
a rota mantém os pedidos locais/a jusante, mas passa a sem caminho (`upstream=None`, métrica
infinita localmente e estado inativo). Uma rota alternativa anunciada mais tarde assume o
montante e recebe os `ACTIVATE` pendentes; o antigo montante recebe `DEACTIVATE` quando a
rota muda enquanto ainda está ligado. Se cair um destino, esse ramo é removido e as
desativações necessárias sobem. A proteção contra ciclos usa split horizon (não reenviar ao
emissor, nem anunciar ao montante recém-ligado) e rejeita um destino a jusante como montante.

Esta reação é coberta por testes unitários; falta ensaiar quedas, recuperações e caminhos
alternativos no CORE.

### 6. Integração do encaminhamento dos dados - implementado; validação pendente

- [x] Expor `RoutingTable.active_destinations(origin, flow, exclude=())`, que devolve uma
  cópia dos vizinhos a jusante ativos, sem duplicar um vizinho que tenha vários subscritores.
- [x] No servidor, emitir pacotes do fluxo apenas quando a rota tem destinos ativos.
- [x] Nos nós intermédios, receber UDP, identificar o vizinho de entrada e replicar cada
  pacote pelos destinos ativos, excluindo a ligação de entrada.
- [x] Manter os dados em UDP, separados das mensagens de controlo TCP.
- [x] Integrar a interface RTP já existente: `--publish-flow` inicia o MJPEG/RTP e
  `--subscribe SERVIDOR:FLUXO` ativa a subscrição e recolhe estatísticas locais.
- [ ] Ensaiar a distribuição ponta a ponta no CORE, incluindo falhas e recuperação.

`OverlayForwarder` (`src/ott/forwarding.py`) escuta por omissão na porta UDP 6000. O nó de
origem envia uma cópia para cada destino a jusante ativo; cada intermédio repete o processo,
sem devolver o datagrama ao vizinho de entrada. Vários subscritores atrás do mesmo vizinho
partilham uma cópia nesse ramo. Os nós de destino entregam o RTP ao `StreamReceiver` local.

O encaminhamento reutiliza os troços comuns da árvore construída pelos `upstream` escolhidos
pelos anúncios (métrica atual: número de saltos). A implementação não escolhe deliberadamente
um caminho alternativo com custo superior só por esse ramo já estar ativo; essa otimização
global da árvore não faz parte da métrica de menor caminho implementada.

Exemplo: no servidor, acrescentar `--publish-flow filme_a`; no cliente, usar
`--subscribe S1:filme_a`. Todos os nós intermédios têm de correr `ott` para encaminhar UDP.
`--stats 2` mostra os vizinhos, as estatísticas RTP do cliente e os contadores do plano de dados.

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
- [ ] CORE: ativar dois clientes e desativar um mantém o outro a receber; desativar o
  último deixa a rota sem tráfego.
- [ ] CORE: exercitar as topologias 1 e 2 antes do cenário 3.

Comando atual para os testes do modelo local:

```sh
PYTHONPATH=src python3 -m unittest tests.test_routing -v
```

Os testes atuais cobrem o modelo, o publicador, aprendizagem, ativação/desativação,
subscrições partilhadas, queda de vizinhos, reaprendizagem por caminho alternativo, mudança
do montante e anúncios a vizinhos novos. O encaminhamento dos pacotes está integrado; os
ensaios destas alterações no CORE continuam pendentes.

## Critério de conclusão da Etapa 2

A etapa estará funcionalmente concluída quando vários clientes puderem ativar o mesmo fluxo,
os nós encaminharem os dados pela árvore de distribuição construída sobre os melhores
caminhos conhecidos, e uma falha de vizinho provocar atualização da rota sem deixar tráfego
preso em caminhos inválidos. Registar os resultados e limitações em `docs/ESTADO.md` e no
relatório.
