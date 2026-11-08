# Mídia SW — handoff para reconstrução como aplicação web

6 de nov. de 2026 · Filipe

> Documento de origem da reconstrução. As regras daqui são a especificação; cada uma veio de um erro real.

## Decisões tomadas no início da reconstrução (6/11/2026)

- Stack: ~~Next.js + Postgres~~ → **Python (só biblioteca padrão) + SQLite + HTML/JS puros**, para não instalar nada. Login Google restrito a @streetwise.com.br (Google Identity Services, token validado no servidor).
- **Nomes nunca diferem por maiúscula/minúscula nem acento** (veículo, cliente, tipo do PI, chat). Pedido do Filipe.
- Valores guardados em centavos inteiros.
- Seed: planilha "Planilha Investimento OOH SW 26" (Drive id `1AmcyBB2sIisKzhVpMVp_odWIjIdqI5a0rIOwUDuS8iM`), exportada em .xlsx para `seed/`.
- LIVRE: os 7,75M migram como estão, sinalizados como origem OTHERS do forecast, para realocação.
- Escopo: tudo de uma vez (todas as telas, painel superadmin, chat, desfazer).
- Mês fechado: não está na planilha (era propriedade do script); seed com 2026 = julho.

## O sistema

O Mídia SW controla o investimento em mídia OOH da Streetwise: quanto cada cliente já gastou (realizado, extraído dos PIs) contra quanto vai gastar (projeção, digitada pelos diretores de mídia), tudo aberto por veículo e por mês.

Hoje roda em Google Apps Script com Google Sheets como banco. A reconstrução é o mesmo sistema em outra casa — aplicação web com banco de dados, login Google Workspace, multi-ano. Não se junta ao painel de Media Intelligence.

Escala: ~2.300 PIs/ano, 15 clientes, 120 veículos canônicos, 8 pessoas. O volume é pequeno; a complexidade está nas regras.

## Vocabulário

| Termo | O que é |
|---|---|
| PI | Pedido de Inserção. Fato gerador do realizado. Base substituída por inteiro a cada upload. Número do PI não é chave — um PI pode ter várias linhas. |
| Mês de veiculação | O mês que conta, não a data de emissão. Vem como MM/AAAA. PI não atravessa mês. |
| Faturável | PI do tipo Veiculado. É o dinheiro. Só ele entra em expectativa, livre e meta. |
| Reaplicação | PI do tipo Reaplicação. Volume sobre bonificação, cliente não paga. Aparece em tudo, nunca entra em expectativa, livre ou meta. ~52% do faturável. |
| Outros tipos | Permuta sem comissão e afins: descartados na importação, com contador visível. |
| Veículo canônico | Nome unificado. 183 grafias → 120 veículos. Canonização na leitura, não na importação. |
| LIVRE | Pseudo-veículo reservado. Verba com mês definido e veículo em aberto. Ninguém cadastra veículo com esse nome. |
| Mês fechado | Último mês que virou fato. Decidido por pessoa, nunca pelo calendário. |
| Expectativa inicial | Combinado com o cliente para o ano. Digitada pelo diretor, não muda sozinha. |
| Expectativa atual | Realizado faturável + projetado faturável. Calculada. |
| Verba livre | Soma da linha LIVRE da projeção. Nada mais. |
| Meta | Compromisso anual por veículo. Só superadmin edita. Comparada só com faturável. |
| Pacote | Combinado fechado com um veículo para um cliente. Cartão no topo da ficha. |

## Regras de cálculo

