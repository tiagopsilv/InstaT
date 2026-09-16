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

**Consequência prática:** como nenhum teto de tráfego pago nem lista de contas foi registrado, qualquer fase que use tráfego pago ou conta real precisa citar essa lacuna na pré-análise e pedir aval com teto antes de executar.

## Fases

| Fase | Status | Início | Fim | Evidência | Commits / PR |
|---|---|---|---|---|---|
| F0 | em andamento (passo 3) | 16/09/2026 | — | `docs/phase-evidence/fase-0/` | — |
| F6a | não iniciada | — | — | — | — |
| F6b | não iniciada | — | — | — | — |
| F1, F2, F5, F3, F6, F7 | não iniciadas | — | — | — | — |
| F4, F8, F9, F10, F11 | não iniciadas | — | — | — | — |

## Desvios registrados

- **Fase 0 antiga / `6175e5b` (14/09/2026):** esqueleto `instat/mobile` e teste de contrato foram escritos antes do ritual (sem log vermelho registrado). Não há TDD retroativo; o desvio fica registrado aqui.
- **Relato da auditoria v13.6 (15/09/2026):** "suíte offline passa" foi verificado só no ambiente global com extras instalados. Reproduzido na F0 em venv limpo: 2 falhas de teste dependentes de extras.
