# Instruções para trabalhar neste projeto

## Contexto

Este repositório implementa o TP2 de ESR: um serviço OTT para distribuição de
multimédia sobre uma rede overlay. O enunciado oficial está em
`docs/enunciado.pdf`. Consulta-o antes de alterar a arquitetura ou decidir que
requisitos estão concluídos.

## Documentação de referência e fonte de verdade

- `docs/ESTADO.md`: estado atual, decisões, limitações e trabalho pendente.
- `docs/protocolo.md`: formato e interações das mensagens.
- `README.md`: estrutura do código e comandos de utilização.
- `topologies/`: topologias overlay em JSON e topologias CORE em XML.
- `docs/relatorio/main.tex`: fonte do relatório.

Ao terminar uma tarefa, atualiza a documentação afetada para refletir o
comportamento real do código. Não descrevas funcionalidades planeadas como
implementadas.

Em caso de dúvida ou conflito, usa esta ordem para decidir o comportamento:

1. requisitos do enunciado;
2. decisões numeradas e limitações em `docs/ESTADO.md`;
3. contrato de mensagens em `docs/protocolo.md`;
4. testes que descrevam o comportamento já suportado;
5. implementação atual.

Se dois documentos se contradizem, isso é um defeito da documentação: assinala
o conflito e corrige o documento errado, em vez de escolher um em silêncio.

Não alteres silenciosamente uma decisão arquitetural (D1, D2, ...). Regista a
decisão substituída, a motivação, o impacto no protocolo/topologias e o plano
de compatibilidade ou migração em `docs/ESTADO.md`; atualiza também
`docs/protocolo.md` e o README quando forem afetados. Não edites o relatório
(`docs/relatorio/`) por iniciativa própria: indica no resumo final que secções
precisam de ser revistas.

## Arquitetura e decisões atuais

- O controlo do overlay usa TCP; o vídeo (RTP/MJPEG) segue por UDP (`src/ott/forwarding.py`).
- Os nós overlay e o bootstrapper estão em `src/ott/`.
- `src/ott/routing.py` contém a tabela de rotas, os anúncios, as subscrições
  agregadas por ramo, o `WITHDRAW` e o tempo de vida. A tabela é indexada por
  `(servidor, fluxo)`; o plano da Etapa 2 está em `docs/ETAPA2.md`.
- `flow` identifica o conteúdo ou stream multimédia transmitido.
- A Etapa 2 segue a Estratégia 1 do enunciado: anúncios do servidor propagados
  pelo overlay e ativação da rota por pedido do cliente.
- Distingue sempre o vizinho de montante, em direção ao servidor, dos destinos
  a jusante que recebem o fluxo.

Confirma `docs/ESTADO.md` e `docs/protocolo.md` antes de alterar estas decisões.

### Invariantes a preservar

- Mantém as dependências numa direção clara: `protocol` valida e serializa;
  `node` gere vizinhos; `bootstrapper` serve a lista de vizinhos a cada nó;
  `routing` decide rotas e ramos; `forwarding` aplica a decisão ao UDP; o
  subpacote `streaming` (`server`, `client`, `rtp`) cria/consome vídeo; `cli`
  apenas compõe estas peças. Não dupliques política de routing, parsing de
  mensagens ou gestão de sockets entre camadas, e não deixes a CLI, o streaming
  ou o forwarding modificar diretamente estruturas internas de `RoutingTable`.
- Uma alteração de protocolo começa em `protocol.py` e em
  `docs/protocolo.md`: validação de entrada **e** de saída, compatibilidade
  com mensagens existentes e testes de mensagem válida, inválida e truncada.
- O estado de routing é sempre indexado por `(server_id, flow)`. `flow` nunca
  é um identificador global implícito.
- Distingue o montante dos destinos a jusante. Preserva a agregação de
  subscrições (só transições 0->1 e 1->0 propagam `ACTIVATE`/`DEACTIVATE`) e o
  RPF estrito do plano de dados.
- Eventos de vizinhos e handlers correm em threads do nó: não os bloqueies e
  não introduzas estado partilhado sem sincronização e sem uma estratégia de
  encerramento. Threads, sockets e forwarders criados pela funcionalidade têm
  de ser fechados nos caminhos normal e de erro.
- Mantém Python 3 e apenas a biblioteca standard (D1), salvo decisão explícita
  e documentada do grupo. Preserva a interface pública de `OverlayNode` ou
  documenta e testa a alteração em todos os consumidores.

