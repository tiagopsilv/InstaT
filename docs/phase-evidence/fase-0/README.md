# Fase 0 — Reconciliar base, empacotamento e contrato

Status: **entregue — sete passos executados; aceite de CI pendente** (o PR da F0 é empilhado sobre outra branch e o workflow só roda para PRs contra `main`)
Base: worktree `f0/python312-packaging` a partir de `5b0b996f7c052367a3b0576e29a74886e3340a0c`
Ambiente: Windows 11, Python 3.12.10 (venv limpo fora do repositório), SQLite 3.49.1

## 1. Análise

Reprodução da evidência que o auditor tinha relatado na v13.6, agora executada (log: `01-analise-reproducao.log`, 16/09/2026 16:03–16:40).

| Item relatado | Resultado reproduzido | Conclusão |
|---|---|---|
| Suíte offline passa | Venv limpo com `.[dev]`: **2 falhas e 2 skips**. `tests/mobile/test_contracts.py::test_build_engines_mixed_cascade_keeps_existing_engines` exige `httpx`; `tests/test_providers.py::...::test_brightdata_engine_accepted_in_engines_list` exige `playwright`. Nenhum dos dois pula quando o extra falta. | **Relato anterior não confirmado integralmente**: só passava no ambiente global, que tinha os extras. Defeito real de teste, que também falharia no CI (instala só `.[dev]`). |
| Wheel sem `instat/mobile` | Confirmado: pacotes `instat`, `instat/config`, `instat/engines`; `Requires-Python: >=3.9`; nenhum arquivo de `instat/logs` | Defeito confirmado |
| Stub mobile como primária → `LoginError` | Confirmado: `LoginError: Failed to login as u`, causa `NotImplementedError` citando "Fase 1" (numeração da v10) | Defeito confirmado; mensagem aponta fase obsoleta (hoje F7) |
| Duração | ~37 min de relógio até o build começar; a suíte emitiu sua última linha de log por volta de 16:07 | Causa **não determinada**; medir no passo 6 com `--durations` |

Achado adicional: `.gitignore` não ignora `build/`, `dist/` nem venvs. Com `packages.find` e `namespaces` no padrão, diretórios sem `__init__.py` (como `instat/logs/diagnostics/`, que na cópia de trabalho principal contém `cookies.json` com `sessionid`) poderiam entrar no wheel como pacote namespace.

## 2. Pesquisa

| Fonte | O que sustenta | O que não comprova |
|---|---|---|
| setuptools — Package discovery (16/09/2026) | "Automatic discovery will **only** be enabled if you **don't** provide any configuration for `packages`"; `namespaces = false` impede incluir diretórios sem `__init__.py` | não garante que dados fora de pacotes sejam excluídos: exige teste do wheel |
| Python Developer's Guide — versions (16/09/2026) | 3.12 em security até 2028-10; 3.13 e 3.14 em bugfix | compatibilidade do InstaT em 3.13/3.14 |
| PyPI instagrapi 3.0.2 (13/09/2026) | `Requires-Python >=3.10`, classificadores 3.10–3.14 | nada sobre o InstaT |
| Keep a Changelog (formato já usado no CHANGELOG) | seção `[Unreleased]` com Changed/Fixed/Removed | decisão de versão (SemVer major) — não é release |

Decisões: descoberta automática com `include = ["instat*"]`, `exclude = ["instat.logs*"]`, `namespaces = false`; testes dependentes de extras pulam explicitamente e o CI instala os extras para executá-los; metadados Python ≥ 3.12 coerentes em todos os arquivos.

## 3. Pré-análise

**Desenho:**
- `pyproject.toml`: `requires-python = ">=3.12"`; classificadores só 3.12 (3.13/3.14 apenas com evidência do passo 6); `[tool.setuptools.packages.find]` com inclusão de `instat*`, exclusão de `instat.logs*` e `namespaces = false`; `ruff target-version = "py312"`; `mypy python_version = "3.12"`.
- `.github/workflows/ci.yml`: matriz com 3.12 (mais 3.13/3.14 só com evidência); instalar `.[dev,httpx,playwright]`; no job de build, instalar o wheel num venv limpo e importar `instat.mobile.engines`.
- `instat/extractor.py`: stub mobile como engine primária falha **antes** de qualquer login com `NotImplementedError` que cita a fase; não vira `LoginError`.
- `instat/mobile/engines.py`: fases atualizadas (`android_ui` → F7, `mobile_api` → F8).
- Testes existentes dependentes de extras: `pytest.importorskip`.
- `README.md`: selo e requisito Python ≥ 3.12. `CHANGELOG.md` `[Unreleased]`: quebra de compatibilidade registrada; **versão do pacote não é alterada** (release não autorizado; bump major é decisão do Tiago).
- `.gitignore`: `build/`, `dist/`, `.venv*/`, `venv*/`.
- `docs/HISTORICO_MOBILE.md`: histórico e autorizações vigentes.

