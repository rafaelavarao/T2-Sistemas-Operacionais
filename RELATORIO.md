# Relatório — SimES: simulador de E/S e de sistema de arquivos

**Integrantes:** Rafaela Varão, Gabriel Gauterio, Gabriel Dalben

**Repositório:** https://github.com/rafaelavarao/T2-Sistemas-Operacionais

## 0. Resumo

O grupo (3 integrantes) completou o núcleo do SimES (SSTF, SCAN, custo de CPU por polling e por interrupção, e o sistema de arquivos com tabelas de arquivos abertos por processo e global) e, além do mínimo exigido (2 módulos), implementou os seis módulos (M1 cache, M2 escalonadores avançados, M3 DMA e buffering, M4 links, M5 proteção e travas, M6 alocação), a integração ponta a ponta e o bônus de armazenamento flash. Os 26 testes fornecidos e 13 testes próprios passam, e os experimentos X1–X13 rodam sem erro. Os três resultados mais importantes:

1. **Escalonar importa mais quando há localidade a explorar.** Na carga sequencial com dois fluxos, o FCFS faz o braço alternar entre regiões a 3.600 cilindros de distância e satura o disco (resposta média de 10,2 s); o SSTF atende cada fluxo em sequência, sem latência rotacional, e cai para 17 ms (X1).
2. **A cache write-back reduz pela metade as escritas físicas, mas as concentra em rajadas.** Com 256 blocos ou mais, a sincronização a cada 5 s despeja ~55 blocos de uma vez na fila FCFS, e a resposta média fica *pior* do que com write-through (X6). Numa queda de energia, perdem-se até 55 blocos.
3. **O custo da alocação depende do padrão de acesso e do modelo de disco.** A FAT foi a melhor alocação em acessos e em tempo. A encadeada fez 18 vezes mais acessos do que a indexada e mesmo assim gastou menos tempo, porque as leituras da corrente caem em blocos fisicamente vizinhos e o modelo não cobra latência rotacional nelas (X11).

## 1. Escopo realizado

| Parte | Situação | Observação |
|---|---|---|
| N1 · Dispositivo (fornecido) | usado | — |
| N2 · Subsistema de E/S (SSTF, SCAN, polling, interrupção) | completo | testes do livro conferem (236 e 331 cilindros) |
| N3 · Sistema de arquivos | completo | todas as regras de custo conferidas pelos testes |
| N4 · Experimentos X1–X5 e relatório | completo | — |
| M1 · Cache de blocos | completo | LRU e FIFO com base comum (OrderedDict) |
| M2 · Escalonamento avançado | completo | C-SCAN, LOOK, C-LOOK e DEADLINE |
| M3 · DMA e buffering | completo | — |
| M4 · Links e compartilhamento | completo | links simbólicos no meio do caminho, ELOOP, detecção de ciclos |
| M5 · Proteção e travas | completo | rwx, ACL, travas consultivas e mandatórias |
| M6 · Alocação em disco | completo | encadeada, FAT e indexada com 2 níveis |
| Integração ponta a ponta | completo | feita como diferencial (não obrigatória para 3 integrantes) |
| Bônus flash | completo | FTL em log com coleta de lixo gulosa; experimento extra declarado na seção 3 (X13) |

Resultado dos testes automáticos (`python3 executar.py testes`): `Ran 39 tests in 0.012s — OK` (26 fornecidos + 13 do grupo em `testes/test_grupo.py`, nenhum pulado).

## 2. Implementação

### N2 — Subsistema de E/S

**Onde:** `simes/es/escalonadores.py` (`SSTF`, `SCAN` e a função auxiliar `_mais_proxima`) e `simes/es/cpu.py` (`custo_cpu`).

**Decisões:** Todos os escalonadores de "menor distância" usam uma única função auxiliar, `_mais_proxima(fila, cabeca, sentido)`. Ela monta tuplas `(distância, índice)` e aplica `min()`, então o empate cai automaticamente na requisição que está antes na fila (a regra comum do enunciado). O parâmetro `sentido` (0, +1 ou −1) filtra as requisições "à frente", e o mesmo cilindro conta nos dois sentidos. O SSTF devolve a direção do movimento feito, ou mantém a atual quando o alvo está no mesmo cilindro. Quando não há nada à frente, o SCAN põe a borda (0 ou `max_cilindro`) em `passar_por`, inverte e procura a mais próxima *a partir da borda*. Se o braço já está na borda, o deslocamento extra é 0 (testado). Polling custa `duracao_ms`, porque a CPU fica em espera ocupada durante todo o atendimento. Interrupção custa `n_blocos × (CUSTO_INTERRUPCAO_MS + CUSTO_COPIA_MS_POR_BLOCO)`.

**Dificuldades:** Desempenho. No X12 com alocação indexada, a fila chega a cerca de 10 mil requisições e a escolha é O(n). A primeira versão, com `lambda` e `abs` por elemento, levava cerca de 6 min sob o profiler. A versão com *list comprehension* e `min` nativo é mais rápida, mas o X12 ainda leva ~2 min, porque o custo O(n²) é inerente a escolher sobre uma lista. Mantivemos a interface fixa (índice na fila em ordem de chegada) em vez de trocar por uma estrutura ordenada.

### N3 — Sistema de arquivos

**Onde:** `simes/arquivos/sistema.py`: `resolver`/`_resolver`, `_localizar_pai`, `_criar_entrada`, `mkdir`, `create`, `open`, `read`, `write`, `seek`, `close` e `unlink`.

**Decisões:**
- **Resolução de caminhos:** `_resolver(inicio, partes, seguir_ultimo, links_seguidos)` lê cada diretório (1 leitura) *antes* de procurar o próximo componente nele. Assim, `/a/b/c` custa as 3 leituras de `/`, `/a` e `/a/b`, e `/` não custa nada. As operações que alteram uma entrada usam `_localizar_pai`, que resolve o caminho até o avô e lê o pai: o mesmo número de leituras de `resolver`.
- **Tabelas:** `tabela_global[fcb_id]` tem um contador de aberturas de todos os processos. `tabelas_processo[proc][handle]` guarda posição e modo. O `close` decrementa o contador, remove a entrada global quando ele zera e, se o arquivo já foi removido (`links == 0`), só então libera os blocos.
- **Leitura e escrita:** `read` limita o intervalo ao tamanho do arquivo e devolve os bytes lidos. `write` calcula quantos blocos faltam, chama `alocador.estender` e executa os extras (realocação, ponteiros, índices) antes de gravar os blocos do intervalo. No modo `'a'`, a posição vai para o fim antes de cada escrita. O `write` marca o FCB como sujo, e o `close` grava 1 bloco do diretório pai.
- **unlink com arquivo aberto:** remove a entrada e decrementa `links`. Os blocos só são liberados se ninguém tiver o arquivo aberto; caso contrário, isso fica para o último `close` (semântica do Unix, Silberschatz 11.1.2).
- **Erros:** modo inválido gera `EINVAL`; reusar um handle já aberto no mesmo processo gera `EBADF`. Se a alocação falha (`ENOSPC`), o FCB recém-criado é descartado, para não ficar órfão.

**Dificuldades:** Respeitar à risca as regras de custo, por exemplo: em `mkdir`, gravar primeiro o bloco do novo diretório e depois o do pai, e não ler o diretório novo. Os testes de custo `(leituras, escritas)` serviram de especificação.

### M1 — Cache de blocos

**Onde:** `simes/es/cache.py`: `_CacheOrdenada`, `CacheLRU` e `CacheFIFO`.

**Decisões:** Uma única base com `OrderedDict` bloco → sujo, em que o primeiro item é sempre a vítima. LRU e FIFO diferem só no atributo `atualiza_no_acerto`: a LRU faz `move_to_end` em todo acerto (de leitura ou de escrita), e a FIFO não muda a ordem. Em write-back, reescrever um bloco já sujo não o duplica, e `sincronizar` limpa todos. Em write-through, nenhum bloco fica sujo.

**Dificuldades:** Decidir o que fazer quando `inserir` recebe um bloco já presente, o que acontece quando duas requisições pendentes faltam no mesmo bloco. Tratamos como acerto: só atualiza a recência, sem expulsar ninguém.

### M2 — Escalonamento avançado

**Onde:** `simes/es/escalonadores.py`: `CSCAN`, `LOOK`, `CLOOK` e `Deadline`.

**Decisões:** C-SCAN: `passar_por = [max_cilindro, 0]` e depois a menor requisição; o retorno conta como deslocamento. LOOK: igual ao SCAN, mas inverte na última requisição, sem `passar_por`. C-LOOK: sem nada acima, salta para o menor cilindro (empate: a mais antiga). DEADLINE: se há requisições vencidas (`agora >= prazo`), atende a de prazo mais antigo e mantém a direção; senão, delega ao SSTF.

