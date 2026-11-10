# Cenário 4: dois servidores numa rede em teia

> Criado em 2026-11-10 para ver **como o programa reage quando um servidor falha e é preciso redirecionar rotas**, e para avaliar rotas
> diferentes com atraso, jitter, perdas e largura de banda diferentes. Poucos clientes (4), uma teia de 13 routers com muitos caminhos
> de comprimentos diferentes e os dois servidores ligados entre si, de modo que **cada um é caminho de passagem dos fluxos do outro**.
> Ficheiros: `topologies/cenario4.xml` (CORE), `topologies/cenario4.json` (overlay) e `topologies/gerar_cenario4.py` (gera os dois; para mudar
> a topologia edita as tabelas do programa e volta a corrê-lo). Cabe no canvas do CORE (1000×750). Ainda **não foi aberto no CORE**; a coerência
> XML/JSON está testada em `tests/test_topologias.py`.

## 1. Requisitos do enunciado e como o cenário os cobre

| Requisito | Como se testa neste cenário |
|---|---|
| Pelo menos 2 servidores com 2 fluxos distintos cada | `S1`: `filme_a`, `filme_b`. `S2`: `filme_c`, `filme_b` (o `filme_b` é uma réplica nos dois) |
| Rotas alternativas; vários clientes em simultâneo | Cada cliente tem dois vizinhos; a teia dá vários caminhos no underlay |
| Não passar dados duas vezes no mesmo troço | `C1`, `C2` e `C4` recebem os mesmos fluxos e partilham ramos |
| Nós criados e destruídos dinamicamente; rotas mantidas | Queda de servidores e de intermédios; o `O5` entra a meio |
| Métricas da rede | Empates de saltos (`C3`, `C4`) decididos pelo RTT; atraso, jitter e perdas por `tc netem` |
| Overlay só em sistemas terminais (recomendação) | Todos os nós overlay são hosts; os 13 routers são só underlay |

O id overlay é **igual ao nome do nó no CORE** (`-i O3` corre no nó `O3`).

## 2. Topologia

**Underlay em teia** (OSPF/FRR, config. própria dentro do XML, como no cenário 3): o router central `r1` liga-se por 6 raios ao anel
interior; cada router do anel interior liga-se ao do anel exterior com o mesmo ângulo; cada anel é um hexágono fechado. Cada host liga-se
por uma sub-rede /24 própria ao seu router (router `.1`, host `.20`). **Todas as ligações têm atraso, perda e largura de banda 0**: criam-se
nos testes.

- **Anel exterior** (hexágono `r8–r9–r10–r11–r12–r13–r8`): `r8`[S2], `r9`[C2], `r10`[C1], `r11`[S1], `r12`[L1], `r13`[L2].
- **Anel interior** (hexágono `r2–r3–r4–r5–r6–r7–r2`): `r2`[C4], `r3`[O3], `r4`[O1], `r5`[C3], `r6`[O2], `r7`[O4].
- **Centro** `r1`[O5], ligado por 6 raios a `r2`…`r7`. **Ligações radiais**: `r2–r8`, `r3–r9`, `r4–r10`, `r5–r11`, `r6–r12`, `r7–r13`.
- Os dois servidores ficam em lados opostos da teia (`r11` a oeste, `r8` a este); cada intermédio fica ao lado do anel interior.

**Overlay** (11 nós; `O5` é o suplente). Ligações (19): `S1–S2` (**os servidores estão ligados: cada um é caminho de passagem dos fluxos do outro**),
`S1–O1`, `S1–O2`, `S2–O3`, `S2–O4`, os degraus `O1–O2` e `O3–O4`, as ligações entre lados `O1–O3` e `O2–O4`, os clientes `C1–{S1, O1}`,
`C2–{S2, O3}`, `C3–{O1, O2}`, `C4–{O3, O4}` e o suplente `O5–{O1, O4}`. `C1` e `C2` ficam junto de um servidor; `C3` e `C4` têm dois
intermédios como vizinhos (empate de saltos).