**Critérios de aceite (definidos antes do código):**
1. O wheel contém **exatamente** os pacotes com `__init__.py` sob `instat/` (comparação automática com a árvore), inclui `instat/config/selectors.json`, **nenhum** arquivo sob `instat/logs/` e nenhum `cookies.json`.
2. Metadados do wheel com `Requires-Python: >=3.12`.
3. `pyproject`, CI, README e ruff/mypy coerentes com o piso 3.12 (teste automatizado).
4. `engines=["android_ui", "selenium"]` falha com `NotImplementedError` citando "F7", sem tentar login.
5. Suíte offline sem falhas em venv limpo 3.12 com `.[dev]` (os testes de extras aparecem como skip explícito) **e** com `.[dev,httpx,playwright]` (os mesmos testes executam e passam).
6. Wheel instalado num venv limpo fora do checkout importa `instat` e `instat.mobile.engines`.
7. `ruff` e `mypy` sem erros em 3.12.
8. 3.13/3.14: declarados só se instalação e suíte passarem de fato nessas versões; caso contrário, registrados como não avaliados ou com a falha observada.

**Caminho do print:** renderizar em PNG, a partir dos logs reais, `01-contratos.png` (testes novos antes e depois), `02-baseline-offline.png` (suíte por versão e extras) e `03-wheel.png` (conteúdo do wheel antes e depois). Sem conta real, sem tráfego pago, sem escrita em produção: **nenhum aval necessário**.

## 4. TDD

| Rodada | Horário | Estado do código | Resultado | Log |
|---|---|---|---|---|
| Vermelho | 16/09/2026 16:46:05 | sem alterações (HEAD `5b0b996`) | 13 falhas, 3 aprovações entre os 16 testes novos | `04-tdd-vermelho.log.txt`; commit `test(f0)` antes da implementação |
| Vermelho, ciclo adicional (stealth) | 16/09/2026, antes da correção do extra | `stealth` sem `setuptools` | 1 falha: `['undetected-chromedriver>=3.5']` | `05b-tdd-vermelho-stealth.log.txt` |
| Verde | 16/09/2026 17:30:35 | após a implementação | 17 de 17 | `05-tdd-verde.log.txt` |

As 3 aprovações no vermelho têm motivo: `selectors.json` já ia no wheel; o Dockerfile já usava 3.12; e a exclusão de logs passava **só porque a lista de pacotes era fixa** — o teste virou a guarda contra regressão da descoberta automática (ele planta um `cookies.json` falso antes do build).

## 5. Execução

- `pyproject.toml`: `requires-python >=3.12`; classificador 3.12; descoberta automática com `include = ["instat*"]`, `exclude = ["instat.logs*"]`, `namespaces = false`; `ruff` py312; `mypy` 3.12; extra `stealth` com `setuptools>=60`.
- `.github/workflows/ci.yml`: matriz 3.12 e 3.13 (3.13 para gerar evidência em Linux, fora dos classificadores); extras `httpx`/`playwright` instalados; smoke test do wheel fora do checkout.
- `instat/extractor.py`: stub mobile como engine primária falha na construção com `NotImplementedError`.
- `instat/mobile/engines.py`: fases F7/F8; método público `not_implemented_error`.
- Testes: pulo explícito sem extras (`test_contracts.py`, `test_providers.py`).
- README, CHANGELOG (`[Unreleased]`, versão não alterada), `.gitignore`.

**Defeitos encontrados durante a execução e corrigidos (fora do TDD original, registrados):**
1. **Regressão introduzida pela própria F0:** a mensagem dos stubs passou a dizer "fase" minúsculo e quebrou o teste existente `test_mobile_engines_are_stubs_that_fail_loud` (exige "Fase"). A implementação foi corrigida; o teste não foi alterado.
2. **Extra `stealth` quebrado em 3.12:** `undetected-chromedriver` 3.5.5 importa `distutils` (removido do Python 3.12) — reproduzido no wheel instalado. Corrigido com `setuptools>=60` no extra, com ciclo vermelho → verde próprio. Só a importação foi verificada; o modo stealth em execução exige Chrome e **não foi testado**.
3. **Teste que abria navegador real:** `test_account_rotation.py::test_inherits_imap_config_engines_timeout` construía o `InstaExtractor` real via `patch(..., side_effect=InstaExtractor)`, abrindo um Firefox visível com credenciais falsas que nunca era fechado. O Firefox órfão manteve a saída aberta e fez a suíte levar ~30 min de relógio duas vezes. Corrigido: o mock só captura argumentos (62,8 s → 0,29 s, nenhum navegador aberto).
4. **`ruff` já falhava na base `5b0b996`** (5 erros: ordem de imports em 3 módulos; variável não usada e `lambda` atribuída em `tests/test_recent_gaps.py`). Corrigidos.

