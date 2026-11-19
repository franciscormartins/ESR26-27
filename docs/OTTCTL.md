# ottctl: lançar cenários, injetar falhas e ver o que se passa

Ferramenta de **apoio aos testes e à apresentação**; não faz parte do `ott` nem do enunciado. Em vez de abrir um terminal por nó
e ler logs, faz-se:

```sh
./bin/ottctl up cenario4 --backend core        # nós de uma sessão do CORE já aberta (Start), ou: --backend local
./bin/ottctl dashboard cenario4                # painel web em http://127.0.0.1:8080
./bin/ottctl kill cenario4 S1                  # falha de um servidor
./bin/ottctl status cenario4 -w                # tabela de todos os nós, de 2 em 2 s
./bin/ottctl down cenario4
```

Só usa a biblioteca standard. As pastas de trabalho (estado e logs de cada nó) ficam em `/tmp/ottctl/<cenário>/`.

## Os dois modos

| | `--backend core` | `--backend local` |
|---|---|---|
| Onde correm os nós | dentro dos nós de uma sessão do CORE (`vcmd`), como à mão | todos nesta máquina, cada um num IP de loopback (`127.0.0.N`) |
| Pré-requisito | abrir o cenário no CORE e fazer **Start** (esperar ≈ 20 s pelo OSPF) | nenhum |
| Rede (atraso, jitter, perdas, cortar ligações) | sim (`tc netem` e `ip link` nos nós) | não |
| Para que serve | os ensaios a sério e a demonstração | desenvolver, demonstrar sem o CORE, testes automáticos |

No modo `core` a sessão é a mais recente que tenha os nós do cenário (`--session N` para escolher). **Este modo foi validado
contra uma sessão real do CORE 9.2.1 (cenário 4): lançamento dos nós, leitura do estado pelo sistema de ficheiros partilhado, `netem`,
`link down` e os testes `G1`, `A1`–`A3`, `B1`, `B3`, `B6` e `C1`–`C7`.**

## Comandos

| Comando | O que faz |
|---|---|
| `ottctl list` | cenários com plano (`topologies/<nome>.plano.json`) |
| `ottctl up CEN [--backend core\|local] [--spare] [--only A,B] [--verbose]` | lança os nós (servidores primeiro). `--spare` lança também os suplentes (ex.: `O5`) |
| `ottctl down CEN` | pára todos os nós |
| `ottctl status CEN [-w]` | nó, estado, vizinhos com RTT, rotas ativas e fluxos recebidos (img/s, perdas) |
| `ottctl routes CEN NÓ` | **tabela de rotas** do nó: montante, saltos, atraso, estado, destinos e candidatos |
| `ottctl events CEN [-n 40] [-f]` | só os eventos relevantes de todos os nós, por ordem de tempo |
| `ottctl stop\|kill\|freeze\|thaw\|start CEN NÓ…` | saída limpa (`SIGTERM`, com BYE), morte abrupta (`SIGKILL`), nó calado (`SIGSTOP`), retomar, (re)lançar |
| `ottctl netem CEN NÓ IFACE --delay 30ms --jitter 10ms --loss 5 --rate 500kbit` | atraso/jitter/perdas/débito numa interface (só `core`); `NÓ` é um nó overlay ou do CORE (`r4`) |
| `ottctl netem-clear CEN NÓ IFACE` · `ottctl link CEN NÓ IFACE up\|down` | remove o `netem`; liga/desliga uma interface |
| `ottctl paths CEN` | caminho que o último `ROUTE_TEST` fez até cada cliente (`C1 S1:filme_a  S1 → O1 → O5 → C1`) |
| `ottctl subs CEN [NÓ FLUXO…] [--reset]` | muda os fluxos que um nó subscreve (relança-o); sem argumentos lista as opções; `--reset` repõe as do plano |
| `ottctl hop CEN A B [--delay 50ms --loss 10 \| --cut \| --restore \| --clear]` | obstáculo no troço overlay A→B, na ligação do underlay a meio do caminho IP verdadeiro (só `core`) |
| `ottctl test CEN [IDS…] [--list] [--repeat N] [--md] [--json F]` | corre os testes automáticos do cenário (ver abaixo); `--repeat` dá mínimo/média/máximo, `--md` uma tabela para o relatório |
| `ottctl dashboard [CEN] [--backend core\|local] [--session N] [--port 8080]` | painel web (sem `CEN` escolhe-se o cenário no próprio painel) |

## O painel (guia completo em `docs/PAINEL.md`)

Grafo do overlay na posição do desenho do CORE, com a cor de cada nó pelo estado (ligado, por lançar, parado, morto, congelado, sem
resposta) e **a árvore de cada fluxo** desenhada com setas (traço cheio = ramo ativo, tracejado = rota sem procura). Nos clientes
aparece o ritmo de cada fluxo (`20/s`, ou `✗` se parou). Ao clicar num nó vê-se a tabela de rotas, os vizinhos com RTT, os fluxos
recebidos (perdas e jitter), o caminho até ao servidor e botões para o lançar, parar, matar, congelar e retomar. Em baixo, os
eventos relevantes (a vermelho o que corre mal, a verde o que recupera). No modo `core` há ainda o formulário para pôr atraso, jitter,
perdas ou débito numa ligação do underlay e para a cortar e repor.

O servidor só escuta em `127.0.0.1`; recusa pedidos cujo `Host` não seja local e só aceita `POST` em JSON; as interfaces e os valores do
`tc` são validados com expressões estritas (no CORE correm como root dentro do nó).