### Nós, IPs e vizinhos esperados

| Nó do CORE = id overlay | Router | IP | Papel | Vizinhos overlay |
|---|---|---|---|---|
| **S1** | r11 | 10.0.34.20 | servidor + bootstrapper: `filme_a`, `filme_b` | C1, O1, O2, S2 |
| **S2** | r8 | 10.0.31.20 | servidor + bootstrapper: `filme_c`, `filme_b` (réplica) | C2, O3, O4, S1 |
| **O1** | r4 | 10.0.27.20 | nó intermédio | C1, C3, O2, O3, O5, S1 |
| **O2** | r6 | 10.0.29.20 | nó intermédio | C3, O1, O4, S1 |
| **O3** | r3 | 10.0.26.20 | nó intermédio | C2, C4, O1, O4, S2 |
| **O4** | r7 | 10.0.30.20 | nó intermédio | C4, O2, O3, O5, S2 |
| **O5** | r1 | 10.0.24.20 | nó **suplente** (só arranca nos testes de entrada) | O1, O4 |
| **C1** | r10 | 10.0.33.20 | cliente | O1, S1 |
| **C2** | r9 | 10.0.32.20 | cliente | O3, S2 |
| **C3** | r5 | 10.0.28.20 | cliente | O1, O2 |
| **C4** | r2 | 10.0.25.20 | cliente | O3, O4 |
| **L1** | r12 | 10.0.35.20 | host livre (fora do overlay) | – |
| **L2** | r13 | 10.0.36.20 | host livre (fora do overlay) | – |

`S1` e `S2` arrancam com `--serve`: **os dois são bootstrappers**. Os outros nós usam `-b` apontado ao `S1`; se este cair, ao `S2`.

### Interfaces dos routers (para `ip link` e `tc`)

| Router | Interfaces: nome → vizinho (IP do router) |
|---|---|
| **r1** | eth0 → r2 (10.0.0.1) · eth1 → r3 (10.0.1.1) · eth2 → r4 (10.0.2.1) · eth3 → r5 (10.0.3.1) · eth4 → r6 (10.0.4.1) · eth5 → r7 (10.0.5.1) · eth6 → O5 (10.0.24.1) |
| **r2** | eth0 → r1 (10.0.0.2) · eth1 → r3 (10.0.6.1) · eth2 → r7 (10.0.11.2) · eth3 → r8 (10.0.18.1) · eth4 → C4 (10.0.25.1) |
| **r3** | eth0 → r1 (10.0.1.2) · eth1 → r2 (10.0.6.2) · eth2 → r4 (10.0.7.1) · eth3 → r9 (10.0.19.1) · eth4 → O3 (10.0.26.1) |
| **r4** | eth0 → r1 (10.0.2.2) · eth1 → r3 (10.0.7.2) · eth2 → r5 (10.0.8.1) · eth3 → r10 (10.0.20.1) · eth4 → O1 (10.0.27.1) |
| **r5** | eth0 → r1 (10.0.3.2) · eth1 → r4 (10.0.8.2) · eth2 → r6 (10.0.9.1) · eth3 → r11 (10.0.21.1) · eth4 → C3 (10.0.28.1) |
| **r6** | eth0 → r1 (10.0.4.2) · eth1 → r5 (10.0.9.2) · eth2 → r7 (10.0.10.1) · eth3 → r12 (10.0.22.1) · eth4 → O2 (10.0.29.1) |
| **r7** | eth0 → r1 (10.0.5.2) · eth1 → r6 (10.0.10.2) · eth2 → r2 (10.0.11.1) · eth3 → r13 (10.0.23.1) · eth4 → O4 (10.0.30.1) |
| **r8** | eth0 → r9 (10.0.12.1) · eth1 → r13 (10.0.17.2) · eth2 → r2 (10.0.18.2) · eth3 → S2 (10.0.31.1) |
| **r9** | eth0 → r8 (10.0.12.2) · eth1 → r10 (10.0.13.1) · eth2 → r3 (10.0.19.2) · eth3 → C2 (10.0.32.1) |
| **r10** | eth0 → r9 (10.0.13.2) · eth1 → r11 (10.0.14.1) · eth2 → r4 (10.0.20.2) · eth3 → C1 (10.0.33.1) |
| **r11** | eth0 → r10 (10.0.14.2) · eth1 → r12 (10.0.15.1) · eth2 → r5 (10.0.21.2) · eth3 → S1 (10.0.34.1) |
| **r12** | eth0 → r11 (10.0.15.2) · eth1 → r13 (10.0.16.1) · eth2 → r6 (10.0.22.2) · eth3 → L1 (10.0.35.1) |
| **r13** | eth0 → r12 (10.0.16.2) · eth1 → r8 (10.0.17.1) · eth2 → r7 (10.0.23.2) · eth3 → L2 (10.0.36.1) |

