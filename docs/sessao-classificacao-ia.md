# Sessão — sync completo, correção da classificação por IA e próxima camada

Registro em primeira pessoa (eu, o assistente) do que fiz nesta sessão e o que fica planejado como próximo passo.

## O que eu fiz

**1. Achei e corrigi o motivo de nem todos os emails aparecerem.** `GmailClient._first_sync` tinha `maxResults: "50"` fixo, sem paginação — só a fatia mais recente da inbox entrava no Apolo. Troquei por paginação real (`list_ids`/`list_uids` + `fetch_header` sob demanda, tanto no Gmail quanto no IMAP), separando "listar IDs" (rápido) de "buscar header" (por item), pra dar pra mostrar progresso ao vivo em vez de um lote silencioso.

**2. Criei o botão "Sincronizar".** Nova tela (`apolo/ui/sync_screen.py`) e módulo (`apolo/sync.py`) que fazem um full-scan por conta (até `APOLO_SYNC_LIMIT`, padrão 500 mais recentes), não-destrutivo (ignora o que já existe no banco), classificando cada email novo pela cascata e mandando o resíduo pro Ollama — tudo com eventos ao vivo (`found → item → analisando → classificado`) que a UI escuta.

**3. Criei o botão/comando "Reclassificar pendentes" (`retry-ia`).** Cobre o caso de um email ficar preso com `regra_casada='default'` porque o Ollama estava fora do ar (ou o processo foi interrompido) na hora em que ele chegou — revarre esses presos e tenta de novo, sem precisar rodar o sync inteiro.

**4. Rodei o sync completo de verdade e usei o retry-ia pra zerar o backlog.** No processo, aprendi (na prática, não só em teoria) que essa máquina é um gargalo real pro Ollama local: pouca RAM livre, swap alto, servidor de inferência de uma requisição por vez (`-np 1`). Cheguei a matar sem querer um processo que não estava travado, só lento — e teve contenção real entre o `apolo.timer` e o `retry-ia` rodando ao mesmo tempo, que resolvi pausando o timer até zerar a fila.

**5. Achei a causa raiz de emails importantes irem pra lixeira.** O modelo local (`qwen2.5:0.5b`) é pequeno demais pra distinguir "automático" de "sem importância" — via o padrão superficial `no-reply@` e ignorava contexto de segurança/instituição. Reproduzi isolado (prompt + resposta crua do modelo) antes de mexer em qualquer coisa.

**6. Corrigi de forma geral, não só pro caso específico.** Em vez de só adicionar `cofre-exemplo.com`/`portal-academico-exemplo.com` na allowlist, criei um grupo de keywords (`alerta_login` em `apolo/rules/config.toml`) cobrindo alerta de login/segurança **em português e inglês**, testado contra vendors que nem tinham aparecido no exemplo original (Google, GitHub, Nubank, Microsoft, Meta, EA). Também reforcei o prompt do Ollama com exemplos (few-shot) como segunda linha de defesa pro que a regra não cobrir. Depois rodei uma correção retroativa na fila (sem tocar em nada que já estava certo) pra já aplicar as regras novas aos ~95 emails que tinham sido classificados errado antes da correção existir.

## O que ficou pra trás (mencionado, não resolvido)

Numa amostragem depois da correção, apareceram mais 3 casos de borda que o dono decidiu **não** corrigir agora: notificação bancária (Nubank — alteração de email), boleto/cobrança (Cruzeiro do Sul Educacional) e CI do GitHub (`Run failed`). Só o padrão "código de segurança" foi ampliado (pegou o caso da EA). Esses três ficam como candidatos naturais pra cobrir na camada nova abaixo.

## Próximos passos: segunda camada de verificação pós-Ollama

A ideia do dono: depois que o Ollama responde, rodar uma **segunda checagem determinística** — não outra chamada de IA, mas uma verificação por palavras-chave com **score por campo**, cobrindo pelo menos três categorias sensíveis: **faculdade**, **segurança**, **bancário**. Objetivo: uma rede de segurança que pega o que o modelo pequeno erra (subestima) sem depender só de allowlist manual caso a caso.

Rascunho de design pra quando formos implementar:

