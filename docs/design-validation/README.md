# Validação do desenho de persistência (modelos isolados)

Artefatos citados como [E2] e [E3] na §6.3 do `docs/ROADMAP_MOBILE.md`.

- **Não são implementação do InstaT** e não testam Android, Instagram nem proxy.
- Modelos SQLite em Python 3.12.10 / SQLite 3.49.1, escritos pelo mesmo auditor que escreveu os testes.
- A ordem (pré-análise → testes → rodada vermelha → modelo → rodada verde) está registrada nos horários dos logs.
- `v13.6/`: SQL literal da v13.5 × desenho v13.6. A rodada vermelha da v13.6 só prova **módulo ausente**.
- `v13.7/`: testes novos rodados contra o modelo v13.6 existente (falha comportamental ou `API_AUSENTE`) e depois contra o v13.7.
- Caminhos locais foram substituídos por `<scratchpad>`/`<temp>` e códigos de cor removidos.

Reprodução (Python 3.12):

```
cd docs/design-validation/v13.7
python test_v137.py models_v137:V137 resultado.json
```

`test_v137.py` procura `models_v136.py` em `../v13.6` — ajuste `sys.path` se mover os arquivos.
