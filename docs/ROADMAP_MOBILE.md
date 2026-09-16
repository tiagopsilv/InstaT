# InstaT Mobile — roadmap de implementação (documento consolidado)

**Versão:** v13.7 · 16/09/2026 · **autossuficiente**: substitui e incorpora a v10 (`docs/ROADMAP_MOBILE.md`) e a v11 (`ROADMAP_MOBILE_REVISADO.md`). Nenhuma seção depende de consulta a documento anterior.
**Status (16/09/2026):** **F0 entregue** (sete passos executados; aceite de CI pendente). **F6a bloqueada no passo 3** (fonte do APK e conformidade da tradução ARM). **F6b bloqueada** por dependência. Demais fases não iniciadas. Não foram executados login no Instagram, Android em Docker nem proxy pago. Estado detalhado: `docs/HISTORICO_MOBILE.md`.

**Histórico de versões:** v1–v10 (14/09/2026, roadmap original) · v11 (15/09/2026, revisão técnica por outra IA sobre o GitHub) · v12/v12.1 (15/09/2026, auditoria independente) · **v13 (16/09/2026, consolidação e correção do desenho de persistência após devolução do Tiago)**.

## Convenções de evidência

| Etiqueta | Significado |
|---|---|
| **Verificado no código** | Lido em um SHA identificado, citado como `arquivo:linha @ SHA` |
| **Relatado pelo auditor** | Leitura, teste ou build executado pela IA auditora num checkout local com branches não publicadas. **O Tiago não reproduziu nem teve acesso a essas branches.** A F0 reproduz; o que não se reproduzir volta a Hipótese |
| **Documentado** | Afirmação do mantenedor, com URL e data de consulta |
| **Relato** | Issue ou comunidade: evidência de que alguém observou, não certificação |
| **Hipótese** | Exige experimento; registrar versão e contexto |
| **Proposta** | Decisão de projeto ainda não implementada e ainda não aprovada pelo Tiago |

Número de estrelas, publicidade ou uma issue isolada não comprovam confiabilidade operacional. Toda referência incorporada tem versão ou commit fixado.

---

## 1. Parecer e escopo da decisão

O objetivo — acessar o Instagram por um ambiente Android em Docker e obter perfis, seguidores e seguindo, preservando a API pública — é viável como **extensão experimental opt-in do InstaT**. Não é tecnicamente correto prometer que um container será indistinguível de um celular físico, que um fingerprint impedirá bloqueios ou que qualquer conta continuará exatamente o cursor de outra.

**Caminho proposto:** reconciliar a base e corrigir defeitos de empacotamento; em paralelo, provar cedo a viabilidade do Android sem conta e, logo em seguida, uma **fatia vertical** (F6b) com login manual e extração de uma lista conhecida pela interface; estabilizar sessão, governador de erros e persistência transacional; depois concorrência, leitura por UI e, só então, avaliar um adaptador de API móvel. Transporte TLS customizado e ponte app→API continuam experimentos condicionais.

**Status das decisões:**
- **Aprovado pelo Tiago (16/09/2026):** inclusão da fase **F6b** (fatia vertical Android) e sua posição, logo após o F6a e antes de F1–F7. Os limiares numéricos da F6b continuam proposta, a confirmar na pré-análise da fase.
- **Aprovado pelo Tiago (16/09/2026):** **solução C** para páginas recebidas (§6.3, I10) — autoridade para gravar pela posse da concessão, confiança no conteúdo por evidência; substitui as opções A e B. **A aprovação é do conceito, não do SQL:** o SQL da v13.5 falhou em 15 de 17 reproduções e foi substituído na v13.6 (§6.3), validado apenas em modelo isolado [E2]. A política `sanity-v1` (§6.3.10) fica fixada na pré-análise da F5 e é calibrada com dados de F6b, F7 e F8.
- **Aprovado pelo Tiago (16/09/2026), rodada de decisões:**
  1. **MVP Android:** um slot, uma conta ativa e um worker de coleta. Não elimina os testes de concorrência da persistência.
  2. **Ordem:** F0 em paralelo com F6a → F6b; depois F1 → F2 → F5 → F3 → F6 → F7. F4 é entrega independente após F5 e não bloqueia o MVP Android. F8 e F9 dependem de resultados e necessidade demonstrada. F10 e F11 seguem os gates anteriores.
  3. **Python mínimo 3.12** na nova versão, com quebra de compatibilidade registrada; 3.13 e 3.14 só declarados com evidência.
  4. **Publicação:** branches de revisão e PRs em rascunho no InstaT, após inspeção de todos os commits; sem push em `main`, force-push, merge ou release.
  5. **Resultado e histórico:** união das observações confiáveis como histórico observado; resultado por execução e `selected_run_id`; seleção da execução mais recente que cumpriu os critérios, senão a mais recente como parcial; datas separadas; completude e suspeitas só na execução selecionada; ausência posterior não comprova unfollow; contrato legado preservado (§6.3.9).
  6. **Limites operacionais iniciais:** 2 releituras adicionais por posição; 3 execuções por job; repetição idempotente não consome tentativa; challenge e restrição interrompem independentemente do saldo; requeue após restrição exige liberação manual e validação positiva da sessão; tempo sozinho não libera. **Não são limites seguros do Instagram.**
  - A aprovação das decisões A–F é da **direção arquitetural**, não certificação antecipada do SQL.
  - Estas decisões **não ampliam** o teto de tráfego pago nem autorizam contas adicionais.
- **Proposta da auditoria, sem aprovação registrada:** valores de `K`, `S`, `replay_ratio`, `frontier_confirm_screens`, tolerância e idade do contador (§6.3.10); nomes dos novos métodos públicos de resultado; fonte do APK e uso de imagem de emulador com tradução ARM (§8, F6a). Onde o documento disser "proposto" ou "recomendado", a decisão continua aberta.

### 1.1 "URA"

Não foi encontrada referência a `URA` em nenhuma das bases inspecionadas nem no ritual. **Pergunta aberta ao Tiago; não bloqueia nenhuma fase.** Se for um documento interno, anexá-lo e revisar este roadmap contra ele.

---

## 2. Base de código verificada

### 2.1 Refs

| Referência | SHA | Onde existe | Conteúdo relevante |
|---|---|---|---|
| GitHub `origin/main` (base da v11) | `50589a3765bde22b2afa7f968812e440415fe2b8` (21/04/2026) | remoto e local | sem `providers.py`, `worker_pool.py`, `diagnostics.py`, `extraction_result.py`, `logging_config.py`, `instat/mobile/` |
| `main` local | `e25c6a97266da1a36f89b581e775ec6087d979dd` | **só local**, 6 commits não publicados | contém todos os itens acima, `use_stdlib_logging`, `with_metadata`, `get_recent_posts`, `stealth_mode='undetected_chrome'` opt-in |
| `docs/roadmap-mobile` — **base proposta** | `5b0b996f7c052367a3b0576e29a74886e3340a0c` | **só local** | `main` local + `6175e5b` (esqueleto `instat/mobile`, `tests/mobile/test_contracts.py`, `tests/test_public_api_contract.py`) + roadmap v6–v10 |

`git ls-remote origin` retorna um único ref: `main = 50589a3`.

**Consequência:** a v11 analisou corretamente o que estava publicado. O código de trabalho está 13 commits à frente. Os achados que dizem "não encontrado" são verdadeiros para o GitHub e falsos para o código local.

### 2.2 Evidência relatada pelo auditor (a reproduzir na F0)

| Verificação | Resultado relatado |
|---|---|
| `pytest tests -m "not e2e and not mobile"` em `5b0b996`, Python 3.12.10, Windows | todos os testes coletados passaram; sem falhas nem erros. Não executado em 3.9/3.10 |
| `pip wheel . --no-deps` em `5b0b996` | wheel **sem `instat/mobile`**; pacotes: `instat`, `instat/config`, `instat/engines`. O import em `extractor.py:62-70` é tolerante, então instalado por wheel `engines=["android_ui"]` vira apenas warning "Unknown engine name". O CI não detecta porque usa `pip install -e` |
| Leitura de código | `login.py`, `login_flow.py`, `session_cache.py`, `extractor.py`, `engines/base.py`, `parallel.py`, `persistent_store.py`, `playwright_engine.py`, `mobile/engines.py`, `tests/test_public_api_contract.py`, `pyproject.toml`, `.github/workflows/ci.yml`, também em `50589a3` |
| Reprodução de T01 e T02 do relatório externo [E1] em `5b0b996` | `session_cache.py` e a lógica de decisão do `SessionRestorer` são idênticos nas duas bases (diff vazio nas linhas de decisão). Resultados iguais aos do relatório: JSON truncado → `JSONDecodeError`; sem `saved_at` → `KeyError`; `saved_at` textual → `TypeError`; cookies como string, `saved_at` futuro e idade exatamente 3.600 s → aceitos. `SessionRestorer.attempt()` retorna `True` para `/challenge/`, `/checkpoint/`, `/accounts/suspended/` e `/auth_platform/codeentry/`, e `False` só para `/accounts/login/` |
| Inspeção de `build/` (16/09/2026) | não rastreado nem ignorado pelo git; criado em 15/09/2026 17:48 pelo `pip wheel .` da auditoria. `build/lib/instat/**`: 40 arquivos **idênticos por SHA-256** aos fontes atuais, nenhum exclusivo de `build/`; `build/bdist.win-amd64/` vazio; sem `instat/mobile`, coerente com o defeito do wheel. Nenhum conteúdo único encontrado; **não apagado** — remoção aguarda o Tiago |
| Inspeção de `instat/logs/diagnostics/` (16/09/2026) | ignorado pelo `.gitignore` (linha 21) e não rastreado. 18 arquivos `cookies.json` de `.instagram.com`; **2 contêm `sessionid` e `ds_user_id`** (lidos só os nomes das chaves, nunca os valores). Origem e validade não verificadas; nada alterado |
| Reprodução do SQL da §6.3 [E2] | SQL literal da v13.5: 2/17; desenho v13.6: 17/17; modelos isolados em SQLite 3.49.1, sem InstaT, Android, Instagram ou proxy |

---

## 3. Achados sobre o roadmap v10 e classificação da auditoria

Legenda: ✅ confirmado · ◐ parcialmente correto · ✖ incorreto.

| ID | Decisão da v10 | Correção da v11 | Auditoria |
|---|---|---|---|
| A01 | "O InstaT loga do zero a cada execução" | contradito pelo código: há restauração de cookies | ✅ `login.py:463-506 @5b0b996` e `:314-351 @50589a3` chamam `_try_cookie_restore` antes do formulário. Nuance: com `max_age=3600`, execuções espaçadas por mais de 1h caem no formulário, então o efeito relatado pode ser real com causa diferente |
| A02 | causa-raiz é falta de persistência | candidatos concretos: TTL de 1h, caminho relativo | ✅ `session_cache.py:33,44`: `.instat_sessions` relativo, `max_age=3600`, `json.loads` sem tratamento de erro |
| A03 | identidade "desktop web"; Selenium dependente de undetected-chromedriver | Firefox com UA de Chrome 89/Android 8; `providers.py` não encontrado | ◐ UA confirmado (`login.py:133-139`), e o Playwright fixa o mesmo UA **inclusive com `browser_type` firefox/webkit**. Mas `providers.py` e undetected-chromedriver existem em `e25c6a9` como `stealth_mode` **opt-in**, default `firefox`. A v10 exagerava ao dizer "dependente"; a v11 errou ao concluir ausência no projeto |
| A04 | basta registrar engines | `get_profile()` exige `_driver` | ✅ `extractor.py:390-392 @5b0b996`. Agravante: `tests/test_public_api_contract.py` parte B injeta `_FakeDriver` em `_driver`, ou seja, **o teste de contrato atual trava o acoplamento** que precisa ser removido |
| A05 | mapa de classes "reais" | vários componentes não encontrados | ◐ correto em `50589a3`; em `e25c6a9` todos existem, inclusive `get_recent_posts` não abstrato (`base.py:37`) |
| A06 | Fase 0 já tem esqueleto em `6175e5b` | não validado; árvore consultada não contém | ◐ a cautela estava certa; verificado que `6175e5b` existe em `docs/roadmap-mobile`, não publicado |
| A07 | cursor por `(alvo,list_type)` permite troca exata de conta | cursor é opaco, sem portabilidade documentada | ✅ (como hipótese) a doc do instagrapi expõe `max_id`/`end_cursor`/`rank_token` por chamada, sem afirmar portabilidade |
| A08 | stores separados garantem retomada | confirmar resultado e checkpoint na mesma transação | ✅ o fluxo da v10 grava cursor e perfis separadamente. Além disso, `PersistentStore.add_batch` lê `existing_before` e insere sem transação explícita, o que torna a contagem de novos imprecisa sob concorrência |
| A09 | "uma conta por worker" com lock por endpoint | separar lease de conta e cooldown por endpoint; heartbeat e fencing | ◐ **corrigido nesta v13:** o problema é real e a v10 é internamente contraditória (a pré-análise fala em lock por conta e por endpoint, o desenho copiado do twscrape trava só por endpoint). A v12 afirmou que heartbeat era excesso; isso estava errado. Conforme I6 da §6.3, **heartbeat independente é obrigatório** quando a unidade de trabalho pode exceder a concessão, o que é o caso da UI Android |
| A10 | "não há paralelismo hoje" | `parallel.py` já usa `ThreadPoolExecutor` | ◐ a v10 não afirma isso: lista `ParallelCoordinator`/`WorkerPool`; "sem paralelismo de verdade" é relato do usuário. Confirmado que `parallel.py:107-111` reusa credenciais (`idx % len(accounts)` ou default para todos) |
| A11 | muitos contexts compartilhados + threads | a API Python do Playwright não é thread-safe | ✅ Documentado [S10]: "Playwright's API is not thread-safe… create a playwright instance per thread" |
| A12 | `login()` descarta fingerprint, `relogin()` preserva | não é regra universal | ✅ instagrapi 3.0.2: "login() reuses a valid saved session" |
| A13 | cookie web + mesmo IP garante handoff | não há garantia; há rejeição documentada | ◐ "sem garantia" está certo; a doc não diz que sessão web é sempre rejeitada, e sim que um `login_required` significa que aquela sessão foi rejeitada |
| A14 | slot sem root depende de exportar sessão de `/data/data` | tornar a ponte opcional | ✅ contradição interna da v10 |
| A15 | porta sticky = IP exclusivo e imutável | sticky troca se o IP ficar indisponível | ✅ Documentado [S2] |
| A16 | exemplo `sessid.slot01` validado | não confirmado nas páginas oficiais | ◐/✖ **`sessid` é documentado** [S21]: `login__cr.au;sessid.123:password@gw.dataimpulse.com:823`, "824 for Socks5 or 823 for HTTPS", IP "for 30 minutes", "not a replacement for Sticky proxies". A conclusão de não combinar `sessid` com porta sticky continua certa, pelo motivo documentado |
| A17 | "redroid 11–13 funciona"; "APK é ARM" | inspecionar ABIs; montar matriz | ◐ matriz certa. O README [S5] diz, **sem citar tag, versão ou arquitetura**, "Published redroid images already got libndk_translation included"; o #933 [S25] relata que **imagens x86_64 a partir da 15** anunciam `arm64-v8a` com `ro.dalvik.vm.native.bridge = libnb.so` ausente e apps só-ARM fecham, **sem mencionar Instagram**. As fontes não se conciliam: presença do tradutor é Hipótese por tag |
| A18 | smartphone físico passa na atestação | não é garantia | ✅ argumento correto; sem fonte específica sobre Instagram e atestação |
| A19 | JA4 igual significa TLS/app igual | JA4 é resumo | ✅ curl_cffi [S11]: "there are still some fields are not covered and can be used to detect you" |
| A20 | tcpdump passivo fornece headers e fingerprint H2 | ClientHello não implica acesso a H2 | ✅ SETTINGS e HEADERS de HTTP/2 trafegam dentro do TLS |
| A21 | 200 req/h, 2–3 s e GramAddict como segurança | não são limites publicados | ✅ nenhuma dessas fontes é do Instagram |
| A22 | TDD: todos os testes devem falhar primeiro | só o comportamento novo | ✅ a v10 exige "todos falham" e ao mesmo tempo pede o teste de contrato verde na Fase 0 |
| A23 | Fase 11 antes do rate limiter; login antes do proxy | governador e saída de rede antes da carga | ✅ a Fase 1 da v10 mede login antes de existir proxy (Fase 2) |
| A24 | tudo em `instat/mobile/` | sessão, fila e orçamento servem web e Android | ✅ |
| A25 | adicionar pasta basta para distribuição | `pyproject.toml` enumera pacotes | ✅ **materializado**: wheel de `5b0b996` omite `instat/mobile` (Relatado pelo auditor). `requires-python >=3.9`; instagrapi ≥3.10 |
| A26 | diferença ≤1% prova completude | contador arredondado não é ground truth | ✅ |
| A27 | KS-test prova humanidade | testa o gerador | ✅ |
| A28 | ritual cumprido por listar sete passos | registrar desvio | ✅ o histórico da v10 registra "esqueleto e teste de contrato adiantados". Porém a própria v11 manteve, na adaptação do ritual, o contexto de fantasy do projeto de origem (corrigido na §7) |

**Totais:** 28 achados — **20 confirmados** (A01, A02, A04, A07, A08, A11, A12, A14, A15, A18–A28) e **8 parcialmente corretos** (A03, A05, A06, A09, A10, A13, A16, A17, sendo que o A16 tem uma parte incorreta). Nenhum integralmente incorreto.

### 3.1 Problemas adicionais no código (verificados)

