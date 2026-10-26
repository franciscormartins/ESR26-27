# ESTADO DO PROJETO – Elemento 1 (Overlay e protocolo de controlo)

> Ficheiro vivo: é atualizado sempre que algo é feito. Serve para saber o ponto da situação,
> relembrar as decisões e preparar as conversas com os docentes.
> **Última atualização: 2026-10-26.**

## 1. Resumo em 30 segundos

- Overlay aplicacional em **Python 3 (só biblioteca standard)**: cada nó é o programa `ott`, liga-se
  por **TCP** aos vizinhos, mantém-nos com PING/PONG, deteta quedas, mede o RTT de cada vizinho e,
  opcionalmente, pede vizinhos substitutos ao bootstrapper.
- O overlay constrói-se de duas formas: **estratégia 2** (`ott -b <bootstrapper>`: o servidor lê um
  ficheiro de topologia e devolve os vizinhos) e **estratégia 1** (`ott viz1 viz2`: vizinhos à mão).
- **Estado:** o código do elemento 1 está completo em relação ao guião e tem 21 testes automáticos a
  passar. Os ensaios no CORE dos cenários 1 e 2 estão feitos para a parte base (ligar, saída limpa,
  reentrada). **Falta ensaiar no CORE** as funcionalidades novas (RTT, timeout, substitutos) e o overlay no cenário 3.
- Falta ainda: o módulo de rotas (E2) e o streaming (E3), que dependem da interface descrita em 5.

## 2. O que está feito (elemento 1)

| Tarefa do guião | Estado | Onde |
|---|---|---|
| Biblioteca de mensagens com validação de campos | ✅ feito | `src/ott/protocol.py`, `tests/test_protocol.py` |
| Documento de especificação das mensagens | ✅ feito (a rever pelo grupo) | `docs/protocolo.md` |
| Aplicação `ott`: servidor TCP + ligações de saída, full-duplex, thread por ligação, HELLO | ✅ feito, ensaiado no CORE | `src/ott/node.py` |
| Overlay com `ott -b <bootstrapper>` e ficheiro de topologia escrito à mão | ✅ feito, ensaiado no CORE (cen. 1 e 2) | `bootstrapper.py`, `topologies/*.json` |
| Estratégia 1 (`ott viz1 viz2`) | ✅ feito, ensaiado no CORE | `cli.py` |
| KEEPALIVE periódico e deteção de vizinho caído (fecho da ligação ou timeout) | ✅ feito; fecho/BYE ensaiado no CORE, timeout só em teste automático | `node.py` |
| Pedido de substituto ao bootstrapper | ✅ feito (opção `--min-neighbors`); só testado automaticamente | `node.py`, `bootstrapper.py` |
| RTT por vizinho entregue ao E2 | ✅ feito (`node.rtt(id)`, `node.rtts()`); só testado em localhost | `node.py` |
| Eventos `vizinho_up/down` para o E2 | ✅ feito (`on_neighbor_up/down`) | `node.py` |
| Testes de saída/entrada de nós com anotação de resultados | 🟡 parcial: cenário 2 medido; falta o resto (ver secção 7) | `docs/testes-core.md` |

Também feito (apoio ao grupo): topologias CORE dos cenários 1, 2 e 3 (`topologies/*.xml`), com o
underlay do cenário 3 a funcionar (routers FRR, OSPF); comandos de arranque por nó (`docs/testes-core.md`).

## 3. Decisões tomadas e porquê

