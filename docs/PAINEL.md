# Painel web (`ottctl dashboard`): funcionalidades e como usar

O painel serve para **testar** e **demonstrar** o overlay. Não faz parte do enunciado: só lê o estado que cada nó escreve (`--status-file`) e
lança ou sinaliza processos. Detalhes técnicos em `docs/OTTCTL.md`.

## 1. Arrancar

```sh
make dashboard                  # liga-se à sessão do CORE mais recente (já com Start)
make dashboard SESSION=2        # escolhe a sessão
make dashboard PORT=9000        # outra porta
```

Abre o endereço que aparece (`http://127.0.0.1:8080`). Se a porta estiver ocupada, usa a seguinte (8081, …).
Equivalente sem `make`: `./bin/ottctl dashboard cenario4 --backend core --port 8080`.

Os **cinco cenários** (`cenario1` … `cenario5`) estão disponíveis. O painel trabalha só com o **CORE**: abre o cenário (`topologies/cenarioN.xml`), faz *Start*, espera ≈ 20 s (OSPF), corre `make dashboard CEN=cenarioN` e carrega em **Lançar tudo**. (O modo local só existe na linha de comandos, `ottctl --backend local`, para os testes automáticos.)

## 2. Cabeçalho

| Controlo | Para que serve |
|---|---|
| **Cenário** / **Abrir** | escolhe o cenário (1–5) e liga-se à sessão do CORE que o tem aberto |
| **Lançar tudo** / **Parar tudo** | arranca os nós (servidores primeiro) ou pára-os todos |
| **Apresentação** | letra maior para projetar (tecla `p`) |
| etiquetas | modo atual, e "a trabalhar…" enquanto corre um teste ou um passo da demonstração |

## 3. Grafo (esquerda)

- Cada **nó** na posição do desenho do CORE. Cor = estado: ligado (verde), por lançar, parado, morto, congelado (azul), sem resposta,
  à espera do bootstrapper. Os servidores são retângulos; o suplente aparece com a etiqueta *suplente*. Os clientes mostram as img/s de
  cada fluxo (`✗` se parou).
- **Setas coloridas** = a árvore de cada fluxo (uma cor por fluxo). Traço cheio = ramo ativo; tracejado = rota conhecida mas ainda sem
  subscritores. **Fluxo**: filtra para ver só a árvore de um fluxo.
- **Vista**: *topologia do CORE + fluxos* (por defeito) desenha o mesmo que o CORE (routers `r1`–`r13`, hosts e ligações, lidos do `.xml`)
  com os nós OTT nos seus hosts; a árvore de cada fluxo segue o **caminho IP verdadeiro** (o painel pergunta a cada router, com `ip route get`,
  qual é o salto seguinte, de 3 em 3 s), por isso se vê a rota mudar quando se mexe numa ligação. Enquanto o primeiro cálculo não chega,
  desenha o caminho mais curto do desenho.
  **Clicar numa ligação** abre o separador *Rede* com ela escolhida (atraso, perdas, cortar). A laranja as ligações com efeito, a
  vermelho as cortadas. Funciona nos cenários 1–5; no 3 os switches aparecem como losangos e os efeitos de rede nas ligações a switches aplicam-se só ao extremo do nó. *Só overlay* mostra apenas os nós OTT.
- **Clicar num nó** escolhe-o para os separadores.

## 4. Separadores (direita)

### Nó
Estado do nó escolhido: botões **Lançar / Parar (limpo) / Matar / Congelar / Retomar** (SIGTERM com BYE, SIGKILL, SIGSTOP, SIGCONT),
vizinhos com RTT, tabela de rotas, fluxos recebidos (img/s, perdas, jitter, parado ou a chegar), contadores do plano de dados UDP
(recebidos, reencaminhados, descartados, RPF) e, no servidor de bootstrap, os nós registados.

### Vídeo
Mosaico com **o vídeo que cada cliente está a receber**, ao vivo. Cada mosaico diz o cliente, o fluxo, o ficheiro de vídeo, as img/s, as
perdas, o jitter e **por que nó chega agora o fluxo** (`via O3`; e `de S1` numa subscrição sem servidor fixo). **Cada fluxo tem o seu filme**:
o `filme_a` é o filme real (`progex/movie.Mjpeg`) e o `filme_b`, `filme_c` e `filme_d` são vídeos de teste de cores diferentes com o nome do
fluxo escrito (`videos/`, feitos por `tools/gerar_videos_dos_cenarios.sh`). Um fluxo publicado por dois servidores (o `filme_c` nos cenários 4
e 5, o `filme_b` no 4) é o **mesmo conteúdo**: ao cair um servidor, o cliente passa para o outro e vê-se o mesmo filme a continuar.
- **agrupar por fluxo** põe lado a lado as janelas do mesmo filme em clientes diferentes; **tamanho** muda o mosaico.
- **Clicar numa janela** desenha o caminho dela no grafo (a amarelo).
- Por baixo estão os botões Lançar / Parar / Matar / Congelar / Retomar do nó escolhido no grafo, para se ver o efeito sem mudar de separador.
  Se o fluxo pára (ou o cliente está parado) o mosaico escurece com «sem imagens».