- `SessionRestorer` (`login_flow.py:60-88`): sucesso é ausência de `/accounts/login` mais presença de `sessionid`. Não valida o usuário esperado nem detecta challenge. **Reproduzido** (relatório externo em `50589a3` [E1]; auditor em `5b0b996`): challenge, checkpoint, conta suspensa e `auth_platform` retornam sucesso.
- **Agravante no orquestrador:** `InstaLogin.login()` retorna `True` logo após a restauração (`login.py:473-474 @5b0b996`), **antes** de `_check_account_blocked` (`login.py:484`). A mesma ordem existe em `50589a3`. Portanto, quando a restauração aceita indevidamente um challenge, o `BlockDetector` não roda, e a extração começa numa sessão bloqueada.
- `PlaywrightEngine` (`playwright_engine.py:142`): sucesso apenas pela URL, sem checar `sessionid`.
- Construtor (`extractor.py:227-236`): falha no login do primário vira `LoginError` antes da cascata. Isso inclui os stubs mobile: `engines=["android_ui","selenium"]` quebra na construção, embora a própria docstring de `mobile/engines.py` mande pôr o stub atrás de um engine funcional.
- `SessionCache.save()` escreve direto com `write_text` e só restringe permissão depois. `load()` levanta exceção para JSON truncado, `saved_at` ausente ou não numérico; aceita cookies de tipo errado e `saved_at` no futuro; e usa `>` na fronteira, aceitando idade exatamente igual a `max_age` (reproduzido, ver §2.2). A fronteira é **política a decidir** na F1, não vulnerabilidade.

### 3.2 Problemas introduzidos ou não tratados pela revisão v11

| ID | Tipo | Problema | Correção nesta v13 |
|---|---|---|---|
| R01 | Bloqueador | base no GitHub `50589a3`, 13 commits atrás do código real | base proposta `5b0b996…` (§2.1); F0 reconcilia |
| R02 | Bloqueador | adaptação do ritual manteve foco em "comunidade/mercado de fantasy" | §7 sem nenhum contexto do projeto de origem |
| R03 | Bloqueador | F4 testa fencing usando a transação que só nasce na F5 | F5 antes de F4 (§8) |
| R04 | Bloqueador | wheel sem `instat.mobile` já hoje, corrigido só na F3 | corrigido na F0 |
| R05 | Arquitetura | Android só na 7ª fase | spike F6a logo após a F0 |
| R06 | Complexidade | estruturas propostas sem invariantes explícitas | invariantes da §6.3 como critério (I1–I11 na v13.6); tabelas e SQL como proposta |
| R07 | Aceite | critérios qualitativos | limiares numéricos por fase, válidos no cenário de teste (§8) |
| R08 | Fonte | `sessid` dito não documentado; correção posterior exagerou sobre o produto mobile | §5.2 corrigida nos dois sentidos |
| R09 | Pendência | "consulta Meta falhou" | §4 com o que a referência do Business Discovery documenta |
| R10 | Decisão | Python 3.9 × extra ≥3.10 sem recomendação | recomendação na §5.3, decisão na F0 |
| R11 | Contrato | "`android_ui`/`mobile_api` não são recursos disponíveis hoje" | os nomes já são aceitos como stubs; F0 dá erro claro quando usados como primário |
| R12 | Teste | teste de contrato mocka `_driver` | F3 reescreve a parte B com adapter sem `_driver` |
| R13 | Escopo | invariante de IP exclusivo afrouxada sem limite | colisão de IP observado entre slots ativos pausa o slot mais novo |

---

## 4. Objetivo, dados e limites

### 4.1 Escopo funcional

Adicionar execução **opt-in** com Android em Docker para consultar dados que a conta de teste consegue acessar, preservando os engines web. "Transformar em mobile" significa acrescentar um backend de automação Android, não criar um aplicativo novo para distribuição.

| Informação | Representação interna | Tratamento |
|---|---|---|
| Perfil | ID estável quando disponível, username, nome, bio, contadores, flags | ausente é `null`; nunca inventar zero |
| Relação | alvo, tipo followers/following, ID do membro ou username observado | registrar origem, horário e escopo |
| Paginação | cursor opaco e contexto de validade | ausência pode significar fim, erro ou desconhecido; classificar |
| Resultado | completo, parcial, bloqueado, cancelado ou erro | guardar motivo e evidência; preservar API legada |
| Auditoria | engine, versão, conta pseudonimizada, duração, tráfego, retries | nunca registrar token, senha, cookie ou cabeçalho de autenticação |

Fora de escopo: DM, publicação, likes, follows e unfollows, e coleta de conteúdo inacessível. Posts recentes e métricas adicionais entram apenas com contrato e necessidade definidos.

**API oficial:** a referência do Business Discovery [S23] documenta, para consultas feitas por conta Business/Creator sobre outras contas Business/Creator, campos públicos como `followers_count` e `media_count` e o edge `/media`. **Não documenta edge de lista de seguidores.** Uso previsto: conferir contadores de perfis Business/Creator. Não substitui a coleta de listas.

### 4.2 Critérios de sucesso do projeto

1. A API legada preserva assinaturas, retornos e defaults existentes no commit de base.
2. Reinício do processo reaproveita sessão validada sem abrir formulário desnecessariamente.
3. Android em Docker abre o app, mantém estado e realiza o roteiro mínimo no ambiente testado.
4. Queda técnica não perde página já confirmada; reprocessamento é admitido e medido.
5. Nenhum worker grava após perder sua concessão.
6. Restrição da conta interrompe a operação correspondente e não dispara troca em cascata para insistir na mesma restrição.
7. Custos e resultados parciais ficam visíveis. Nenhuma meta de "indetectável", "zero ban" ou "100% dos seguidores".

### 4.3 Riscos não técnicos

Automação e API privada violam os Termos do Instagram, com risco de banimento das contas. Listas de seguidores são dado pessoal sob a LGPD: base legal, minimização, retenção e anonimização nos prints. A política da DataImpulse quanto a login em redes sociais precisa ser verificada no plano contratado. Onde houver cobertura, preferir a API oficial com contas próprias.

---

## 5. Pesquisa e decisões de arquitetura

### 5.1 Android em Docker

| Opção | Evidência e vantagem | Limite | Decisão proposta |
|---|---|---|---|
| redroid [S5] | Android em container, arm64 e amd64, persistência de `/data` (`-v ~/data:/data`) | depende do kernel do host; exemplo upstream usa `--privileged`; bridge ARM e ABI precisam de validação | primeiro candidato, em host Linux isolado |
| budtmo/docker-android [S13] | emulador com noVNC (`WEB_VNC=true`, porta 6080) e KVM; WSL2 documentado apenas para Windows 11 com `nestedVirtualization` | exige `/dev/kvm`; pesado | alternativa se houver KVM funcional |
| aparelho físico via ADB | controle de comparação para separar falha do app de falha do ambiente | não satisfaz "aparelho em Docker" | plano alternativo explícito, registrado, não conclusão automática |

**Tradução ARM (ponto crítico, fontes em conflito):** o README [S5] afirma "Published redroid images already got libndk_translation included", sem indicar tag, versão ou arquitetura. O #933 [S25] relata que as imagens **x86_64 a partir da versão 15** anunciam `x86_64,arm64-v8a,x86,armeabi-v7a,armeabi` com `ro.dalvik.vm.native.bridge = libnb.so` inexistente, o que faz apps só-ARM instalarem e fecharem ao abrir; a issue não menciona Instagram. Como as fontes não se conciliam, **a presença de tradutor é Hipótese por tag** e é medida no F6a: para cada imagem, registrar por digest `ro.product.cpu.abilist`, `ro.dalvik.vm.native.bridge` e a existência do binário apontado. Os ABIs dos splits do APK também são Hipótese, a inspecionar antes da instalação.

Registrar sempre imagem por digest, kernel, arquitetura, APK e splits com hash, certificado de assinatura e versão. Separar permissões do Docker no host de root dentro do Android. ADB e noVNC ficam em rede privada ou loopback. Não montar o socket do Docker no controlador.

Para a UI, começar por **openatx/uiautomator2** [S14] (3.7.0, 26/06/2026, Python ≥3.8) pela integração Python. **Appium UiAutomator2** [S15] é alternativa quando houver necessidade de infraestrutura WebDriver. São projetos distintos e parâmetros de gestos não são intercambiáveis. GramAddict [S16] serve como referência de organização, não como garantia de compatibilidade nem fonte de limites seguros.

### 5.2 DataImpulse

- **Categorias de produto:** o provedor oferece **quatro categorias distintas** [S4]: "Residential Proxies", "Datacenter Proxies", "Mobile Proxies" e "Premium Residential". Mobile é produto próprio, e compartilhar hostname ou porta com o residencial não elimina essa distinção — a mesma página mostra `gw.dataimpulse.com` na porta 823. **Pendência real:** como o plano contratado seleciona o pool mobile (parâmetro, credencial ou plano), o que não está na documentação técnica consultada. Verificar no painel na F6 e registrar produto, região e ASN observado. Um UA Android não transforma IP residencial em móvel.
- **Conexões** [S1]: `gw.dataimpulse.com:823` (HTTP/HTTPS) e `:824` (SOCKS5) rotativos; portas **10000–20000** para sticky; intervalo de rotação de 1 a 120 minutos, default 30.
- **`sessttl`** [S2]: controla o intervalo, e a mesma página documenta que, se o IP escolhido ficar indisponível, o sistema o substitui automaticamente. Portanto sticky não é imutável.
- **`sessid`** [S21]: `login__cr.au;sessid.123:password@gw.dataimpulse.com:823`; "port is specified in the settings as 824 for Socks5 or 823 for HTTPS"; mantém o IP "for 30 minutes"; "not a replacement for Sticky proxies, but rather an alternative…". **Proposta:** usar porta sticky com `sessttl` e não combinar com `sessid`, salvo teste que demonstre necessidade.
- **Erros 407** [S19]: `NO_USER`, `TRAFFIC_EXHAUSTED`, `THREADS_EXHAUSTED`, `PORT_NOT_ALLOWED` (porta sticky não permitida pelo plano) e `USER_BLOCKED`. O governador mapeia cada uma e nunca as trata como bloqueio do Instagram.
- **Preço:** a página comercial mobile [S4] anuncia US$2/GB. O orçamento usa o valor efetivamente contratado.
- **Bloqueios** [S17]: a lista pública não nomeia Instagram. Isso não equivale a autorização contratual.

**Proposta operacional:** afinidade de sessão por slot com observação do IP real; checkpoint e pausa em mudança inesperada; rotação planejada apenas na fronteira da sessão de trabalho. Não exigir que todo IP pertença a uma conta com exclusividade: detectar compartilhamento e aplicar orçamento agregado por IP observado. Colisão de IP entre slots ativos pausa o slot mais novo (configurável). A saída falha fechada se o túnel cair. Testar TCP, DNS, IPv6 e UDP/QUIC conforme o suporte real; teste de IP num cliente isolado não prova a rota de todos os processos Android. Registrar tráfego medido localmente e consumo faturado, com a diferença explicada.

### 5.3 Backend de API móvel e versões de Python

Avaliar **instagrapi** [S7] como adaptador antes de manter à mão endpoints, assinaturas e headers. Versão 3.0.2 (13/09/2026) com `Requires-Python >=3.10` [S22]; suporte a 3.9 removido na 2.5.0; transporte HTTP/2 via `curl_cffi` na instalação padrão; `load_settings()` seguido de `login()` reutiliza sessão válida. A sessão deve ser persistida no formato suportado, sem supor que cookie de browser é equivalente.

**Decisão aprovada (16/09/2026): Python mínimo 3.12** na nova versão, com quebra de compatibilidade registrada no CHANGELOG. Até `5b0b996`, o InstaT declarava `requires-python >=3.9` e o CI rodava 3.9, 3.10 e 3.12.

Situação das versões segundo o Python Developer's Guide [S41], consultado em 16/09/2026:

| Versão | Estado | Fim de vida |
|---|---|---|
| 3.14 | bugfix | 2030-10 |
| 3.13 | bugfix | 2029-10 |
| 3.12 | **security** ("only security fixes are accepted and no more binaries are released") | 2028-10 |

- O 3.12 está em fase só de segurança; o piso escolhido é válido até 2028-10, mas **não recebe mais binários**.
- instagrapi 3.0.2 declara `Requires-Python >=3.10` e classificadores de 3.10 a 3.14 [S22] — compatível com o piso.
- **3.13 e 3.14 só são declarados (classificadores e matriz de CI) quando houver execução real** de instalação e suíte nessas versões; sem evidência, ficam fora dos metadados.

### 5.4 Identidade, User-Agent, headers e fingerprinting

- **Web:** usar identidade compatível com o navegador em execução. Hoje o Selenium roda Firefox declarando Chrome 89/Android 8 e o `PlaywrightEngine` aplica o mesmo UA **inclusive com `browser_type` firefox ou webkit**. Corrigir os dois antes de acrescentar patches de fingerprint.
- **App Android:** deixar o app gerar UA e headers. Guardar perfil e estado persistentes; não reescrever identificadores a cada execução.
- **API móvel:** a biblioteca versionada gera transporte e headers; os testes distinguem campos estáveis, dinâmicos e específicos de endpoint.
- **Fingerprint spoofing:** experimento separado, com hipótese, versão, comparação e rollback. Não é requisito do MVP se o caminho normal funcionar.
- Locale e timezone são configuração explícita; o país do proxy não determina fuso nem idioma. Não copiar identidade completa entre contas.

Unpinning, extração de chaves e falsificação de telemetria não são requisitos do produto. Se uma investigação exigir instrumentação, delimitar o laboratório e registrar o que ele mede. O app executado nesse laboratório não prova equivalência com outro ambiente.

### 5.5 TLS e HTTP/2

curl_cffi [S11] permite customização por `ja3`, `akamai` e `extra_fp`, traz um exemplo `okhttp4_android10` e declara que "there are still some fields are not covered and can be used to detect you". **Não há target OkHttp pronto**: os alvos móveis são navegadores. JA4 [S12] é um resumo em seções com hashes truncados, logo igualdade é evidência parcial e diferença não prova falha. Preferir primeiro o transporte do adaptador mantido e medir. Um navegador aberto no Android consultando um site de diagnóstico mede o navegador, não o app. ClientHello é observável passivamente; SETTINGS e HEADERS de HTTP/2 não são, por trafegarem dentro do TLS. Definir diferenças toleradas, protocolos e versões, e não bloquear o projeto por igualdade de hashes.

### 5.6 Comportamento, warming e limitação

"Comportamento humano" traduz-se em UI confiável: localizar o elemento atual, aguardar tela estável, tocar dentro do alvo, confirmar efeito e interromper em estado inesperado. Pausas variáveis ajudam a evitar rajadas, mas não provam humanidade. Stories e feed não são obrigatórios: consomem banda e geram efeitos visíveis, como visualizações.

"Session warming" é um piloto gradual de sessão validada, com orçamento baixo e observação. Não prescrever calendário fixo nem promover conta com challenge automaticamente.

O governador precede a coleta real e controla conta, endpoint, IP observado e orçamento global. Usa relógio monotônico para duração, timestamps UTC para persistência, backoff limitado, jitter e `Retry-After` quando aplicável. Não transportar cotas de APIs oficiais para APIs privadas.

| Sinal | Ação |
|---|---|
| Timeout ou 5xx transitório | retry finito e idempotente; persistir progresso; reatribuição por falha técnica |
| 407 do proxy | diagnosticar pela causa documentada [S19]; nunca classificar como bloqueio do Instagram |
| 429 ou limite comunicado | pausar o escopo afetado e reduzir taxa; não multiplicar tentativas por outros slots |
| Challenge ou checkpoint | estado `needs_attention`; parar a automação daquela operação e apresentar diagnóstico |
| Feedback ou restrição | congelar a operação afetada até revisão; não liberar só porque a concessão expirou |
| Sessão inválida | validar estado; recuperação limitada; nunca recarregar o mesmo cookie em laço |
| Página vazia | distinguir lista vazia legítima, fim, falha de parse e resposta de restrição. Há relato de restrição devolvendo **lista vazia sem erro** enquanto o contador do perfil mostra o valor real [S37]: página vazia sem sinal inequívoco de fim é gravada como `suspect`, não avança o cursor e vira sinal ao governador (§6.3, I10) |

---

## 6. Arquitetura e contratos

### 6.1 Componentes

| Camada | Responsabilidade | Reuso e alteração |
|---|---|---|
| `InstaExtractor` / `Profile` | contrato público | preservar API; remover dependência interna de `_driver` para metadados |
| `EngineManager` | capacidades, seleção e erros tipados | separar fallback técnico de restrição de conta |
| Sessões | carregar, validar, renovar e persistir por backend | evoluir `SessionCache`; não misturar cookies web e settings mobile no mesmo schema |
| Scheduler | concessão exclusiva de conta, jobs e cooldown | evoluir `SessionPool`; lógica comum fora de `instat/mobile` |
| Persistência | dados, checkpoints e commits atômicos | evoluir `PersistentStore` com migração e backup |
| Engines | Selenium, Playwright, httpx, Android UI e API móvel opcional | adaptadores com capacidades explícitas |
| Infra mobile | container, volume, ADB, túnel e health checks | um volume Android por slot; isolamento de segredos |
| Diagnóstico | eventos, custo e evidência visual | `DiagnosticCollector` **já existe** em `e25c6a9`; evoluir para `adb screencap` e `uiautomator dump` |

### 6.2 Compatibilidade

- Default continua `engines=['selenium']`. Os nomes `selenium`, `playwright` e `httpx` seguem válidos com o mesmo efeito.
- `mobile_api` e `android_ui` **já são aceitos como stubs** (`extractor.py:305-309`, `mobile/engines.py`). Até existir implementação, usá-los como primário deve falhar com mensagem que cite a fase (F8 e F7), e não com `LoginError` genérico (corrigido na F0).
- **Resultado e histórico (decisão 5):** as visões `run_result` e `job_view` (§6.3.9) entram como **métodos públicos novos e explícitos**; `get_followers()`/`get_following()` continuam retornando `List[str]` com o comportamento atual. A união histórica é sempre rotulada como histórico observado, nunca como lista atual. Nomes definitivos dos métodos: pré-análise da F5.
- Preservar `BaseEngine.extract()` e callbacks legados. Metadados operacionais não mudam o retorno padrão `List[str]`. Capacidades novas seguem o padrão de `get_recent_posts` (`base.py:37`, não abstrato), sem impor método abstrato que quebre subclasses externas.
- O teste de contrato mantém a parte A (assinaturas). A parte B, que injeta `_FakeDriver`, é substituída na F3 por um adapter fake **sem `_driver`**, para atravessar a delegação real.

