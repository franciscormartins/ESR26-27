# Revisão cruzada: `src/ott/routing.py` (Elemento 2), revista pelo Elemento 1

> O guião manda o E1 rever o código do E2. Revisão feita em 2026-11-09 (sobre o `routing.py` de então, 410 linhas) e
> **reverificada em 2026-11-10** sobre o código atual. Cada achado foi confirmado com um programa de reprodução antes de ser
> reportado. **Todos estão resolvidos** pelo E2, com testes.

| # | Gravidade | Achado (2026-11-09) | Resolução | Teste |
|---|---|---|---|---|
| 1 | Crítica | Os handlers recebiam o objeto `Neighbor`, o `routing.py` esperava o **id (str)**: sobre nós reais nenhuma rota ficava ativa (os testes usavam um nó falso que passava texto) | O nó passa o id (`fn(node, vizinho_id, msg)`, D20, `e83e37b`) | `test_overlay` (`senders == ["A"]`); `test_integration` com nós reais |
| 2 | Alta | A época voltava a 1 quando o servidor reiniciava e os nós ignoravam os anúncios novos (rota presa ~40 min) | A época começa na hora do arranque em ms (D27) | `test_default_epochs_start_from_clock_so_restart_is_newer` |
| 3 | Média | O montante oscilava a cada época (16 `ACTIVATE`/`DEACTIVATE` em 4 épocas com a topologia parada) | Desempate por RTT com histerese (D21); "época nova que chega primeiro por caminho pior não causa churn" | `test_new_epoch_arriving_first_by_worse_path_causes_no_churn` |
| 4 | Média | Rotas nunca expiravam sem anúncios | `route_ttl` (`--route-ttl`, por omissão 6 × intervalo) | `test_background_thread_expires_silent_upstream`, `test_repeated_announcement_renews_lifetime` |
| 5 | Baixa | `seen_announcements` nunca era lido e crescia sem limite | Campo retirado | – |
| 6 | Info | A perda do montante não era comunicada a jusante | Mensagem `WITHDRAW` e reparação rápida (D22) | `test_withdraw_cascades_and_clears_downstream_branches`, `test_upstream_down_with_feasible_alternative_repairs_without_withdraw` |

## Do lado do E1 (reposto a 2026-11-10 por cima do código do E2)

- Uma exceção num handler (`KeyError`, por exemplo) fechava a ligação ao vizinho. Agora fica no log e a ligação mantém-se (D29).
- Um nó que não é bootstrapper respondia `nó desconhecido` a um `REGISTER`; passa a responder `este nó não é bootstrapper`.
- Sete testes de robustez novos (`RobustnessTest`): lixo na porta, cabeçalho de 2 GiB, ligação muda, mensagem truncada,
  pedido de bootstrap inválido, exceção num handler, `REGISTER` a um nó que não é bootstrapper.

## O que está bem no `routing.py`

Locks bem usados (os envios fazem-se fora do lock, agora por uma fila ordenada, D28), callbacks de vizinhança que só enfileiram,
split horizon, `ACTIVATE`/`DEACTIVATE` agregados por ramo e boa cobertura de testes.