- **Realizado só em mês fechado.** Realizado = PIs com mês ≤ mês fechado. Projeção = meses > mês fechado. O mês corrente nunca é fato.
- **Mês fechado não vem do calendário.** Fica gravado e só muda quando alguém confirma na tela. (Erro: em 1º/set o sistema promoveu agosto sozinho, antes dos PIs subirem; o mês apareceu zerado e a projeção de agosto sumiu.)
- **Na primeira leitura o valor é gravado e congela.** (Erro: a primeira versão caía no calendário quando a propriedade estava vazia.)
- **PIs do mês aberto ficam visíveis à parte.** Na ficha, linha âmbar com quanto já foi emitido no mês corrente.
- **Livre = só a linha LIVRE.** Não é resíduo. Cliente sem linha LIVRE tem livre zero. (Erro: livre = expectativa − realizado − projetado gerava livre negativo que comia o livre dos outros no consolidado.)
- **Totais do consolidado somam por cliente, não agregam.** Vale para livre e qualquer métrica com piso/regra por cliente.
- **Reaplicação fora das contas de dinheiro.** Expectativa, livre e meta ignoram reap. Linha própria na grade, coluna própria nas tabelas, botão liga/desliga que afeta tudo ao mesmo tempo — inclusive chat e Excel exportado. (Erro: ranking somava reap com o botão desligado.)
- **Meta compara só faturável**, independente do botão de reap. LIVRE fica fora dos dois lados da comparação no rodapé.
- **Canonização na leitura.** PIs gravados crus. Corrigir o de-para corrige o histórico sem novo upload.
- **Nome novo nunca some.** Veículo/cliente fora do de-para entra como pendente, mapeado para si mesmo, aparece para o admin e continua somando.

## Papéis e permissões

| | superadmin | admin | diretor | operação |
|---|---|---|---|---|
| Visão geral (todos os clientes) | sim | sim | — | — |
| Ficha de cliente | todos | todos | só os dele | — |
| Editar projeção, expectativa, pacotes | todos | todos | só os dele | — |
| Subir e apagar PIs | sim | sim | — | sim |
| Fechar o mês | sim | sim | — | — |
| Equipe, de-para, grupos | sim | sim | — | — |
| Criar/alterar admin e superadmin | sim | — | — | — |
| Editar meta de veículo | sim | — | — | — |
| Ver uso do app | sim | — | — | — |

- Barreira sempre no servidor; esconder na interface é cortesia.
- **Superadmin herda admin por construção, dentro da função de checagem** — nunca listando os papéis em cada chamada. (Erro: checagens `['admin','operacao']` ficaram de fora e o superadmin não subia PI. Duas vezes.)
- **Interface acompanha** — sem comparar papel com string literal `'admin'`.
- Travas: não rebaixar/inativar o último superadmin ativo; não fechar mês que não terminou.
- Um cliente, um diretor. Sem tabela de vínculo.

## Modelo de dados

Multi-ano desde o início: ano em tudo que é fato ou plano, seletor de ano na interface.

| Tabela | Campos | Notas |
|---|---|---|
| pi | numero, cliente_bruto, veiculo_bruto, tipo, valor, mes, ano | Cru. Sem chave natural. Substituída por ano a cada upload. |
| veiculo | nome_bruto, canonico, status, ativo | De-para. status ∈ ativo, pendente. |
| cliente | nome_bruto, canonico, diretor_email, ativo, grupo | grupo preenchido = fora dos totais por padrão. |
| pessoa | email, nome, papel, ativo | papel ∈ superadmin, admin, diretor, operacao. |
| expectativa | cliente, ano, valor, quem, quando | Inicial. Uma por cliente/ano. |
| projecao | cliente, veiculo, tipo, ano, mes, valor, quem, quando | Veículo pode ser LIVRE. Meses passados ficam gravados. |
| meta | veiculo, ano, valor, quem, quando | Só faturável. |
| pacote | id, cliente, veiculo, valor, inicio, fim, descricao, status, quem, quando | |
| config | chave, valor | mes_fechado por ano. |
| acesso | quando, email, nome, papel | Append-only. Uma linha por hora de uso. |
| log | quando, quem, acao, detalhe | Auditoria de escrita. |

- **Valores em reais com centavos. Exibição em milhões, armazenamento nunca arredonda.** (Erro: campo exibia 1 casa, salvar regravava a tela; R$ 28.191,42 virava zero.) Tela de edição mantém valor exato por baixo e grava só células tocadas.
- **Nunca apague projeção de meses passados** — é a base da comparação projetado × acontecido por diretor.

## Telas