**Dificuldades:** Nenhuma relevante; os três totais do livro (382, 299 e 322) conferiram de primeira.

### M3 — DMA e buffering

**Onde:** `simes/es/cpu.py` (modo `dma`) e `simes/es/buffering.py` (`simular_buffering`).

**Decisões:** O buffering é uma recorrência direta das regras: o produtor começa o bloco *k* em `max(fim_producao[k−1], fim_gravacao[k−n])`, a diferença entre os dois soma em `bloqueio_produtor_ms`, e o dispositivo começa em `max(fim_producao[k], fim_gravacao[k−1])`. Não é preciso simular eventos.

**Dificuldades:** Nenhuma.

### M4 — Links e compartilhamento

**Onde:** `simes/arquivos/sistema.py`: `_resolver` (seguimento de links), `link`, `symlink` e `percorrer`.

**Decisões:** Ao encontrar um link simbólico no meio do caminho (ou no fim, se `seguir_simbolico`), `_resolver` resolve o alvo recursivamente, a partir da raiz (alvo absoluto) ou do diretório do link (alvo relativo), propagando a contagem de links seguidos. Se ela passa de `MAX_LINKS_SIMBOLICOS` (8), lança `ELOOP`. Se o alvo não existe, marca `_link_pendente`, e o `open` conta isso em `links_pendentes`. O `link` não segue um link simbólico no último componente, como o `link(2)` do Linux, e recusa diretórios com `EPERM`. O `percorrer` é uma busca em profundidade iterativa com o conjunto `visitados`: chegar a um diretório já visitado conta `ciclos_detectados` e não desce. Seguir um link simbólico durante a travessia custa a resolução do alvo (leituras de diretório), como num `stat` real. Links em laço encontrados na travessia são contados em `links_em_laco`.

**Dificuldades:** Distinguir um `ENOENT` causado por link pendente de um `ENOENT` comum (caminho inexistente). Resolvemos com o marcador ligado só quando a falha ocorre *dentro* da resolução de um alvo.

### M5 — Proteção e travas

**Onde:** `simes/arquivos/sistema.py`: `_verificar_permissao`, `_verificar_trava`, `usuario`, `chmod`, `setacl`, `lock` e `unlock`.

**Decisões:** As permissões exigidas por modo ficam numa tabela (`r`→r, `w`/`a`→w, `rw`→rw). O custo de verificação segue o enunciado: 1 para root, a posição da *primeira* entrada do usuário na ACL, ou `len(acl)+1` quando se recorre aos bits rwx. `chmod` aceita o número convertido pelo simulador (`600` → `int("600", 8)`). `chmod` e `setacl` compartilham `_alterar_protecao` (só o dono ou root, senão `EPERM`). O `close` libera a trava se quem fecha é o dono dela.

**Dificuldades:** O simulador converte `"600"` em inteiro decimal; foi preciso reinterpretar o valor como octal.

### M6 — Alocação em disco

**Onde:** `simes/arquivos/alocacao.py`: `_AlocadorNaoContiguo`, `AlocadorEncadeado`, `AlocadorFAT` e `AlocadorIndexado`.

**Decisões:** Encadeada e FAT compartilham `_reservar` (primeiros blocos livres, com `ENOSPC` e `falhas += 1`). A encadeada guarda `extra['cursor']` e devolve como extras as leituras dos blocos intermediários da corrente; ao estender, grava o novo ponteiro no antigo último bloco. A indexada calcula o número de índices por `blocos_de_indice(n)` (1 até 1024 blocos de dados, depois `1 + ceil(n/1024)`). Ao estender, acrescenta os índices que faltarem (inclusive na passagem de 1 para 2 níveis) e devolve 1 escrita por índice do arquivo. `bloco_fisico` lê 1 ou 2 índices.

**Dificuldades:** A passagem de 1 para 2 níveis no `estender`: o índice antigo passa a ser o de 1º nível e são acrescentados os de 2º nível. Há teste próprio para isso.

### Integração ponta a ponta

**Onde:** `simes/integracao.py` (`historico_para_requisicoes`).

**Decisões:** Ordenação estável por instante: acessos da mesma operação chegam no mesmo instante e mantêm a ordem em que a camada de blocos os fez. Os ids são renumerados de 0 a n−1.

**Dificuldades:** Nenhuma na conversão; o custo foi o tempo de simulação (ver N2).

### Bônus — DiscoFlash

**Onde:** `simes/disco.py` (`DiscoFlash`) e `experimentos_extras.py`.

**Decisões:** A FTL grava em log: um bloco apagável "ativo" recebe as páginas em ordem; regravar um bloco lógico invalida a página antiga. A capacidade física é a lógica × 1,07 (área de reserva), arredondada em blocos de 64 páginas. A coleta de lixo roda quando sobram menos de 2 blocos livres. É **gulosa**: a vítima é o bloco fechado com menos páginas válidas, cada página válida é lida e reprogramada, e o bloco é apagado. O tempo da coleta é cobrado da escrita que a disparou (coleta em primeiro plano, o pior caso). Também há `precondicionar(fracao)` para deixar o dispositivo "usado", usado só no experimento extra.

**Dificuldades:** Um erro sutil: as cópias da coleta podiam abrir um bloco ativo novo, e o código que disparou a coleta abria *outro* por cima, desperdiçando blocos até esgotar a reserva. A correção (`_abrir_novo_ativo` confere se a coleta já abriu um bloco) é verificada por um teste de consistência entre o mapa lógico→físico e os contadores de páginas válidas.

## 3. Experimentos

Observação sobre o modelo: um acesso aleatório de 1 bloco custa em média cerca de 1 + 1.667 × 0,003 + 4,17 + 0,04 ≈ **10,2 ms** (busca média de um terço do disco). Um bloco em sequência ao anterior custa só **0,04 ms**. Essa diferença de 250 vezes explica boa parte dos resultados abaixo.

### X1 — Escalonadores sob quatro padrões de carga

**Hipótese:** SSTF e SCAN reduzem o deslocamento do braço e a resposta média em relação ao FCFS nas cargas aleatória, concentrada e em rajadas. O SSTF tem a menor resposta média, mas a maior espera máxima (tendência à inanição). Na carga sequencial a diferença entre os três é pequena, porque as requisições já chegam ordenadas.

**Fundamentação:** Silberschatz 10.4.1–10.4.3: o FCFS é justo, mas ignora a posição do braço; o SSTF é guloso e pode causar inanição; o SCAN limita a espera a cerca de duas varreduras. A vazão não deve mudar enquanto o disco não estiver saturado, porque é dada pela taxa de chegada.

**Configuração:** 4 cargas padronizadas (2.000 requisições de 8 blocos) × FCFS, SSTF e SCAN; disco rígido; CPU por interrupção.

**Resultados:**

| Carga | Escal. | Deslocamento (cil.) | Resp. média (ms) | p95 (ms) | Espera máx. (ms) | Vazão (req/s) |
|---|---|---:|---:|---:|---:|---:|
| sequencial | FCFS | 7.184.093 | 10.241,1 | 19.565,5 | 20.598,1 | 61,5 |
| sequencial | SSTF | 2.336.365 | 17,4 | 32,8 | 37,4 | 167,5 |
| sequencial | SCAN | 2.469.069 | 23,2 | 43,7 | 48,6 | 167,4 |
| aleatória | FCFS | 3.316.472 | 50,1 | 145,1 | 193,5 | 82,5 |
| aleatória | SSTF | 2.631.714 | 21,0 | 48,6 | 116,7 | 82,5 |
| aleatória | SCAN | 3.372.680 | 31,5 | 65,1 | 116,7 | 82,4 |
| concentrada | FCFS | 1.864.164 | 19,8 | 48,6 | 96,0 | 83,8 |
| concentrada | SSTF | 1.613.030 | 15,0 | 37,2 | 130,5 | 83,8 |
| concentrada | SCAN | 3.338.708 | 33,2 | 68,3 | 118,8 | 83,7 |
| rajadas | FCFS | 3.341.490 | 267,0 | 505,3 | 597,3 | 83,5 |
| rajadas | SSTF | 209.811 | 147,5 | 277,7 | 297,0 | 84,4 |
| rajadas | SCAN | 199.955 | 148,6 | 278,7 | 285,4 | 84,4 |

**Evidências:** resultados/es.csv (linhas com experimento=X1); graficos/X1_resposta_media_ms.png; graficos/X1_deslocamento_total.png; graficos/X1_espera_max_ms.png