## Convenções de implementação

- Reutiliza a interface existente de `OverlayNode`, incluindo
  `register_handler`, `send`, `broadcast`, eventos de vizinho e métricas RTT.
- Valida mensagens, configurações e entradas na fronteira do módulo, antes de
  alterar o estado do nó ou da rota; não deixes dados inválidos chegarem ao
  estado de rotas ou ao plano de dados.
- Faz alterações pequenas, coesas e no nível certo da arquitetura.
- Mantém comportamentos idempotentes quando uma mensagem pode ser repetida e
  explicita o tratamento de falha, reinício, expiração e perda de vizinho.

## Fluxo para alterações de comportamento

Aplica-se a qualquer alteração que mude o comportamento do código; correções
de texto ou de documentação dispensam os passos 2 e 4.

1. Antes de editar, identifica a etapa do enunciado, os módulos envolvidos e
   as decisões D* e documentos que podem ser afetados.
2. Quando a alteração atravessa várias camadas, implementa de baixo para cima:
   protocolo/validação -> nó e eventos -> routing -> forwarding/streaming ->
   CLI, definindo e testando o contrato de cada camada antes de a usar na
   seguinte.
3. Atualiza testes e documentação na mesma alteração. Uma funcionalidade não
   está concluída enquanto não tiver comportamento verificável e documentado.
4. Executa primeiro os testes diretamente afetados e depois a suite completa
   quando a alteração tocar interfaces entre módulos, rede, protocolo, CLI ou
   topologias.
5. No resumo final, indica o comportamento entregue, os testes executados e
   os que não foram executados, com a razão. Diz se a validação foi só local
   (`unittest`, localhost) ou também ensaiada no CORE; os testes locais não
   substituem o ensaio no CORE. Não declares uma etapa concluída sem a
   validação exigida em `docs/ESTADO.md`/`docs/ETAPA2.md`.

## Testes

### Regra de evolução dos testes

- **Nova feature:** adiciona testes novos antes ou juntamente com a
  implementação. Cobre o percurso principal, validação/erro e pelo menos uma
  transição de ciclo de vida relevante (repetição, queda, expiração, troca de
  rota ou encerramento, conforme aplicável).
- **Feature existente alterada:** atualiza os testes que definem o contrato
  antigo e acrescenta testes para o novo comportamento e para o limite que
  motivou a mudança. Nunca apagues, ignores ou enfraqueças um teste apenas para
  fazer a suite passar; se o contrato deixou mesmo de existir, documenta a
  razão e substitui a cobertura equivalente.
- **Correção de defeito:** acrescenta primeiro um teste de regressão que falha
  sem a correção, salvo se a falha só for reproduzível no CORE; nesse caso,
  regista um procedimento reproduzível em `docs/ESTADO.md` ou
  `docs/ETAPA2.md`.
- **Alteração entre camadas:** além do teste unitário, cria ou atualiza um
  teste de integração com `OverlayNode` real quando a mudança afetar mensagens,
  callbacks de vizinhos, subscrições, UDP/RPF ou a CLI.
- Mantém os testes determinísticos: usa timeouts limitados, limpa threads e
  sockets em `tearDown`/`finally` e não depende de uma ordem de execução.

### Onde adicionar cobertura

| Alteração | Testes principais |
|---|---|
| Envelope, tipo ou validação de mensagem | `tests/test_protocol.py` |
| Ligações, HELLO, keepalive, RTT, bootstrapper ou substitutos | `tests/test_overlay.py` |
| Anúncios, seleção, `WITHDRAW`, TTL ou subscrições | `tests/test_routing.py` |
| RPF e encaminhamento UDP | `tests/test_forwarding.py` |
| Pacote RTP/MJPEG, emissor ou recetor | `tests/test_streaming.py` |
| Opções, validação ou composição da linha de comando | `tests/test_cli.py` |
| Contratos entre overlay, routing e plano de dados | `tests/test_integration.py` |

Executa os testes relevantes para a alteração. Para a tabela de rotas:

```sh
PYTHONPATH=src python3 -m unittest tests.test_routing -v
```

Para a suite completa:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

Se não executares algum teste, indica isso no resumo final.

## Limites do estado atual

A existência de um tipo de mensagem, estrutura de dados, topologia ou documento
não significa que a funcionalidade esteja integrada ou testada. Confirma o código
e o estado registado em `docs/ESTADO.md` antes de afirmar que uma etapa do
enunciado está concluída.
