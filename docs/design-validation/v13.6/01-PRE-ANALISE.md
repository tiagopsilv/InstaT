# Pré-análise da revisão v13.5 → v13.6 (escrita antes de testes e modelos)

Escopo: revisão documental. Nenhuma implementação de produção. Nenhum teste com Android, Instagram ou proxy.
Artefatos de verificação: modelos SQLite isolados em scratchpad; não fazem parte do InstaT.

## Classificação de evidência usada
- CÓDIGO LOCAL: `5b0b996` não tem `jobs`, concessão nem reivindicação (git grep). Itens 1–6 são lacunas do SQL PROPOSTO.
- SQL PROPOSTO v13.5: transcrito literalmente da §6.3 para `models_v135.py`.
- ANALOGIA: Kafka/SQS/Temporal/Stripe sustentam regras de posse, reprocessamento e idempotência; não o Instagram.
- HIPÓTESE: comportamento do Instagram (contador, lista vazia silenciosa) até F6b/F7/F8.

## Decisões de desenho (a validar pelos testes)

### D1 Identidade (itens 1 e 3)
- Execução = `runs.run_id` (INTEGER AUTOINCREMENT, globalmente único). Contém conta, proprietário, geração e `cursor_context`.
  Resolve colisão de `lease_gen` entre contas: nada compara geração sem conta; páginas referenciam `run_id`.
- Tentativa = `pages.attempt_id` (gerado pelo worker ANTES da requisição) `UNIQUE` + `content_hash`.
  Mesmo `attempt_id` + mesmo hash = repetição do mesmo commit → no-op idempotente.
  Mesmo `attempt_id` + hash diferente = erro (conflito), nunca sucesso. [Stripe: chave identifica retries; parâmetros diferentes → erro]
- Posição lógica = `(run_id, pos)` ordinal; NÃO o texto do cursor (NULL é distinto em UNIQUE no SQLite).
  Várias observações por posição (suspect, depois nova leitura trusted). No máximo uma trusted: índice parcial
  `UNIQUE(run_id, pos) WHERE quality='trusted'` + predicado `runs.next_pos = :pos`.
- Violação de UNIQUE fora do caso attempt_id+hash igual = erro classificado, não sucesso.

### D2 Reivindicação (item 2)
Na mesma transação: solicitante com concessão válida (conta, proprietário, geração, validade, auth_state ok,
restricted_until, cooldown) → job aberto → run anterior sem posse vigente → cria run → atualiza job com predicado otimista.

### D3 Motivos de parada (item 3)
lease_lost: nenhuma gravação. account_restricted: nenhuma operação nova; resposta já em voo pode virar suspect se a posse
for confirmada na transação. cancel/budget: nenhuma operação nova; resposta em voo segue regras normais. technical_error:
resposta falha não é gravada. Heartbeat classifica 0 linhas na MESMA transação (restrita × posse perdida).
Heartbeat atrasado além de ttl/2 = posse não verificável = tratar como lease_lost.
F5(a) dividido: revogação antes do envio (nada enviado/gravado) × revogação com requisição em voo (suspect).

### D4 Proveniência (item 4)
`members` (identidade) + `observations(page_id, member_id)` + `pages` + `runs` + `member_merges`.
Confiança nunca é coluna global: resultado do job = membros com observação em página trusted de runs DESTE job.
Fusão reaponta observações e registra `member_merges`; timestamps derivam das observações.

### D5 Recuperação (item 5)
suspect_total = histórico (nunca diminui). suspect_open(run) = suspect sem trusted na mesma (run,pos).
Nova leitura trusted na mesma (run,pos) resolve; o job pode completar no MESMO job. Limites: max_rereads_per_pos,
max_runs. Motivo account_restricted/challenge: sem releitura na run; nova run exige `requeued_at` posterior (sem retry automático).

### D6 Progresso sem cursor (item 6)
`jobs.progress_kind` ∈ cursor|scan. `runs.progress_state` ∈ not_started|in_progress|end_confirmed|end_unknown.
Scan: pos = rodada; tela repetida (hash igual) → stuck_rounds; rodada nova sem membro novo → rounds_without_new,
contada só após replay_done (membro novo OU cobertura ≥ replay_ratio dos membros confiáveis já conhecidos do job).
stuck ≥ S → end_unknown. Ausência de cursor nunca é fim.

### D7 Sanidade (item 7) — política v1, versionada por página
Página trusted exige TODOS: posse; auth_state ok; classificador reconhece positivamente a resposta/tela; itens bem-formados
sem duplicata interna; não vazia (exceto pos 0 com contador exato 0 lido na run e estado vazio reconhecido).
Job complete exige: end_confirmed; suspect_open = 0; contador não inconsistente. Contador ∈ exato|arredondado|ausente|desatualizado.
`complete` ≠ 100%; precisão/recall só contra conjunto conhecido.

### D8 Backup (item 8)
Ponto de consistência: conexão coordenadora com BEGIN IMMEDIATE (exclui escritores) → contagem/hash da origem →
backup(pages=-1) pela mesma conexão → fim da transação → verificação do destino. [sqlite backup: snapshot do início da cópia]

## Testes planejados (esperam o comportamento corrigido; rodam contra v13.5 e v13.6)
T01 retry exato com cursor NULL | T02 suspect→trusted mesmo cursor/geração | T03 contas com geração igual |
T04 reivindicação sem concessão | T05 revogação com resposta em voo | T06 perda de posse antes do commit |
T07 confiança histórica em job novo | T08 progresso UI sem cursor | T09 ponto de consistência do backup |
T10 fusão preserva proveniência | T11 suspeita histórica × aberta | T12 sem nova run automática após restrição