**Análise:**
- **Sequencial (a maior surpresa).** A carga tem *dois* fluxos, P1 a partir do bloco 20.000 (cilindro 312) e P2 a partir do 250.000 (cilindro 3.906), com chegadas intercaladas a cada ~6 ms. O FCFS alterna entre eles: cada requisição paga uma busca de ~3.600 cilindros mais a latência rotacional, cerca de 1 + 3.594 × 0,003 + 4,17 + 0,32 ≈ 16,3 ms. Isso é mais do que o intervalo entre chegadas, então o disco satura (utilização > 1), a fila cresce sem limite e a resposta média chega a 10 s. A vazão cai para 61 req/s, o único caso em que ela muda. O SSTF fica no fluxo onde o braço está enquanto houver requisições dele na fila. Como o bloco pedido é o seguinte ao último transferido, o modelo não cobra rotação (0,32 ms por requisição). O ganho veio de *preservar a sequencialidade*, não de reduzir a distância por si só, e por isso a hipótese de "diferença pequena" foi refutada para essa carga.
- **Aleatória e concentrada.** O SSTF reduz a resposta média (−58% e −24%), como esperado. O SCAN, porém, desloca o braço *mais* que o FCFS: com fila curta (disco com utilização de ~85%), ele vai até a borda do disco mesmo sem requisições lá. Na concentrada, 80% das requisições estão nos primeiros 20% do disco, e cada ida até o cilindro 4.999 é desperdício. É exatamente o problema que o LOOK resolve (X7).
- **Inanição do SSTF.** Na concentrada, a espera máxima do SSTF (130,5 ms) é a maior dos três, acima até do FCFS (96 ms): requisições fora da região quente esperam enquanto chegam requisições próximas da cabeça.
- **Rajadas.** Com 50 requisições chegando juntas a cada 600 ms, há uma fila longa para ordenar: SSTF e SCAN reduzem o deslocamento 16 vezes e a resposta média quase à metade. Com fila longa, o SCAN fica praticamente igual ao SSTF e tem a menor espera máxima (285 ms).

**Veredito:** PARCIALMENTE CONFIRMADA

### X2 — Polling × interrupção conforme o dispositivo

**Hipótese:** No disco rígido, o polling consome uma fração grande da CPU, porque a CPU fica em espera ocupada durante os ~10 ms de cada acesso, enquanto a interrupção gasta pouco. No dispositivo rápido a relação se inverte: o polling custa menos que as interrupções.

**Fundamentação:** Silberschatz 13.2.1 e 13.2.2: o polling é eficiente só quando o dispositivo responde rápido, e a interrupção tem custo fixo de tratamento e troca de contexto por evento. No modelo, polling = duração do atendimento; interrupção = n × (0,015 + 0,002) ms.

**Configuração:** FCFS; cargas aleatória e sequencial; disco rígido × dispositivo rápido (sem busca nem rotação, 0,008 ms por bloco); modos polling e interrupção.

**Resultados:**

| Dispositivo | Carga | CPU polling (%) | CPU interrupção (%) |
|---|---|---:|---:|
| HDD | aleatória | 86,28 | 1,12 |
| HDD | sequencial | 100,00 | 0,84 |
| rápido | aleatória | 0,53 | 1,12 |
| rápido | sequencial | 1,07 | 2,28 |

**Evidências:** resultados/es.csv (linhas com experimento=X2); graficos/X2_cpu.png

**Análise:** No HDD, o polling consome 86% da CPU na carga aleatória e 100% na sequencial. Com FCFS, a carga sequencial satura o disco (ver X1): ele nunca fica ocioso, e a CPU também não. Com interrupção, cada requisição de 8 blocos custa 8 × 0,017 = 0,136 ms, cerca de 1% do tempo. No dispositivo rápido, uma requisição de 8 blocos leva 8 × 0,008 = 0,064 ms; esperar isso em polling sai *mais barato* que tratar 8 interrupções (0,136 ms), por isso a interrupção custa o dobro. Pelo modelo, o ponto de equilíbrio é um tempo de dispositivo por bloco igual ao custo de uma interrupção mais uma cópia (0,017 ms): abaixo disso, compensa fazer polling. Os tempos de resposta são idênticos entre os modos, porque o modelo não faz a CPU competir com o dispositivo.

**Veredito:** CONFIRMADA

### X3 — Acesso sequencial × direto

**Hipótese:** Com alocação contígua, a leitura sequencial (compilador) e a direta (banco de dados) fazem o mesmo número de acessos por leitura (1 bloco de 4 KiB), mas a direta custa bem mais tempo por operação, porque cada leitura vai para uma posição aleatória (busca + rotação) enquanto a sequencial aproveita o bloco seguinte.

**Fundamentação:** Silberschatz 11.2.1 e 11.2.2 (métodos de acesso) e 12.4.1: na alocação contígua, o bloco *i* é `início + i`, e o acesso direto não tem custo extra de *endereçamento*. O custo extra vem do *disco* (seção 10.1.1: tempo de posicionamento).

**Configuração:** cargas `fs_compilador` (3 builds de 40 fontes + cabeçalhos, leituras de 4 KiB do início ao fim) e `fs_banco_dados` (tabela de 64 MiB, 3.000 transações com `seek` aleatório, 80% leituras, mais log em modo acréscimo); alocação contígua.

**Resultados:**

| Carga | Operação | n | Acessos médios | Tempo médio (ms) |
|---|---|---:|---:|---:|
| compilador | read | 1.719 | 1,00 | 1,48 |
| compilador | write | 726 | 1,00 | 0,91 |
| compilador | open | 600 | 3,00 | 8,20 |
| banco_dados | read | 2.405 | 1,00 | 5,49 |
| banco_dados | write | 1.190 | 1,00 | 5,54 |
| banco_dados | seek | 3.000 | 0,00 | 0,00 |

Totais: compilador 1,38 acessos e 3,07 ms por operação; banco_dados 0,55 acessos e 3,01 ms por operação.

**Evidências:** resultados/arquivos.csv (experimento=X3); resultados/arquivos_por_operacao.csv (experimento=X3); graficos/X3_tempo_por_operacao.png

**Análise:** Cada `read` faz exatamente 1 acesso nos dois casos. A diferença está no tempo: 1,48 ms (sequencial) contra 5,49 ms (direto), 3,7 vezes mais. No compilador, a primeira leitura de cada arquivo depois do `open` paga busca e rotação, e as seguintes são o bloco vizinho (0,04 ms); a média de 1,48 ms vem dessa mistura, mais a interferência das aberturas de cabeçalhos entre um bloco e outro. No banco de dados, quase toda leitura paga rotação (4,17 ms) e uma busca curta dentro dos 16.384 blocos da tabela (~256 cilindros, ~1,8 ms). Os totais por operação enganam: o banco de dados parece ter "menos acessos por operação" (0,55) só porque metade das operações são `seek`, que não acessam o disco. Por isso a métrica certa é o tempo por leitura, não a média geral.

**Veredito:** CONFIRMADA

### X4 — Custo de resolver caminhos conforme a profundidade

**Hipótese:** O número de acessos do `open` cresce linearmente, com profundidade + 1 leituras de diretório (raiz e cada nível), e o tempo do `open` cresce na mesma proporção, cerca de uma busca mais uma rotação (~5 ms) por nível.

**Fundamentação:** Silberschatz 11.3 (estrutura de diretórios em árvore) e 12.3: sem cache de diretórios, cada componente do caminho exige ler o diretório que o contém. As regras do simulador definem 1 leitura por diretório.

**Configuração:** um arquivo no fim de uma cadeia de 1 a 10 diretórios, aberto, lido e fechado 20 vezes por P2.

**Resultados:**

| Profundidade | Acessos por open | Tempo por open (ms) |
|---:|---:|---:|
| 1 | 2 | 4,25 |
| 5 | 6 | 4,41 |
| 10 | 11 | 4,61 |

**Evidências:** resultados/arquivos_por_operacao.csv (experimento=X4, operacao=open); resultados/arquivos.csv (experimento=X4); graficos/X4_open_por_profundidade.png

**Análise:** Os acessos seguem exatamente *d* + 1, como esperado. O tempo, porém, cresce só 0,04 ms por nível, e não ~5 ms. Os diretórios foram criados em sequência com alocação contígua (first-fit), então ocupam blocos vizinhos (1, 2, 3, …). Ler um após o outro é transferência sequencial, sem busca nem rotação: só a primeira leitura (a raiz) paga a latência rotacional. A parte da hipótese sobre o tempo foi refutada por um efeito de *localidade espacial dos metadados* que não esperávamos. Num sistema de arquivos envelhecido, com diretórios espalhados, cada nível custaria de fato uma busca. É por isso que o ext4 tenta alocar o inode e o diretório perto do pai (grupos de blocos, alocador Orlov), e que todo sistema real mantém uma cache de nomes (dentry cache no Linux), que zera esse custo nos acessos repetidos.

**Veredito:** PARCIALMENTE CONFIRMADA

### X5 — Tabelas de arquivos abertos com vários processos

**Hipótese:** A tabela global fica limitada pelo número de arquivos *distintos* abertos ao mesmo tempo (no máximo 12 páginas). As tabelas por processo somam 1 entrada por abertura (8 trabalhadores × 3 = 24). O contador de aberturas de uma página chega a alguns poucos processos (≤ 8).