Cada cliente guarda a imagem mais recente em `/tmp/ottctl/<cenário>/frames/<nó>/` (`--frame-dir`, no máximo 10 por segundo); o painel serve-a
como MJPEG em `/api/video?node=C1&flow=S1:filme_a`.

### Caminhos — o ROUTE_TEST (Etapa 3) ao vivo
Cada servidor envia um `ROUTE_TEST` a cada 2 s pela árvore ativa (`route_test` no plano; 0 desliga) e cada cliente regista o caminho que
ele fez. A tabela mostra, para **cada subscrição de cada cliente**, o caminho `S1 → O2 → C1` (servidor a azul, cliente a verde), os saltos, a
latência acumulada da rota e a idade do último teste (a cinzento se já é antigo: o caminho deixou de existir). **Clicar numa linha** desenha o caminho
no grafo e abre os **obstáculos desse caminho**:
- **Nós**: matar, congelar ou parar cada nó intermédio (funciona nos dois modos);
- **Troços** (só CORE): para cada ligação overlay do caminho, **+50 ms**, **+200 ms**, **10 % de perdas**, **cortar**, **repor** e **limpar**. O painel
  descobre o caminho IP verdadeiro entre os dois nós (`ip route get` em cada router) e põe o efeito na ligação do underlay a meio dele;
- **Histórico deste caminho**: as últimas mudanças de montante e de caminho dessa subscrição.
Por baixo da tabela, a **poupança de tráfego** de cada fluxo: clientes, cópias que saem do servidor, ligações overlay usadas pela árvore e quantas seriam
precisas sem overlay (a soma dos saltos de cada cliente): é a prova de que o ramo comum leva uma só cópia.
Depois de cada obstáculo vê-se o caminho mudar na tabela e no grafo (e o vídeo continuar, ou parar se não houver alternativa).

### Clientes — o professor escolhe os filmes
Escolhe um nó (cliente, intermédio ou suplente), marca os fluxos que ele deve subscrever (`S1:filme_b`, `S2:filme_c`, ou **qualquer servidor** para um
fluxo replicado: usa o mais perto e passa para o outro se esse cair) e carrega em **Aplicar**: o nó é parado (BYE) e relançado com as subscrições
novas. O servidor só emite o que alguém pede e o ramo novo sobe do cliente ao servidor pelo caminho inverso. **Repor as do plano** desfaz todas as mudanças.
Um `*` junto ao nó indica que as subscrições diferem das do plano. Na linha de comandos: `ottctl subs cenario5 C1 S1:filme_b filme_c`.

### Rotas — tabelas de encaminhamento
É a tabela pedida no enunciado, **uma linha por fluxo**:

| Coluna | Significado |
|---|---|
| **Fluxo** | `servidor:fluxo` (ex.: `S1:filme_a`) |
| **Custo** | saltos e atraso acumulado em ms (a métrica é a latência, D34; os saltos só desempatam). `∞` = sem caminho |
| **Origem** | o vizinho de onde chega o fluxo (montante); `(origem)` no próprio servidor; `(retirada)` após `WITHDRAW` |
| **Destinos** | vizinhos a jusante a quem o nó reenvia o fluxo; `+ local` se o próprio nó o consome |
| **Estado** | `inativa` até haver subscritores, `ativa` quando o fluxo corre |
| candidatos | montantes alternativos conhecidos (`vizinho:saltos/atraso`), para ver porque se escolheu um caminho |

Usa o seletor **nó** para ver um nó ou **todos os nós** (útil para ver a rede inteira num ecrã), e **Copiar (Markdown)** para colar a
tabela no relatório. Atualiza-se de segundo a segundo; se um nó estiver parado mostra o último estado conhecido. Na linha de comandos:
`./bin/ottctl routes cenario4 O1`.

### Testes
Lista os testes do cenário: **G1–G9 servem os cinco cenários** (arranque, queda e regresso de um nó, **de cada servidor**, nó congelado, cliente que sai e volta,
**ROUTE_TEST** e o caminho que muda, nó suplente, **réplica com failover**, **mudança de subscrições**), **N1–N4** são de rede e só correm no CORE
(**atraso, corte de ligação, router desligado e perdas no caminho ativo de um cliente**), e o cenário 4 ainda tem A, C e B (falhas de servidores,
entradas e saídas, atraso/perdas em ligações concretas). Os testes G e N escolhem sozinhos o nó, o cliente e o troço a partir do caminho
medido, por isso não dependem dos nomes dos nós. Escolhe, define as repetições e
**Correr**; cada resultado mostra o que foi verificado, as medições (tempo de recuperação, interrupção, RTT, perdas) com o intervalo
esperado e notas. **Copiar** dá a tabela em Markdown (mín/média/máx) para o relatório. Os testes repõem o que alteraram (rede, nós).
Equivalente: `make test ARGS="A1 A2 --repeat 3"`, `make test-md`.