### 6.3 Persistência e retomada — invariantes

**O critério de correção são as invariantes.** O SQL desta seção é **Proposta validada apenas em modelo isolado** (§6.3.14, [E2], [E3]): modelos SQLite em Python 3.12.10 / SQLite 3.49.1, sem InstaT, Android, Instagram ou proxy.

**Aprovações e seus limites:**
- **Solução C** (16/09/2026) e **direção arquitetural das decisões A–F** (16/09/2026) estão aprovadas pelo Tiago.
- **O SQL não tem certificação antecipada.** A v13.5 falhou em 15 de 17 reproduções; a v13.6 falhou em 10 cenários comportamentais da v13.7 e não implementava outros 14. A F5 porta os cenários de [E3] contra a implementação real.
- A revisão externa desta rodada não abriu a v13.6 nem reproduziu testes: **não é validação independente**.

**Classificação da evidência nesta seção:**
- **Código local:** `5b0b996` não tem tabela de jobs, concessão, reivindicação nem páginas — só `profiles_seen` (`persistent_store.py`). Tudo aqui é **SQL proposto**.
- **Documentado:** SQLite (UNIQUE [S38], backup [S39], WAL [S43]), `sqlite3` do Python [S26], Stripe [S40].
- **Analogia:** Kafka [S31], SQS [S33], Temporal [S34], Stripe [S40] — sustentam regras de posse, reprocessamento e idempotência, não o comportamento do Instagram.
- **Hipótese:** comportamento do Instagram (lista vazia silenciosa, contador, marcador de fim na UI) até F6b, F7 e F8.

#### 6.3.1 Invariantes

| # | Invariante | Mecanismo | Teste obrigatório ([E3]) |
|---|---|---|---|
| I1 | **Exclusividade por conta:** no máximo uma concessão válida por conta | aquisição por `UPDATE` condicional em `BEGIN IMMEDIATE`; `lease_gen` incrementa | C01 (4 processos, 1 vencedor) |
| I2 | **Expiração:** conta de worker morto volta a ser elegível após `lease_until`, e só então | `lease_until` comparado na aquisição | T06, D05 |
| I3 | **Fencing:** sem posse vigente, nada é gravado; posse = conta, proprietário, geração e validade **da execução** | revalidação na transação de escrita | T06, D05 |
| I4 | **Interrupção por estado:** restrição, challenge e cancelamento bloqueiam **nova** operação remota; restrição também bloqueia aquisição, reivindicação e renovação. **Tempo decorrido não libera conta restrita:** só liberação manual com sessão validada | filtros de elegibilidade; verificação antes de cada envio; `release_account` | T05, D03, L04 |
| I5 | **Gravação atômica:** página observada, membros, observações e — **somente se `trusted`** — avanço de posição e cursor vão juntos, ou nada vai; página `suspect` é gravada **sem** avançar | uma transação por página (§6.3.8) | T01, T02, N04 |
| I6 | **Operação mais longa que a concessão:** heartbeat independente com motivo classificado; `lease_ttl` e `busy_timeout` validados | §6.3.4, §6.3.12 | T05, T06; [E1 T06] |
| I7 | **Horário válido:** `:now` calculado após o `BEGIN IMMEDIATE` obter o bloqueio | §6.3.2 | [E1] espera real pelo bloqueio |
| I8 | **Propriedade e reivindicação:** só reivindica quem tem concessão vigente e elegível; só grava a execução corrente; nova execução **automática** só após `lease_lost` ou `technical_error` | §6.3.6 | T03, T04, T12, D04, D05, X02 |
| I9 | **Backup sem bloquear o trabalho:** backup online por conexão própria, **passo único**, com prazo; verificação pela própria cópia; comparação exata com a origem só em janela de manutenção drenada; backup a partir de conexão com transação aberta é recusado | §6.3.13 | B01–B05; teste adicional de inanição |
| I10 | **Confiança por observação e por execução:** confiança é propriedade de uma observação numa página `trusted` de uma execução; completude e membros de uma execução usam **só** essa execução; a união entre execuções é **histórico observado**, nunca lista atual nem prova de completude | `observations` → `pages.run_id` → `runs.job_id`; `run_result`/`job_view` (§6.3.9) | T07, T10, T10b, N01, N02, N03, N07, N09 |
| I11 | **Idempotência por tentativa:** mesma tentativa e mesmo conteúdo canônico → nada gravado, nenhum contador consumido; mesma tentativa com conteúdo diferente ou de outra execução → erro explícito; nova leitura → nova identidade | `pages.attempt_id UNIQUE` + `content_hash` canônico (§6.3.3) | T01, X01, N04, N05, N08, L01 |
| I12 | **Limites sem ciclo automático:** até 2 releituras **adicionais** por posição e 3 execuções por job; esgotar limite, terminar sem prova de fim, cancelar ou sofrer restrição exige **requeue manual**; requeue não ultrapassa o limite de execuções | §6.3.6, §6.3.9 | L01, L02, L03, D03, T12 |

**Garantia de durabilidade:** toda página cujo commit foi **confirmado** permanece gravada, com qualidade e proveniência, após queda do processo; nenhuma página é gravada sem posse vigente no instante do commit. Respostas recebidas e **não confirmadas** podem ser perdidas e são obtidas de novo por nova leitura, contada como reprocessamento [S33].

#### 6.3.2 Fonte de verdade dos estados e cálculo do horário

**Estados (I4).** Um único campo decide elegibilidade: `accounts.auth_state ∈ {ok, needs_attention, restricted}`. Marcar restrição grava `restricted_at`. A conta só volta a `ok` por `release_account`, que exige confirmação explícita de **sessão validada** e grava `auth_validated_at` e `released_by`. Elegibilidade exige `auth_validated_at ≥ restricted_at`. `restricted_until` é informativo: sozinho, nunca libera [E3 L04].

**Cálculo de `:now` (I7).** `BEGIN IMMEDIATE` pode esperar pelo bloqueio de escrita até o `busy_timeout` [S9, S26]. `:now` é lido **depois** de obter o bloqueio e antes de validar concessão, cooldown, limites ou posição. Durações usam relógio monotônico; persistência usa UTC.

#### 6.3.3 Identidades: execução, tentativa, posição e conteúdo canônico

| Conceito | Representação | Por que existe |
|---|---|---|
| **Execução** | `runs.run_id` autoincremento, com conta, proprietário, geração e `cursor_context` | `lease_gen` é por conta; contas com a mesma geração coexistem na mesma posição lógica [E3 T03] |
| **Tentativa** | `pages.attempt_id` único no banco, pertencente a exatamente uma execução | separar repetição do mesmo commit de nova leitura [S40] |
| **Posição lógica** | `(run_id, pos)` ordinal interno + `runs.next_pos` | cursor `NULL` não serve de chave [S38]; em UI, `pos` **não** é posição remota (§6.3.9) |
| **Conteúdo canônico** | `page-v1` + `content_hash` SHA-256 | comparar repetições sem depender de formatação |

**Especificação da tentativa:**
- Uma tentativa é **uma leitura remota**: uma requisição e sua resposta na API, ou uma captura de tela após uma ação na UI.
- `attempt_id` = UUIDv4 gerado **antes** do envio e gravado, junto com a resposta bruta, num **spool local durável** do worker (arquivo escrito atomicamente) **antes** do commit. Após crash, o worker relê o spool e repete o commit com o mesmo `attempt_id` e o mesmo conteúdo [E3 N04].
- Spool perdido = a resposta não é recuperável = **nova leitura** com novo `attempt_id`, contada como reprocessamento.
- Escopo: `attempt_id` já gravado em **outra execução** → erro `attempt_id_de_outra_execucao` [E3 N08].

**Conteúdo canônico `page-v1`:** JSON UTF-8 com chaves ordenadas e separadores compactos, **sem** horários e **sem** qualidade (que depende do estado no commit). Campos:
- `schema`, `run_id`, `pos`, `cursor_in`, `cursor_out`;
- `items`: lista na ordem recebida, cada item `{pk | null, u}`, com username normalizado (NFC, sem espaços nas pontas, minúsculas);
- `page_ok`, `empty_state`, `loading`, `end_marker`, `screen_hash`, `counter`.

**Regras de comparação:**
- mesmo `attempt_id`, mesma execução, mesmo `content_hash` → `duplicate:<qualidade>`; **nada é gravado**: progresso, observações, contador de releituras e contadores de varredura intactos [E3 T01, N04, L01];
- diferença apenas de normalização de username → mesmo hash → `duplicate` [E3 N05];
- mesmo `attempt_id` e hash diferente → erro `attempt_id_reutilizado_com_conteudo_diferente` [E3 X01, N05];
- várias observações por `(run_id, pos)` são permitidas; no máximo uma `trusted` (predicado `pos = next_pos` + índice parcial) [E3 T02, X02];
- nenhuma violação de unicidade é convertida em sucesso.

#### 6.3.4 Motivos de parada e ações permitidas

| Motivo | Detecção | Nova operação remota | Resposta já em voo | Gravação | Nova execução |
|---|---|---|---|---|---|
| `lease_lost` | renovação com 0 linhas **sem** posse; heartbeat sem sucesso há mais de `lease_ttl/2`; commit rejeitado por posse | não | descartada | **nenhuma** | automática, dentro de `max_runs` |
| `account_restricted` / `challenge` | renovação com 0 linhas **com** posse e `auth_state <> 'ok'`; verificação antes do envio; classificador | não | confirmada **como `suspect`** (`account_revoked`) se a posse estiver vigente no commit | só essa página, sem avançar | só após liberação manual da conta (com sessão validada) **e** requeue manual |
| `cancel_requested` | `request_cancel` no job | não | segue a política normal de qualidade | normal | só após requeue manual [E3 D03] |
| `technical_error` | worker (timeout, 5xx, parse, reinício não recuperável) | não nesta execução | resposta com falha não é gravada | nenhuma dessa resposta | automática, dentro de `max_runs` [E3 D04] |
| `reread_limit` | commit rejeitado por limite de releituras | não | — | — | só após requeue manual [E3 L02] |
| `end_confirmed` / `end_unknown` (`no_end_evidence`, `screen_stuck`, `cursor_absent_no_end_signal`) | commit (§6.3.9) | não | — | — | só após requeue manual (atualização ou nova tentativa deliberada) |

- **Revogação antes do envio:** a verificação antes do envio recusa; nada é requisitado nem gravado.
- **Revogação com requisição em voo:** a resposta pode ser confirmada como `suspect` se a posse ainda estiver vigente; a posição não avança; nenhuma nova requisição; o heartbeat classifica `account_restricted` [E3 T05].
- **Worker obsoleto:** conta, proprietário ou geração da execução não conferem → `rejected:posse`, inclusive se a mesma conta readquiriu concessão com geração nova [E3 D05].
- **Reinício do app** não é motivo de parada por si: abre novo segmento na mesma execução (§6.3.9). Só vira `technical_error` se não houver recuperação.

**Renovação com classificação na mesma transação:**

```sql
BEGIN IMMEDIATE;   -- :now calculado depois (I7)
UPDATE accounts SET lease_until = :now + :lease_ttl
 WHERE account = :acct AND lease_owner = :me AND lease_gen = :my_gen
   AND lease_until > :now AND auth_state = 'ok';
-- 1 linha → 'renewed'
-- 0 linhas → na MESMA transação:
SELECT 1 FROM accounts
 WHERE account = :acct AND lease_owner = :me AND lease_gen = :my_gen AND lease_until > :now;
--   existe     → 'account_restricted'
--   não existe → 'lease_lost'
COMMIT;
```

#### 6.3.5 Aquisição e liberação da conta (I1, I2, I4)

```sql
BEGIN IMMEDIATE;   -- pode esperar; só depois calcular :now
UPDATE accounts
   SET lease_owner = :me, lease_until = :now + :lease_ttl, lease_gen = lease_gen + 1
 WHERE account = :acct
   AND auth_state = 'ok'
   AND (restricted_until IS NULL OR restricted_until <= :now)
   AND (restricted_at IS NULL OR (auth_validated_at IS NOT NULL AND auth_validated_at >= restricted_at))
   AND (lease_until IS NULL OR lease_until <= :now)
   AND NOT EXISTS (SELECT 1 FROM endpoint_cooldowns
                    WHERE account = :acct AND endpoint = :endpoint AND cooldown_until > :now);
-- rowcount 1 → concessão obtida; ler lease_gen de volta na MESMA transação
COMMIT;
```

**Liberação manual (MVP):** `release_account(conta, por, sessão_validada, agora)`. Sem `sessão_validada = true` → `rejected:sessao_nao_validada`. A validação positiva da sessão (usuário esperado autenticado, sem tela de challenge) é feita e registrada **antes** da chamada; o registro vai para o histórico operacional.

#### 6.3.6 Reivindicação do job e requeue (I4, I8, I12)

```sql
BEGIN IMMEDIATE;   -- :now calculado depois (I7)

-- 1. solicitante: conta, proprietário, geração, validade e elegibilidade (inclui sessão validada)
SELECT 1 FROM accounts
 WHERE account = :acct AND lease_owner = :me AND lease_gen = :my_gen AND lease_until > :now
   AND auth_state = 'ok'
   AND (restricted_until IS NULL OR restricted_until <= :now)
   AND (restricted_at IS NULL OR (auth_validated_at IS NOT NULL AND auth_validated_at >= restricted_at))
   AND NOT EXISTS (SELECT 1 FROM endpoint_cooldowns
                    WHERE account = :acct AND endpoint = :endpoint AND cooldown_until > :now);
-- nenhuma linha → ROLLBACK 'solicitante_sem_concessao'

-- 2. job: SELECT status, current_run, attempts, requeued_at FROM jobs WHERE job_id = :job
--    status = 'failed'             → ROLLBACK 'job_encerrado'
--    attempts >= :max_runs (3)     → ROLLBACK 'limite_de_execucoes'

-- 3. execução anterior
SELECT r.ended_at, r.stop_reason,
       EXISTS (SELECT 1 FROM accounts a
                WHERE a.account = r.account AND a.lease_owner = r.lease_owner
                  AND a.lease_gen = r.lease_gen AND a.lease_until > :now) AS vigente
  FROM runs r WHERE r.run_id = :current_run;
--    não encerrada e vigente     → ROLLBACK 'job_com_execucao_vigente'
--    não encerrada e não vigente → UPDATE runs SET ended_at = :now, stop_reason = 'lease_lost'
--    stop_reason NOT IN ('lease_lost','technical_error')
--      AND NOT (requeued_at > ended_at) → ROLLBACK 'aguardando_requeue'

-- 4. nova execução
INSERT INTO runs (job_id, account, lease_owner, lease_gen, cursor_context, started_at,
                  segment_started_at, replay_target, replay_done)
VALUES (:job, :acct, :me, :my_gen, :cursor_context, :now, :now,
        :membros_confiaveis_do_job, :replay_target = 0);
UPDATE jobs
   SET current_run = last_insert_rowid(), status = 'running', attempts = attempts + 1,
       cancel_requested_at = NULL, updated_at = :now
 WHERE job_id = :job AND current_run IS :current_run;   -- 0 linhas → ROLLBACK 'concorrencia'
COMMIT;
```

**Requeue manual** `requeue(job, por, agora)`: rejeitado se `attempts ≥ max_runs` [E3 L03], se o job está `failed` ou se há execução ativa; caso contrário grava `requeued_at` e `requeued_by` e põe o job em `pending`. Requeue **não** ultrapassa o limite de execuções; ampliar o limite é decisão manual fora do fluxo, ainda não especificada.

Mudar de execução muda `cursor_context`: a nova execução começa em `pos = 0`, sem presumir portabilidade de cursor.

#### 6.3.7 Verificação antes de cada envio (I4)

Antes de **cada** requisição remota ou ação de UI: posse vigente da execução, execução corrente no job, `auth_state = 'ok'` e ausência de cancelamento. Resultado `ok`, `lease_lost`, `run_not_current`, `account_restricted` ou `cancel_requested`; qualquer valor diferente de `ok` impede o envio. Não substitui a revalidação no commit.

#### 6.3.8 Commit de página — solução C

**Status:** conceito **aprovado pelo Tiago em 16/09/2026**. SQL revisado na v13.7; sem certificação própria.

```sql
BEGIN IMMEDIATE;   -- :now calculado depois (I7)

-- 0. repetição (I11)
SELECT run_id, content_hash, quality FROM pages WHERE attempt_id = :attempt_id;
--    existe, run_id <> :run_id        → ROLLBACK; erro 'attempt_id_de_outra_execucao'
--    existe, mesmo hash               → ROLLBACK; 'duplicate:<quality>' (nada gravado)
--    existe, hash diferente           → ROLLBACK; erro 'attempt_id_reutilizado_com_conteudo_diferente'

-- 1. posse da execução corrente (I3, I8); estado da conta lido, não exigido
SELECT a.auth_state, r.next_pos, r.trusted_cursor, r.progress_state, r.segment, j.progress_kind
  FROM accounts a
  JOIN runs r ON r.account = a.account AND r.lease_owner = a.lease_owner AND r.lease_gen = a.lease_gen
  JOIN jobs j ON j.job_id = r.job_id AND j.current_run = r.run_id
 WHERE a.account = :acct AND a.lease_owner = :me AND a.lease_gen = :my_gen AND a.lease_until > :now
   AND r.run_id = :run_id AND r.ended_at IS NULL;
--    nenhuma linha → ROLLBACK 'posse'; progress_state final → ROLLBACK 'execucao_finalizada'

-- 2. posição (I8, I12)
--    :pos <> next_pos                               → ROLLBACK 'posicao_obsoleta'
--    cursor: trusted_cursor IS NOT :cursor_in        → ROLLBACK 'cursor_divergente'
--    páginas já gravadas em (run_id, pos) > 2        → ROLLBACK 'limite_de_releituras'

-- 3. qualidade por sanity-v1 (§6.3.10) e observação imutável
INSERT INTO pages (attempt_id, run_id, segment, pos, cursor_in, cursor_out, canonical_schema,
                   content_hash, n_items, quality, reason, policy_version, received_at, committed_at)
VALUES (:attempt_id, :run_id, :segment, :pos, :cursor_in, :cursor_out, 'page-v1',
        :content_hash, :n_items, :quality, :reason, 'sanity-v1', :received_at, :now);

-- 4. membros (reconciliação §6.3.11) e observações
INSERT OR IGNORE INTO observations (page_id, member_id, username_seen) VALUES (...);

-- 5. somente se :quality = 'trusted': progresso (§6.3.9); se o estado ficar final,
--    encerrar a execução e atualizar o status do job pelo resultado DESTA execução
UPDATE runs SET next_pos = next_pos + 1, trusted_cursor = :cursor_out, progress_state = :estado, ...
 WHERE run_id = :run_id;
COMMIT;
```