## Atalhos (`Makefile`)

`make dashboard` abre o painel (local) e `make dashboard-core [SESSION=N]` liga-o a uma sessão do CORE já iniciada; `make test`,
`make test-core`, `make test-md`, `make up|down|status|events`, `make unit` e `make help`. Variáveis: `CEN`, `PORT`, `BACKEND`,
`SESSION`, `ARGS`. No separador *Rede* há botões de perdas rápidas (1, 5, 10, 20, 30 %) para a ligação escolhida.

## Vídeos e ROUTE_TEST nos cenários

O plano pode ter `"videos": {"filme_b": "videos/filme_b.Mjpeg"}` (MJPEG de cada fluxo) e `"route_test": 2` (segundos entre os `ROUTE_TEST` de cada
servidor; 0 desliga). Sem `videos`, usa-se `videos/<fluxo>.Mjpeg` se existir e, senão, o filme real `progex/movie.Mjpeg`; por isso cada fluxo tem o seu
vídeo e o mesmo fluxo publicado por dois servidores é o mesmo vídeo. Os vídeos de `videos/` fazem-se com `tools/gerar_video_teste.py` (precisa do Pillow;
`tools/gerar_videos_dos_cenarios.sh`). Os servidores arrancam com `--publish-flow fluxo=ficheiro` e `--route-test 2`; cada nó escreve os testes que
recebeu em `route_tests` no ficheiro de estado e o orquestrador junta-os em `state["paths"]`.

## Testes automáticos (`ottctl test` e separador *Testes*)

Cada teste prepara a situação, provoca a falha, espera pela reação e **mede** (tempo até haver rota nova, interrupção do vídeo, RTT,
perdas). Os de `G` (G1–G9) servem qualquer cenário: arranque, queda de um nó, **de cada servidor**, nó congelado, cliente que sai e volta, **ROUTE_TEST**
(o caminho é o da árvore e muda quando um nó cai), nó suplente, **réplica com failover**, **subscrições que mudam**. Os de `N` (N1–N4) são de rede e só
correm no CORE, no caminho ativo de um cliente (atraso, corte de uma ligação, router de passagem desligado, perdas). Os de `A` (falhas de servidores),
`C` (arranque, suplentes, bootstrapper) e `B` (rede em ligações concretas, só no CORE) são do cenário 4. Os testes `B` põem e retiram o `netem` por si. Resultados medidos em local e no CORE estão em
`docs/ESTADO.md`. Duas observações que saíram daqui:

- **B3**: com 10 % de perdas de pacotes perdem-se ≈ 44 % das imagens, porque cada imagem JPEG (≈ 8 KiB) vai num só datagrama UDP que
  o IP parte em ≈ 6 fragmentos e basta perder um (esperado 1 − 0,9⁶ ≈ 47 %).
- **B6**: com 100 ms no S1 o C1 troca o `filme_c` para o caminho de 3 saltos; é o comportamento da D34 (latência primeiro), já não o
  "saltos primeiro" da D21.

## Demonstração guiada

O separador *Demonstração* percorre passos (arranque, árvore de um fluxo, queda de um nó, regresso, atraso na rede…) com texto
explicativo e executa cada ação a pedido. Modo de apresentação: tecla `p` (ou `#demo,present` no endereço), `espaço`/`→` avança,
`f` põe os fluxos em foco. O separador *Rede* mostra os efeitos `netem`/`link` ativos e o *Nó* os gráficos (img/s, jitter, RTT) com as
ações marcadas no tempo (histórico amostrado a 1 Hz no servidor, `/api/history`).

## Como funciona

1. **`ott --status-file F`** (em `src/ott/status.py`): cada nó reescreve `F` de segundo a segundo com os vizinhos e o RTT, a tabela
   de rotas, os fluxos e os contadores do plano de dados, e uma lista de **eventos relevantes** (vizinho que cai ou volta, rota nova,
   montante que muda, rota sem caminho, `WITHDRAW`, fluxo que pára ou volta). Os nós do CORE partilham o sistema de ficheiros do
   anfitrião, por isso não é preciso rede para o ler.
2. **O plano** (`topologies/<cenário>.plano.json`) diz o que corre em cada nó: servidor, fluxos publicados, subscrições (`SERVIDOR:FLUXO`, ou só `FLUXO` para usar
   qualquer servidor que o publique, D38), suplentes.
   O do cenário 4 sai do `gerar_cenario4.py`.
3. **O orquestrador** (`src/ott/ctl/`) monta a linha de comandos do `ott` de cada nó e lança-a no modo escolhido.

## Limitações

- O estado de um nó passa a ter `phase` ("a registar" enquanto espera pelo bootstrapper) e eventos `fluxo_lacuna` (interrupção ≥ 1 s
  que recupera antes de o fluxo ser dado como parado).

- Os eventos de rotas e de fluxos saem de comparar fotografias de 0,5 s: duas mudanças dentro do mesmo intervalo contam como uma.
- Um nó morto com `kill -9` deixa de escrever; o painel mostra-o como "morto" (se foi o `ottctl` a matá-lo) ou "sem resposta".
- O modo `local` não pode atrasar nem cortar ligações (precisa de root) e só dá uma máquina: serve para ver o comportamento das rotas.
- Só um `ott` por nó do CORE: `ottctl up` recusa-se a lançar um nó que o orquestrador sabe estar a correr.