- **Visão geral** — KPIs na ordem: expectativa atual, realizado faturável, projetado faturável, livre, reaplicação, expectativa inicial (cinza). Tabela por cliente ordenável por qualquer coluna (padrão expectativa atual desc), total no rodapé. Grade consolidada por veículo com colunas de meta.
- **Ficha do cliente** — expectativa inicial como pílula clicável ao lado do nome. Pacotes em cartões no topo. KPIs. Grade por veículo com meses futuros editáveis (sem tela separada de projeção). Barra preta no rodapé quando há alteração não salva.
- **Tela do veículo** — grade invertida: clientes nas linhas. Chega clicando no nome do veículo em qualquer grade.
- **PIs** — card de fechamento do mês no topo, upload por arrastar, download do modelo, apagar tudo.
- **Equipe** — pessoas e papéis, vínculo cliente↔diretor, grupo, veículos pendentes, uso do app.

## A grade

- Linhas: cada veículo gera duas sub-linhas, faturável e reaplicação, nome ocupando as duas.
- Colunas: 12 meses + realizado + projetado + ano. Top 10 por padrão, botão para expandir.
- Fronteira fato/estimativa: linha vertical preta após o mês fechado; células futuras hachuradas.
- LIVRE fixa no pé, fora da ordenação e do top 10.
- Colunas redimensionáveis arrastando a borda — arrastar não dispara a ordenação do mesmo cabeçalho.

## Exceção da Mondelez

Regra pontual. Na ficha da Mondelez, linha fixa no topo: **Veículos Long Tail**, somando tudo que não é Eletromidia, JCDecaux, Kallas, NeOOH ou BrMalls. Os cards de KPI dela mostram o recorte long tail embaixo do total. Só nela. No sistema novo: marcação na tabela de veículos em vez de nomes no código (renomear canônico mudaria o número em silêncio).

## Chat sobre os dados

Campo de pergunta na visão geral, responde na hora, **sem IA**. Código interpreta; as mesmas funções das telas fazem a conta. Nunca inventa número; quando não entende, diz.

Entende cliente, veículo, cruzados, métrica, mês, rankings e quebras (livre por cliente, reap por veículo). Toda resposta decomposta — realizado, projetado, total — e declara o que está fora da conta.

**Casamento por palavra inteira.** (Erro: "investimento" contém "tim"; "nos" casava com o veículo Nós; "maiores" virava maio.)

## Painel superadmin

Hoje é feito editando abas à mão. Cada item vira tela:

1. **De-para de veículos** — editar canônico, resolver pendentes, agrupar grafias. Busca, edição em massa, visão de quanto cada nome movimenta.
2. **Sugestão de fusões** — remove razão social e praça, ignora palavras genéricas (outdoor, shopping), propõe pares. Calibrado contra 20 fusões aprovadas: reencontra as 20 sem falso positivo.
3. **De-para de clientes** — idem, mais vínculo com diretor e grupo.
4. **Colar projeção em massa** — importador com pré-visualização e conferência de total (já se importou 203 linhas de uma vez).
5. **Editar PI individual** — adicionar, corrigir, remover sem refazer a base.
6. **Fechar e reabrir mês** — seletor, não botão de avançar.
7. **Equipe e papéis** — com as travas do superadmin.
8. **Metas por veículo.**
9. **Grupos de cliente** — quem fica fora dos totais por padrão.
10. **Uso do app e log.**

## O que não existe hoje e vale criar

- **Desfazer.** Operações destrutivas (apagar todos os PIs, substituir a base, importar projeção) com confirmação forte e, idealmente, reversão.
- **Importação que não substitui tudo.**

## Armadilhas

- **Concentração extrema.** Eletromidia ~76% da base. Kallas espalhada em oito grafias (All Space, Codemp, Zanzar, Grupo Kallas): aparecia 1,97M, real 3,3M.
- **Grafias que não são variações.** ES Outdoor, Cuiabá Outdoor e Outdoor Mídia são empresas diferentes.
- **Subsidiárias invisíveis.** Otima, Brasil Outdoor, Globo Comunicação = Eletromidia; All Space, Codemp = Kallas. Não se descobre por algoritmo.
- **Agregação duplicada diverge.** Uma implementação só da conta. Validar contra uma terceira independente, escrita só para conferir.
- **Datas que o Sheets converte.** 01/2026 virou data. Parsear MM/AAAA explicitamente.
- **Cache invisível.** Se houver cache, invalidação explícita e visível.

## Seed e migração