**Fundamentação:** Silberschatz 11.1.2: a tabela por processo guarda posição e modo de cada abertura, e a tabela do sistema guarda uma vez só as informações independentes de processo (FCB em memória) e o contador de aberturas.

**Configuração:** carga `fs_servidor_web`: 8 trabalhadores abrem 3 de 12 páginas cada um, leem em pedaços de 16 KiB e fecham, 40 rodadas.

**Resultados:** tabela global máxima = **12**; tabela por processo máxima = **3**; soma máxima das tabelas por processo = **24**; contador de aberturas máximo = **6**; 17.824 acessos (2,84 por operação).

**Evidências:** resultados/arquivos.csv (experimento=X5); graficos/X5_tabelas.png

**Análise:** Os números batem com a hipótese. A tabela global chegou a 12 (todas as páginas abertas por algum processo numa mesma rodada), enquanto havia 24 aberturas. O compartilhamento economiza metade das entradas globais e garante uma única cópia dos metadados, o que é essencial para a coerência (todos veem o mesmo tamanho). O contador chegou a 6: numa rodada, 6 dos 8 trabalhadores sortearam a mesma página. Ele é o que permite saber quando a entrada global pode ser liberada, e foi usado também no `unlink` com o arquivo aberto (blocos liberados só no último `close`). Cada processo mantém sua própria posição: dois processos lendo a mesma página não interferem um no outro, o que o teste `test_tabelas_compartilhadas` confere.

**Veredito:** CONFIRMADA

### X6 — Cache de blocos (M1)

**Hipótese:** A taxa de acertos cresce com a capacidade até o tamanho do conjunto de trabalho e depois estabiliza. A LRU acerta mais que a FIFO nas capacidades pequenas (localidade temporal da distribuição de Zipf). A write-back reduz as escritas físicas e a resposta média em relação à write-through, ao custo de perder os blocos sujos numa queda de energia.

**Fundamentação:** Silberschatz 13.4.3 (caching), 12.6.2 (buffer cache unificada, write-back e sync periódico) e 12.7 (recuperação e consistência); política LRU (seção 9.4.4, por analogia com a substituição de páginas).

**Configuração:** carga `es_reuso` (3.000 requisições de 1 bloco, Zipf sobre 4.000 blocos, 65% leituras, intervalo médio de 8 ms); FCFS; LRU e FIFO; capacidades de 64 a 2.048 blocos; write-through e write-back; queda de energia simulada no meio da carga (t = 11.733 ms).

**Resultados:**

| Cache | Cap. | Escrita | Acerto (%) | Resp. média (ms) | p95 (ms) | Escritas físicas | Perdidos na queda |
|---|---:|---|---:|---:|---:|---:|---:|
| nenhuma | — | — | — | 3.499,96 | 6.427,45 | 1.045 | 0 |
| LRU | 64 | WT | 39,6 | 74,66 | 211,54 | 1.045 | 0 |
| LRU | 64 | WB | 39,6 | 23,36 | 110,37 | 715 | 28 |
| FIFO | 64 | WT | 31,3 | 436,11 | 851,18 | 1.045 | 0 |
| FIFO | 64 | WB | 33,1 | 46,27 | 223,51 | 810 | 22 |
| LRU | 256 | WT | 57,7 | 22,05 | 75,93 | 1.045 | 0 |
| LRU | 256 | WB | 57,7 | 47,63 | 393,39 | 543 | 55 |
| LRU | 1024 | WT | 67,1 | 16,89 | 62,59 | 1.045 | 0 |
| LRU | 1024 | WB | 67,0 | 38,93 | 117,20 | 541 | 55 |
| FIFO | 1024 | WT | 67,1 | 16,89 | 62,59 | 1.045 | 0 |

**Evidências:** resultados/es.csv (linhas com experimento=X6); graficos/X6_acerto.png; graficos/X6_escrita.png

**Análise:**
- **Sem cache, o disco está saturado.** Cada acesso aleatório leva ~10,2 ms e as requisições chegam a cada 8 ms (utilização ≈ 1,28), então a resposta média passa de 3,5 s. Qualquer cache que reduza a carga do disco abaixo de 1 muda o regime: com FIFO de 64 blocos e 31% de acertos, a utilização ainda fica perto de 0,9 e a resposta é de 436 ms. Com a LRU de 64 (40% de acertos), cai para 75 ms. Perto da saturação, poucos pontos de acerto valem muito tempo de resposta (comportamento de fila M/M/1).
- **LRU × FIFO:** a LRU ganha em todas as capacidades pequenas (+8 pontos com 64 blocos, +4,5 com 256) porque mantém os blocos "quentes" da Zipf. A partir de 1.024 blocos as duas ficam *idênticas*: a carga toca só 992 blocos distintos, tudo cabe na cache e nunca há expulsão. Os 67% de acerto são o teto dado pelas faltas compulsórias (o primeiro acesso a cada bloco).
- **Write-back (resultado contrário à hipótese):** ela reduz as escritas físicas em 48% (541 contra 1.045), porque reescritas do mesmo bloco se acumulam. Com 64 e 128 blocos ela também melhora a resposta. Com 256 blocos ou mais, porém, a resposta média e o p95 *pioram* (LRU 256: 47,6 ms contra 22,1 ms; p95 de 393 ms). A sincronização a cada 5 s despeja de uma vez os ~55 blocos sujos na fila FCFS; cada um custa ~10 ms, e as leituras que chegam logo depois esperam até ~550 ms. A write-through espalha o mesmo trabalho ao longo do tempo. Com cache pequena, as expulsões de blocos sujos acontecem aos poucos e não há rajada.
- **Durabilidade:** a write-through nunca perde dados; a write-back perde até 55 blocos (todos os sujos no instante da queda), quase metade dos 469 blocos distintos escritos. Esse é o preço de durabilidade da gravação adiada.

**Veredito:** PARCIALMENTE CONFIRMADA

### X7 — Escalonadores avançados e inanição (M2)

**Hipótese:** Com carga crescente, a espera máxima do SSTF cresce mais que a dos outros (inanição). O DEADLINE mantém a resposta média do SSTF e limita a espera máxima perto do prazo de leitura (200 ms). C-SCAN e C-LOOK dão a espera mais uniforme (menor p95 e menor espera máxima), com média um pouco maior. LOOK e C-LOOK deslocam menos o braço do que SCAN e C-SCAN.

**Fundamentação:** Silberschatz 10.4.4–10.4.6 (C-SCAN, LOOK e a escolha do algoritmo) e a documentação do mq-deadline do Linux (prazos de 500 ms para leitura e 5 s para escrita, reduzidos aqui para 200 ms e 2 s).

**Configuração:** carga concentrada gerada com 2.000 requisições, semente 7, intervalo médio de 14, 11, 9 e 8 ms; os 7 escalonadores; HDD.

**Resultados:**

| Intervalo (ms) | Métrica | FCFS | SSTF | SCAN | C-SCAN | LOOK | C-LOOK | DEADLINE |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 14 | resp. média (ms) | 15,8 | 13,4 | 29,5 | 40,1 | 13,8 | 14,7 | 13,4 |
| 14 | espera máx. (ms) | 71,3 | 86,7 | 114,4 | 116,9 | 72,8 | 102,8 | 86,7 |
| 9 | resp. média (ms) | 61,2 | 21,9 | 41,8 | 55,7 | 24,6 | 28,4 | 22,0 |
| 9 | espera máx. (ms) | 244,5 | 265,5 | 210,0 | 172,1 | 161,9 | 139,4 | 204,3 |
| 8 | resp. média (ms) | 281,6 | 27,8 | 49,0 | 65,8 | 31,4 | 38,9 | 28,4 |
| 8 | p95 (ms) | 538,0 | 79,6 | 116,6 | 142,1 | 87,5 | 100,2 | 84,8 |
| 8 | espera máx. (ms) | 616,2 | 327,1 | 226,7 | 223,1 | 170,3 | 163,8 | 244,8 |
| 8 | deslocamento (mil cil.) | 1.812 | 1.151 | 1.530 | 1.590 | 1.165 | 1.258 | 1.149 |

**Evidências:** resultados/es.csv (linhas com experimento=X7); graficos/X7_espera_max_ms.png; graficos/X7_resposta_p95_ms.png