### Demonstração
Roteiro guiado para a apresentação, **um para cada cenário**: cada passo tem um texto, foca o grafo nos nós relevantes e **executa** a ação,
mostrando depois o que foi medido. O guião comum (cenários 1, 2, 3 e 5): arranque e rotas, o filme de cada fluxo, o caminho de cada cliente, cai um
nó do caminho, o nó volta, cai um servidor (a réplica entra, o resto pára), o servidor volta, atraso num troço do caminho (CORE), corta uma ligação
(CORE), o cliente muda de filme, repor. O do cenário 4 mantém o seu guião (S1 cai, réplica, S2 congela, entra o O5…) e acrescenta o caminho de cada
cliente, o corte de uma ligação e a mudança de filme. Cada passo que tem um separador próprio (Vídeo, Caminhos, Clientes) mostra um botão **Ver no
separador…**. **Seguinte / Anterior / Recomeçar**.
Teclas: `espaço` ou `→` seguinte, `←` anterior, `p` modo apresentação, `f` ecrã inteiro. Endereço direto: `/#demo,present`.

### Routers (vista da topologia do CORE)
Os routers (`r1`…) são **clicáveis** no grafo: abre o separador *Rede* com o router escolhido. **Desligar router** põe em baixo todas as
suas interfaces (como se tivesse morrido: o OSPF recalcula e os nós OTT mudam de caminho); **Ligar router** repõe-nas. Um router desligado fica
a vermelho. Por baixo estão as ligações do router (um botão por vizinho): escolhe uma e muda-lhe o atraso, as perdas ou corta-a.

### Rede
Escolhe uma **ligação do underlay** e aplica **atraso, jitter, perdas e débito** (`tc netem`, nos dois extremos: 30 ms por lado dá ≈ 60 ms
de RTT). Botões de **atraso rápido** (20, 50, 100, 200 ms) e de **perdas rápidas** (1, 5, 10, 20, 30 %) usam também os valores escritos nos outros campos. **Aplicar**, **Limpar**,
**Cortar** (link down) e **Repor**. Os efeitos ativos ficam listados, com **Repor toda a rede**. Nota: 10 % de perda de pacotes dá ≈ 45 %
de imagens perdidas, porque cada imagem vai em vários fragmentos IP e basta perder um.

## 5. Gráficos e eventos (em baixo)

- **Fluxos no tempo**: imagens/s, jitter ou RTT dos clientes (escolhe a métrica). Um histórico é amostrado de segundo a segundo no
  servidor, por isso não se perde ao recarregar a página. As ações feitas no painel (matar, atrasar, cortar…) aparecem como marcas.
- **Eventos relevantes**: vizinho que cai ou volta, rota nova, montante que muda, rota sem caminho, `WITHDRAW`, fluxo que pára, volta ou tem
  interrupção. Vermelho = problema, verde = recuperação. **só o nó escolhido** filtra.

## 6. Receitas rápidas

| Quero… | Faço… |
|---|---|
| ver a tabela de rotas de um nó | separador **Rotas**, escolho o nó (ou «todos») |
| ver o que acontece quando um nó cai | **Nó** → **Matar** num nó intermédio; acompanho *Eventos* e o gráfico de img/s; **Lançar** para o repor |
| testar perdas numa ligação | modo CORE → **Rede** → escolho a ligação → **10%** → vejo as perdas e o jitter nos clientes |
| medir o tempo de recuperação | **Testes** → A1 com repetições → **Copiar** |
| ver o vídeo e testar uma falha | separador **Vídeo** → clico num nó do grafo (ex.: `O3`) → **Matar** → vejo o `via` mudar e o vídeo continuar; **Lançar** para o repor |
| comparar filmes de servidores diferentes | **Vídeo** → agrupar por fluxo: `filme_b` (azul) e `filme_d` (laranja) são de servidores diferentes; o `filme_c` é o mesmo nos dois |
| ver o caminho de um cliente mudar | **Caminhos** → clico na linha do cliente → mato um nó do caminho (ou, no CORE, +200 ms / cortar num troço) → a linha e o grafo mudam |
| pôr um cliente a ver outro filme | **Clientes** → escolho o nó e os fluxos → **Aplicar** → **Vídeo** |
| apresentar | **Apresentação**, separador **Demonstração**, `espaço` para avançar |

## 7. Segurança e limitações

O servidor só escuta em `127.0.0.1`, recusa pedidos de outros anfitriões e só aceita `POST` em JSON; os valores de rede são validados
antes de chegarem ao `tc`. O estado vem de ficheiros escritos de segundo a segundo, por isso duas mudanças dentro do mesmo intervalo
contam como uma. Só um `ott` por nó do CORE.