**Regras:**
- Página `suspect` nunca avança posição nem cursor; é gravada com suas observações, para auditoria.
- Membros só contam para uma execução com observação `trusted` **daquela execução** (I10).
- Não há rebaixamento retroativo de páginas confiáveis; F6b, F7 e F8 medem se isso é necessário.

**Base da decisão:** Kafka [S31] (gravar enquanto dono, nunca após perder a posse); Kleppmann [S32] (posse verificada na transação de escrita); SQS [S33] (não confirmado é reprocessado); Temporal [S34] (cancelamento pelo heartbeat); Stripe [S40] (chave de idempotência; parâmetros diferentes → erro); SQLite [S38] (NULL distinto em UNIQUE); relatos [S35–S37] (listas vazias ou incompletas sem erro).

#### 6.3.9 Progresso, fim e resultado

**Estado de progresso por execução:** `not_started` → `in_progress` → `end_confirmed` ou `end_unknown`. Estado final encerra a execução (`ended_at`, `stop_reason`).

**Backend com cursor (`progress_kind = 'cursor'`):**
- página `trusted` com `cursor_out` → `in_progress`;
- **fim exige sinal explícito da API** (`end_marker`, reconhecido pelo classificador) → `end_confirmed`;
- página `trusted` sem `cursor_out` e sem sinal de fim → `end_unknown` (`cursor_absent_no_end_signal`) [E3 T11b]. **Ausência de cursor não prova fim.**

**Backend sem cursor (`progress_kind = 'scan'`: Android UI e Selenium):**
- `pos` é contador **interno** de rodadas; **não garante** que a lista remota ficou igual.
- **Continuidade:** duas telas `trusted` consecutivas no mesmo segmento, com telas diferentes, precisam compartilhar ao menos um membro; sem sobreposição → `continuity_gaps += 1` → a execução **não pode completar** [E3 U03]. Reordenação e remoção com sobreposição não geram lacuna nem duplicata [E3 U02].
- **Tela repetida** (mesmo `screen_hash`) incrementa `stuck_rounds`; `stuck_rounds ≥ S` → `end_unknown` (`screen_stuck`) [E3 T08b].
- **Reinício do app** → novo **segmento** na mesma execução: `segment + 1`, releitura reiniciada, contadores de varredura zerados, **sem consumir execução** [E3 U04]. Cada segmento recomeça do topo da lista.
- **Releitura:** termina quando o segmento reobserva `replay_ratio` dos membros confiáveis do job anteriores ao segmento, **ou** quando `frontier_confirm_screens` telas **consecutivas** trazem membro novo. Uma inserção isolada no trecho já conhecido **não** encerra a releitura [E3 U01].
- **Rodada sem novos:** tela nova, sem indicador de carregamento, sem membro novo para o job, **após** a releitura → `rounds_without_new += 1`.
- **Fim confirmado exige evidência positiva:**
  - `end_marker` reconhecido na tela (Hipótese a verificar na F6b: existência e forma do marcador), **ou**
  - `rounds_without_new ≥ K` **e** contador **exato** compatível com a coleta **desta execução** [E3 U06].
  - `rounds_without_new ≥ K` sem essa evidência → `end_unknown` (`no_end_evidence`) [E3 T08c, U06]. **Falta de novos membros, sozinha, não é fim.**
- Retry exato de uma rodada não conta duas vezes (I11) [E3 T08a].

**Resultado — contrato (decisão 5, aprovada):**

| Visão | Conteúdo | Regra |
|---|---|---|
| `run_result(run_id)` | status (`running`/`complete`/`partial`), motivos, estado, evidência de fim, membros `trusted` **da execução**, `suspect_open`, `continuity_gaps`, verificação do contador, início/fim, `stop_reason` | `complete` exige `end_confirmed`, `suspect_open = 0`, `continuity_gaps = 0` e contador não inconsistente — **tudo calculado só com a execução** |
| `job_view(job_id)` → seleção | `selected_run_id`, `selected_run_status`, `selected_run_at`, `selected_members`, `selected_suspect_open` | execução **mais recente que cumpriu os critérios**; se nenhuma cumpriu, a mais recente, **apresentada como parcial** [E3 N02, N09] |
| `job_view` → última tentativa | `last_attempt_run_id`, `last_attempt_status`, `last_attempt_state`, `last_attempt_stop_reason`, `last_attempt_at` | exibida **separada** da seleção, com data própria [E3 N02] |
| `job_view` → histórico | `history_observed` {username: primeira e última observação confiável}, `history_label` | união das observações `trusted` de todas as execuções do job; rótulo fixo: "histórico observado: não é lista atual nem prova de completude" [E3 N01] |

- Uma execução parcial **não** completa suas lacunas com membros de outras execuções [E3 N09].
- **Ausência em execução posterior não comprova unfollow:** a visão não tem campo de remoção [E3 N01].
- Membro confiável no histórico mas só suspeito na execução atual **não** entra no resultado dessa execução [E3 N03].
- **Contrato público legado intacto:** `get_followers()` e `get_following()` continuam retornando `List[str]` com o comportamento atual. As visões acima entram como **métodos novos e explícitos** (nomes a definir na F5/F7), nunca mudando em silêncio um retorno existente.

**Recuperação de suspeitas:**
- `suspect_total` (histórico do job) nunca diminui; `suspect_open` é da execução.
- Nova leitura `trusted` na mesma `(run_id, pos)` resolve a suspeita; o job pode completar no mesmo job [E3 T02, T11].
- Limites (I12): 2 releituras **adicionais** por posição (3 observações no total); a quarta é rejeitada; duplicata idempotente não consome [E3 L01]. Esgotado → `reread_limit` → requeue manual [E3 L02]. Máximo de 3 execuções por job; a quarta reivindicação e o requeue são rejeitados [E3 L03].
- Challenge ou restrição interrompem **independentemente do saldo** de releituras e execuções.
- **Trilha de auditoria:** `pages`, `runs` e `member_merges` nunca são apagados nem reescritos.

#### 6.3.10 Política de sanidade `sanity-v1`

**Definida aqui e fixada na pré-análise da F5, antes dos testes e da implementação.** Cada página grava `policy_version`. Os valores são **limites operacionais iniciais escolhidos pelo projeto, não documentados pelo Instagram nem calibrados**; a F6b produz os dados e fixtures para calibração, e uma calibração gera `sanity-v2` sem reclassificar páginas antigas em silêncio.

| Parâmetro | Significado | Unidade | Inicial | Calibração |
|---|---|---|---|---|
| `K` | telas `trusted` consecutivas no segmento, após a releitura, com tela diferente e sem carregamento visível, sem membro novo para o job | telas | 3 | F6b: rodadas sem novos antes do fim real no conjunto conhecido |
| `S` | telas `trusted` consecutivas com `screen_hash` idêntico após ação de rolagem | telas | 3 | F6b: frequência de telas repetidas sem travamento |
| `replay_ratio` | fração dos membros confiáveis do job, anteriores ao segmento, que o segmento precisa reobservar | razão em (0, 1] | 0,95 | F6b/F7: estabilidade da lista entre execuções |
| `frontier_confirm_screens` | telas consecutivas com ao menos um membro novo para encerrar a releitura pela fronteira | telas | 2 | F6b: inserções isoladas observadas |
| `counter_tolerance` | tolerância absoluta ao comparar a coleta **da execução** com o contador | membros | max(2, ⌈1% do limite superior⌉) | F6b/F7: diferença entre contador e conjunto conhecido |
| `counter_stale_s` | idade máxima da leitura do contador em relação ao último commit | segundos | 1.800 | F7 |
| `max_rereads_per_pos` | releituras **adicionais** por posição (tentativas distintas) | tentativas | 2 | **aprovado** como limite operacional inicial |
| `max_runs` | execuções totais por job | execuções | 3 | **aprovado** como limite operacional inicial |

**Página `trusted` exige todos os critérios:** posse vigente no commit (senão nada é gravado); `auth_state = 'ok'` (senão `account_revoked`); classificador **reconhece positivamente** a resposta ou a tela — ausência de erro não basta (senão `classifier_not_positive`); itens bem-formados e sem duplicata interna (senão `malformed_items`); página não vazia, salvo `pos = 0` com estado vazio reconhecido e contador exato zero (senão `empty_without_end`).

**Contador exibido** (sinal, nunca prova):

| Estado | Tratamento |
|---|---|
| **ausente** | não verifica (`counter_unavailable`); não habilita fim por contador |
| **exato** | tolerância `counter_tolerance`; é o único estado que pode habilitar fim sem `end_marker` |
| **arredondado** | converter para intervalo `[lo, hi]`; inconsistente só fora do intervalo ± tolerância; **não** habilita fim |
| **desatualizado** | leitura mais velha que `counter_stale_s` → tratado como ausente |
| **lista mutável** | registrar leitura no início e no fim quando possível; diferença entra no relatório |

- **Na ausência de evidência suficiente, o resultado é `end_unknown`/`partial`.**
- **Contador compatível não prova completude.** Precisão e recall só são provados contra conjunto conhecido (F6b, F7).
- Relato #2797 [S37] mantido como relato, com trechos literais registrados na v13.6; nenhuma regra depende exclusivamente dele.

#### 6.3.11 Layout proposto e identidade de membros

| Tabela | Colunas essenciais |
|---|---|
| `accounts` | `account` (PK), `auth_state`, `restricted_until`, `restricted_at`, `auth_validated_at`, `released_by`, `lease_owner`, `lease_until`, `lease_gen` |
| `endpoint_cooldowns` | `account`, `endpoint`, `cooldown_until`, `reason`, PK `(account, endpoint)` |
| `jobs` | `job_id` (PK), `target`, `list_type`, `endpoint`, `progress_kind`, `status`, `end_reason`, `current_run`, `attempts`, `requeued_at`, `requeued_by`, `cancel_requested_at`, `updated_at` |
| `runs` | `run_id` (PK), `job_id`, `account`, `lease_owner`, `lease_gen`, `cursor_context`, `started_at`, `ended_at`, `stop_reason`, `progress_state`, `end_evidence`, `next_pos`, `trusted_cursor`, `segment`, `segment_started_at`, `replay_target`, `replay_done`, `frontier_streak`, `rounds_without_new`, `stuck_rounds`, `continuity_gaps`, `last_screen_hash`, `last_screen_members`, `counter_kind`, `counter_lo`, `counter_hi`, `counter_read_at`, `last_commit_at` |
| `pages` | `page_id` (PK), `attempt_id` (**UNIQUE**), `run_id`, `segment`, `pos`, `cursor_in`, `cursor_out`, `canonical_schema`, `content_hash`, `n_items`, `quality`, `reason`, `policy_version`, `received_at`, `committed_at`; índice parcial `UNIQUE(run_id, pos) WHERE quality = 'trusted'` |
| `members` | `member_id` (PK), `target`, `list_type`, `member_pk`, `username` (normalizado), `first_seen_at`, `last_seen_at`, `identity_state` |
| `observations` | `page_id`, `member_id`, `username_seen` (bruto); PK `(page_id, member_id)` |
| `member_merges` | `merge_id`, `loser_member_id`, `survivor_member_id`, `merged_at`, `reason` |

**Deduplicação de `members`:** índices parciais `UNIQUE(target, list_type, member_pk) WHERE member_pk IS NOT NULL` e `UNIQUE(target, list_type, username) WHERE member_pk IS NULL`, com reconciliação explícita na transação da página:
- **sentido A** (sem `pk`, depois com `pk`): a linha sem `pk` é promovida; observações continuam nela [E3 T10];
- **sentido B** (com `pk`, depois UI sem `pk`): exatamente uma linha com `pk` e aquele username → a observação aponta para ela;
- **fusão:** observações da linha perdedora são **reapontadas** (mantêm `page_id`, portanto execução e job); `first_seen_at` mínimo, `last_seen_at` máximo; fusão registrada em `member_merges` [E3 T10b];
- **fusão não transfere confiança entre execuções:** a confiança de cada observação continua sendo a da sua página; um membro fundido só conta numa execução se houver observação `trusted` **dessa** execução [E3 N07];
- username mutável e associado a mais de um `pk` → linha própria com `identity_state = 'ambiguous'`, sem fusão por suposição.

**Migração.** Copiar `profiles_seen` para `members` com `member_pk = NULL`, com execução e página sintéticas marcadas `suspect` (`legacy_import`), para que dado legado não conte como confiável. Backup antes, em janela de manutenção (§6.3.13).

#### 6.3.12 Heartbeat, validação de tempo e conexões

- **Heartbeat (I6):** tarefa própria a cada `lease_ttl/3`, com classificação (§6.3.4). Última renovação bem-sucedida com mais de `lease_ttl/2` → posse não verificável → `lease_lost`: sem commit.
- **Validação antes de qualquer SQL:** `lease_ttl` finito e positivo, em segundos; `busy_timeout ≤ lease_ttl/6`; timeout de cada requisição remota `< lease_ttl/2`.
- **Conexões:** uma por thread, incluindo heartbeat e backup [S26]. Banco em WAL; escritas com `BEGIN IMMEDIATE`.

#### 6.3.13 Backup e restauração (I9)

**Padrão — backup online, sem bloquear o trabalho:**
1. **Conexão própria**, sem transação aberta. A partir de conexão com transação aberta, o backup é **recusado** com erro imediato (`BackupMisuse`) — na v13.6, `backup()` pela mesma conexão com `BEGIN IMMEDIATE` aberto travou indefinidamente [E2; E3 B04].
2. **Passo único** (`pages = -1`): a cópia é o snapshot "as it was when the copying commenced" [S39]. Em WAL, "readers do not block writers" [S43]: heartbeat e escritores continuam durante a cópia [E3 B01: 9 renovações, 0 falhas, pior latência 11 ms, 67 escritas concorrentes].
3. **Prazo máximo:** estourado → status `timeout`, arquivo `.partial` removido, nada promovido [E3 B02].
4. **Promoção só após verificação:** o destino é escrito como `.partial` e só vira o arquivo final depois da verificação.

**Por que não incremental em produção:** escrita de outra conexão reinicia o backup incremental [S39], e **o próprio heartbeat é um escritor**. Reproduzido [E3, teste adicional pós-implementação]: com **apenas** o heartbeat renovando a cada 50 ms, o backup incremental (1 página por passo) de um banco com 102 páginas **não terminou em 6 s** (2.421 passos); sem escritor, terminou em 0,27 s; em passo único, com heartbeat, terminou imediatamente. Incremental só em janela de manutenção.

**Custo do passo único:** a leitura longa impede o checkpoint do WAL enquanto dura — "a long-running read transaction can prevent a checkpointer from making progress" [S43]. Em bancos grandes, medir a duração e agendar fora de pico; o prazo máximo limita o impacto.

**Verificação do backup online — ponto de consistência = a própria cópia:**
- `PRAGMA integrity_check = ok` e `PRAGMA foreign_key_check` vazio;
- invariantes internas: no máximo uma página `trusted` por `(run_id, pos)`; `runs.next_pos` igual ao número de páginas `trusted` da execução;
- marcador registrado **da cópia**: `max(run_id)`, `max(page_id)`, `max(committed_at)`, total de páginas;
- **não** comparar com contagens da origem, que continuou mudando [E3 B05: origem com 54 linhas de carga, cópia com 18, verificação correta].

**Comparação exata com a origem — só em janela de manutenção:**
- pré-condição: **nenhuma concessão vigente e nenhuma execução aberta com posse** — senão `rejected:nao_drenado`;
- backup em passo único e digest por tabela (linhas ordenadas por `rowid`) da origem e da cópia; iguais → `digest_equal = true` [E3 B03].

**Restauração:** restaurar a cópia num caminho separado e repetir a verificação antes de usá-la. Migração de schema só com backup verificado em janela de manutenção.

#### 6.3.14 Evidência de validação do SQL [E2, E3]

| Rodada | Horário (16/09/2026) | Modelo | Resultado |
|---|---|---|---|
| E2 vermelha (v13.6 inexistente) | 15:07:59 | v13.5 literal | 1/15 — **v13.6: módulo ausente, não falha por cenário** |
| E2 final | 15:14:25 | v13.5 / v13.6 | 1/15 / 15/15 |
| E3 vermelha 1 (testes novos) | 15:54:54 | v13.6 | 19 PASS, 10 FAIL, 14 API_AUSENTE |
| E3 vermelha 2 (após corrigir **bugs dos testes**, sem mudar expectativas) | 15:57:44 | v13.6 | 19 PASS, 10 FAIL, 14 API_AUSENTE |
| E3 verde | 16:00:06 | v13.7 | **43 PASS, 0 FAIL, 0 API_AUSENTE** |
| E3 adicional, pós-implementação (não TDD) | 16:00:43 | v13.7 | inanição do backup incremental confirmada |

**Leitura crítica:**
- Falhas comportamentais reais contra a v13.6: T08c, T11b, N05, N08, U01, U06, L02, L03, e N01/N09 (reivindicação da segunda execução recusada porque a v13.6 não encerra execuções).
- API_AUSENTE (14) significa método ou coluna inexistente na v13.6, não falha comportamental.
- Correções feitas nos testes **antes** do modelo v13.7 e **sem mudar expectativas**: conexão do heartbeat criada na própria thread; carga do backup renovando a concessão; horários de drenagem em B03; D03 com concessão nova para isolar a falta de requeue; N01/N09 conferindo o claim.
- B02 passou por **timeout**, não por conclusão: prova que o heartbeat não bloqueia e que o prazo é explícito, não que backup lento conclui.
- Modelo e testes foram escritos pelo mesmo auditor; a F5 porta os 43 cenários contra a implementação real.
### 6.4 Segurança operacional e custo

