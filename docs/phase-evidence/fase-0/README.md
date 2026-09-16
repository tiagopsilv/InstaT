# Fase 0 — Reconciliar base, empacotamento e contrato

Status: em andamento (passo 3 concluído; passo 4 a seguir)
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
Pendente.

## 5. Execução
Pendente.

## 6. Testes
Pendente.

## 7. Prints — todos abertos e lidos
Pendente.