**Análise:**
- **Inanição no SSTF:** a espera máxima do SSTF vai de 87 ms para 327 ms com a carga e passa a ser a pior de todas, exceto o FCFS saturado. Já a média do SSTF continua a menor. É o comportamento clássico: o SSTF otimiza o caso médio à custa da cauda.
- **DEADLINE:** com intervalos de 14 e 11 ms, nenhuma requisição chega a vencer o prazo, e o DEADLINE é *idêntico* ao SSTF (mesmos números). Sob carga alta, ele corta a espera máxima do SSTF (265 → 204 ms com 9 ms; 327 → 245 ms com 8 ms) com custo mínimo na média (+0,6 ms). A espera máxima ainda passa de 200 ms, por dois motivos: o prazo só é verificado quando o disco escolhe a próxima requisição (sem preempção), e as escritas têm prazo de 2 s. Ele limita a inanição, mas não dá garantia rígida, assim como o mq-deadline.
- **LOOK e C-LOOK foram os mais justos**, ao contrário do que esperávamos para o C-SCAN. Com 8 ms, o C-LOOK teve a menor espera máxima (164 ms) e o LOOK, a melhor combinação de média (31 ms) e cauda (170 ms). SCAN e C-SCAN pagam a ida até as bordas, inútil numa carga em que 80% das requisições estão no primeiro quinto do disco: deslocam 30–40% mais e têm a pior média entre os não-FCFS.
- **Vazão:** com 8 ms o FCFS satura (121 req/s contra ~125 dos demais), e a resposta dele explode para 282 ms. Os outros mantêm o disco abaixo da saturação porque reduzem o tempo médio de serviço.

**Veredito:** PARCIALMENTE CONFIRMADA

### X8 — DMA e buffering (M3)

**Hipótese:** O custo de CPU da interrupção cresce linearmente com o tamanho da requisição, e o do DMA é constante (uma programação e uma interrupção por requisição). O polling é sempre o mais caro no HDD. No buffering, o buffer duplo elimina o bloqueio do produtor quando produção e consumo têm ritmos parecidos, e o terceiro buffer não traz ganho com ritmos constantes. Quando o dispositivo é mais lento que o produtor, nenhum número de buffers evita o bloqueio.

**Fundamentação:** Silberschatz 13.2.3 (DMA: a CPU só inicia a transferência e trata uma interrupção no fim) e 13.4.2 (buffering: lidar com diferenças de velocidade entre produtor e consumidor; buffer duplo).

**Configuração:** (a) carga aleatória de 1.000 requisições, intervalo de 40 ms, com 1, 8, 64 e 256 blocos por requisição; polling, interrupção e DMA; FCFS. (b) Produtor de 2 ms por bloco; dispositivo de 0,5, 2 e 8 ms por bloco; 1, 2 e 3 buffers; 500 blocos.

**Resultados:**

| Blocos/req | CPU polling (%) | CPU interrupção (%) | CPU DMA (%) |
|---:|---:|---:|---:|
| 1 | 25,17 | 0,04 | 0,04 |
| 8 | 25,87 | 0,34 | 0,04 |
| 64 | 31,44 | 2,71 | 0,04 |
| 256 | 50,52 | 10,82 | 0,04 |

| Consumo (ms) | Buffers | Tempo total (ms) | Bloqueio do produtor (ms) | Utilização do disp. (%) |
|---:|---:|---:|---:|---:|
| 0,5 | 1 | 1.250,0 | 249,5 | 20,0 |
| 0,5 | 2 / 3 | 1.000,5 | 0,0 | 25,0 |
| 2,0 | 1 | 2.000,0 | 998,0 | 50,0 |
| 2,0 | 2 / 3 | 1.002,0 | 0,0 | 99,8 |
| 8,0 | 1 | 5.000,0 | 3.992,0 | 80,0 |
| 8,0 | 2 | 4.002,0 | 2.988,0 | 99,95 |
| 8,0 | 3 | 4.002,0 | 2.980,0 | 99,95 |

**Evidências:** resultados/es.csv (linhas com experimento=X8); resultados/buffering.csv; graficos/X8_cpu.png; graficos/X8_buffering.png

**Análise:** Com 1 bloco, interrupção e DMA empatam (0,017 contra 0,018 ms por requisição). A partir daí, a interrupção cresce linearmente (256 × 0,017 = 4,35 ms por requisição, 10,8% da CPU) e o DMA fica constante em 0,04%. Com 256 blocos, o DMA é 270 vezes mais barato. O polling cresce com o tempo de transferência e chega a 50% da CPU com 256 blocos. No buffering, um buffer simples serializa produção e gravação (tempo = 500 × (2 + c)). Com dois buffers, o produtor gera o bloco seguinte enquanto o anterior é gravado, e o tempo cai para `max(produção, consumo) × 500 + min(...)`, ou seja, 1.002 ms em vez de 2.000 com ritmos iguais. Isso dobra a utilização do dispositivo (50% → 99,8%). O terceiro buffer não ajuda com ritmos constantes: ele só absorve *variações* de ritmo (rajadas). Com o dispositivo 4 vezes mais lento (8 ms), o gargalo é o próprio dispositivo: o tempo total é limitado por 500 × 8 = 4.000 ms, e o produtor fica bloqueado ~3 s qualquer que seja o número de buffers. Buffers não aumentam a vazão do recurso mais lento.

**Veredito:** CONFIRMADA

### X9 — Links e compartilhamento (M4)

**Hipótese:** O hard link continua funcionando depois de removido o nome original, porque o FCB só é liberado quando `links` chega a 0. O link simbólico para um arquivo removido fica pendente (`ENOENT`). O laço l1 ↔ l2 gera `ELOOP`. O hard link para diretório é negado (`EPERM`). A travessia detecta o ciclo criado por `/dados/sub/volta → /atalhos` e termina.

**Fundamentação:** Silberschatz 11.3.6 e 11.3.7 (diretórios em grafo acíclico e geral): contagem de referências para hard links, ponteiros pendentes nos links simbólicos, e proibição de hard links para diretórios para evitar ciclos que a contagem de referências não resolve.

**Configuração:** carga `fs_links` (hard link, links simbólicos para arquivo e diretório, um ciclo, um laço, leituras pelos atalhos, travessias de `/` e de `/atalhos`, remoção dos originais).

**Resultados:**

| Indicador | Valor |
|---|---:|
| operações / erros | 56 / 3 |
| erros: ENOENT / ELOOP / EPERM | 1 / 1 / 1 |
| ciclos detectados (3 travessias) | 5 |
| links pendentes (open + travessias) | 3 |
| links em laço encontrados na travessia | 6 |
| diretórios / arquivos visitados nas travessias | 11 / 9 |
| acessos totais (por operação) | 258 (4,61) |

**Evidências:** resultados/arquivos.csv (experimento=X9); resultados/arquivos_por_operacao.csv (experimento=X9)

**Análise:** Os três erros são exatamente os previstos: `open` de `b_simbolico` depois de `unlink /dados/b.txt` (ENOENT, link pendente), `open /atalhos/l1` (ELOOP depois de 8 saltos) e `link /dados /atalhos/dir_rigido` (EPERM). A leitura de `a_rigido` depois do `unlink` de `a.txt` funcionou: o FCB tinha `links = 2`. O caminho `/atalhos/dados_simbolico/sub/volta/a_rigido` atravessa *dois* links simbólicos no meio do caminho e resolve corretamente. Na travessia de `/`, o conjunto de diretórios visitados interrompe duas vezes a descida: em `volta → /atalhos` (ciclo real) e em `/dados`, que é alcançado tanto por `dados_simbolico` quanto pela raiz. Sem essa marcação, o `percorrer` não terminaria. O custo médio por operação é alto (4,6 acessos, 16,6 ms) porque seguir um link simbólico obriga a resolver o caminho do alvo desde a raiz. O link simbólico é flexível (atravessa sistemas de arquivos, pode apontar para diretórios), mas custa indireção e pode ficar pendente. O hard link é barato, mas fica restrito ao mesmo volume e a arquivos.

**Veredito:** CONFIRMADA

### X10 — Proteção e travas (M5)

**Hipótese:** As negações (`EACCES`) acontecem nas 41 aberturas sem permissão (1 inicial de bob, 20 de carol, 20 de bob em escrita) e o `chmod` de quem não é dono dá `EPERM`. O custo de verificação cresce com a posição do usuário na ACL. Com trava consultiva, as operações conflitantes passam, mas são contadas como violações; com trava mandatória, viram `EBUSY` e não acessam o disco.

**Fundamentação:** Silberschatz 11.6.2 (controle de acesso: bits de dono/grupo/outros × listas de controle de acesso) e 11.1.2 (travas de arquivo, *advisory* × *mandatory*).

**Configuração:** carga `fs_protecao` (alice dona, bob com ACL `r--` na 11ª posição, carol sem permissão; 20 rodadas de conflito de trava entre P1 e P2), com bloqueio consultivo e mandatório.

**Resultados:**

| Indicador | Consultivo | Mandatório |
|---|---:|---:|
| EACCES (negados) | 41 | 41 |
| EPERM | 1 | 1 |
| EBUSY | 20 | 60 |
| travas concedidas / negadas | 40 / 20 | 40 / 20 |
| violações consultivas | 40 | — |
| bloqueados (mandatório) | — | 40 |
| verificações de permissão | 961 | 961 |
| acessos ao disco | 613 | 573 |

