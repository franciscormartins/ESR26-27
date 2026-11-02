# Instruções para trabalhar neste projeto

## Contexto

Este repositório implementa o TP2 de ESR: um serviço OTT para distribuição de
multimédia sobre uma rede overlay. O enunciado oficial está em
`docs/enunciado.pdf`. Consulta-o antes de alterar a arquitetura ou decidir que
requisitos estão concluídos.

## Documentação de referência

- `docs/ESTADO.md`: estado atual, decisões, limitações e trabalho pendente.
- `docs/protocolo.md`: formato e interações das mensagens.
- `README.md`: estrutura do código e comandos de utilização.
- `topologies/`: topologias overlay em JSON e topologias CORE em XML.
- `docs/relatorio/main.tex`: fonte do relatório.

Ao terminar uma tarefa, atualiza a documentação afetada para refletir o
comportamento real do código. Não descrevas funcionalidades planeadas como
implementadas.

## Arquitetura e decisões atuais

- O projeto usa Python 3 e, tanto quanto possível, apenas a biblioteca standard.
- O controlo do overlay usa TCP; o vídeo está previsto para UDP.
- Os nós overlay e o bootstrapper estão em `src/ott/`.
- `src/ott/routing.py` contém o modelo inicial da tabela de rotas. A tabela é
  indexada por `(servidor, fluxo)`.
- `flow` identifica o conteúdo ou stream multimédia transmitido.
- A Etapa 2 segue a Estratégia 1 do enunciado: anúncios do servidor propagados
  pelo overlay e ativação da rota por pedido do cliente.
- Distingue sempre o vizinho de montante, em direção ao servidor, dos destinos
  a jusante que recebem o fluxo.

Confirma `docs/ESTADO.md` e `docs/protocolo.md` antes de alterar estas decisões.

## Convenções de implementação

- Mantém a separação entre protocolo, nó overlay, encaminhamento e streaming.
- Reutiliza a interface existente de `OverlayNode`, incluindo
  `register_handler`, `send`, `broadcast`, eventos de vizinho e métricas RTT.
- Valida mensagens recebidas antes de alterar o estado do nó ou da rota.
- Evita bloquear os callbacks de vizinhos; são executados nas threads do nó.
- Mantém compatibilidade com a versão de Python usada pelo projeto.
- Atualiza os testes correspondentes quando muda o comportamento implementado.

## Testes

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