## 6. Testes

Ambiente: Windows 11; venvs limpos fora do repositório. Logs: `06-testes-312.log.txt`, `06-testes-313.log.txt`, `06-tentativa-uv-313-314.log.txt`.

| Verificação | Resultado |
|---|---|
| Suíte offline, 3.12.10, `.[dev]` | 633 passed, 4 skipped (extras ausentes, explícitos), 0 failed — 140,8 s |
| Suíte offline, 3.12.10, `.[dev,httpx,playwright]` | 637 passed, 0 skipped, 0 failed — 147,4 s |
| Suíte offline, 3.13.15, `.[dev,httpx,playwright]` (sem `stealth`) | 637 passed, 0 failed — 139,4 s |
| 3.14.7 | **não avaliado**: o `python.exe` do venv criado pelo `uv` foi bloqueado por política de Controle de Aplicativo do Windows (erro 4551). A política não foi contornada |
| `stealth` em 3.13 | **não avaliado**: o build do `undetected-chromedriver` pelo `uv` foi bloqueado pela mesma política |
| `ruff check instat tests` | All checks passed |
| `mypy` | Success: no issues found in 42 source files |
| Wheel | pacotes `instat`, `instat/config`, `instat/engines`, `instat/mobile`; 0 arquivos de `instat/logs`; 0 `cookies.json`; `Requires-Python: >=3.12` |
| Wheel instalado em venv limpo com `[httpx,playwright,stealth]`, fora do checkout | `instat`, `instat.mobile.engines` (`android_ui`, `mobile_api`) e `undetected_chromedriver 3.5.5` importam |
| CI remoto | **não executado** nesta fase |

Interpretadores 3.13 e 3.14 foram instalados pelo `uv` no diretório gerenciado do usuário (`%APPDATA%\uv\python`); remoção: `uv python uninstall 3.13 3.14`.

**Critérios de aceite:** 1 wheel com todos os subpacotes, sem logs/cookies — cumprido; 2 `Requires-Python >=3.12` — cumprido; 3 coerência de metadados — cumprido; 4 stub com F7 sem login — cumprido; 5 suíte sem falhas com e sem extras — cumprido; 6 wheel fora do checkout — cumprido; 7 `ruff`/`mypy` — cumprido; 8 3.13/3.14 declarados só com evidência — cumprido (3.13 só na matriz de CI; 3.14 não avaliado). **Pendente:** execução real do CI.

## 7. Prints — todos abertos e lidos

| Arquivo | O que vi | Problema/melhoria | Ação | Leitor/data |
|---|---|---|---|---|
| `01-contratos.png` | 17 testes; vermelho 16:46:05 com 13 falhas e 3 aprovações; verde 17:30:35 com 17 aprovações; stealth marcado como ciclo adicional | não explica por que `excludes_logs_and_cookies` já passava; espaço em branco no rodapé | explicação registrada no passo 4; espaço aceito | auditor (IA), 16/09/2026 |
| `02-baseline-offline.png` (1ª versão) | números corretos | textos cortados; linhas aprovadas sem cor; **3.14 "não avaliado" em vermelho, como se fosse falha** | regra de cores e larguras corrigidas; regerado | auditor (IA), 16/09/2026 |
| `02-baseline-offline.png` (final) | base em vermelho (2 falhas); finais em verde; 3.14 em amarelo; tempos iguais aos logs | "4 skipped" em verde é aceitável (extras ausentes explícitos) | nenhuma | auditor (IA), 16/09/2026 |
| `03-wheel.png` (1ª versão) | pacotes, logs, cookies e `Requires-Python` corretos | **linha do extra stealth vazia** (METADATA com `\r\n` não casava com a regex); "não" da linha mobile sem destaque | regex e cores corrigidas; regerado | auditor (IA), 16/09/2026 |
| `03-wheel.png` (final) | stealth antes `undetected-chromedriver>=3.5`, depois `+ setuptools>=60`; mobile não → sim; importação fora do checkout ok | nenhum | nenhuma | auditor (IA), 16/09/2026 |