- **Onde entra no pipeline:** hoje `ollama.classify(...)` é chamado em 6 lugares (`apolo/cli.py`: `_ai_pass`, `_ai_pass_gmail`, `_retry_proton_rows`, `_retry_gmail_rows`; `apolo/sync.py`: `_sync_imap_pasta`, `_sync_gmail_pasta`), cada um gravando o resultado direto no banco. Antes de adicionar a camada, vale centralizar isso numa função só (ex.: `apply_ia_decision(...)` num módulo novo, tipo `apolo/verify.py`) que primeiro chama o Ollama e **depois** roda a verificação — assim a lógica nova entra num lugar só em vez de duplicar em 6 pontos.
- **Score, não só match binário:** cada categoria (`faculdade`, `seguranca`, `bancario`) tem uma lista de termos com peso (não todo termo pesa igual — "boleto" pesa mais que "aluno" pra decidir "não é lixo"). Soma os pontos que baterem no assunto/remetente (e talvez trecho, se der pra manter rápido) e compara com um limiar por categoria.
- **Direção do override:** essa camada só teria efeito quando o Ollama sugeriu `lixeira` — se o score de alguma categoria protegida passar do limiar, sobrescreve pra `manter` (ou `revisar`, a definir) e registra isso no `regra_casada` de um jeito rastreável (ex.: `ia:ruido→verify:bancario`), pra não perder o rastro de que foi um override, não a decisão original.
- **Onde vivem os termos/pesos:** provavelmente uma seção nova no `rules/config.toml` (ex.: `[[verify]]` por categoria, parecido com `[[keywords]]` mas com peso em vez de ação direta), pra não precisar mexer em código pra ajustar a lista depois.
- **Sementes pros três casos vistos nesta sessão** (ponto de partida, não lista fechada):
  - *bancário*: boleto, fatura, cobrança, pix, extrato, cartão de crédito, conta corrente + domínios de banco conhecidos (nubank.com.br, itau, bradesco, etc.).
  - *segurança*: reforça/generaliza o que já existe em `alerta_login`, mas como score em vez de match binário.
  - *faculdade*: universidade, faculdade, disciplina, matrícula, boletim, nota, aluno, professor, campus, `.edu.br`, nomes de plataforma (blackboard, moodle, classroom).
- **Em aberto pro dono decidir antes de eu implementar:** os pesos/limiares de cada categoria (vou precisar de uma primeira rodada de ajuste manual olhando exemplos reais); se o override deveria virar `manter` direto ou só rebaixar pra `revisar` (mais conservador); se entra só no fluxo novo (sync/retry-ia) ou também no incremental (`cmd_run`); e se cobre também os 3 casos deixados de lado (bancário, boleto, CI do GitHub) já nessa entrega ou depois.

## Implementado (camada 2)

Decisões do dono: override rebaixa pra `revisar` (conservador, nunca promove
direto pra `manter`); entra nas três categorias do rascunho (`seguranca`,
`bancario`, `faculdade`) de uma vez; roda nos 6 pontos de chamada (incremental
`cmd_run` incluído, não só sync/retry-ia).

- **`apolo/verify.py` (novo):** centraliza a chamada ao Ollama + a camada 2 em
  `apply_ia_decision(...)`, substituindo os 6 `ollama.classify(...)` diretos em
  `cli.py`/`sync.py`. `verify(...)` só age quando a IA sugeriu `lixeira`: soma
  o peso dos termos batidos e, se passar do limiar da categoria, rebaixa pra
  `revisar` e marca a categoria de forma rastreável (`ruido→verify:bancario`).
- **Detecção de idioma:** heurístico simples por contagem de palavras
  funcionais PT vs EN (sem lib externa). Empate (inclusive 0 a 0, ex.: assunto
  curto demais ou trecho vazio) devolve "não identificado" — nesse caso a
  verificação **não roda** e a sugestão original do Ollama (lixeira) fica como
  está, por decisão do dono ("lixeira certeira": sem sinal pra questionar,
  mais vale não arriscar que tentar advinhar o idioma).
- **Termos e pesos:** vivem em `rules/config.toml`, seção `[verify.<categoria>]`,
  com `termos_ambos` (loanwords/nomes próprios/domínios, sempre avaliados) e
  `termos_pt`/`termos_en` (só avaliados se o idioma bateu). Pesos e limiares
  (5 por categoria) são o primeiro palpite — ainda faltam ajustar olhando
  exemplos reais, como já estava previsto.
