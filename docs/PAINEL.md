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

O painel trabalha só com o **CORE**: abre o cenário (`topologies/cenarioN.xml`), faz *Start*, espera ≈ 20 s e só depois abre o painel.
(O modo local continua disponível na linha de comandos `ottctl --backend local`, para os testes automáticos.)

## 2. Cabeçalho

| Controlo | Para que serve |
|---|---|
| **Cenário** / **Abrir** | escolhe o cenário (1–4) e liga-se à sessão do CORE que o tem aberto |
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
  vermelho as cortadas. Funciona nos cenários 1–4; no 3 os switches aparecem como losangos e os efeitos de rede nas ligações a switches aplicam-se só ao extremo do nó. *Só overlay* mostra apenas os nós OTT.
- **Clicar num nó** escolhe-o para os separadores.

## 4. Separadores (direita)

### Nó
Estado do nó escolhido: botões **Lançar / Parar (limpo) / Matar / Congelar / Retomar** (SIGTERM com BYE, SIGKILL, SIGSTOP, SIGCONT),
vizinhos com RTT, tabela de rotas, fluxos recebidos (img/s, perdas, jitter, parado ou a chegar), contadores do plano de dados UDP
(recebidos, reencaminhados, descartados, RPF) e, no servidor de bootstrap, os nós registados.

### Vídeo
Mosaico com **o vídeo que cada cliente está a receber**, ao vivo. Cada mosaico diz o cliente e o fluxo, as img/s, as perdas, o jitter e
**por que nó chega agora o fluxo** (`via O3`). Por baixo estão os botões Lançar / Parar / Matar / Congelar / Retomar do nó escolhido no
grafo, para se ver o efeito sem mudar de separador. Se o fluxo pára (ou o cliente está parado) o mosaico escurece com «sem imagens». Cada
cliente guarda a imagem mais recente em `/tmp/ottctl/<cenário>/frames/<nó>/` (`--frame-dir`, no máximo 10 por segundo); o painel serve-a
como MJPEG em `/api/video?node=C1&flow=S1:filme_a`.

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
Lista os testes do cenário (G genéricos, A falhas de nós, C arranque e suplentes, B rede — só no CORE). Escolhe, define as repetições e
**Correr**; cada resultado mostra o que foi verificado, as medições (tempo de recuperação, interrupção, RTT, perdas) com o intervalo
esperado e notas. **Copiar** dá a tabela em Markdown (mín/média/máx) para o relatório. Os testes repõem o que alteraram (rede, nós).
Equivalente: `make test ARGS="A1 A2 --repeat 3"`, `make test-md`.

### Demonstração
Roteiro guiado para a apresentação: cada passo tem um texto, foca o grafo nos nós relevantes e **executa** a ação (matar o S1,
congelar o S2, entrar o O5, atrasar uma ligação…), mostrando depois o que foi medido. **Seguinte / Anterior / Recomeçar**.
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
| apresentar | **Apresentação**, separador **Demonstração**, `espaço` para avançar |

## 7. Segurança e limitações

O servidor só escuta em `127.0.0.1`, recusa pedidos de outros anfitriões e só aceita `POST` em JSON; os valores de rede são validados
antes de chegarem ao `tc`. O estado vem de ficheiros escritos de segundo a segundo, por isso duas mudanças dentro do mesmo intervalo
contam como uma. Só um `ott` por nó do CORE.