### O que cada ligação overlay atravessa no underlay

Para pôr atraso, jitter ou perdas numa **só** ligação overlay usa-se uma ligação *direta* (um único caminho): aplicar o `tc` nos dois routers.
Nas ligações *com 2 caminhos* o OSPF reparte o tráfego por dois caminhos de custo igual (cada ligação TCP apanha um): não servem para um teste
determinístico; para atrasar essas, usar o `tc` na `eth0` do host (atrasa tudo o que esse nó envia).

| Ligação overlay | Routers | Ligações do underlay | Caminhos | Interface em cada router |
|---|---|---|---|---|
| S1–S2 | r11–r8 | 3 | 2 | usar `eth0` do host |
| S1–O1 | r11–r4 | 2 | 2 | usar `eth0` do host |
| S1–O2 | r11–r6 | 2 | 2 | usar `eth0` do host |
| S2–O3 | r8–r3 | 2 | 2 | usar `eth0` do host |
| S2–O4 | r8–r7 | 2 | 2 | usar `eth0` do host |
| O1–O2 | r4–r6 | 2 | 2 | usar `eth0` do host |
| O3–O4 | r3–r7 | 2 | 2 | usar `eth0` do host |
| O1–O3 | r4–r3 | 1 | **1 (direta)** | r4: `eth1` · r3: `eth2` |
| O2–O4 | r6–r7 | 1 | **1 (direta)** | r6: `eth2` · r7: `eth1` |
| C1–S1 | r10–r11 | 1 | **1 (direta)** | r10: `eth1` · r11: `eth0` |
| C1–O1 | r10–r4 | 1 | **1 (direta)** | r10: `eth2` · r4: `eth3` |
| C2–S2 | r9–r8 | 1 | **1 (direta)** | r9: `eth0` · r8: `eth0` |
| C2–O3 | r9–r3 | 1 | **1 (direta)** | r9: `eth2` · r3: `eth3` |
| C3–O1 | r5–r4 | 1 | **1 (direta)** | r5: `eth1` · r4: `eth2` |
| C3–O2 | r5–r6 | 1 | **1 (direta)** | r5: `eth2` · r6: `eth1` |
| C4–O3 | r2–r3 | 1 | **1 (direta)** | r2: `eth1` · r3: `eth1` |
| C4–O4 | r2–r7 | 1 | **1 (direta)** | r2: `eth2` · r7: `eth2` |

## 3. Lançar o cenário (sem abrir 11 terminais)

Com o cenário aberto no CORE (**Start**, esperar ≈ 20 s) basta, na pasta do projeto:

```sh
./bin/ottctl up cenario4 --backend core        # lança os 10 nós (o O5 só com --spare)
./bin/ottctl dashboard cenario4                # painel web: grafo, rotas, fluxos e eventos
./bin/ottctl kill cenario4 S1                  # os testes A1 a A7 e C1 a C5 reduzem-se a comandos deste género
./bin/ottctl down cenario4
```

Sem o CORE: `--backend local` põe os mesmos nós nesta máquina (sem `tc`). Detalhes em `docs/OTTCTL.md`. Os comandos manuais ficam abaixo,
para quem preferir ver cada terminal.