| Aba | O que carrega |
|---|---|
| Veiculos | De-para completo (~183–197 linhas em ~120 canônicos). Ativo mais valioso — feito à mão. |
| Clientes | Nomes, canônicos, diretor, grupo whitelabel. |
| Equipe | Emails e papéis. |
| PIs | Base de 2026. |
| Projecao | Projeção importada do forecast, ago–dez. |
| Expectativa, Metas, Pacotes | O que estiver preenchido. |
| Acessos, Log | Dispensáveis (podem ser importados como histórico). |

- **Não reconstrua o de-para do zero nem confie num algoritmo para refazê-lo.**
- **Confira o total: a projeção importada soma exatamente R$ 104.343.084,63.**

## Pendências

- **LIVRE errado hoje.** Os 7,75M em LIVRE são o bucket OTHERS do forecast. Distribuição: Keeta 2,71M, Friboi 1,50M, Mitsubishi 1,15M (= projeção inteira dela), Seara 0,85M, Reckitt 0,51M, resto pulverizado.
- **Casas decimais na projeção** — bug de perda de dado ativo no sistema atual.
- **Mês fechado parado em julho.** Se PIs de ago/set/out subiram, esses meses ainda aparecem como projeção.
- **Virada de ano.** Decidir se o Apps Script ganha 2027 na marra ou se a migração acelera.

## Decisões em aberto

- **Agrupar clientes para negociação** (Burger King, Popeyes, Starbucks, Subway = ZAMP). Campo grupo hoje exclui dos totais; agrupar para somar é outra coluna.
- **Meta por cliente** (objetivo interno ≠ expectativa inicial do cliente).
- **Cadastro de veículo pelo diretor** — recusado; diretor lança em LIVRE e pede cadastro.
- **Visão mobile da grade** — cartões por veículo, se o uso no celular crescer.

## Observações da leitura do seed (6/11/2026)

- Projecao tem 178 linhas de dados (handoff fala em 203 importadas) — validar pelo total.
- Mondelez: diretor Herbert Gomes está inativo na Equipe.
- Seara: diretor é testeai@streetwise.com.br (conta de teste); o único pacote também foi criado por ela.
- Bacio e ZAMP sem diretor; ZAMP inativa com expectativa de R$ 50M.

## Conferência do seed (.xlsx completo, 6/11/2026)

- **PIs:** 2.395 linhas, jan–jul/2026 + 8 linhas de ago. Ago–out ainda não subiram (coerente com mês fechado = julho).
- **Mês de veiculação vem como número de série de data** (46023 = 01/01/2026), não MM/AAAA. O importador precisa aceitar os dois.
- **Tipo com grafias variadas:** `Veiculado`, `veiculado`, `REAPLICAÇÃO`, `Reaplicação`. Normalizar sem caixa e sem acento.
- Faturável R$ 158.397.191,05; reaplicação R$ 54.047.799,04 (~34% do faturável, não 52%).
- **De-para:** 197 grafias → 133 canônicos (não 183 → 120).
- **7 nomes brutos nos PIs sem linha no de-para**, todos iguais a um canônico só com caixa diferente: Eletromidia (16,7M), BRMalls (3,3M), Kallas (1,1M), JCDecaux (1,0M), Weooh (0,7M), Guipa, MULTIPLAN. Com casamento exato, BRMalls e Weooh viram veículos separados de BrMalls e WeOOH. Regra no sistema novo: nome bruto sem de-para que bate com um canônico ignorando caixa/acento vai direto para esse canônico.
- Eletromidia = 58,5% do faturável (não 76%), com o casamento acima.
- **Projeção:** 178 linhas, total R$ 106.578.907,23 — **não bate com R$ 104.343.084,63**, porque a projeção foi editada depois da importação (ver Log). O checksum da migração passa a ser o total atual da aba: R$ 106.578.907,23 (FAT 91.578.907,23 + 1 linha REAP de 15M, BetMgm/Eletromidia/ago).
- **LIVRE hoje soma R$ 3.511.922,85, não 7,75M** — os diretores já realocaram parte. Seara 0,90M, Keeta 0,80M, Ecovias 0,50M, Reckitt/Mondelez/Pague Menos 0,30M, Tim 0,24M, Subway 0,17M. Mitsubishi e Friboi não têm mais LIVRE.
- Sem chaves duplicadas na projeção; todos os veículos/clientes da projeção e das metas existem como canônicos.