**Evidências:** resultados/arquivos.csv (experimento=X10); resultados/arquivos_por_operacao.csv (experimento=X10)

**Análise:** As 41 negações e o EPERM batem com a hipótese. As 961 verificações mostram o custo da ACL: cada abertura de bob custa 11 comparações, porque a entrada dele é a 11ª, depois de 10 usuários fictícios. Cada abertura de alice (dona, sem entrada na ACL) custa 12, porque a lista inteira é percorrida antes de recorrer aos bits rwx. Uma ACL longa torna toda verificação mais cara, inclusive para quem não está nela. Nas travas, `lock` de quem não detém a trava dá EBUSY nos dois modos (20 vezes): a trava é sempre respeitada *por quem pede a trava*. A diferença está em quem a ignora. No modo consultivo, as 40 operações conflitantes (P2 escrevendo e P1 lendo enquanto o outro detém a trava) são executadas, e uma delas é uma escrita concorrente que poderia corromper dados. No mandatório, as mesmas 40 viram EBUSY, e por isso há 40 acessos a menos ao disco (573 contra 613). A trava mandatória protege mesmo contra processos que não cooperam, ao custo de checar a trava em todo `read`/`write`.

**Veredito:** CONFIRMADA

### X11 — Métodos de alocação (M6)

**Hipótese:** A contígua é a mais rápida para ler, mas sofre fragmentação externa: falhas (`ENOSPC`) com espaço total sobrando e realocações caras ao crescer arquivos. A encadeada não fragmenta, mas é péssima no acesso direto (segue a corrente) e a mais lenta no total. A FAT tem os mesmos blocos da encadeada sem os acessos extras e fica próxima da contígua. A indexada paga 1–2 leituras de índice por bloco, entre a FAT e a encadeada.

**Fundamentação:** Silberschatz 12.4.1–12.4.3 (contígua, encadeada com FAT, indexada com esquema multinível) e 12.5.1 (bitmap de espaço livre).

**Configuração:** carga `fs_alocacao` numa partição de 20.000 blocos: criação e remoção até 85% de ocupação, crescimento de 60 arquivos em modo acréscimo, 100 leituras diretas e uma leitura sequencial completa dos 5 maiores arquivos.

**Resultados:**

| Alocador | Acessos | Acessos/op | Tempo total (ms) | ENOSPC | Realocações | Maior trecho livre | Trechos livres | Descontinuidades/arquivo |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| contígua | 18.151 | 7,35 | 85.273,7 | 113 | 59 | 154 | 50 | 0,00 |
| encadeada | 191.309 | 77,52 | 31.412,7 | 0 | 0 | 1.939 | 1 | 0,64 |
| FAT | 4.690 | 1,90 | 9.886,2 | 0 | 0 | 1.939 | 1 | 0,64 |
| indexada | 10.585 | 4,29 | 35.345,4 | 0 | 0 | 1.805 | 1 | 0,65 |

Por operação: `write` custa 15,1 acessos e 78,0 ms na contígua (realocações), 142,9 acessos e 24,5 ms na encadeada, 2,0 acessos e 4,7 ms na FAT, 5,0 acessos e 20,0 ms na indexada. `read` custa 74,9 acessos na encadeada contra 3,1 na FAT.

**Evidências:** resultados/arquivos.csv (experimento=X11); resultados/arquivos_por_operacao.csv (experimento=X11); graficos/X11_alocacao.png; graficos/X11_fragmentacao.png

**Análise:**
- **Contígua:** a fragmentação externa aparece com clareza. No fim sobram 2.052 blocos livres, divididos em 50 trechos, o maior com só 154 blocos; 113 escritas falharam com `ENOSPC`, mesmo havendo espaço total. As 59 realocações copiam o arquivo inteiro bloco a bloco, em leituras e escritas alternadas que perdem a rotação a cada bloco. Por isso o `write` médio custa 78 ms, e a contígua foi a *mais lenta* no total, o oposto do que esperávamos. O custo de leitura da contígua é ótimo; o problema é o crescimento de arquivos.
- **Encadeada:** 191 mil acessos, 18 vezes a indexada, por causa das leituras diretas (seguir a corrente desde o início) e do modo acréscimo (o cursor recomeça a cada `open`, e chegar ao fim do arquivo lê a corrente inteira). Mesmo assim o tempo total (31,4 s) foi *menor* que o da indexada. Num disco recém-formatado, os "primeiros blocos livres" de um arquivo costumam ser vizinhos, e o modelo do disco não cobra rotação para o bloco seguinte ao último transferido: seguir a corrente custa ~0,04 ms por bloco. Isso é um artefato do modelo: num disco envelhecido, cada ponteiro seria um acesso aleatório de ~10 ms.
- **FAT:** a melhor em tudo (4.690 acessos, 9,9 s). Ela não fragmenta (descontinuidades de 0,64 por arquivo, e as escritas nunca falham) e localiza qualquer bloco sem acesso extra, porque a tabela está em memória. O custo real que o modelo não mede é a memória: 4 bytes por bloco do volume, ou seja, 1,2 MB para os 320 mil blocos do disco simulado, e centenas de MB num disco de terabytes.
- **Indexada:** 1 leitura de índice por bloco de dados, alternando índice e dado. Como o índice não é vizinho do dado, cada par perde a rotação, e isso explica os 19 ms por `read` (contra 4,4 na FAT). Os arquivos de até 256 blocos usam 1 índice; nenhum chegou ao segundo nível nesta carga (testado à parte). Com uma cache de índices (X12), esse custo some quase todo.

**Veredito:** PARCIALMENTE CONFIRMADA

### X12 — Integração ponta a ponta

**Hipótese:** A alocação indexada gera mais requisições físicas que a contígua (índices) e, portanto, mais tempo total. SSTF e SCAN reduzem muito o deslocamento, porque o histórico tem muitas requisições quase simultâneas. A cache LRU de 1.024 blocos absorve as leituras de índices e diretórios e reduz muito a resposta, sobretudo na indexada.

**Fundamentação:** Silberschatz 12.6 (eficiência e desempenho: buffer cache, layout), 13.4.1 (escalonamento de E/S) e 13.4.3 (caching): a organização no disco define o padrão de requisições que as camadas de baixo recebem.

**Configuração:** cargas `fs_compilador` e `fs_banco_dados` executadas com contígua e indexada, guardando o histórico. O histórico vira requisições (`historico_para_requisicoes`), que passam por FCFS, SSTF e SCAN, sem cache e com LRU de 1.024 blocos em write-back.

**Resultados:**

| Carga + alocador | Requisições | Escal. | Cache | Resp. média (ms) | Tempo total (ms) | Deslocamento | Acerto (%) |
|---|---:|---|---|---:|---:|---:|---:|
| compilador + contígua | 5.400 | FCFS | — | 4.970,9 | 11.986,7 | 6.900 | — |
| compilador + contígua | 5.400 | SSTF | — | 5.170,1 | 9.988,0 | 10 | — |
| compilador + contígua | 5.400 | SSTF | LRU | 0,15 | 1.951,5 | 80 | 90,4 |
| compilador + indexada | 11.705 | FCFS | — | 14.961,0 | 32.044,6 | 10.772 | — |
| compilador + indexada | 11.705 | SSTF | — | 10.563,6 | 28.847,7 | 11 | — |
| compilador + indexada | 11.705 | SSTF | LRU | 0,15 | 1.951,5 | 90 | 95,7 |
| banco_dados + contígua | 3.610 | FCFS | — | 6.158,8 | 19.847,0 | 360.570 | — |
| banco_dados + contígua | 3.610 | SSTF | LRU | 1.411,9 | 12.991,0 | 2.697 | 4,8 |
| banco_dados + indexada | 10.314 | FCFS | — | 22.146,1 | 52.179,6 | 924.508 | — |
| banco_dados + indexada | 10.314 | SSTF | — | 17.353,2 | 42.512,1 | 257 | — |
| banco_dados + indexada | 10.314 | SCAN | LRU | 493,3 | 12.550,4 | 40.249 | 74,5 |

**Evidências:** resultados/es.csv (linhas com experimento=X12); graficos/X12_resposta.png