## 3.1. Comandos de cada nó (à mão)

Em todos os terminais: `cd /home/franciscorm/Documentos/ESR26-27`. Arrancar primeiro `S1` e `S2` (IP do `S1`: `10.0.34.20`; do `S2`: `10.0.31.20`); os outros por qualquer ordem.

| Nó | Comando |
|---|---|
| **S1** | `./bin/ott --serve topologies/cenario4.json -i S1 --publish-flow filme_a --publish-flow filme_b -v --stats 5` |
| **S2** | `./bin/ott --serve topologies/cenario4.json -i S2 --publish-flow filme_c --publish-flow filme_b -v --stats 5` |
| **O1** | `./bin/ott -i O1 -b 10.0.34.20 -v --stats 5` |
| **O2** | `./bin/ott -i O2 -b 10.0.34.20 -v --stats 5` |
| **O3** | `./bin/ott -i O3 -b 10.0.34.20 -v --stats 5` |
| **O4** | `./bin/ott -i O4 -b 10.0.34.20 -v --stats 5` |
| **C1** | `./bin/ott -i C1 -b 10.0.34.20 --subscribe S1:filme_a --subscribe S2:filme_c -v --stats 5` |
| **C2** | `./bin/ott -i C2 -b 10.0.34.20 --subscribe S1:filme_a --subscribe S2:filme_c -v --stats 5` |
| **C3** | `./bin/ott -i C3 -b 10.0.34.20 --subscribe S1:filme_b --subscribe S2:filme_b -v --stats 5` |
| **C4** | `./bin/ott -i C4 -b 10.0.34.20 --subscribe S1:filme_a --subscribe S2:filme_c -v --stats 5` |
| **O5** (suplente) | `./bin/ott -i O5 -b 10.0.34.20 -v --stats 5` (só nos testes de entrada) |
| **L1, L2** | sem `ott`: hosts livres para bootstrap, `ping`, `tcpdump` |

| Cliente | Subscreve | Para quê |
|---|---|---|
| C1 | S1:filme_a + S2:filme_c | `filme_a` vem direto do `S1`; `filme_c` **passa pelo S1** (`S2–S1–C1`) |
| C2 | S1:filme_a + S2:filme_c | espelho: `filme_a` **passa pelo S2** (`S1–S2–C2`) |
| C3 | S1:filme_b + S2:filme_b | **a mesma imagem das duas origens** (réplica): se um servidor cai, o outro continua |
| C4 | S1:filme_a + S2:filme_c | empates de saltos: três caminhos para `filme_a`, dois para `filme_c` |

### Saltos no overlay por fluxo e por falha (sem o `O5`)

Saltos do servidor ao cliente; `–` = esse fluxo deixa de existir (o servidor caiu). Depois de cada falha o caminho mais curto muda onde o número sobe.

| Cliente: fluxo | (sem falha) | sem S1 | sem S2 | sem O1 | sem O2 | sem O3 | sem O4 |
|---|---|---|---|---|---|---|---|
| C1: S1:filme_a | 1 | – | 1 | 1 | 1 | 1 | 1 |
| C1: S2:filme_c | 2 | 3 | – | 2 | 2 | 2 | 2 |
| C2: S1:filme_a | 2 | – | 3 | 2 | 2 | 2 | 2 |
| C2: S2:filme_c | 1 | 1 | – | 1 | 1 | 1 | 1 |
| C3: S1:filme_b | 2 | – | 2 | 2 | 2 | 2 | 2 |
| C3: S2:filme_b | 3 | 3 | – | 3 | 3 | 3 | 3 |
| C4: S1:filme_a | 3 | – | 3 | 3 | 3 | 3 | 3 |
| C4: S2:filme_c | 2 | 2 | – | 2 | 2 | 2 | 2 |

Por exemplo, `C1` recebe `S2:filme_c` em 2 saltos (via `S1`); sem o `S1` passa a 3 (`S2–O3–O1–C1`): é a rota que tem de ser redirecionada.