| # | Decisão | Porquê / alternativa |
|---|---|---|
| D1 | Python 3, só biblioteca standard | O código de apoio do livro está em Python; sem dependências para correr em qualquer nó do CORE. |
| D2 | Controlo em TCP, vídeo em UDP (E3) | TCP dá fiabilidade ao registo/vizinhos e a queda da ligação deteta a saída de um nó. |
| D3 | Mensagens = JSON com **prefixo de 4 bytes de tamanho** (não `\n`) | Delimitação robusta e rejeição de mensagens gigantes antes de as ler. **Desvio ao guião** (que sugeria uma linha JSON): levar ao grupo/docente. |
| D4 | Envelope fixo `{type, src, data}` com campos obrigatórios por tipo, validados ao enviar e ao receber | Detetar erros cedo; um nó com bug não deixa o vizinho num estado incoerente (ligação inválida é fechada). |
| D5 | Uma só ligação TCP por par de nós; se ambos ligam ao mesmo tempo, **fica a do nó de menor id** | Evita ligações duplicadas e eventos `up` repetidos; ambos aplicam a mesma regra. |
| D6 | Topologia **simétrica**: uma aresta no ficheiro vale nos dois sentidos e os dois lados tentam ligar-se | Funciona com ligações diretas no CORE, onde só um lado alcança o outro (sem rotas), e dá resiliência à ordem de arranque. |
| D7 | O servidor é o bootstrapper (mesmo executável: `--serve`) | Pedido no enunciado ("usar o servidor como bootstrapper permanente"). |
| D8 | Vivacidade: PING a cada 2 s, morto após 6 s sem nada; fecho da ligação deteta-se logo | Equilíbrio entre rapidez e falsos positivos; valores configuráveis. |
| D9 | PING/PONG levam `ts` do emissor e o PONG devolve-o; RTT = média móvel (α = 0,3) | Mede RTT sem relógios sincronizados; a média suaviza o ruído para a métrica do E2. |
| D10 | Substitutos só com `--min-neighbors X` (por omissão desligado) | Os clientes são folhas (1 vizinho) e não devem ser forçados a ter X. O bootstrapper propõe nós já registados, perto dos vizinhos atuais, e esquece os que não ligam em 10 s. |
| D11 | Uma só tentativa de ligação em curso por alvo | Evitava acumular threads quando um vizinho está lento ou inalcançável. |
| D12 | Um só endereço (`host`) por nó no ficheiro de topologia | Simples; basta um dos lados alcançar o outro. **Limitação** para nós com várias interfaces (ver 8). |
| D13 | Cenário 3: underlay com routers FRR/OSPF e nós overlay também nos routers | A Figura 1 põe nós overlay em routers; só há 10 hosts para 11 nós overlay. Atribuição overlay→CORE **por confirmar**. |
| D14 | Routers com FRR (não Quagga) e `frr.conf`/`frrboot.sh` próprios no XML | O Quagga instalado não tem `vtysh`, e o CORE 9.2.1 gera config que o FRR 10 rejeita (ver `docs/testes-core.md`). |

## 4. Arquitetura (resumo)

```
src/ott/protocol.py     formato, tipos e validação das mensagens
src/ott/node.py         OverlayNode: ligações, HELLO, PING/PONG, RTT, eventos, substitutos
src/ott/bootstrapper.py BootstrapService: vizinhos e substitutos a partir do ficheiro JSON
src/ott/cli.py          comando `ott` (-b, --serve, --min-neighbors, --stats ...)
src/ott/routing.py      (esqueleto) E2
src/ott/streaming/      (vazio) E3
topologies/             cenarios 1-3: .json (overlay) e .xml (CORE)
docs/protocolo.md       especificação das mensagens e interações
docs/testes-core.md     comandos por nó e testes a fazer no CORE
```

Cada nó tem threads para: aceitar ligações, uma por vizinho (leitura) e uma de manutenção
(ligar em falta, PING, timeouts, substitutos).

## 5. Interface para o Elemento 2 e 3

```python
node.register_handler("ANNOUNCE", fn)   # fn(node, vizinho, msg)
node.send(id, make_msg(...)) ; node.broadcast(msg, exclude={origem})
node.neighbor_ids()
node.on_neighbor_up(fn) ; node.on_neighbor_down(fn)   # fn(node, id)
node.rtt(id) ; node.rtts()                            # segundos (média móvel) ou None
```

Pendente do grupo: definir os campos de `ANNOUNCE`, `ACTIVATE`, `DEACTIVATE` e `ROUTE_TEST`
(hoje só exigem `data` ser um objeto).

## 6. Como testar