**Análise:**
- **A organização no disco multiplica o trabalho.** A indexada gera 2,2 vezes (compilador) e 2,9 vezes (banco de dados) mais requisições que a contígua: cada bloco de dados vem precedido da leitura do índice, e blocos consecutivos não se fundem numa única requisição, porque o índice fica no meio. Sem cache, o tempo total cresce na mesma proporção (12,0 → 32,0 s e 19,8 → 52,2 s).
- **Os históricos chegam em rajada.** As operações da carga de arquivos acontecem a cada 0,5 ms, mas cada uma gera requisições de vários ms. O disco fica saturado, e as respostas médias sem cache são de segundos. Nesse cenário o SSTF quase zera o deslocamento (10 cilindros contra 6.900) e reduz o tempo total em 17–22%. No compilador com contígua, porém, a resposta *média* do SSTF ficou pior que a do FCFS (5.170 contra 4.971 ms): ele termina antes, mas algumas requisições antigas esperam muito (inanição).
- **A cache muda tudo quando há reuso.** No compilador (3 builds dos mesmos fontes), 90–96% de acerto: a resposta média cai de segundos para 0,15 ms, e o tempo total passa a ser o instante da última chegada. A indexada tem acerto *maior* (95,7%) porque os blocos de índice são os mais reutilizados, e a cache elimina justamente a desvantagem dela. No banco de dados, a tabela de 64 MiB (16.384 blocos) não cabe na cache e o acerto da contígua é de só 4,8%. Na indexada sobe para 74,5%, porque quase todos os acertos são dos 17 blocos de índice, e a resposta cai 45 vezes (22 s → 0,49 s).
- **Efeito colateral do SCAN com cache:** com a fila esvaziada pela cache, o SCAN volta a fazer varreduras inúteis até as bordas (deslocamento de 40–50 mil cilindros contra menos de 100 do SSTF), o mesmo fenômeno do X1.
- **Atenção:** no compilador com write-back, o número de escritas físicas é 0. A carga termina em 1,95 s, antes da primeira sincronização (5 s), e todos os dados escritos ficaram só na memória. Uma queda de energia nesse momento perderia todos os `.o` gerados.

**Veredito:** CONFIRMADA

### X13 — Armazenamento flash (bônus)

**Hipótese:** O flash responde uma a duas ordens de grandeza mais rápido que o disco rígido, porque não tem busca nem rotação. As regravações provocam coleta de lixo, com amplificação de escrita maior que 1 e apagamentos.

**Fundamentação:** Silberschatz 10.1.2 (discos de estado sólido) e literatura de FTL (Agrawal et al., 2008): só é possível gravar em página apagada, o apagamento é por bloco, e a coleta de lixo copia as páginas válidas antes de apagar.

**Configuração:** oficial: cargas `es_reuso` e `es_aleatoria`, FCFS, HDD × flash. **Extra (declarado, fora dos resultados oficiais):** `python3 experimentos_extras.py`, com o DiscoFlash (mesmos parâmetros FLASH_*) reduzido para 32.768 páginas lógicas, pré-condicionado a 50–95% de ocupação e submetido a 50.000 escritas de 4 KiB, uniformes ou "quentes" (80% das escritas em 20% dos dados).

**Resultados:**

| Carga | Disp. | Resp. média (ms) | p95 (ms) | Amplificação | Apagamentos |
|---|---|---:|---:|---:|---:|
| reuso | HDD | 3.499,96 | 6.427,45 | — | — |
| reuso | flash | 0,09 | 0,20 | 1,00 | 0 |
| aleatória | HDD | 50,10 | 145,09 | — | — |
| aleatória | flash | 0,64 | 1,60 | 1,00 | 0 |

Experimento extra (flash usado):

| Ocupação | Amplificação (uniforme) | Amplificação (quente) | Latência máx. de escrita, uniforme (ms) |
|---:|---:|---:|---:|
| 50% | 1,19 | 1,35 | 9,5 |
| 70% | 1,63 | 1,81 | 14,9 |
| 80% | 2,14 | 2,39 | 28,3 |
| 90% | 3,26 | 3,49 | 46,7 |
| 95% | 4,58 | 4,97 | 65,1 |

**Evidências:** resultados/es.csv (linhas com experimento=X13); resultados/extras/flash_gc.csv; graficos/extra_flash_amplificacao.png

**Análise:**
- **Desempenho:** o flash é 78 vezes mais rápido na carga aleatória (0,64 contra 50 ms). Na carga de reuso, o HDD está saturado (ver X6) e a diferença chega a quatro ordens de grandeza: o flash atende uma escrita em 0,2 ms e uma leitura em 0,025 ms, e nunca satura.
- **Amplificação oficial = 1,0:** as cargas oficiais gravam no máximo 4.792 páginas num dispositivo de 342 mil, e a coleta de lixo nunca roda. Isso refuta a hipótese *nessas condições*: um SSD novo, ou com bastante espaço livre (e TRIM), não amplifica.
- **Experimento extra:** com o dispositivo "usado", a amplificação cresce rápido com a ocupação, de 1,19 (50%) para 4,58 (95%). Com pouco espaço livre, a vítima gulosa ainda tem muitas páginas válidas para copiar. Os apagamentos crescem junto (896 → 3.575), o que gasta a vida útil, e a latência máxima de escrita chega a 65 ms (uma escrita que dispara a coleta espera a cópia de dezenas de páginas e um apagamento), o mesmo efeito de cauda das pausas de GC de SSDs reais.
- **Surpresa:** escritas com localidade ("quentes") amplificaram *mais* do que as uniformes. A política gulosa sem separação quente/frio mistura dados quentes e frios no mesmo bloco, e os frios acabam copiados repetidamente. É por isso que FTLs reais e o F2FS separam fluxos quentes e frios e usam a política custo-benefício em vez da gulosa.

**Veredito:** PARCIALMENTE CONFIRMADA

## 4. Relação com sistemas reais

**Escalonamento de disco (X1, X7, X12).** O Linux usa a camada multiqueue (blk-mq) com os escalonadores `mq-deadline`, `bfq`, `kyber` e `none`. O `mq-deadline` é o DEADLINE do X7 com dois refinamentos: ordena por setor dentro de lotes (como o C-LOOK, para preservar a sequencialidade, efeito que o X1 mostrou ser decisivo) e dá prazos de 500 ms para leituras e 5 s para escritas, preferindo leituras porque processos esperam por elas, enquanto escritas costumam ser assíncronas. O X6 mostrou o que acontece sem essa preferência: rajadas de escrita da sincronização atrasando leituras. O BFQ distribui "orçamentos" de setores entre processos e é o indicado para HDDs em desktops interativos. Para SSDs NVMe, o padrão é `none`: sem busca nem rotação (X13), reordenar não compensa. O Windows não expõe um escalonador configurável; usa prioridades de E/S (`IoPriority`) e deixa a reordenação para o NCQ do próprio disco. O macOS, hoje quase só em SSD/APFS, também delega ao dispositivo. Em todos os casos, o NCQ/TCQ do disco faz um escalonamento parecido com o SSTF dentro do hardware.

**Polling, interrupção e DMA (X2, X8).** Todo controlador de disco moderno usa DMA com listas de *scatter-gather*. Para dispositivos muito rápidos, o Linux voltou a usar polling: `io_poll` e o *hybrid polling* do NVMe, e o `io_uring` com `IORING_SETUP_IOPOLL`. Isso confirma o X2: quando a E/S termina em microssegundos, tratar uma interrupção custa mais que esperar. As placas de rede usam NAPI (interrupção para acordar, polling sob carga), a mesma ideia. Também é comum a *coalescência de interrupções*, que faz a interrupção virar "uma por lote", aproximando-a do custo do DMA no X8.

**Cache e gravação periódica (X6, X12).** O cache de páginas do Linux é write-back. A thread de *writeback* acorda a cada 5 s (`vm.dirty_writeback_centisecs = 500`, o mesmo valor de `INTERVALO_SYNC_MS`) e grava páginas sujas há mais de 30 s (`dirty_expire_centisecs`). Há ainda limites (`dirty_ratio`, `dirty_background_ratio`) que escalonam a gravação aos poucos, justamente para evitar as rajadas do X6. O Windows tem o *lazy writer* do Cache Manager, que grava 1/8 das páginas sujas a cada segundo. O macOS usa a Unified Buffer Cache. A perda medida no X6 é a razão de existirem `fsync()`, `FlushFileBuffers()` e `F_FULLFSYNC` (no macOS, o `fsync` sozinho não esvazia a cache do disco), e do *journaling* do ext4, do NTFS e do APFS, que garante a consistência dos metadados após a queda.

**Resolução de caminhos e tabelas (X4, X5).** O Linux mantém a *dentry cache* e a *inode cache*: depois do primeiro `open`, resolver `/a/b/c` não lê o disco, o que anula o crescimento linear do X4. As tabelas do X5 correspondem à tabela de descritores do processo (`files_struct`), à *open file description* (`struct file`, com a posição) e ao inode em memória. Há uma diferença: no Linux, a posição fica na `struct file`, e dois processos podem compartilhá-la depois de um `fork`. No Windows, o equivalente é a tabela de *handles* do processo apontando para *file objects* do kernel.