## 4. Testes que este cenário permite (por ensaiar)

Método: `docs/testes-core.md` §0 (o `ott` em segundo plano com o log num ficheiro, `date +%T.%N` antes da ação, tempos tirados das horas dos logs).
Com `--stats 5` cada cliente escreve, por fluxo, `recebidos`, `perdidos`, `atrasados` e `jitter`, e cada nó `dados UDP: recebidos/encaminhados/descartados`.
Com `-v`, enquanto o `O5` não arranca, o `O1` e o `O4` escrevem `falha ao ligar a …` de 2 em 2 s (normal). **Nenhum teste foi ainda corrido**: o
"esperado" vem das decisões do `ESTADO.md` (D21 métrica, D22 `WITHDRAW`, D24 subscrições agregadas, D27 épocas) e dos ensaios do E2 no cenário 3.

### A. Falha de um servidor (o objetivo principal)

| # | Teste | Como | Esperado |
|---|---|---|---|
| A1 | Cai o `S1`, que é caminho de passagem do `filme_c` | Todos a receber; `kill -9` no `S1` | Os fluxos do `S1` terminam nos clientes (`recebidos` pára; `WITHDRAW`). O `filme_c` do `C1` tem de **mudar de `S2–S1–C1` para `S2–O3–O1–C1`**: medir o tempo sem imagens. `C2` e `C4` só perdem os fluxos do `S1`: o `filme_c` deles não se altera |
| A2 | Cai o `S2` (espelho) | `kill -9` no `S2` | O `filme_a` do `C2` passa de `S1–S2–C2` a `S1–O1–O3–C2` (o `C2` perde também o `filme_c`, que era do `S2`) |
| A3 | Réplica: o cliente sobrevive à queda | `C3` a receber `S1:filme_b` e `S2:filme_b`; `kill -9` no `S1` | `C3` continua a receber o `filme_b` do `S2`: se esse caminho passava pelo `S1` (há 4 caminhos de 3 saltos empatados) há uma curta interrupção até redirecionar, senão nenhuma. O contador do `S1:filme_b` pára |
| A4 | O servidor volta | Reiniciar o `S1` depois de A1 | Épocas novas (D27): os fluxos do `S1` voltam em ≈ 1–2 s e o `filme_c` do `C1` regressa a `S2–S1–C1` (menos saltos), com uma só troca |
| A5 | Falha silenciosa contra falha com fecho | `kill -STOP` no `S1` (ou `ip link set eth3 down` no `r11`) em vez de `kill -9` | Só se deteta pelo timeout do keepalive (5–8 s): o redirecionamento demora essa diferença a mais do que em A1 |
| A6 | Caem os dois servidores | `kill -9` em `S1` e `S2`, depois reiniciar ambos | Nenhuma rota fica presa (`route_ttl`, D27); ao voltarem, todos os fluxos regressam |
| A7 | O redirecionamento escolhe a alternativa mais rápida | `delay 30ms` no `eth0` do `O1` (B2); depois `kill -9` no `S1` | O `S2:filme_b` do `C3` fica por `S2–O4–O2–C3` (evita o `O1` lento); o `filme_c` do `C1` não tem alternativa e tem de passar pelo `O1`, mesmo lento |

### B. Rotas com atraso, jitter, perdas e largura de banda diferentes

Todos os comandos `tc` aplicam-se nos **dois** extremos da ligação (cada `tc` atrasa o que sai desse lado). Remover com `tc qdisc del dev <iface> root`.

