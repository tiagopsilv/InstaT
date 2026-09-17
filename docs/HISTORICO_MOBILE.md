# Histórico — InstaT Mobile

Registro contínuo das fases do roadmap (`docs/ROADMAP_MOBILE.md`), das decisões e das autorizações vigentes.
Cada fase detalha seus sete passos em `docs/phase-evidence/fase-N/README.md`.

## Autorizações vigentes

| Data | Escopo | Limite registrado | Referência |
|---|---|---|---|
| 14/09/2026 | Contas de teste dedicadas do Tiago para prints e testes da antiga Fase 11 | **Sem teto de tráfego, sem lista de contas e sem limite de ações registrados** | roadmap v10, Fase 11, pré-análise: "aval já dado para elas" |
| 16/09/2026 | F6b (fatia vertical Android) e sua posição na ordem | limiares da fase continuam proposta | roadmap v13.3 |
| 16/09/2026 | Solução C (conceito) para páginas recebidas | aprovação de direção, não do SQL | roadmap v13.5 |
| 16/09/2026 | Rodada de decisões: MVP de um slot; ordem das fases; Python ≥ 3.12; publicação de branches de revisão e PRs em rascunho (sem push em `main`, force-push, merge ou release); resultado e histórico; limites operacionais | **não amplia teto de tráfego pago nem autoriza contas adicionais** | roadmap v13.7 |
| 16/09/2026 | F1 liberada em paralelo ao bloqueio da F6a | sem conta real nem tráfego (a F1 usa só fakes) | resposta do Tiago nesta sessão |
| 16/09/2026 | F6a: fonte do APK = imagem de emulador com Play Store; tradução ARM = **não usar**, avaliar alternativa | **conflito aberto**: as imagens x86_64 do emulador dependem de tradução ARM, a menos que a Play entregue uma variante x86_64 do Instagram (hipótese não verificada, que exige login com conta Google, ou seja, um aval com teto). Alternativas: host ARM64 ou dispositivo físico | resposta do Tiago nesta sessão |
| 16/09/2026 | Trailers `Claude-Session` nos commits já publicados: manter como estão | sem reescrita de histórico | resposta do Tiago nesta sessão |

**Consequência prática:** como nenhum teto de tráfego pago nem lista de contas foi registrado, qualquer fase que use tráfego pago ou conta real precisa citar essa lacuna na pré-análise e pedir aval com teto antes de executar.

## Conta de teste e credenciais

- **Credenciais:** `C:\Projetos\instat_env.txt` (local, fora do repositório), lido por `tests/_env_loader.py`. Valores **nunca** entram neste histórico, em commits, PRs, logs ou prints.
- **Regras para não bloquear a conta:** `docs/ROADMAP_MOBILE.md` §7.1 — sessão reaproveitada antes de login, no máximo uma tentativa de login por execução, parada imediata em challenge com resolução manual no app oficial, uma execução por vez, mesmo IP/dispositivo, testes reais opt-in e fora do CI.
- **Estado conhecido da conta (evidência local, não verificada no Instagram):** comentário em `_real_features_smoke.py` indica restrição ("currently-flagged"); `_real_run.log` de 24/04/2026 terminou em `LoginError`. **Pendente:** verificação manual no app oficial antes do primeiro teste real.

| Data | Verificação manual da conta no app oficial | Resultado | Quem |
|---|---|---|---|
| — | ainda não feita | — | — |

## Fases

| Fase | Status | Início | Fim | Evidência | Commits / PR |
|---|---|---|---|---|---|
| F0 | **entregue — aceite de CI ok**: run 35148419267 com lint, 3.12, 3.13 (na reexecução; a 1ª falha foi rate limit do webdriver-manager, corrigido na F1) e build/smoke do wheel | 16/09/2026 | 16/09/2026 | `docs/phase-evidence/fase-0/` | `b06443d`; PR #3 (rascunho); PR #4 de CI fechado sem merge |
| F6a | **bloqueada (passo 3)**: APK via Play Store decidido, mas tradução ARM vetada → conflito (ver Autorizações); redroid exige kernel WSL customizado | 16/09/2026 | — | `docs/phase-evidence/fase-6a/` | — |
| F6b | bloqueada por dependência (F6a) | — | — | — | — |
| F1 | **entregue — sete passos executados; CI ok** (run 35152282343: lint, 3.12, 3.13, build) | 16/09/2026 | 16/09/2026 | `docs/phase-evidence/fase-1/` | `923a9ad` (testes vermelhos), `5255349` (implementação); PR #5 (rascunho, base F0); PR #6 de CI fechado sem merge |
| F2 | **entregue — sete passos executados; CI pendente** | 16/09/2026 | 16/09/2026 | `docs/phase-evidence/fase-2/` | branch `f2/governador-erros-orcamento` |
| F5, F3, F6, F7 | não iniciadas | — | — | — | — |
| F4, F8, F9, F10, F11 | não iniciadas | — | — | — | — |

## Desvios registrados

- **Fase 0 antiga / `6175e5b` (14/09/2026):** esqueleto `instat/mobile` e teste de contrato foram escritos antes do ritual (sem log vermelho registrado). Não há TDD retroativo; o desvio fica registrado aqui.
- **Relato da auditoria v13.6 (15/09/2026):** "suíte offline passa" foi verificado só no ambiente global com extras instalados. Reproduzido na F0 em venv limpo: 2 falhas de teste dependentes de extras.
