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

No modo `core` a sessão é a mais recente que tenha os nós do cenário (`--session N` para escolher). **Este modo foi testado com um
executor simulado e com a experiência dos ensaios manuais; ainda não foi corrido contra uma sessão real do CORE.**

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
| `ottctl dashboard CEN [--port 8080]` | painel web |

## O painel

Grafo do overlay na posição do desenho do CORE, com a cor de cada nó pelo estado (ligado, por lançar, parado, morto, congelado, sem
resposta) e **a árvore de cada fluxo** desenhada com setas (traço cheio = ramo ativo, tracejado = rota sem procura). Nos clientes
aparece o ritmo de cada fluxo (`20/s`, ou `✗` se parou). Ao clicar num nó vê-se a tabela de rotas, os vizinhos com RTT, os fluxos
recebidos (perdas e jitter), o caminho até ao servidor e botões para o lançar, parar, matar, congelar e retomar. Em baixo, os
eventos relevantes (a vermelho o que corre mal, a verde o que recupera). No modo `core` há ainda o formulário para pôr atraso, jitter,
perdas ou débito numa ligação do underlay e para a cortar e repor.

O servidor só escuta em `127.0.0.1`; recusa pedidos cujo `Host` não seja local e só aceita `POST` em JSON; as interfaces e os valores do
`tc` são validados com expressões estritas (no CORE correm como root dentro do nó).

## Como funciona

1. **`ott --status-file F`** (em `src/ott/status.py`): cada nó reescreve `F` de segundo a segundo com os vizinhos e o RTT, a tabela
   de rotas, os fluxos e os contadores do plano de dados, e uma lista de **eventos relevantes** (vizinho que cai ou volta, rota nova,
   montante que muda, rota sem caminho, `WITHDRAW`, fluxo que pára ou volta). Os nós do CORE partilham o sistema de ficheiros do
   anfitrião, por isso não é preciso rede para o ler.
2. **O plano** (`topologies/<cenário>.plano.json`) diz o que corre em cada nó: servidor, fluxos publicados, subscrições, suplentes.
   O do cenário 4 sai do `gerar_cenario4.py`.
3. **O orquestrador** (`src/ott/ctl/`) monta a linha de comandos do `ott` de cada nó e lança-a no modo escolhido.

## Limitações

- Os eventos de rotas e de fluxos saem de comparar fotografias de 0,5 s: duas mudanças dentro do mesmo intervalo contam como uma.
- Um nó morto com `kill -9` deixa de escrever; o painel mostra-o como "morto" (se foi o `ottctl` a matá-lo) ou "sem resposta".
- O modo `local` não pode atrasar nem cortar ligações (precisa de root) e só dá uma máquina: serve para ver o comportamento das rotas.
- Só um `ott` por nó do CORE: `ottctl up` recusa-se a lançar um nó que o orquestrador sabe estar a correr.