Sessões, volumes Android, dumps e capturas têm retenção e permissões próprias; `.gitignore` sozinho não protege backups nem artefatos de CI. Remover segredos antes de gerar evidência publicável. Usar contas de teste autorizadas e dados mínimos.

**Achado local (16/09/2026):** os pacotes de diagnóstico em `instat/logs/diagnostics/` guardam `cookies.json` em texto puro, e dois deles contêm `sessionid` de `.instagram.com` (§2.2). Estão fora do git, mas qualquer cópia da pasta leva a sessão junto. **Proposta para a F1:** diagnóstico nunca grava valores de cookies de autenticação (só nomes e presença), e os arquivos existentes são revisados e removidos pelo Tiago — decisão aberta, nada foi apagado.

`custo_total = tráfego_faturado_GB × preço_contratado + infraestrutura + manutenção`. Reportar custo por mil **registros únicos válidos**, com falhas e warming incluídos no numerador. Imagens e vídeos do app contam como custo; medir vantagem antes de optar por caminho híbrido.

---

## 7. Ritual obrigatório — os sete passos

Origem: memória `ritual-de-fase-obrigatorio` (definido pelo Tiago em 12/08/2026, atualizado em 21/08/2026). Exigências do original, todas mantidas: análise medida e não suposta; pesquisa na internet, nunca pulada por parecer simples; pré-análise com o desenho antes do código e **o caminho do print resolvido aqui, nunca no passo 7**; TDD antes do código; execução; testes; prints com **leitura de cada imagem**; e um `.md` de histórico no repositório, atualizado a cada fase. Nunca reportar fase concluída sem ter lido os prints.

O ritual nasceu em outro projeto. Aqui valem apenas os sete passos e as regras acima, **sem nenhuma referência de domínio daquele projeto**.

| Ordem | Etapa | Evidência e condição |
|---|---|---|
| 1 | Análise | fato observado, métrica e reprodução. Relato é identificado como relato; hipótese não vira medição |
| 2 | Pesquisa na internet e GitHub | documentação dos mantenedores e comunidade técnica pertinente (Instagram, Android em container, proxies, TLS), com data e versão; quando houver fluxo de instalação ou uso, considerar a facilidade para quem é novo na biblioteca. Obrigatória em toda fase |
| 3 | Pré-análise | desenho, contratos, critérios numéricos e caminho do print: quem autentica, com qual conta de teste, em qual slot ou ambiente, sobre qual alvo, e se o estado precisa ser preparado. Resolver o acesso antes de codar |
| 4 | TDD | testes do comportamento novo escritos antes da implementação, com log vermelho pelo motivo correto e horário anterior ao commit de implementação. Regressão existente permanece verde; não introduzir defeito artificial |
| 5 | Execução | implementação delimitada; registrar arquivos e mudanças de contrato |
| 6 | Testes | resultados reais de unitários, integração, regressão e ambiente pertinente; reportar falhas, skips e limitações |
| 7 | Prints e leitura de CADA imagem | abrir cada imagem gerada e registrar, uma linha por imagem, o que foi visto, o problema ou melhoria, e a ação. Capturar sem olhar não conclui a fase |

**Autorizações (equivalente ao "escrita em produção" do original, sem interromper o trabalho repetidamente):**
1. Manter em `docs/HISTORICO_MOBILE.md` um **registro de autorizações vigentes**: escopo, contas, teto de tráfego e custo, data e referência. A F0 transcreve os avais já existentes, inclusive o registrado na v10 para contas de teste dedicadas.
2. Na pré-análise, a fase **cita a autorização vigente** que cobre o que precisa. Se estiver coberta, prossegue sem novo pedido.
3. Pedir aval novo, junto com o desenho, apenas quando a fase **exceder** o escopo registrado: conta não listada, orçamento acima do teto, ação visível no Instagram além de leitura, ou escrita em dados de produção.

### 7.1 Conta de teste e credenciais locais — uso sem bloquear a conta

**Fonte única das credenciais:** `C:\Projetos\instat_env.txt` — arquivo **local, fora do repositório**, no formato `CHAVE=VALOR` (uma por linha; `#` para comentário). Quem o lê é `tests/_env_loader.py`:
- `load_env()` lê o arquivo (ou o caminho em `INSTAT_ENV_FILE`), exige as chaves obrigatórias e as injeta em `os.environ`; mensagens de erro citam **só nomes** de chaves, nunca valores;
- `load_imap_config()` monta a configuração IMAP a partir das chaves opcionais.

| Chave | Obrigatória | Conteúdo |
|---|---|---|
| `INSTAT_USERNAME` | sim | usuário da conta de teste |
| `INSTAT_PASSWORD` | sim | senha da conta de teste |
| `INSTAT_PROFILE_ID` | sim | perfil-alvo controlado usado nos testes |
| `INSTAT_IMAP_HOST`, `INSTAT_IMAP_USER` | não | caixa de e-mail que recebe o código de verificação |
| `INSTAT_IMAP_PASSWORD_FILE` ou `INSTAT_IMAP_PASSWORD` | não | caminho de um arquivo com a senha de app do IMAP (preferível) ou a própria senha |

**Por que os valores não estão neste documento:** este roadmap é versionado e publicado. Segredos não entram em código, documentação nem controle de versão [S47], e a regra prática é que o código "could be made open source at any moment, without compromising any credentials" [S48]. O documento aponta para o arquivo; os valores ficam só nele.

**Conferir o arquivo sem expor valores** (PowerShell):

```powershell
$p = if ($env:INSTAT_ENV_FILE) { $env:INSTAT_ENV_FILE } else { 'C:\Projetos\instat_env.txt' }
if (-not (Test-Path $p)) { "arquivo ausente: $p" } else {
  $keys = Get-Content $p | Where-Object { $_ -match '^\s*[A-Z_]+\s*=' } | ForEach-Object { ($_ -split '=', 2)[0].Trim() }
  foreach ($k in 'INSTAT_USERNAME','INSTAT_PASSWORD','INSTAT_PROFILE_ID') { "{0}: {1}" -f $k, $(if ($keys -contains $k) { 'presente' } else { 'AUSENTE' }) }
}
```

**Regras para não bloquear a conta de teste** (obrigatórias em toda fase que use a conta real):

1. **Nunca expor valores:** nenhum valor do arquivo em `.md`, commit, PR, issue, log, print, saída de teste ou conversa. Logs e diagnósticos registram só nomes de chaves e presença. Se um valor vazar, **trocar a senha e encerrar as sessões da conta** antes de qualquer novo teste [S47].
2. **Teste com conta real é opt-in e nunca roda no CI:** marcador `real` (a registrar na F1), excluído por padrão (`-m "not e2e and not mobile and not real"`), executado só com `INSTAT_REAL_TESTS=1` **e** o arquivo presente; sem as duas condições, o teste é pulado com motivo explícito.
3. **Reutilizar sessão antes de qualquer login:** carregar a sessão salva e validá-la com uma chamada leve; login por formulário só se a sessão for comprovadamente inválida. A documentação do instagrapi resume o risco: "If you call `.login()` from scratch on every run, Instagram sees repeated fresh logins. That is much more suspicious than reusing a stable device session" [S49].
4. **No máximo uma tentativa de login por formulário por execução.** Falhou → a execução para. **Sem nova tentativa automática.**
5. **Challenge, checkpoint, conta suspensa ou "confirme que é você":** parar toda automação da conta, marcar `needs_attention` e resolver **manualmente no app oficial**. A conta só volta ao uso por liberação manual com sessão validada (I4, §6.3.5); tempo decorrido não libera. Resolver challenge automaticamente só com tratadores comprovados [S49].
6. **Uma execução por vez com a conta:** um worker, uma concessão (I1). Nunca rodar dois scripts, duas máquinas ou dois testes reais com a mesma conta ao mesmo tempo.
7. **Mesmo ambiente por conta:** mesmo IP/proxy, mesmo navegador ou dispositivo e mesmo idioma/região; não trocar proxy no meio de uma sessão ("Keep one stable proxy/IP per account whenever possible" [S49]).
8. **Ritmo baixo e pausas variáveis** entre requisições, com o menor volume que responda à pergunta do teste. Os valores são **limites operacionais do projeto**, não limites publicados pelo Instagram.
9. **Intervalo entre execuções com login por formulário:** proposta inicial de **no mínimo 24 h**; limite operacional do projeto, sem fonte do Instagram, a revisar com dados da F1/F6b.
10. **Estado atual da conta antes do primeiro uso real:** há sinais locais de restrição anterior — um comentário em `_real_features_smoke.py` descreve a conta como "currently-flagged" e `_real_run.log` (24/04/2026) terminou em `LoginError`. Antes de qualquer teste real, confirmar **manualmente no app oficial** que a conta entra sem challenge pendente, e registrar a data dessa verificação no histórico.
11. **Autorização:** a conta está coberta pelo aval de contas de teste registrado na v10, **sem teto de tráfego nem limite de ações registrado**. Toda pré-análise que use a conta real cita esse aval e registra o teto da fase; testes com proxy pago continuam exigindo teto aprovado.
12. **Artefatos locais:** diagnósticos antigos em `instat/logs/diagnostics/` contêm cookies de sessão em texto puro (§6.4). Não copiar, anexar nem publicar essa pasta.

**Fases de backend:** a imagem mostra resultado real inspecionável, como execução controlada, timeline ou diagnóstico. Gráfico decorativo não substitui log nem teste. Faltando ambiente para o passo 7, o status é `entregue, passo 7 pendente`.

**Desvios anteriores:** registrar honestamente o código feito antes do ritual e os testes já existentes. Não fabricar log vermelho nem screenshot retroativo. Este documento é entrega documental e não certifica execução de nenhuma fase.

**Status possíveis:** `não iniciada`, `em andamento (passo N)`, `bloqueada (motivo)`, `entregue, passo 7 pendente`, `concluída`. Nenhuma fase recebe `concluída` sem evidência dos sete passos.

---

## 8. Fases

**Sequência aprovada (16/09/2026):** `F0 ∥ (F6a → F6b) → F1 → F2 → F5 → F3 → F6 → F7`; **F4** independente após F5; **F8** e **F9** condicionadas a resultados e necessidade demonstrada; **F10** e **F11** pelos gates anteriores.

- `∥` = em paralelo. F6a e F6b não dependem da F0: usam código descartável fora do pacote. A F6b depende do F6a aprovado.
- Posição da F6b aprovada em 16/09/2026; ordem completa aprovada na rodada de decisões de 16/09/2026.
- **Ponto a esclarecer (registrado, não resolvido por suposição):** a ordem aprovada coloca F1 depois de F6b. Se F6a/F6b ficarem bloqueados por ambiente ou decisão, esta versão **não** inicia F1 por conta própria; a F1 aguarda confirmação do Tiago se pode avançar em paralelo, já que serve também aos engines web.

- Os IDs vêm da revisão v11, preservados para rastreabilidade. Mudanças desta v13: **F5 antes de F4** (o fencing testado na F4 depende da transação da F5); **F3 depois da F4** (desacoplar `get_profile` só é pré-requisito da F7); e as novas **F6a** e **F6b**.
- **MVP Android (aprovado 16/09/2026):** um slot, uma conta ativa, um worker de coleta. A F5 ainda implementa e testa I1–I12 com múltiplos processos. **Gate da F6b:** se falhar, F6 e F7 não começam sem decisão registrada.
- **F4 (paralelismo web)** é entrega independente após a F5 e **não bloqueia** o MVP Android.
- **F8 e F9** só começam com necessidade demonstrada pelos resultados da F7 (ou pelo fracasso documentado do caminho UI); podem terminar decidindo não adotar.
- Os critérios numéricos valem **nos ambientes e cenários de teste descritos**. Não são garantia de funcionamento em produção nem contra mudanças do Instagram.
- Toda fase segue os sete passos da §7 e produz `docs/phase-evidence/fase-N/README.md` mais atualização de `docs/HISTORICO_MOBILE.md`.

### F0 — Reconciliar base, empacotamento e contrato

1. **Análise:** registrar o SHA completo de base `5b0b996f7c052367a3b0576e29a74886e3340a0c` e os demais da §2.1; listar commits locais não publicados; **reproduzir** os itens relatados pelo auditor (suíte offline, wheel sem `instat.mobile`, `engines=["android_ui","selenium"]` resultando em `LoginError`) — o que não se reproduzir volta a Hipótese; inventariar testes e separar baseline offline de telemetria real inexistente; transcrever as autorizações vigentes.
2. **Pesquisa:** `setuptools` com `packages.find` versus lista explícita; política de versões de Python e fim de suporte do 3.9; `Requires-Python` do instagrapi [S22]; documentação do próprio InstaT (README, ARCHITECTURE, USAGE) e alternativas Android [S5, S13].
3. **Pré-análise:** decidir publicação das branches e versão mínima de Python (ambas pendentes do Tiago); definir campos necessários, matriz de Python e critérios de aceite; caminho do print: saída do teste de wheel e relatório de baseline local, **sem conta real e sem tráfego pago**, portanto sem necessidade de novo aval.
4. **TDD:** (a) teste que constrói o wheel, instala num venv temporário fora do checkout e importa `instat.mobile.engines` — vermelho hoje; (b) teste que usa stub mobile como primário e espera erro citando a fase — vermelho hoje; (c) caracterização das assinaturas públicas, que permanece verde; não forçar regressão existente a falhar.
5. **Execução:** corrigir `packages` no `pyproject.toml`; mensagem explícita dos stubs; criar `docs/HISTORICO_MOBILE.md` com histórico, autorizações e manifesto de evidências; fixar as decisões de arquitetura deste documento.
6. **Testes:** suíte base no ambiente declarado, em todas as versões da matriz decidida; construir e instalar o wheel; capturar resultados, dependências ausentes e skips, sem alegar execução de CI remoto.
7. **Prints:** `01-contratos.png` (resultado dos testes de contrato), `02-baseline-offline.png` (suíte e ambiente), `03-wheel.png` (conteúdo do wheel instalado). Abrir e ler cada imagem, registrando divergências.

**Aceite:** referência de código inequívoca; o wheel contém 100% dos subpacotes de `instat/`, conferido por comparação automática com a árvore; testes (a) e (b) verdes no CI; suíte offline sem falha na matriz decidida; contrato caracterizado; baseline honesto.

### F6a — Spike de viabilidade Android, sem conta Instagram

1. **Análise:** inventariar host (kernel, arquitetura, binder, KVM, memória); medir tempo de boot e consumo; nenhuma conta, nenhum proxy pago.
2. **Pesquisa:** README do redroid [S5] e o conflito com o #933 [S25]; budtmo [S13]; ABIs dos splits do APK; `uiautomator2` [S14].
3. **Pré-análise:** matriz mínima em host x86_64 de duas tags oficiais, uma anterior à 15 e outra a partir da 15, ambas registradas por digest, contra um APK com hash e ABIs registrados; para cada tag, coletar `ro.product.cpu.abilist`, `ro.dalvik.vm.native.bridge` e a existência do binário apontado; compose com versões fixas, volume por slot, ADB em loopback; caminho do print: `adb exec-out screencap -p`, sem conta e sem aval adicional.
4. **TDD:** testes com marcador `mobile`, vermelhos antes do provisionamento: o slot aparece em `adb devices` **no estado `device`** — `offline` e `unauthorized` falham, porque aparecer na lista não basta (relatos recorrentes de ADB offline no redroid [S29, S30]); o pacote aparece em `pm list packages`; `pidof com.instagram.android` retorna o **mesmo PID** ao longo de 60 s após abrir (PID diferente indica crash e reinício); logcat sem `ANR` nem `FATAL EXCEPTION` do pacote; as propriedades de ABI e bridge são coletadas e registradas. Cenários separados para cold boot e para `docker restart`.
5. **Execução:** provisionar container e volume; instalar o APK verificado por hash; registrar incompatibilidades; nenhum login.
6. **Testes:** suíte `mobile` local; registrar logcat de qualquer crash, com a tag e o digest usados.
7. **Prints:** `01-boot.png`, `02-app-aberto.png`, `03-abilist.png`, `04-apos-restart.png`. Ler cada imagem, procurando idioma incorreto, aviso de dispositivo não suportado e layout cortado.

**Aceite:** em pelo menos uma tag, com ADB em estado `device`, o app abre até a tela de login e continua vivo por 60 s ou mais, com o mesmo PID e sem ANR ou exceção fatal no logcat, em 5 de 5 aberturas, tanto após cold boot quanto após `docker restart`; e `/data` persiste após o restart (app instalado e preferência de idioma mantidos). Se nenhuma das duas tags passar, a fase fica `bloqueada`, com logcat anexado, e a decisão entre budtmo e aparelho físico acontece antes de as demais fases consumirem esforço mobile.

### F6b — Fatia vertical Android: sessão manual, lista conhecida e extração pela UI (spike)

**Status da decisão:** inclusão e posição aprovadas pelo Tiago em 16/09/2026. Limiares numéricos: proposta, a confirmar no passo 3.

**Pergunta da fase.** No ambiente aprovado no F6a: (a) uma sessão autenticada **manualmente** permanece válida após reinícios? (b) a interface do app expõe a lista de seguidores de forma legível? (c) é possível extrair essa lista com precisão e completude medidas contra um conjunto conhecido, inclusive após interrupção?

**Separação de objetivos.** Esta fase trata só de **simular o aparelho** (já provado no F6a) e de **abrir, rolar e ler a interface**. Não testa nem afirma indistinguibilidade humana, nem atestação de integridade. Executar o app oficial não torna o container um aparelho certificado, e um proxy muda só a saída de rede.

**Escopo.** Código descartável em `spikes/f6b/`, fora do pacote e não importado por `instat`. Sem governador, persistência transacional ou contrato público (isso é F1–F7). Somente leitura: nenhum follow, like, DM ou visualização deliberada de stories. Dependência: F6a aprovado (tag, digest e APK registrados). Independente da F0.