**Links (X9).** O Linux limita a 40 os links simbólicos seguidos numa resolução e devolve `ELOOP`. O `find -L` detecta ciclos com um conjunto de (dispositivo, inode) visitados, como o nosso `percorrer`, e avisa "File system loop detected". Hard links para diretórios são proibidos no ext4 e no NTFS. O macOS foi uma exceção histórica no HFS+ (usado pelo Time Machine), não suportada no APFS. O NTFS tem hard links, links simbólicos (que exigem privilégio ou o modo desenvolvedor) e *junctions*.

**Proteção e travas (X10).** As ACLs POSIX do Linux (`setfacl`) avaliam as entradas em ordem fixa (dono, usuários nomeados, grupo, outros), e as ACLs do NTFS são listas ordenadas de ACEs com as de negação primeiro. Nos dois casos, uma ACL longa custa mais em cada verificação, como no X10, e por isso o resultado é guardado em cache no handle aberto. No Linux, as travas `flock` e `fcntl` são *consultivas*; o suporte a travas mandatórias (montagem com `-o mand`) foi removido no kernel 5.15 por ser frágil. No Windows, as travas são *mandatórias*: `LockFileEx` e os modos de compartilhamento do `CreateFile` fazem `ReadFile`/`WriteFile` falharem com `ERROR_LOCK_VIOLATION`, o EBUSY do X10. O macOS segue o modelo BSD (consultivas).

**Alocação (X11).** A FAT32/exFAT dos pendrives e cartões SD é a FAT do X11: simples, sem fragmentação externa, compatível com tudo, mas com tabela proporcional ao volume e ponteiros a seguir sem a tabela em memória. O ext4 usa *extents*: trechos contíguos (início, tamanho) organizados numa árvore de até 4 por inode, que combina a leitura barata da contígua com o crescimento flexível da indexada. Ele usa ainda *alocação atrasada* e *multiblock allocator* para obter extents grandes e evitar as realocações do X11. O NTFS guarda *runs* (extents) na MFT, e o APFS usa extents numa B-tree com *copy-on-write*. Nenhum sistema moderno realoca um arquivo inteiro para crescê-lo, a principal fonte de custo da contígua no X11.

**Flash (X13).** SSDs reais usam FTLs com área de reserva de 7% a 28%, coleta de lixo em segundo plano, nivelamento de desgaste e separação de dados quentes e frios. O comando TRIM (`fstrim.timer` ou a opção `discard` no Linux, "Otimizar unidades" no Windows, `trimforce` no macOS) informa quais páginas deixaram de ser válidas. Sem ele, o SSD trata blocos apagados pelo sistema de arquivos como válidos e os copia na coleta, exatamente o que faz a amplificação crescer com a ocupação no experimento extra. O F2FS (Android) é um sistema de arquivos em log feito para flash, com separação quente/frio, a solução para a surpresa do padrão "quente".

## 5. Limitações do simulador

- **Disco sem cache interna, NCQ nem zonas:** todos os cilindros têm a mesma taxa de transferência, e a busca é linear na distância (na prática, a busca é ~raiz da distância para distâncias curtas). Isso exagera o custo de buscas longas e a vantagem de SSTF e SCAN.
- **Rotação binária:** o modelo cobra 0 ou meia volta. Qualquer bloco "seguinte" sai de graça, mesmo depois de muito tempo ocioso, o que favoreceu a encadeada no X11 e os diretórios vizinhos no X4. Num disco real, a posição angular depende do tempo decorrido.
- **Disco recém-formatado:** os alocadores começam com o disco vazio e "primeiros livres" ficam contíguos. Sistemas reais envelhecidos têm muito mais fragmentação, o que pioraria encadeada, FAT e indexada.
- **Sem cache de diretórios nem de índices no sistema de arquivos:** cada `open` relê todos os diretórios (X4) e cada bloco da indexada relê o índice (X11). Sistemas reais têm dentry cache e buffer cache; o X12 mostra quanto isso muda o resultado.
- **CPU não concorre com o disco:** o polling ocupa a CPU, mas não atrasa nada (X2). O custo de copiar com interrupção não depende do tamanho real do bloco.
- **Um único dispositivo, sem preempção nem filas por processo:** o DEADLINE só age na escolha seguinte (a espera máxima passou do prazo no X7), e não há como reproduzir a justiça por processo do BFQ.
- **Cargas sintéticas e de execução única (semente fixa):** sem intervalos de confiança. Diferenças pequenas (por exemplo, LOOK × SSTF no X7) podem mudar com outra semente. As cargas de arquivos geram as requisições a cada 0,5 ms, independentemente de o disco ter terminado as anteriores (sistema aberto), o que satura o disco no X12.
- **Flash simplificado:** sem coleta em segundo plano, sem TRIM, sem nivelamento de desgaste e sem paralelismo entre chips. O tempo da coleta é cobrado da escrita que a disparou (pior caso para a latência).

## 6. Conclusões

1. O desempenho de um disco rígido é dominado pelo posicionamento (~10 ms) e não pela transferência (0,04 ms por bloco). Tudo o que preserva a sequencialidade (SSTF nos fluxos sequenciais, diretórios vizinhos, extents) vale mais do que reduzir a distância média.
2. Não há escalonador ótimo: o SSTF dá a melhor média e a pior cauda, o SCAN desperdiça movimento até as bordas com fila curta, e LOOK/C-LOOK e DEADLINE são os melhores compromissos sob carga alta. Isso explica a escolha do Linux (`mq-deadline` e BFQ para HDD, `none` para NVMe).
3. A interrupção vence o polling em dispositivos lentos e perde em dispositivos rápidos, e o DMA torna o custo de CPU independente do tamanho da transferência.
4. A cache é a otimização mais poderosa quando há reuso (de segundos para 0,15 ms no X12), mas a write-back troca durabilidade e regularidade da latência por menos escritas físicas.
5. A escolha de alocação depende da carga: a contígua sofre com crescimento e fragmentação externa, a encadeada com acesso direto, a indexada com a indireção do índice (que a cache resolve), e a FAT é a melhor desde que a tabela caiba em memória.
6. O flash elimina o custo de posicionamento, mas traz outro: a amplificação de escrita cresce rapidamente com a ocupação, o que justifica a área de reserva, o TRIM e a separação de dados quentes e frios.

## 7. Referências

- SILBERSCHATZ, A.; GALVIN, P. B.; GAGNE, G. *Operating System Concepts*. 9. ed. Wiley, 2013. Seções 10.1–10.4, cap. 11, 12.4–12.7 e 13.2–13.4.
- Documentação do kernel Linux: *Deadline IO scheduler tunables*: https://docs.kernel.org/block/deadline-iosched.html
- Documentação do kernel Linux: *BFQ (Budget Fair Queueing)*: https://docs.kernel.org/block/bfq-iosched.html
- Documentação do kernel Linux: *Documentation for /proc/sys/vm* (`dirty_writeback_centisecs`, `dirty_expire_centisecs`): https://docs.kernel.org/admin-guide/sysctl/vm.html
- Documentação do kernel Linux: *ext4 Data Structures and Algorithms* (extents, alocação atrasada): https://docs.kernel.org/filesystems/ext4/index.html
- Microsoft Learn: *LockFileEx function*: https://learn.microsoft.com/windows/win32/api/fileapi/nf-fileapi-lockfileex
- Microsoft Learn: *File Caching* (Cache Manager e lazy writer): https://learn.microsoft.com/windows/win32/fileio/file-caching
- APPLE INC. *Apple File System Reference*. Apple Developer Documentation, 2020.
- AGRAWAL, N. et al. *Design Tradeoffs for SSD Performance*. USENIX ATC, 2008.
- LEE, C. et al. *F2FS: A New File System for Flash Storage*. USENIX FAST, 2015.
- Documentação do Python: `collections.OrderedDict`: https://docs.python.org/3/library/collections.html#collections.OrderedDict

## 8. Declaração de autoria e uso de IA

**Quem fez o quê:** Rafaela Varão - N2 e Módulos de E/S (M1 - M2), Gabriel Gauterio - N3 e Módulo de arquivos (M4 - M6) e Gabriel Dalbem - integração, bônus flash, experimentos e revisão do relatório. todos revisaram as análises.

**Uso de ferramentas de IA:**

| Ferramenta | Finalidade | Onde foi usada (arquivos ou seções) |
|---|---|---|
| Claude Code (Anthropic) | Geração de código das partes marcadas com TODO, testes próprios e experimento extra | `simes/es/escalonadores.py`, `simes/es/cpu.py`, `simes/es/cache.py`, `simes/es/buffering.py`, `simes/arquivos/sistema.py`, `simes/arquivos/alocacao.py`, `simes/integracao.py`, `simes/disco.py`, `testes/test_grupo.py`, `experimentos_extras.py` |
| Claude Code (Anthropic) | Redação do rascunho do relatório (hipóteses, análises, relação com sistemas reais) a partir dos resultados gerados | `RELATORIO.md`, seções 0 a 7 |
