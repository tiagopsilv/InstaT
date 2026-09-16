# Pré-análise da revisão v13.6 → v13.7 (escrita ANTES dos testes novos e do modelo v13.7)

Escopo: aplicar decisões aprovadas pelo Tiago em 16/09/2026 e revisar I1–I11, schema e SQL (itens A–F).
A avaliação externa desta rodada NÃO abriu a v13.6 nem reproduziu testes: não é validação independente.

## Leitura crítica dos testes existentes (v13.6)
- A rodada "vermelha" da v13.6 só provou AUSÊNCIA DE MÓDULO (ModuleNotFoundError). Não é falha comportamental por cenário.
  Falha comportamental por cenário existe apenas contra o SQL literal da v13.5.
- T09 (coordenador com BEGIN IMMEDIATE durante o backup) passou, mas o procedimento bloqueia heartbeat e escritores:
  REJEITADO como padrão pela decisão E. Substituído por B01–B05.
- T03 passou na v13.6 em parte porque a nova execução recomeça em pos 0; não verificava coexistência de páginas
  confiáveis de contas com mesma geração na mesma posição. Reforçar.
- T08c e T11 dependiam de "sem próximo cursor = fim" e "K rodadas sem novos = fim". A decisão C/F exige evidência
  positiva de fim. Expectativas revisadas (end_marker explícito) — mudança registrada, não TDD retroativo.
- Novos testes rodam primeiro contra o modelo v13.6 existente: falha comportamental real quando a API existe;
  "API AUSENTE" quando o adaptador v13.6 não tem o método (não conta como falha comportamental).

## Decisões aplicadas (aprovadas)
1. MVP: 1 slot, 1 conta ativa, 1 worker. Testes de concorrência da persistência continuam obrigatórios (C01).
2. Ordem: F0 ∥ (F6a → F6b) → F1 → F2 → F5 → F3 → F6 → F7. F4 independente após F5. F8/F9 por necessidade demonstrada.
   F10/F11 por gates. Aprovações de F6b e solução C preservadas.
3. Python ≥ 3.12 (quebra de compatibilidade). 3.13/3.14 só declarados com evidência.
4. Publicar branches de revisão + PRs draft; inspeção de segredos antes; sem main/force/merge/release.
5. Resultado: união histórica exposta como histórico observado; resultado por run; selected_run_id = run mais recente
   que cumpriu critérios, senão a mais recente apresentada como parcial; datas separadas; completude só na run selecionada;
   ausência posterior não é unfollow; contrato legado intacto.
6. Limites: 2 releituras adicionais por posição; 3 execuções por job; repetição idempotente não consome tentativa;
   challenge/restrição interrompem independentemente do saldo; requeue após restrição = liberação manual + validação
   positiva da sessão; tempo sozinho não libera.

## A. Idempotência — especificação
- Tentativa = UMA leitura remota (uma requisição/resposta de API ou uma captura de tela após uma ação).
- attempt_id = UUIDv4 gerado imediatamente ANTES do envio, gravado no spool local durável do worker junto com a resposta
  bruta, ANTES do commit. Retry após crash lê o spool: mesmo attempt_id e mesmo conteúdo. Spool perdido = nova leitura,
  novo attempt_id, contada como reprocessamento.
- Escopo: único no banco; pertence a exatamente uma execução. attempt_id existente com run_id diferente = erro.
- Conteúdo canônico "page-v1": JSON UTF-8, chaves ordenadas, separadores compactos, sem timestamps nem qualidade.
  Campos: schema, run_id, pos, cursor_in, cursor_out, items (ordem recebida; cada item {pk|null, u}), page_ok,
  empty_state, loading, end_marker, screen_hash, counter.
  Normalização de username: NFC, strip, minúsculas. content_hash = SHA-256 dos bytes canônicos.
- Mesmo attempt_id + mesmo hash (mesma run) → duplicate: nada gravado; progresso, observações, contadores e limite de
  releitura intactos. Mesmo attempt_id + hash diferente → erro AttemptConflict explícito.

## B. Proveniência e confiança
- observations(page_id, member_id) → pages.run_id → runs.job_id. Fusão reaponta member_id, preserva page_id.
- Confiança de uma run = observações trusted DAQUELA run. Nunca de outra run nem de outro job.
- Run parcial não completa por união histórica: completude e coleta calculadas só com a própria run.

## Resultado (decisão 5)
- run_result(run): membros trusted da run, estado, suspect_open, gaps, verificação de contador, status complete|partial|running.
- job_view(job): selected_run_id, selected_run_status, selected_run_at, selected_members, selected_suspect_open,
  last_attempt_run_id, last_attempt_state, last_attempt_stop_reason, last_attempt_at, history_observed
  {username: first/last trusted}, history_label fixo. Sem campo de "removido".