1. **Análise:** registrar tag e digest aprovados no F6a e a versão do APK; formular as perguntas (a), (b) e (c) como hipóteses; definir a conta de teste que fará login e a **conta-alvo controlada** cuja lista de seguidores será o conjunto conhecido, com N entre 20 e 100; exportar e **congelar** esse conjunto com data e hora antes do teste, e não alterar seguidores da conta-alvo durante a fase.
2. **Pesquisa:** documentação do UI Automator e do comando `uiautomator dump`; Appium UiAutomator2 [S15] e openatx/uiautomator2 [S14] como consumidores da mesma hierarquia de acessibilidade; limites conhecidos de listas virtualizadas (só itens visíveis na hierarquia); relatos sobre a persistência de `/data` no redroid [S5]. Registrar o que cada fonte sustenta e o que não comprova.
3. **Pré-análise:**
   - **Saída de rede, decidida antes do login.** Proposta: conexão residencial do próprio host. **Proibido IP de datacenter sem proxy móvel.** Se o host disponível for uma VPS, a F6b aguarda a saída mínima da F6 (túnel sticky com kill switch) e isso é registrado.
   - **Autorização.** Citar a autorização vigente para contas de teste (§7); se não cobrir, pedir aval junto com este desenho. Nenhuma conta valiosa.
   - **Credenciais e proteção da conta.** Usar a conta definida em `C:\Projetos\instat_env.txt` e seguir **todas** as regras de §7.1: login manual uma única vez, sessão reaproveitada nos reinícios, parada imediata em challenge, uma execução por vez, nenhum valor exposto em prints ou logs.
   - **Login manual.** Um humano digita as credenciais pela tela espelhada (scrcpy ou noVNC) e resolve um eventual challenge **uma vez**. O script nunca digita credenciais. Challenge repetido: parar e registrar.
   - **Dados de terceiros.** Proposta: formar o conjunto conhecido com **contas de teste do próprio Tiago** seguindo a conta-alvo, para que prints e dumps não contenham dados de terceiros. Se não for possível, anonimizar antes de qualquer commit.
   - **Método de extração, sem framework no início.** Laço: `adb shell uiautomator dump` → ler o XML → rolar (`adb shell input swipe`) → aguardar tela estável (dois dumps consecutivos com hierarquia igual, ou timeout) → repetir. Appium ou uiautomator2 só entram se o método bruto se mostrar insuficiente.
   - **Classificação de nós.** Quais nós correspondem a seguidores e quais a outros elementos (hipótese: sugestões de contas, cabeçalhos, busca) é **decidido a partir dos primeiros dumps observados**, não escrito antes. Nenhum seletor é pré-definido.
   - **Deduplicação** por username, porque a UI provavelmente não expõe `pk` (hipótese; sentido B da §6.3).
   - **Critério de fim de lista.** K rodadas consecutivas sem username novo (K inicial = 3, configurável), registrando se havia indicador de carregamento visível. O teste mede quantas vezes esse critério para antes do fim real.
   - **Retomada no spike.** Interromper o processo e reiniciar: reabrir o app, renavegar até a lista e continuar, reaproveitando os usernames já gravados num JSON simples. Isso mede o custo de retomada de navegação, **não** persistência atômica (F5).
   - **Caminho do print:** `adb exec-out screencap -p`, com a conta de teste e a conta-alvo definidas acima; nomes na tela pertencem a contas de teste do Tiago, ou são anonimizados.
   - **Critérios de aceite** definidos abaixo, antes de qualquer código.
4. **TDD** (em duas etapas, porque parte das fixtures só existe depois da observação):
   - **4a, antes de qualquer execução contra o Instagram:** testes vermelhos para o que não depende do app — cálculo de precisão, recall e duplicatas contra o conjunto conhecido; lógica do critério de fim de lista com sequências sintéticas, inclusive atraso de carregamento que simula falso fim; detecção de tela estável com sequências sintéticas de dumps.
   - **4b, depois do primeiro dump real:** capturar manualmente um dump da lista (observação do passo 1, sem código de extração), salvá-lo como fixture sanitizada e escrever testes vermelhos do classificador de nós **antes** de escrevê-lo. Registrar os horários para comprovar a ordem.
5. **Execução:** provisionar conforme F6a; login manual; implementar o script do spike (laço dump-rolagem-espera, classificador, deduplicação, critério de fim, registro de métricas); executar as rodadas do passo 6.
6. **Testes:**
   - **Sessão:** após o login manual, 3 reinícios (`docker restart`) com app reaberto, verificando se continua logado; uma verificação adicional com 24 h ou mais de idade da sessão.
   - **Extração:** 5 execuções completas sobre o conjunto conhecido. Por execução: precisão, recall, duplicatas removidas, rodadas de rolagem, paradas antes do fim real, tempo total, erros e estados inesperados.
   - **Interrupção:** 3 execuções encerradas perto da metade da lista e retomadas, medindo recall final e rodadas extras.
   - **Parada:** qualquer challenge, aviso de erro ou tela inesperada interrompe a execução e é registrado com dump e screenshot; nunca repetir automaticamente.
   - **Consumo:** registrar tráfego de rede do container (ex.: `docker stats`) por execução, como referência de custo para a F6.
   - **Dados para `sanity-v1`** (§6.3.10): formato do contador exibido (exato, arredondado e a partir de que valor), se muda durante a execução, frequência de telas repetidas e de rodadas sem membro novo antes do fim real, e se há estado vazio reconhecível. Esses dados calibram `K`, `S` e `replay_ratio`; sem eles os valores continuam provisórios.
7. **Prints:** `01-login-manual.png`, `02-sessao-apos-restart.png`, `03-perfil-alvo.png`, `04-lista-inicio.png`, `05-lista-fim.png`, `06-elementos-nao-seguidores.png` (se existirem), `07-retomada.png`, `08-metricas.png` (renderizado dos resultados). Ler cada imagem procurando idioma incorreto, sobreposições (teclado, banners, diálogos), seções que não são seguidores, texto truncado e sinais de challenge.

**Aceite (proposta, a confirmar no passo 3):**
- **Sessão:** 3 de 3 reinícios sem pedido de login; nenhum challenge após o login inicial durante a fase; sessão válida na verificação de 24 h.
- **Extração:** em 5 de 5 execuções, precisão = 100% (nenhum item retornado fora do conjunto conhecido) e recall ≥ 95%.
- **Fim de lista:** nenhuma das 5 execuções para com recall abaixo de 95% por falso fim.
- **Retomada:** 3 de 3 interrupções terminam com recall ≥ 95%.
- Tempo e tráfego registrados, sem meta.

**Gate:**
- **Passa:** seguir a sequência. Os dumps e fixtures da F6b viram insumo da F7, que transforma o spike em engine com governador, persistência e contrato público.
- **Falha — registrar a causa e decidir antes de F6/F7:**
  - *Sessão não persiste ou challenge repetido:* repetir o mesmo roteiro num aparelho físico via ADB, para separar problema do ambiente de problema da conta.
  - *Hierarquia sem usernames legíveis:* decidir entre OCR (com precisão medida no mesmo conjunto), API móvel (F8) ou encerrar o caminho UI.
  - *Fim de lista não confiável:* um ajuste do critério e nova rodada; persistindo, registrar como limitação e decidir.
- Em qualquer falha, F0, F1, F2, F5 e F4 podem continuar, porque também servem aos engines web.

### F1 — Sessão persistente confiável e identidade web

1. **Análise:** medir acertos e erros de cache, rejeição por idade, diretório efetivo, autenticação falsa positiva e quantidade de logins de formulário; reproduzir o UA incompatível nos três `browser_type` do Playwright e no Selenium.
2. **Pesquisa:** persistência no Selenium e no Playwright [S18]; práticas do adaptador móvel [S7]; políticas de validade de sessão, sem supor sessão eterna.
3. **Pré-análise:** esquema versionado por backend, caminho estável, escrita atômica, validação positiva do usuário esperado e migração; caminho do print: fake server com sessão válida, expirada e em challenge, sem conta real.
4. **TDD:** casos já reproduzidos [E1, T01; §2.2] — JSON truncado, `saved_at` ausente, `saved_at` não numérico, cookies de tipo errado, `saved_at` no futuro, e fronteira de TTL conforme a política decidida (proposta: expirar com `idade >= max_age`); leitura inválida vira *cache miss*, nunca exceção. Restauração [E1, T02; §2.2]: `/challenge/`, `/checkpoint/`, `/accounts/suspended/` e `/auth_platform/` **não** podem retornar sucesso; e o caminho de restauração **deve passar pelo `BlockDetector`**, hoje ignorado porque `login()` retorna antes. Também: cache além de uma hora ainda sujeito a validação; sessão revogada; identidade errada; concorrência de gravação; interrupção durante `save()`. Falhas reproduzidas antes do código. Validade do *arquivo* e validade da *sessão* são testadas separadamente: um cache íntegro não prova autenticação.
5. **Execução:** evoluir cache e restauração existentes; executar a detecção de bloqueio também no caminho de restauração; validação positiva do usuário esperado; corrigir o UA incompatível; registrar o motivo de cada decisão de sessão.
6. **Testes:** processo novo reutiliza sessão válida no fake server; sessão inválida não produz sucesso; recuperação tem limite; regressão dos engines web.
7. **Prints:** `01-restaurada.png`, `02-expirada.png`, `03-challenge.png`. Ler estado e mensagens de diagnóstico em cada uma.

**Aceite:** zero autenticações falsas positivas nos cenários controlados; zero aberturas de formulário em 10 reinícios com sessão válida confirmada; UA coerente com o browser em execução nos três `browser_type` e no Selenium. Melhoria em contas reais só é afirmada após piloto.

### F2 — Governador de erros e orçamento, antes da coleta

1. **Análise:** mapear retries, laços de fallback, sinais de restrição e ausência de orçamento nos fluxos atuais.
2. **Pesquisa:** políticas de erro documentadas e práticas de backoff; distinguir erro do serviço Instagram de erro do proxy [S7, S19].
3. **Pré-análise:** máquina de estados e orçamentos comuns, com chave `(conta, operação)`; a chave por IP observado entra na F6 e F10; caminho do print: timeline de respostas do fake server com relógio controlado.
4. **TDD:** 429 com e sem `Retry-After`; as cinco causas de 407 [S19]; 5xx; challenge; cancelamento; teto de bytes; nenhuma troca automática de conta para insistir numa restrição.
5. **Execução:** integrar classificação e pausa aos engines e ao orquestrador; configurar tetos e motivo terminal.
6. **Testes:** provar retries finitos, interrupção e preservação de parciais, sem introduzir esperas reais em testes unitários.
7. **Prints:** `01-timeline.png`, `02-orcamento.png`, `03-alerta.png`. Ler horários, estados e mensagens.

**Aceite:** toda tentativa passa por orçamento e classificação; para cada sinal da tabela da §5.6, as tentativas ficam dentro do teto configurado com relógio falso; zero trocas de conta disparadas por `needs_attention` ou `restricted`; challenge não entra em laço de recuperação.

### F5 — Resultados e checkpoints atômicos

**Pré-requisitos:** F1 e F2 concluídas, na ordem aprovada. **A direção arquitetural (decisões A–F) está aprovada; o SQL não está certificado**: a F5 começa revisando I1–I12 e o SQL da §6.3 contra a implementação, sem herdar o resultado dos modelos [E2, E3].

1. **Análise:** mapear o schema atual (`profiles_seen`, PK por username) e confirmar que o código de base não tem jobs, concessão, reivindicação nem páginas; mapear cenários de queda entre página e progresso; listar os 43 cenários de [E3] e o teste adicional de inanição do backup que precisam virar testes da implementação.
2. **Pesquisa:** transações SQLite [S9]; UNIQUE com `NULL` [S38]; API de backup [S39] e WAL [S43]; `sqlite3` do Python [S26]; idempotência por chave [S40]; analogias de posse e reprocessamento [S31–S34]. **Arquivar cópia datada** do relato [S37].
3. **Pré-análise:**
   - schema e SQL da §6.3 v13.7; invariantes **I1–I12** como critério;
   - **política `sanity-v1` fixada e versionada aqui, antes dos testes** (§6.3.10): significado, unidade e valor inicial de `K`, `S`, `replay_ratio`, `frontier_confirm_screens`, tolerância e idade do contador, usando os dados e fixtures da F6b quando existirem; sem dados da F6b, os valores continuam provisórios e o estado sem evidência é `end_unknown`/`partial`;
   - especificação da tentativa e do spool durável; conteúdo canônico `page-v1` (§6.3.3);
   - motivos de parada e ações (§6.3.4); liberação manual com sessão validada e requeue manual (§6.3.5, §6.3.6);
   - contrato de resultado: `run_result`, `job_view`, seleção, última tentativa e histórico observado (§6.3.9), com **nomes de métodos públicos novos** definidos aqui, sem alterar retornos legados;
   - backup online em passo único com prazo e verificação pela cópia; janela de manutenção drenada para comparação exata (§6.3.13);
   - uma conexão por thread, incluindo heartbeat e backup;
   - caminho do print: páginas controladas, timeline de concessões, crash e backup, **sem conta real e sem tráfego pago** — sem necessidade de novo aval.
4. **TDD** (vermelho antes do código, com horário registrado): portar os cenários de [E3] contra a implementação real:
   - **idempotência:** T01, X01, X02, N04 (crash e nova conexão), N05 (canônico), N08 (outra execução), L01 (duplicata não consome releitura);
   - **proveniência e resultado:** T07, T10, T10b, N01 (Ana/Bruno × Ana/Carla), N02 (última tentativa falha após execução válida), N03 (confiável no histórico, suspeito na execução atual), N07 (fusão sem contaminação), N09 (execução parcial não completa por união);
   - **UI sem cursor:** T08a, T08b, T08c, U01 (inserção na releitura), U02 (remoção e reordenação), U03 (lacuna de continuidade), U04 (reinício do app), U06 (fim sem evidência × contador exato);
   - **backend com cursor:** T02, T03, T11, T11b (cursor ausente não é fim);
   - **motivos de parada:** T05 (restrição em voo), T06 (perda de posse), D03 (cancelamento), D04 (falha técnica), D05 (worker obsoleto);
   - **reivindicação e limites:** T04, T12, L02 (sem ciclo automático), L03 (três execuções), L04 (liberação manual com sessão validada), C01 (processos concorrentes);
   - **backup:** B01 (passo único com heartbeat e escritor), B02 (prazo explícito), B03 (manutenção drenada), B04 (guarda de conexão com transação), B05 (verificação pela cópia) e inanição do incremental com heartbeat;
   - **mantidos de versões anteriores:** queda antes, durante e depois do commit; username reaproveitado por outro `pk` → `ambiguous`; migração legada não conta como confiável; renovação de concessão expirada e de conta revogada rejeitadas; heartbeat atrasado → `lease_lost`; espera pelo bloqueio enquanto a concessão expira; validação de `lease_ttl` e `busy_timeout`;
   - cenários de [E1] (8 processos, 100 encerramentos, espera real) **reexecutados contra a implementação**, não herdados.
5. **Execução:** migração com backup verificado em janela de manutenção e rollback; identidades de execução, tentativa e posição; spool durável; reivindicação, commit, liberação e requeue conforme §6.3; heartbeat com classificação em conexão própria; `sanity-v1` gravada por página; visões de resultado como métodos novos.
6. **Testes:** reinício em processo novo não perde página confirmada; zero duplicatas lógicas; reprocessamento contado; disputa entre processos; os 43 cenários de [E3] contra a implementação; contrato público legado intacto.
7. **Prints:** `01-antes-crash.png`, `02-retomada.png`, `03-integridade.png`, `04-heartbeat-e-motivos.png`, `05-progresso-sem-cursor.png` (sequências sintéticas, não Android), `06-selecao-e-historico.png`, `07-backup-online.png`. Ler cada um.

**Aceite:**
- 100 injeções de falha contra fake server paginado com N = 1.000 membros conhecidos: zero páginas **confirmadas** perdidas, zero duplicatas lógicas, reprocessamento reportado;
- em 100% das tentativas: commit sem posse, reivindicação sem concessão ou sem sessão validada, renovação de conta revogada, gravação por execução não corrente, segunda leitura confiável da mesma posição, reuso de `attempt_id` com conteúdo diferente ou de outra execução, quarta releitura e quarta execução são rejeitados; nenhuma violação de unicidade vira sucesso;
- os 43 cenários de [E3] passam contra a implementação;
- backup online com workers ativos: heartbeat sem falha durante a cópia, cópia verificada pelo próprio ponto de consistência; manutenção drenada com `digest_equal = true`;
- resultado de cada execução calculado só com a própria execução; seleção e histórico conforme §6.3.9; contrato legado intacto;
- toda página com `policy_version`;
- nenhuma promessa de portabilidade de cursor entre contas sem experimento.

### F4 — Scheduler com exclusividade (entrega própria de paralelismo web)

1. **Análise:** reproduzir workers com credenciais repetidas (`parallel.py:107-111`) e queda durante a execução.
2. **Pesquisa:** twscrape [S20] como referência arquitetural, sem copiar cotas de outra plataforma; concorrência no Playwright [S10].
3. **Pré-análise:** concessão por conta com cooldown por endpoint, reutilizando as invariantes já testadas na F5, sem reimplementar a transação; alvos independentes como unidade inicial de paralelismo; definir o proprietário dos objetos de browser; caminho do print: dois processos contra serviço fake.
4. **TDD:** corrida por uma conta; worker lento que perde a concessão; processo morto; liberação tardia; estado de challenge que não expira junto com a concessão.
5. **Execução:** adaptar pool e fila; limitar workers ao número de contas elegíveis; uma instância de Playwright por thread, ou um processo dono dos contexts.
6. **Testes:** concorrência entre processos separados; rejeição de commit tardio; ausência de conta usada em dobro.
7. **Prints:** `01-pool.png`, `02-lease-expirado.png`, `03-worker-obsoleto.png`. Ler a timeline e o dono de cada concessão.

**Aceite:** em 1.000 iterações de disputa com 2 a 4 processos, zero intervalos com duas concessões ativas para a mesma conta; conta em `needs_attention` nunca concedida; número de workers menor ou igual ao de contas elegíveis; falha técnica permite recuperação e restrição continua respeitada.

### F3 — Metadados independentes de Selenium