| # | Teste | Como | Esperado |
|---|---|---|---|
| B1 | Atraso numa ligação direta | `tc qdisc add dev <iface> root netem delay 30ms` em `r4` (`eth1`) e `r3` (`eth2`): ligação `O1–O3` | RTT `O1–O3` ≈ 60 ms (`--stats`); as rotas com empate de saltos que usavam essa ligação mudam para a alternativa, **uma só vez** |
| B2 | Atraso num nó inteiro | `tc qdisc add dev eth0 root netem delay 20ms` no host `O1` | +20 ms no RTT de todas as ligações do `O1`; `C3` (S1:filme_b, empate `O1`/`O2`) passa pelo `O2` |
| B3 | Jitter | `netem delay 20ms 15ms distribution normal` na ligação `O1–O3` | `jitter` dos clientes sobe; com a histerese (> 20 % e > 1 ms) o caminho **não oscila** (contar as mudanças nos logs `-v`) |
| B4 | Perdas | `netem loss 10%` na ligação `C3–O1` (`r5` `eth1`, `r4` `eth2`) | `perdidos` ≈ 10 % no `C3` para os fluxos que vêm por `O1`; o controlo TCP aguenta |
| B5 | Largura de banda | `tc qdisc add dev <iface> root tbf rate 500kbit burst 16kbit latency 100ms` numa ligação do caminho ativo | Perdas e atraso nos clientes desse ramo; comparar com o débito do fluxo |
| B6 | Menos saltos ganha mesmo com pior atraso | `delay 100ms` no `eth0` do `S1`: o `filme_c` do `C1` continua por `S2–S1–C1` (2 saltos) em vez de `S2–O3–O1–C1` (3 saltos) | Esperado pela métrica (saltos primeiro, RTT só desempata, D21). **Útil para a apreciação crítica**: com 100 ms a rota por 3 saltos seria melhor |
| B7 | Falha no underlay | Cortar `r4–r3` (`ip link set` com a interface de B1 em baixo) | O OSPF reencaminha pelo centro (`r4–r1–r3`); a ligação `O1–O3` sobrevive se o OSPF convergir em menos de 6 s; anotar se o overlay nota |
| B8 | Isolar um nó pela rede | `ip link set eth4 down` no `r4` | `O1` deixa de responder (timeout 5–8 s) e as rotas mudam, como em A5 |

### C. Entradas e saídas de nós, clientes e bootstrap

| # | Teste | Como | Esperado |
|---|---|---|---|
| C1 | Entrada do nó suplente | Arrancar o `O5` com tudo a correr | Liga-se a `O1` e `O4`; passa a ser caminho alternativo (a partir daí, uma queda de `O2` ou `O3` pode usá-lo) |
| C2 | Clientes a entrar e sair | `C4` pára e volta a arrancar | Só mudam os ramos de `C4`; os outros não perdem imagens (D24) |
| C3 | O servidor só envia se há clientes | Parar `C1`, `C2` e `C4` (só fica o `C3`, que só subscreve `filme_b`) | O `S1` e o `S2` deixam de emitir `filme_a` e `filme_c` (sem subscritores) e continuam a emitir o `filme_b` |
| C4 | Ordem trocada | Clientes primeiro e servidores no fim | Os clientes ficam à espera e escrevem `subscrito a …` quando o fluxo é anunciado |
| C5 | Bootstrapper principal em baixo | Parar o `S1`; parar o `O3` e voltar a lançá-lo com `-b <IP do S2>` | O `O3` volta a entrar; com `-b <IP do S1>` falha com `indisponível` |
| C6 | Substitutos | `O4` com `--min-neighbors 4` (tem `C4`, `O2`, `O3`, `S2`); parar o `O3` | ≈ 5 s depois pede `REPLACE`; o bootstrapper propõe um nó entre os vizinhos dos vizinhos atuais; total ≈ 7 s |
| C7 | Bootstrap | `L1`: `./bin/ott -i O99 -b <IP do S1> -v`; `L2`: `-b <IP do O3>` | `{'reason': 'nó desconhecido'}` e `{'reason': 'este nó não é bootstrapper'}` |

Antes de qualquer teste: esperar ≈ 20 s pelo OSPF e confirmar com `ping -c3 10.0.31.20` a partir de `C1` e `ping -c3 10.0.34.20` a partir de `C2`.
Se o XML for redesenhado no CORE perde-se a configuração do FRR: `python3 topologies/aplicar_frr.py topologies/cenario4.xml` (ou voltar a correr `gerar_cenario4.py`).
