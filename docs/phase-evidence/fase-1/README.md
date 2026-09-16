# Fase 1 — Sessão persistente confiável e identidade web

Branch: `f1/sessao-identidade-web`, criada a partir de `b06443d` (F0). A F1 foi liberada em paralelo pelo usuário.
Autorização: nenhuma conta real e nenhum tráfego. Todos os testes usam fakes ou o fake server local. A conta de teste (§7.1) **não** é usada.

## Passo 1 — Análise (medida)

Script: [`f1_analise.py`](f1_analise.py). Log: [`01-analise.log.txt`](01-analise.log.txt).

| # | Achado | Medição |
|---|---|---|
| A1 | `SessionCache.load` quebra com arquivo inválido | JSON truncado → `JSONDecodeError`; sem `saved_at` → `KeyError`; `saved_at` em texto → `TypeError` |
| A2 | O tipo e a data dos dados carregados não são validados | `cookies` em string é devolvido; `saved_at` no futuro é aceito |
| A3 | Fronteira de TTL | Idade exatamente igual a 3600 s é aceita |
| A4 | Diretório do cache depende do cwd | Dois cwds geram dois caches distintos |
| A5 | Escrita não atômica | `write_text` direto, seguido de `chmod` |
| B1 | Restorer (Selenium) tem falso positivo | challenge, checkpoint, suspended e auth_platform → sucesso (**4 de 4**) |
| B2 | Sem validação de identidade | `ds_user_id` de outra conta → sucesso |
| B3 | Playwright restaura só por URL | Não verifica `sessionid` |
| C1 | `login()` pula o BlockDetector na restauração | challenge → `True`; detector não é chamado |
| D1 | UA incoerente no Selenium/Firefox | Motor Gecko declara Chrome 89 / Android 8 |
| D2 | UA incoerente no Playwright | chromium, firefox e webkit usam o mesmo UA do Chrome 89 |
| E1 | Formulários de login em 10 reinícios | A cada 10 min: 1 de 10 (a restauração não renova `saved_at`); a cada 2 h: 10 de 10 |
| F1 | Testes "offline" dependem de rede | `tests/test_login.py` não substitui o `GeckoDriverManager`; o CI 3.13 falhou por rate limit e passou ao reexecutar |

## Passo 2 — Pesquisa

- **Escrita atômica.** Grava num temporário no mesmo diretório, faz `fsync` e depois `os.replace`. No Windows, `os.replace` sobrescreve de forma atômica (MoveFileEx com REPLACE_EXISTING). Fontes:
  - [DEV — atomic writes](https://dev.to/susumun/atomic-writes-how-tempfile-osreplace-prevent-corrupted-json-2lo8)
  - [python-atomicwrites](https://python-atomicwrites.readthedocs.io/)
  - [zetcode os.replace](https://zetcode.com/python/os-replace/)
- **UA do Firefox.** O formato é `Mozilla/5.0 (Android <v>; Mobile; rv:<gecko>) Gecko/<gecko> Firefox/<v>`. Um motor Gecko que declara Chrome é detectável pelas APIs do navegador. Fonte: [MDN — Firefox UA](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/User-Agent/Firefox).
- **Estados de bloqueio.** Os indicadores de URL e HTML já catalogados em `instat/block_detector.py` são reutilizados, sem lista nova.

## Passo 3 — Pré-análise (critérios definidos antes dos testes)

Critérios de aceite:

1. **Leitura do cache.** Qualquer arquivo inválido vira cache miss, sem exceção: JSON ruim, schema, tipos, `saved_at` no futuro (tolerância de 300 s) ou versão desconhecida.
2. **TTL.** A idade precisa ser menor que o TTL; idade igual ao TTL expira. Uma restauração validada renova `saved_at`.
3. **Escrita atômica.** Temporário 0600 no mesmo diretório, depois `os.replace`. Uma falha no meio preserva o arquivo anterior.
4. **Diretório estável.** O padrão é `INSTAT_SESSION_DIR` ou `~/.instat/sessions`.
   - O `.instat_sessions` do cwd é lido como fallback legado.
   - O Dockerfile define `INSTAT_SESSION_DIR=/app/.instat_sessions`, o que preserva o volume do docker-compose.
   - A detecção de bloqueio ganha `suspended` (`/accounts/suspended/`).
5. **Schema v2.** `{version: 2, backend, username, ds_user_id?, saved_at, cookies}`.
   - `backend` registra só a procedência. Os cookies web são compartilhados entre Selenium, Playwright e httpx; rejeitá-los por backend geraria logins extras, o gatilho que a F1 quer evitar.
   - Um `username` diferente do pedido é miss.
   - Um `ds_user_id` salvo que não aparece nos cookies do mesmo arquivo é miss.
   - O v1 continua legível.
   - *Revisado na pré-análise, antes de qualquer teste.*
6. **Restorer.** Sucesso exige, ao mesmo tempo:
   - URL fora de login e dos estados de bloqueio do BlockDetector;
   - `sessionid` presente;
   - `ds_user_id` igual ao salvo, quando houver.
   Em challenge, devolve um resultado distinto e não abre formulário.
7. **`login()`.** Após restaurar, roda o BlockDetector. Um challenge levanta a exceção de bloqueio, sem tentar o formulário.
8. **Playwright.** Usa a mesma validação (item 6).
9. **UA.** O Selenium/Firefox não sobrescreve o UA com Chrome. No Playwright, o UA é compatível com o browser_type:
   - chromium: Chrome;
   - firefox: Firefox;
   - webkit: Safari.
10. **Reinícios.** 10 reinícios com sessão válida abrem **0** formulários. Em cenários controlados, falsos positivos = **0**.
11. **Testes sem rede.** `test_login.py` substitui o `GeckoDriverManager`. O marcador `real` fica registrado e é opt-in (`INSTAT_REAL_TESTS=1`); o CI o exclui.
12. **Limite de recuperação.** No máximo 1 restauração por `login()`. Uma falha cai para o formulário uma única vez.

Prints, em `docs/phase-evidence/fase-1/`, usando o fake server local estendido com a rota `/challenge/`:

- `01-restaurada.png`
- `02-expirada.png`
- `03-challenge.png`

Cada um será lido após ser gerado.

Fora de escopo: conta real, Instagram, proxy e Android.