1. **Análise:** reproduzir o erro de `get_profile()` sem `_driver`; levantar todas as propriedades delegadas ao Selenium.
2. **Pesquisa:** padrões de capacidade e contrato no próprio projeto (`base.py:37` como modelo); documentação de adapters [S7, S14].
3. **Pré-análise:** leitor opcional de perfil e objetos internos, com defaults preservados; caminho do print: relatório comparativo entre adapters fake, sem conta real.
4. **TDD:** mesmo resultado público com adapter fake **sem atributo `_driver`**; subclasse legada de `BaseEngine` continua instanciável; parte B do teste de contrato reescrita para atravessar a delegação real.
5. **Execução:** delegação interna, registro opt-in e capacidades explícitas.
6. **Testes:** snippet legado e matriz de capacidades; verificar mensagem explicativa quando o extra ou a versão não são suportados.
7. **Prints:** `01-contrato-legado.png`, `02-perfil-sem-driver.png`, `03-capacidades.png`. Ler cada resultado.

**Aceite:** a nova capacidade funciona sem Selenium, sem que o teste vire mock da própria API pública.

### F6 — Android em Docker com saída DataImpulse

1. **Análise:** partir da tag aprovada no F6a; inventariar recursos; medir consumo por slot.
2. **Pesquisa:** parâmetros e limitações do plano contratado [S1, S2, S3, S19]; seleção do pool mobile no painel; tun2socks em namespace de rede.
3. **Pré-análise:** compose com versões fixas, volume por slot, controlador e túnel; kill switch; caminho do print: boot, teste de rede com servidor de diagnóstico e painel de consumo. Uso de tráfego pago exige citar a autorização vigente ou pedir aval com teto.
4. **TDD:** checks de boot; persistência após restart; kill switch; DNS, IPv6 e UDP conforme suporte; falhar quando rota direta for possível. Falhas do proxy classificadas pela causa documentada [S19], sem virar "bloqueio do Instagram": proxy indisponível, credencial inválida, quota esgotada e porta sticky não permitida pelo plano; IP mudando antes do `sessttl` gera pausa e registro.
5. **Execução:** provisionar container e rede; registrar produto, região e ASN observados.
6. **Testes:** observar a rota do ambiente inteiro e não só de um cliente; medir troca antecipada de IP e indisponibilidade do túnel; registrar bytes reais e limitações.
7. **Prints:** `01-boot.png`, `02-app-aberto.png`, `03-rede.png`, `04-killswitch.png`. Ler visualmente todos, além dos logs.

**Aceite:** em teste de 10 minutos, zero pacotes saindo fora do túnel (captura no host filtrando o IP do gateway); com o túnel derrubado, zero conexões externas bem-sucedidas; IP observado a cada minuto por 120 minutos em porta sticky, com o número de trocas registrado (medição, não meta); bytes medidos comparados ao painel, com a diferença explicada. Falhando, a fase fica bloqueada e o aparelho físico serve como diagnóstico registrado.

### F7 — AndroidUiEngine para leitura e sessão persistida

1. **Análise:** partir dos dumps, fixtures, classificador e métricas da F6b; após a F6, observar se a saída pelo proxy altera login, telas intermediárias, listas, idioma ou seletores em relação à F6b.
2. **Pesquisa:** uiautomator2 [S14] versus Appium [S15]; GramAddict [S16] como referência de organização; versão do app.
3. **Pré-análise:** adapter somente de leitura; autenticação assistida quando necessário; **conjunto de teste conhecido**: uma conta-alvo controlada pelo Tiago cujos seguidores são conhecidos, com N entre 20 e 100; caminho do print e autorização citada antes do código, por envolver conta real.
4. **TDD:** fixtures de hierarquia para perfil e lista; seletor ausente; elemento antigo; truncamento; estado vazio; challenge; nenhuma ação de follow, like ou DM.
5. **Execução:** navegação, leitura, espera por estado, timeout, cancelamento e diagnóstico; persistir `/data` com isolamento por slot.
6. **Testes:** roteiro pequeno real e reinício do container; comparar o resultado **item a item com o conjunto conhecido**, não com o contador arredondado. Cenários reais adicionais: idioma PT e EN; teclado cobrindo o botão; lista legitimamente vazia; challenge; sessão revogada; scroll sem itens novos (distinguir fim de lista de lista travada); o mesmo membro aparecendo em duas telas; processo morto no meio da página, que retoma do checkpoint; sessão com 2 h, 24 h e 72 h de idade, contando os logins efetivamente evitados. **Coleta de dados para I10:** frequência de páginas vazias sem sinal de fim; e, se houver restrição durante a fase, comparação das páginas imediatamente anteriores ao sinal contra o conjunto conhecido, para verificar se conteúdo não vazio já vinha degradado antes da detecção.
7. **Prints:** `01-autenticacao.png`, `02-perfil.png`, `03-lista.png`, `04-retomada.png`, `05-erro.png`. Ler cada tela e corrigir seletor, layout ou idioma incorretos.

**Aceite:** contra o conjunto conhecido de seguidores da conta-alvo controlada, **precisão igual a 100%** (todo item retornado pertence ao conjunto) e **recall maior ou igual a 95%** por execução, com a parcialidade explícita no restante; zero logins de formulário em 3 reinícios de container com sessão válida. Em alvos vivos, reportar cobertura estimada, e não completude.

### F8 — Experimento de API móvel por adapter mantido

1. **Análise:** medir custo e tempo da UI; listar campos que justificariam um adapter adicional.
2. **Pesquisa:** versão do instagrapi, suporte de Python, sessão e paginação [S7, S8, S22]; comparar esforço com implementação própria.
3. **Pré-análise:** adapter isolado, com autenticação própria e critérios de adoção; caminho do print com conta e alvo autorizados, sem exigir exportação de sessão do app.
4. **TDD:** fixtures versionadas; respostas parciais; flags; página vazia suspeita; falha de autenticação; cursor incompatível; contrato público preservado.
5. **Execução:** caminho mínimo apenas se a hipótese se justificar; registrar dependências e capacidades; nenhuma chave histórica de assinatura copiada como verdade atual.
6. **Testes:** comparar com o **mesmo conjunto conhecido da F7**; reiniciar e retomar no mesmo contexto; troca de contexto tem cenário próprio e não presume portabilidade de cursor. Cursor repetido, cursor expirado, página vazia acompanhada de erro e lista alterada entre páginas: todos com limite de iterações, parciais preservadas e nenhum sucesso silencioso. Reproduzir, se ocorrer com conta de teste, o padrão do relato [S37]: contador com valor real e endpoints de lista devolvendo zero sem erro — deve resultar em página `suspect` e job `parcial`, nunca em `completo`.
7. **Prints:** `01-comparacao-ui-api.png`, `02-sessao.png`, `03-cursor-invalido.png`. Ler divergências, sem expor segredos.

**Aceite:** decisão documentada de adotar, manter experimental ou rejeitar. A ponte app→API só é considerada com prova independente de necessidade e viabilidade.

### F9 — Identidade e diagnóstico de TLS e fingerprint

1. **Análise:** separar erros atribuíveis ao transporte dos demais; registrar evidência antes de atribuir bloqueio ao fingerprint.
2. **Pesquisa:** curl_cffi [S11] e JA4 [S12]; opções do adapter; observabilidade de TLS.
3. **Pré-análise:** experimento de compatibilidade com diferenças conhecidas e rollback; definir como obter evidência do cliente correto; print de comparação redigida, sem tokens.
4. **TDD:** estabilidade dos campos persistentes; validade dos headers dinâmicos; configuração de TLS; leitura das medições; nenhum hash único tratado como prova total.
5. **Execução:** corrigir incoerências demonstradas; customizar transporte apenas se necessário e mensurável.
6. **Testes:** comparar operação e transporte em versões fixas; documentar o que não pôde ser observado, inclusive HTTP/2 criptografado.
7. **Prints:** `01-identidade.png`, `02-transporte.png`, `03-diferencas.png`. Ler cada evidência e o escopo da captura.

**Aceite:** compatibilidade observada com limitações explícitas. Sem baseline observável do app, não declarar fingerprint idêntico.

### F10 — Piloto gradual, custo e escala controlada

1. **Análise:** coletar duração, taxa de sessão restaurada, registros únicos, restrições e consumo do piloto de um slot.
2. **Pesquisa:** limites de infraestrutura; revalidar preço, plano e compatibilidade upstream [S4, S5, S7].
3. **Pré-análise:** crescer apenas por critério, com baseline e duração comparáveis; tetos de custo e interrupção definidos; caminho do print: painel de slots de teste. Citar autorização vigente de tráfego ou pedir teto novo.
4. **TDD:** orçamento agregado por IP observado; colisão de IP entre slots ativos pausando o slot mais novo; cancelamento por custo; sessão não validada; fila sem conta elegível.
5. **Execução:** piloto gradual e warming como política configurável, sem calendário fixo nem engajamento artificial obrigatório.
6. **Testes:** falha de container, de proxy e de processo; conjunto controlado sem perda; em dados vivos, reportar intervalo, amostra e parcialidade.
7. **Prints:** `01-slots.png`, `02-tempo-e-erros.png`, `03-custos.png`. Ler unidades, denominadores e resultados.

**Aceite:** escala limitada aos recursos e ao orçamento observados; paralelismo não usado para multiplicar carga após um limite do serviço.

### F11 — Release, regressão, drift e operação

1. **Análise:** comparar o resultado final com F0 e F1; listar débitos, gates pendentes e caminhos realmente suportados.
2. **Pesquisa:** versões upstream, incompatibilidades e documentação de operação dos componentes escolhidos.
3. **Pré-análise:** release opt-in, feature flags, rollback, migrações e alarmes; caminho do print: fluxo de instalação e diagnóstico para usuário novo.
4. **TDD:** drift de parser e de versão; compatibilidade do pacote; interrupção clara diante de configuração não suportada.
5. **Execução:** atualizar README, USAGE, ARCHITECTURE, TROUBLESHOOTING, CHANGELOG e o histórico; fixtures sem dado sensível.
6. **Testes:** matriz de Python do núcleo, matriz mobile explicitada, wheel em ambiente limpo e smoke test autorizado; registrar limitações sem esconder skips.
7. **Prints:** `01-instalacao.png`, `02-fluxo-mobile.png`, `03-drift.png`, `04-rollback.png`. Abrir e ler todos.

**Aceite:** a documentação corresponde ao que foi validado; o release não altera defaults nem converte experimento em garantia.

---

## 9. Estratégia de testes e evidência

Até `5b0b996`, o CI rodava Python 3.9, 3.10 e 3.12, executava `pytest tests -m "not e2e and not mobile"`, `ruff` e `mypy`, e instalava só `.[dev]` com `pip install -e`. **Defeito encontrado na F0:** dois testes dependem de extras opcionais (`httpx`, `playwright`) que `.[dev]` não instala e não pulam quando faltam — num venv limpo com `.[dev]` eles falham. A F0 corrige os testes (pulam explicitamente sem o extra) e o CI (instala os extras para que rodem de fato), e passa a matriz para Python ≥ 3.12.

| Suíte | Conteúdo | Execução |
|---|---|---|
| Unitária e de contrato | sem Instagram, relógio controlado, fixtures | todo PR |
| **Pacote instalado** | construir o wheel, instalar em venv limpo fora do checkout e importar os subpacotes | todo PR (hoje inexistente: `pip install -e` esconde o defeito do empacotamento) |
| Integração local | SQLite, múltiplos processos, fake server, crashes | todo PR pertinente; não excluir apenas por se chamar "e2e" |
| Browser fake | fluxo Selenium e Playwright contra servidor local | job com navegador instalado |
| Android smoke | boot, ADB, UI controlada e persistência | runner com capacidades declaradas |
| Piloto real | conta de teste, proxy e app | execução delimitada com orçamento; nunca CI irrestrito |

Não alterar comandos antes de confirmar os marcadores reais. Lint e mypy verdes não substituem comportamento: o `pyproject.toml` atual desativa várias categorias do mypy (`attr-defined`, `assignment`, `arg-type` e outras), então cobrir os módulos novos explicitamente, sem usar essa configuração como prova de tipagem.

### 9.1 Modelo obrigatório de registro por fase

Cada fase cria `docs/phase-evidence/fase-N/README.md` e atualiza `docs/HISTORICO_MOBILE.md`.

```markdown
# Fase N — título
Status: em andamento (passo 1)
Base: branch / SHA / versões

## 1. Análise
Problema observado, reprodução, métrica, hipótese separada.

## 2. Pesquisa
Fonte / data / versão / o que sustenta / o que não comprova. Alternativas e decisão.

## 3. Pré-análise
Desenho, contrato, testes e rollback.
Caminho do print: quem autentica, conta, ambiente, alvo, estado a preparar.
Autorização vigente aplicável (referência) ou pedido novo, com o motivo de exceder o escopo.
Critérios de aceite definidos antes de implementar.

## 4. TDD
Teste novo / motivo da falha / log vermelho / referência temporal.
Regressões existentes verdes; sem vermelho fabricado.

## 5. Execução
Mudanças e commits reais.

## 6. Testes
Comando, ambiente, resultado, logs, skips e limitações.

## 7. Prints — todos abertos e lidos
| Arquivo | O que vi | Problema/melhoria | Ação | Leitor/data |
|---|---|---|---|---|

## Encerramento
Critérios cumpridos / pendentes / riscos / próximo passo.
```

---

## 10. Riscos e gates

| Risco | Gate ou mitigação |
|---|---|
| App não abre ou fecha por ABI/tradutor | **F6a**: duas tags por digest; falha nas duas bloqueia a fase e aciona a decisão entre budtmo e aparelho físico |
| App não loga ou não mantém sessão em ambiente emulado | **F6b**: login manual e 3 reinícios; falha leva a comparar com aparelho físico; sem meta de burlar atestação |
| Interface não expõe a lista de forma legível ou o fim da lista não é confiável | **F6b**: precisão e recall contra conjunto conhecido; falha leva a decidir entre OCR, API móvel ou encerrar o caminho UI |
| Perda de trabalho em queda | invariantes I1–I12, testadas na F5 com múltiplos processos; garantia restrita a páginas confirmadas (§6.3.1) |
| Lista vazia ou incompleta aceita como completa | `sanity-v1`: página vazia sem fim vira `suspect`; job com suspeita **aberta** não completa; resultado conta só observações `trusted` do próprio job; ausência de cursor não é fim [S35–S37] |
| SQL de persistência aprovado sem validação | aprovação da C e das decisões A–F é de direção; SQL revalidado em [E2] e [E3] e portado contra a implementação na F5 |
| Backup que nunca termina ou bloqueia o heartbeat | backup online em **passo único** por conexão própria, com prazo; incremental só em manutenção — reproduzido que o incremental não conclui com o heartbeat ativo [E3] |
| Uso de imagem de emulador com tradução ARM fora do uso declarado | o Google declara a tradução ARM das imagens x86 como "only be used for application development and debug purposes" [S45]; **decisão de conformidade do Tiago** antes do F6a |
| Testes que dependem de extras e não pulam | corrigido na F0 (pulo explícito + extras no CI) |
| Challenge tratado como sessão válida | reproduzido no código atual (§3.1); F1 corrige restauração e passa o caminho de restauração pelo `BlockDetector` |
| Conta usada em dobro | I1 e I4, testadas na F4 |
| Política do provedor quanto a login em rede social | verificar no plano contratado antes do piloto da F6 |
| Banimento de contas de teste | contas dedicadas, warming gradual, governador e congelamento por restrição |
| Drift de versão do app | fixtures versionadas e drift check na F11 |
| Custo de banda | métrica de GB por mil registros únicos válidos; tetos configurados |
| LGPD e Termos | minimização, retenção, anonimização nos prints; API oficial onde houver cobertura |

---

## 11. Histórico e rastreabilidade