**Automático (21 testes, ~7 s):**

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -t .
```

Cobrem: validação do protocolo, overlay por bootstrapper e recuperação, eventos up/down, RTT, ligação
duplicada, vizinho calado (timeout), mensagem inválida, substituto, alvos expirados e tentativas em paralelo.

**No CORE:** `docs/testes-core.md` tem os comandos de cada nó para os cenários 1, 2 e 3 e a lista de testes
(arranque, saída limpa, queda abrupta, nó congelado `kill -STOP`, link em baixo, RTT com atraso, substituto).
Usar `--stats 5` para ver os vizinhos e o RTT; guardar logs com `tee`.

## 7. O que esperar dos resultados e como avaliar

| Teste | Esperado | Como medir |
|---|---|---|
| Arranque | todos os vizinhos do JSON ligados em ≤ ~4 s, uma ligação por par | logs `HELLO: vizinho X ligado`; `--stats` |
| Saída limpa (Ctrl-C) / processo morto | vizinhos detetam em milissegundos (BYE / fecho TCP) | diferença entre a hora da ação (`date`) e o log `desligado` |
| Nó congelado (`kill -STOP`) ou link em baixo (`ip link set ... down`) | deteção por timeout em ≈ 6–8 s | idem |
| Reentrada | volta a ligar em ≤ ~2 s | logs |
| RTT | em localhost/CORE sem atraso ≈ 0,3–1 ms; com atraso D no link ≈ 2·D | `--stats` |
| Substituto | ≈ 5 s depois de ficar com vizinhos a menos, `substitutos propostos` e novo HELLO | logs |

Critérios: correção (vizinhos certos, sem duplicados), **tempo de deteção e de recuperação** (média e máximo
de ~10 repetições), robustez (sem threads penduradas nem exceções nos logs).

**Resultados já medidos**

- Cenário 2 no CORE (2026-10-22): Ctrl-C num nó → vizinhos detetam em ≤ 2 ms; reentrada do nó → overlay
  completo em ≈ 2,0 s (primeiros vizinhos em 0,9 s). Sem vizinhos "fantasma" após ligações duplicadas.
- Cenário 3, underlay (2026-10-22): `ping` do `C6` ao `Server` 3/3, 0 % de perda; OSPF convergiu em < 30 s.
- Localhost (2026-10-26): RTT 0,4–0,7 ms; 21/21 testes automáticos, estáveis em 4 execuções seguidas.

## 8. Limitações e riscos conhecidos

- Um só `host` por nó no ficheiro (nós com várias interfaces só são alcançáveis por um IP).
- O bootstrapper não expira nós: só sabe que estão vivos por terem pedido registo/substitutos.
- Ficheiro de topologia estático (um nó novo tem de constar do ficheiro).
- Na entrada, quando os dois lados tentam ligar-se em simultâneo, abre-se e fecha-se uma ligação à toa (inofensivo).
- Sem autenticação nem cifra no controlo.
- O cenário 2 desenhado tem 2 nós intermédios; o enunciado mostra 3 (S1 liga a um só, que se liga aos outros dois).

## 9. Por fazer

**Elemento 1**
- [ ] Ensaiar no CORE: timeout (`kill -STOP`), link em baixo, RTT com atraso, substituto (`--min-neighbors`); registar tempos (10 repetições).
- [ ] Ensaiar o overlay no cenário 3 (11 nós) e **confirmar a atribuição overlay→CORE** (cenário 3 de `docs/testes-core.md`).
- [ ] Rever a especificação (`docs/protocolo.md`) com o grupo e fixar os campos das mensagens do E2.
- [ ] Passar a especificação e os resultados para o relatório (`docs/relatorio/main.tex` ainda vazio).
- [ ] Decidir se refazemos o cenário 2 com 3 intermédios, como no enunciado.

**Outros elementos (para saber o que depende de nós)**
- [ ] E2: tabela de rotas, anúncios, ativação, adaptação a quedas (usa `on_neighbor_down` e `rtt`).
- [ ] E3: servidor/cliente de vídeo, plano de dados, cenários e medições.

## 10. Perguntas para os docentes

1. Podemos usar **prefixo de tamanho** em vez de uma linha JSON por mensagem? (D3)
2. Quantos vizinhos **X** por nó se espera, e quantos nós overlay no cenário 3? Os clientes (folhas) também têm de ter X?
3. Basta o servidor conhecer o overlay por **ficheiro escrito à mão**, ou querem entrada dinâmica de nós novos?
4. Podem correr nós overlay **em routers** no CORE (como na Figura 1), dado que o enunciado aconselha só sistemas terminais?
5. O cenário 2 do enunciado tem 3 intermédios; a nossa topologia de teste tem 2 com um triângulo. Serve?
6. Dois servidores: são dois nós overlay distintos com anúncios independentes?
7. Datas e conteúdos das duas avaliações intermédias (5 % + 5 %).

## 11. Registo de alterações

| Data | O que mudou |
|---|---|
| 2026-10-26 | Validação de campos no protocolo (`ProtocolError`); eventos `up/down`; RTT por vizinho; pedido de substitutos (`REPLACE`, `--min-neighbors`); uma tentativa de ligação por alvo; opções `--stats`/`--replace-after`; 21 testes; `docs/protocolo.md`; `cenario3.json` com a atribuição proposta; este ficheiro. |
| 2026-10-22 | Cenários 1 e 2 ensaiados no CORE (ligar, Ctrl-C, reentrada); `cenario3.xml` validado e routers FRR a funcionar (config própria no XML); `docs/testes-core.md`. |
| 2026-10-20 | Etapas 0 e 1: protocolo, nó, bootstrapper, CLI, topologias e primeiros testes. |