## C. UI — especificação
- pos = contador interno de rodadas; não é posição remota.
- Continuidade: duas telas trusted consecutivas no mesmo segmento, com tela diferente, devem compartilhar ≥1 membro;
  sem sobreposição → runs.continuity_gaps += 1 → run não completa (partial, continuity_gap).
- Reinício do app = novo segmento na mesma run: segment+1, releitura reinicia, contadores zerados; não consome execução.
- Releitura termina por cobertura ≥ replay_ratio dos membros confiáveis do job anteriores ao segmento, OU por
  frontier_confirm_screens telas CONSECUTIVAS com membro novo (uma inserção isolada não encerra a releitura).
- Fim confirmado exige evidência positiva: end_marker reconhecido pelo classificador, OU (K rodadas sem novos após
  releitura E contador exato compatível só com a run). K rodadas sem novos sem essa evidência → end_unknown (no_end_evidence).
- Tela repetida S vezes → end_unknown (screen_stuck).
- Backend com cursor: fim também exige end_marker (sinal explícito de fim da API); cursor ausente sozinho → end_unknown.

## D. Motivos de parada
lease_lost (nada grava) · account_restricted/challenge (nada novo; em voo → suspect se posse) ·
cancel_requested (nada novo; em voo → qualidade normal) · technical_error (resposta falha não grava; nova run automática
permitida dentro do limite) · end_confirmed/end_unknown · reread_limit.
Nova run automática só após lease_lost ou technical_error. Demais motivos exigem requeue manual.

## E. Backup — especificação
- Padrão: backup ONLINE por conexão própria, sem segurar bloqueio de escrita; prazo máximo; contenção tratada.
  Destino escrito como .partial e só promovido após verificação; timeout remove .partial e retorna status timeout.
- Verificação do backup online: ponto de consistência = estado contido na própria cópia (snapshot). Verificar
  integrity_check, foreign_key_check e invariantes internas (uma trusted por (run,pos); next_pos = posições trusted;
  observações referenciam páginas e membros). NÃO comparar com contagens da origem que continuou mudando.
- Comparação exata com a origem: só em janela de manutenção (nenhuma run ativa, nenhuma concessão vigente); digest
  por tabela ordenada por PK.
- Guarda: backup a partir de conexão com transação aberta → erro imediato (evita o travamento reproduzido na v13.6).

## F. Sanidade — parâmetros (sanity-v1; operacionais; NÃO documentados pelo Instagram; calibrar na F6b)
| Parâmetro | Significado | Unidade | Inicial |
|---|---|---|---|
| K | telas trusted consecutivas no segmento, após releitura, com tela diferente e sem indicador de carregamento, sem membro novo para o job | telas | 3 |
| S | telas trusted consecutivas com screen_hash idêntico após ação de rolagem | telas | 3 |
| replay_ratio | fração dos membros trusted do job anteriores ao segmento que precisam ser reobservados | razão (0,1] | 0.95 |
| frontier_confirm_screens | telas consecutivas com ≥1 membro novo para encerrar a releitura pela fronteira | telas | 2 |
| counter_tolerance | tolerância absoluta ao comparar coleta da run com contador exato/arredondado | membros | max(2, ceil(1% de hi)) |
| counter_stale_s | idade máxima da leitura do contador em relação ao último commit | segundos | 1800 |
| max_rereads_per_pos | releituras adicionais por posição (tentativas distintas) | tentativas | 2 |
| max_runs | execuções totais por job | execuções | 3 |
Sem evidência suficiente → end_unknown / partial. Contador compatível não prova completude.

## Testes planejados (IDs) — rodar contra v13.6 existente ANTES do modelo v13.7
Existentes revisados: T01 T02 T03(+coexistência) T04 T05 T06 T07 T08a T08b T08c(end_marker) T10 T10b T11(end_marker) T12 X01 X02
Novos: N01 Ana/Bruno×Ana/Carla · N02 última tentativa falha após run válida · N03 confiável no histórico, suspeito na run
atual · N04 commit repetido após crash · N05 mesma tentativa conteúdo diferente / canônico igual · N07 fusão sem
contaminação · N08 attempt_id em outra run · N09 run parcial não completa por união · U01 inserção na releitura ·
U02 remoção/reordenação · U03 lacuna de continuidade · U04 reinício do app · U06 fim sem evidência · D03 cancelamento ·
D04 falha técnica · D05 worker obsoleto · L01 limite de releitura e duplicata · L02 releitura esgotada sem ciclo ·
L03 limite de execuções · L04 liberação manual com validação · B01 backup online com heartbeat e escritor ·
B02 backup lento com prazo · B03 manutenção drenada · B04 guarda de conexão com transação · B05 verificação pela cópia ·
C01 corrida de reivindicação entre processos