| Registro | Estado |
|---|---|
| v1–v10 (14/09/2026) | roadmap original, em `docs/ROADMAP_MOBILE.md @5b0b996`, com modificação local não commitada |
| Fase 0 antiga / `6175e5b` | existe localmente em `docs/roadmap-mobile`, não publicada; esqueleto e teste de contrato adiantados em relação ao ritual (desvio registrado, sem TDD retroativo) |
| v11 (15/09/2026) | revisão documental sobre o GitHub `50589a3` |
| v12 / v12.1 (15/09/2026) | auditoria independente sobre `5b0b996`; suíte offline e wheel **relatados pelo auditor**, não reproduzidos pelo Tiago |
| **v13 (16/09/2026)** | consolidação num documento único; sete passos explícitos em todas as fases; §6.3 corrigida (renovação de concessão expirada, SQL alinhado às restrições, deduplicação por índices parciais, conexão por thread); A09 corrigido; precisão e recall da F7 contra conjunto conhecido; categorias residencial e mobile da DataImpulse restabelecidas como distintas; decisões marcadas como propostas |
| v13.1 (16/09/2026) | Correções após segunda conferência do Tiago: deduplicação explicitada **nos dois sentidos**, com limite de identidade e estado `identidade_ambigua`; nova invariante **I7** (`:now` calculado após obter o bloqueio de escrita); totais dos achados corrigidos para 20 confirmados e 8 parciais; MVP de um slot marcado como proposta da auditoria, sem aprovação registrada |
| v13.2 (16/09/2026) | Incorporação do relatório externo de testes adversariais [E1] (autor sem acesso à branch local; testes contra `50589a3` e contra o SQL deste documento). T01 e T02 **reproduzidos pelo auditor em `5b0b996`**, com o agravante de `login()` retornar antes do `BlockDetector`. T03–T06 aceitos como lacunas do desenho, não defeitos do código: renovação agora verifica `auth_state`; novas invariantes I8 (propriedade do job) e I9 (backup em WAL); validação de `lease_ttl` e `busy_timeout`; opção A/B para página já recebida, pendente do Tiago. F6a exige ADB em `device`, PID estável e ausência de ANR. Cenários reais adicionais em F6, F7 e F8 |
| v13.3 (16/09/2026) | **Aprovado pelo Tiago:** nova fase F6b (fatia vertical: sessão manual, lista conhecida e extração pela UI) logo após o F6a e antes de F1–F7. F6b com sete passos, TDD em duas etapas (lógica sintética antes; classificador depois do primeiro dump real), saída de rede decidida antes do login, extração por `uiautomator dump` sem framework, e gate com ramificação por causa de falha. Sequência atualizada para `F0 ∥ (F6a → F6b) → …`; F7 passa a partir dos resultados da F6b |
| v13.4 (16/09/2026) | A pedido do Tiago, pesquisa para decidir entre A e B. Resultado: **solução C** substitui as duas — autoridade para gravar vem da posse da concessão (prática documentada do Kafka [S31]); confiança no conteúdo vem de evidência sobre a página. Motivo decisivo: relato recente [S37] de restrição devolvendo lista vazia sem erro, caso que nem A nem B cobriam. Novas: invariante I10, tabela `pages`, colunas `jobs.suspect_pages` e `relations.first_trusted_at`, três verificações de sanidade, cenários (a)–(f) na F5 e coleta de dados em F7 e F8. Fontes S31–S37. Confirmação do Tiago pendente |
| v13.5 (16/09/2026) | **Aprovado pelo Tiago:** solução C (I10) para páginas recebidas, substituindo as opções A e B. Limiares de sanidade seguem para definição na F5 e calibração com dados de F6b, F7 e F8 |
| v13.6 (16/09/2026) | Revisão da §6.3 e da F5 após revisão externa. SQL literal da v13.5 reproduzido em SQLite: **2/17**; desenho novo: **17/17** em modelo isolado [E2]. Mudanças: identidade separada em execução (`runs`), tentativa (`attempt_id` + hash) e posição lógica; reivindicação valida o solicitante; motivos de parada com ações permitidas e heartbeat classificado; F5(a) dividido em antes do envio e em voo; confiança por observação e execução (`observations`), sem coluna global; fusão com auditoria; suspeita histórica × aberta e requeue explícito; progresso sem cursor com estados, tela repetida e releitura; política `sanity-v1` versionada; backup com coordenador e outra conexão (backup pela mesma conexão travou); garantia de durabilidade restrita a commits confirmados; nova invariante I11. Aprovações de F6b e da solução C (conceito) preservadas; o SQL não tem aprovação própria |
| v13.7 (16/09/2026) | **Decisões aprovadas pelo Tiago** (MVP de um slot, ordem, Python ≥ 3.12, publicação em rascunho, resultado e histórico, limites operacionais) aplicadas. §6.3 revisada contra A–F: tentativa com spool e conteúdo canônico `page-v1`; escopo do `attempt_id`; motivos de parada com cancelamento; liberação manual com sessão validada; requeue manual e nova invariante I12 (limites sem ciclo automático); UI com continuidade, segmentos, fronteira e fim só com evidência positiva; cursor ausente não é fim; `run_result`/`job_view` com seleção, última tentativa e histórico observado; backup online em passo único com prazo e verificação pela cópia, manutenção drenada para comparação exata; parâmetros `sanity-v1` com significado e unidade. Modelo: v13.6 19 PASS/10 FAIL/14 API_AUSENTE → v13.7 43/43 [E3]; incremental não conclui com heartbeat ativo. A revisão externa desta rodada não abriu a v13.6: **não é validação independente**. Execução de F0 e F6a registrada em `docs/HISTORICO_MOBILE.md` e `docs/phase-evidence/` |
| F1–F11 e F6b | **não iniciadas** |

### 11.1 Correspondência com as fases da v10

| Fase v10 | Destino |
|---|---|
| 0 Fundação | F0 (inclui empacotamento) e F3 |
| 1 Docker | F6a, F6b e F6 |
| 2 Proxy | F6 e F10 |
| 3 Identidade | F1 e F9 |
| 4 TLS | F9, condicionada a evidência |
| 5 Handoff/API | F8; a ponte deixa de ser requisito |
| 6 Comportamento/UI | F6b (spike) e F7 |
| 7 Warming | F10 |
| 8 Rate limiting | F2, antecipada |
| 9 Escala | F4, F6 e F10 |
| 10 Validação/drift | F11 |
| 11 Sessão/pool/cursor | F1, F5 e F4 |

---

## 12. Fontes

Código do InstaT fixado por SHA; fontes externas consultadas em 15 e 16/09/2026. Páginas mutáveis precisam ser revistas na implementação.

### Código

- **C1:** `instat/login.py` (restauração de cookies, Firefox com UA de Chrome) e `instat/login_flow.py` (`SessionRestorer`, `FormLogin`).
- **C2:** `instat/session_cache.py` (persistência, `max_age`, permissões).
- **C3:** `instat/engines/selenium_engine.py` e `instat/engines/httpx_engine.py`.
- **C4:** `instat/engines/playwright_engine.py` (UA fixo, restauração por URL).
- **C5:** `instat/extractor.py` (construtor, `_build_engines`, `get_profile`) e `instat/profile.py`.
- **C6:** `instat/engines/base.py` e `instat/engines/engine_manager.py`.
- **C7:** `pyproject.toml` e `.github/workflows/ci.yml`.
- **C8:** `instat/parallel.py` e `instat/persistent_store.py`.
- **C9:** `instat/mobile/engines.py` e `tests/test_public_api_contract.py` (existentes apenas em `5b0b996`).

### Externas

- **S1:** DataImpulse — tipos de conexão: https://docs.dataimpulse.com/proxies/types-of-connections
- **S2:** DataImpulse — intervalo de sessão e substituição de IP indisponível: https://docs.dataimpulse.com/proxies/parameters/session-interval
- **S3:** DataImpulse — sintaxe de parâmetros: https://docs.dataimpulse.com/proxies/parameters
- **S4:** DataImpulse — proxies mobile (categoria de produto e preço anunciado): https://dataimpulse.com/mobile-proxies/
- **S5:** redroid — README upstream ("Published redroid images already got libndk_translation included", sem tag/versão/arquitetura): https://github.com/remote-android/redroid-doc
- **S6:** redroid — documentação de deploy (WSL2, kernel): https://github.com/remote-android/redroid-doc/blob/master/deploy/wsl.md
- **S7:** instagrapi — README, persistência, transporte e limitações: https://github.com/subzeroid/instagrapi
- **S8:** instagrapi — operações de usuário e paginação: https://github.com/subzeroid/instagrapi/blob/master/docs/usage-guide/user.md
- **S9:** SQLite — transações: https://sqlite.org/lang_transaction.html
- **S10:** Playwright Python — threading: https://playwright.dev/python/docs/library#threading
- **S11:** curl_cffi — customização de fingerprint e limites de JA3/Akamai: https://curl-cffi.readthedocs.io/en/latest/impersonate/customize.html
- **S12:** FoxIO — JA4 (JA4 sob BSD; demais componentes sob licença própria): https://github.com/FoxIO-LLC/ja4
- **S13:** budtmo/docker-android — KVM, noVNC e WSL2: https://github.com/budtmo/docker-android
- **S14:** openatx/uiautomator2: https://github.com/openatx/uiautomator2
- **S15:** Appium UiAutomator2 driver: https://github.com/appium/appium-uiautomator2-driver
- **S16:** GramAddict (referência de organização, não de limites seguros): https://github.com/GramAddict/bot
- **S17:** DataImpulse — sites bloqueados: https://docs.dataimpulse.com/blocked-websites
- **S18:** Playwright — estado de autenticação: https://playwright.dev/python/docs/auth
- **S19:** DataImpulse — erros (causas de 407): https://docs.dataimpulse.com/errors
- **S20:** twscrape — pool de contas como referência arquitetural: https://github.com/vladkens/twscrape
- **S21:** DataImpulse — parâmetro Session ID (`sessid`, portas 823/824, ~30 min, não substitui sticky): https://docs.dataimpulse.com/proxies/parameters/session-id
- **S22:** instagrapi no PyPI (3.0.2 em 13/09/2026, `Requires-Python >=3.10`): https://pypi.org/project/instagrapi/
- **S23:** Meta — IG User Business Discovery (campos públicos e edge `/media`; sem edge de seguidores): https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/business_discovery
- **S24:** uiautomator2 no PyPI: https://pypi.org/project/uiautomator2/
- **S25:** redroid-doc, issue #933 — "arm64-v8a is advertised without a translator, so ARM-only apps crash on launch" (relato; imagens x86_64 a partir da 15): https://github.com/remote-android/redroid-doc/issues/933
- **S26:** Python — módulo `sqlite3`, restrição de uso da conexão pela thread criadora (`check_same_thread`) e `Connection.backup()`: https://docs.python.org/3/library/sqlite3.html
- **S27:** SQLite — como um banco pode ser corrompido (cópia de banco ativo): https://sqlite.org/howtocorrupt.html
- **S28:** SQLite — Write-Ahead Logging: https://sqlite.org/wal.html
- **S29:** redroid-doc, issue #28 (2021) — relato de ADB offline: https://github.com/remote-android/redroid-doc/issues/28
- **S30:** redroid-doc, issue #830 (2025) — relato de ADB offline: https://github.com/remote-android/redroid-doc/issues/830
- **S31:** Apache Kafka — `ConsumerRebalanceListener` (commit em revogação ordenada × partições perdidas): https://kafka.apache.org/39/javadoc/org/apache/kafka/clients/consumer/ConsumerRebalanceListener.html
- **S32:** Martin Kleppmann — "How to do distributed locking" (pausa após expiração e fencing tokens): https://martin.kleppmann.com/2016/02/08/how-to-do-distributed-locking.html
- **S33:** AWS — Amazon SQS visibility timeout (reprocessamento, at-least-once, DLQ): https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-visibility-timeout.html
- **S34:** Temporal — Activity execution ("Activities must heartbeat to receive cancellations"): https://docs.temporal.io/activity-execution
- **S35:** instaloader, issue #1021 (2021) — 1.469 de 1.474 seguidores, sem exceção (relato): https://github.com/instaloader/instaloader/issues/1021
- **S36:** dilame/instagram-private-api, issue #1407 (2021) — 194 de 214 seguidores, sem erro, não confirmada (relato): https://github.com/dilame/instagram-private-api/issues/1407
- **S37:** instagrapi, issue #2797 (aberta em 14/09/2026, sem resposta de mantenedor) — com checkpoint, métodos de lista devolvem zero linhas, parte deles sem erro, enquanto o contador mostra o valor real (relato, lido em 16/09/2026): https://github.com/subzeroid/instagrapi/issues/2797 — reaberta pelo auditor em 16/09/2026 com trechos literais em §6.3.10; a revisão externa não conseguiu reabrir; arquivar cópia na F5
- **S38:** SQLite — CREATE TABLE, restrições UNIQUE ("NULL values are considered distinct from all other values, including other NULLs"): https://sqlite.org/lang_createtable.html
- **S39:** SQLite — Online Backup API (snapshot "as it was when the copying commenced"; reinício quando outra conexão escreve): https://sqlite.org/backup.html
- **S40:** Stripe — Idempotent requests (chave identifica retries; parâmetros diferentes geram erro): https://docs.stripe.com/api/idempotent_requests
- **S41:** Python Developer's Guide — Status of Python versions (3.12 security até 2028-10; 3.13 e 3.14 bugfix): https://devguide.python.org/versions/
- **S42:** GitHub Docs — Creating a pull request (rascunho com `gh pr create --draft`; escolha da base): https://docs.github.com/en/pull-requests/how-tos/create-pull-requests/creating-a-pull-request
- **S43:** SQLite — Write-Ahead Logging ("readers do not block writers"; leitura longa impede checkpoint): https://www.sqlite.org/wal.html
- **S44:** setuptools — Package discovery (`packages.find`, `namespaces = false`; lista explícita desliga a descoberta): https://setuptools.pypa.io/en/latest/userguide/package_discovery.html
- **S45:** Android Developers Blog — Run ARM apps on the Android Emulator (imagens x86 do Android 11; uso "only … for application development and debug purposes"): https://android-developers.googleblog.com/2020/03/run-arm-apps-on-android-emulator.html
- **S46:** Docker Hub — tags `budtmo/docker-android` (emulator_11.0 a 14.0, amd64, 2,77–3,03 GB, atualizadas em 01/09/2026): https://hub.docker.com/r/budtmo/docker-android/tags
- **S47:** OWASP — Secrets Management Cheat Sheet (segredos fora de código e documentação; remoção de segredos em logs; revogação e troca após exposição): https://cheatsheetseries.owasp.org/cheatsheets/Secrets_Management_Cheat_Sheet.html
- **S48:** The Twelve-Factor App — Config (configuração separada do código; código publicável sem comprometer credenciais): https://12factor.net/config
- **S49:** instagrapi — Best practices (reutilizar sessão em vez de login a cada execução; um proxy/IP estável por conta; cuidado com challenge): https://subzeroid.github.io/instagrapi/latest/usage-guide/best-practices/

S27–S30 foram citadas pelo relatório externo [E1]; a auditoria não reabriu S29 e S30, que servem apenas para motivar a checagem do estado `device`, não como prova de causa neste projeto.

### Evidência externa

- **E1:** `RELATORIO_TESTES_INSTAT.md` (16/09/2026) — auditoria executável de testes adversariais por outra IA, sem acesso à branch local. 29 testes novos contra `SessionCache` e `SessionRestorer` publicados em `50589a3`, contra o SQL deste documento e contra um modelo de referência isolado. Suíte do repositório em `50589a3` com `-m "not e2e"`: 448 passed, 2 skipped, 5 deselected. T01 e T02 reproduzidos pelo auditor em `5b0b996`; T03–T06 são achados sobre o **desenho**, não sobre código implementado. O modelo de referência do relatório não é implementação do InstaT.
- **E2:** reproduções da revisão v13.6 (16/09/2026), pacote em `scratchpad/v136/` da sessão de auditoria: `01-PRE-ANALISE.md` (desenho, escrito antes dos testes), `models_v135.py` (SQL literal da v13.5), `test_repro.py` (15 testes TDD), `02-tdd-red.log` (15:07:59, v13.6 inexistente), `models_v136.py` (desenho proposto), `03-tdd-green.log` (15:14:25), `04-diag-backup.log` (backup pela mesma conexão travou), `test_extra.py` + `06-extra.log` (2 testes pós-implementação, não TDD), `05-reproducoes.png` (lido). Resultado: v13.5 2/17, v13.6 17/17. Python 3.12.10, SQLite 3.49.1, Windows. **Não testa InstaT, Android, Instagram nem proxy**; modelo e testes escritos pelo mesmo auditor.
- **E3:** revisão v13.7 (16/09/2026), versionada em `docs/design-validation/v13.7/`: `01-PRE-ANALISE-v13.7.md` (escrita antes dos testes), `test_v137.py` (43 cenários), rodadas vermelhas contra o modelo v13.6 (`02-red-contra-v136.log.txt` às 15:54:54 e `03-red2-contra-v136.log.txt` às 15:57:44, esta após corrigir bugs dos testes sem mudar expectativas; ambas 19 PASS, 10 FAIL, 14 API_AUSENTE), `models_v137.py`, `04-green-v137.log.txt` (16:00:06, 43/43), `extra_backup_starvation.py` + `06-extra-backup-starvation.log.txt` (pós-implementação, não TDD: incremental não conclui com heartbeat ativo) e `05-revisao-v13.7.png` (lido). Python 3.12.10, SQLite 3.49.1, Windows. **Não testa InstaT, Android, Instagram nem proxy**; API_AUSENTE não é falha comportamental; modelo e testes escritos pelo mesmo auditor.

**Limitação declarada:** parte das páginas externas foi lida por ferramenta de resumo, então as citações são curtas e aproximadas. Nenhum teste com Instagram, Android, proxy pago ou conta real foi executado na produção deste documento.

---

## 13. Próxima ação

**Estado em 16/09/2026** (detalhes em `docs/HISTORICO_MOBILE.md` e `docs/phase-evidence/`):

| Fase | Estado | O que falta |
|---|---|---|
| F0 | entregue; aceite de CI pendente | execução real do CI (o PR empilhado não dispara o workflow; ver abaixo) |
| F6a | bloqueada no passo 3 | decisões do Tiago: fonte do APK; conformidade do uso da tradução ARM das imagens do emulador; redroid exige kernel WSL customizado |
| F6b | bloqueada por dependência | F6a aprovada |
| F1 | não iniciada | confirmação de que pode avançar com F6a/F6b bloqueadas |
| F2, F5, F3, F6, F7 | não iniciadas | ordem aprovada |

**Decisões pendentes do Tiago, na ordem em que destravam trabalho:**
1. **F1 com F6a/F6b bloqueadas:** a ordem aprovada põe F1 depois de F6b. Autorizar F1 em paralelo (ela serve também aos engines web) ou manter a espera.
2. **Fonte do APK para o F6a:** extrair do próprio celular via ADB (recomendado), imagem com Play Store e conta Google, ou outra.
3. **Conformidade:** usar imagens do emulador cuja tradução ARM o Google declara "only … for application development and debug purposes" [S45].
4. **CI da F0:** o workflow só dispara em PR contra `main`. Para obter a evidência de CI sem mesclar nada: abrir um PR de rascunho temporário da branch da F0 contra `main`, ou ampliar o gatilho do workflow para PRs em qualquer base.
5. **Verificação manual da conta de teste** no app oficial antes do primeiro teste real (§7.1, regra 10).
6. **Versão do pacote:** a quebra para Python ≥ 3.12 pede bump major no próximo release (não feito).
7. **Limpeza local:** `build/` (cópias geradas), `cookies.json` com sessão em `instat/logs/diagnostics/` e os interpretadores 3.13/3.14 instalados pelo `uv` em `%APPDATA%\uv\python`.

**Achados técnicos desta rodada que já estão corrigidos na branch da F0:** wheel sem `instat.mobile`; stub mobile primário virando `LoginError`; dois testes dependentes de extras sem pulo; extra `stealth` sem importar em Python 3.12 (`distutils`); teste que abria Firefox real e travava a suíte; cinco erros de `ruff` preexistentes.

**Primeiro passo técnico recomendado após as decisões:** se a F1 for liberada, começar pelos casos vermelhos já reproduzidos no código (restauração de sessão aceitando challenge, `login()` retornando antes do `BlockDetector`, falhas do `SessionCache`) e registrar o marcador `real` com as regras da §7.1.
