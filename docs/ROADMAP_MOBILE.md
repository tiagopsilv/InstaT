# InstaT Mobile — Roadmap de Implementação

> **Objetivo:** fazer o InstaT (hoje Selenium, Playwright e httpx, com identidade *desktop web*) se apresentar ao Instagram como **um celular Android real**. O plano usa um aparelho emulado em Docker, proxy móvel DataImpulse, identidade de device coerente, TLS igual ao do app, comportamento humano, aquecimento de sessão e limite de requisições adaptativo.
>
> **Documento vivo.** Ao terminar, toda fase atualiza o [Histórico](#7-histórico-de-fases) e a pasta de evidências.
> v1 em 14/09/2026 · v2 em 14/09/2026 (pesquisa aprofundada, critérios de aceite, gates e template de fase) · **v3 em 14/09/2026** (correções da pesquisa: certificate pinning, tradução ARM no redroid, `sessttl` da DataImpulse, curl_cffi sem perfil OkHttp, ritmo de leitura medido pela comunidade, mapa refeito sobre as classes reais do InstaT — ver [Correções da v3](#correções-da-v3)) · **v4 em 14/09/2026** (contrato público imutável em [§3.1](#31-contrato-público-imutável): o mobile é opt-in via novos nomes de engine e a API atual não muda) · **v5 em 14/09/2026** (assinatura `signed_body`/`ig_sig_key_version`, limites reais do GramAddict, modo de GPU do redroid, custo ~US$2/GB da DataImpulse) · **v6 em 14/09/2026** (dor atual: [§1.9](#19-o-gargalo-real-hoje-login-travando-e-sem-paralelismo-resiliente) + [Fase 11 prioritária](#fase-11--paralelismo-resiliente-multi-conta--prioridade): sessão persistente sem re-login, AccountPool com failover e resume por cursor) · **v7 em 14/09/2026** (apêndice de implementação da Fase 11: reuso de cookie no Selenium, concorrência com threads, `AccountPool` em SQLite espelhando o twscrape, idempotência por `pk`) · **v8 em 14/09/2026** (decisão de arquitetura §3.2 sobre Selenium × Playwright) · **v9 em 14/09/2026** (⚠️ **correção após pesquisa** em [§3.2](#32-decisão-de-arquitetura-selenium--playwright--migração-gradual-sem-big-bang): o stealth do Playwright **não** é mais fraco — é mais limpo; revisada de "não substituir" para **migração gradual** para Playwright começando pela Fase 11, sem *big-bang*) · **v10 em 14/09/2026** (concretudes: mapa de endpoints da API privada com `rank_token`/`max_id`/`X-IG-App-ID` na Fase 5, ferramentas de verificação de TLS `ja4`/`peetprint_hash` na Fase 4, teto de ~200 req/h por IP e quebra a cada 2–4 semanas por rotação de `doc_id`).

### Correções da v3
| O que a v2 dizia | O que a pesquisa mostrou | Onde mudou |
|---|---|---|
| Capturar headers com mitmproxy + CA de sistema | O app do IG usa **certificate pinning**; CA de sistema não basta. É preciso *unpinning* com Frida em um container **de laboratório** com root ([Instagram-SSL-Pinning-Bypass](https://github.com/0xSHAK1B/Instagram-SSL-Pinning-Bypass), [tópico GitHub](https://github.com/topics/instagram-ssl-pinning-bypass)) | 1.5, Fases 1, 3 e 4 |
| "redroid roda o APK" | O redroid 15+ anuncia `arm64-v8a` **sem tradutor**, e o app fecha na abertura ([issue #933](https://github.com/remote-android/redroid-doc/issues/933)). Funciona em **Android 11–13 com `libndk_translation`** (confirmado pelo [instadroid](https://github.com/ivylikethevine/instadroid)), adicionado via [redroid-script](https://github.com/ayasa520/redroid-script) (`-n`; `-m` para Magisk só no lab) | 1.2, Fase 1 |
| Sticky "até 120 min" sem controle | A duração se controla pelo parâmetro **`sessttl.<min>`** no usuário | 1.3, Fase 2 |
| "curl_cffi com perfil OkHttp" | O curl_cffi **não tem target OkHttp**. Os alvos móveis prontos são `chrome_android`/`safari_ios`, e para OkHttp é preciso `ja3` + `akamai` + `extra_fp` próprios | 1.4, Fase 4 |
| Rate limit só em teoria | A comunidade relata **50–100 itens por página**, jitter de **2–3 s** em listas grandes, **30 min** de pausa em `please_wait_a_few_minutes` e horas + login manual no app em `feedback_required` | 1.7, Fase 8 |
| Device props "coerentes" sem mecanismo | O redroid aceita `ro.product.system.*` e `androidboot.redroid_width/height/dpi` como argumentos do container, mas há relatos de props que **não fazem efeito** ([issue #481](https://github.com/remote-android/redroid-doc/issues/481)), então é preciso **medir** com `getprop` | 1.5, Fase 3 |

---

## Sumário
0. [Ritual de fase (obrigatório)](#0-ritual-de-fase-obrigatório)
1. [Pesquisa consolidada](#1-pesquisa-consolidada)
2. [Arquitetura alvo](#2-arquitetura-alvo)
3. [Princípios de projeto](#3-princípios-de-projeto)
4. [Fases](#4-fases)
5. [Mapa com o código existente](#5-mapa-com-o-código-existente)
6. [Riscos e gates](#6-riscos-e-gates)
7. [Histórico de fases](#7-histórico-de-fases)
8. [Template de registro de fase](#8-template-de-registro-de-fase)
9. [Fontes](#9-fontes)

---

## 0. Ritual de fase (obrigatório)

Toda fase segue **os 7 passos, na ordem, sem exceção**. Nenhum passo é pulado por parecer simples.

| # | Passo | Entregável obrigatório | Critério para avançar |
|---|-------|------------------------|----------------------|
| 1 | **Análise do problema** | Números medidos (logs, `ExtractionResult.to_dict()`, capturas) em `docs/phase-evidence/fase-N/01-analise.md` | Há pelo menos 1 métrica de baseline, e nada é "achismo" |
| 2 | **Pesquisa na internet + GitHub** | Tabela *opção × prós × contras × fonte* e a decisão justificada | ≥ 3 fontes, incluindo ≥ 1 repositório GitHub ativo e ≥ 1 doc oficial |
| 3 | **Pré-análise (desenho)** | Diagrama/contratos + **caminho do print** (quem loga, em qual device/container, que estado preparar, se toca conta real) + critérios de aceite | Se tocar conta real ou gastar banda paga relevante, **aval do Tiago registrado junto com o desenho** |
| 4 | **TDD** | Testes escritos **antes**, comprovadamente vermelhos (log do pytest salvo) | Todos falham pelo motivo certo |
| 5 | **Execução** | Código | Testes da fase verdes |
| 6 | **Testes** | `pytest -m "not e2e and not mobile"`, `ruff`, `mypy` (CI) + suíte `mobile`/`e2e` local | 100% verde, sem teste desativado |
| 7 | **Print + leitura de CADA imagem** | PNGs em `docs/phase-evidence/fase-N/prints/NN-descricao.png` + `07-leitura-prints.md` com **uma linha por imagem** (o que viu, erro ou melhoria) | Todas as imagens lidas e os achados triados (corrigido ou virou issue) |

**Regras:**
- Fase só vira **concluída** com os 7 ✅. Sem os prints lidos, o status é **"entregue, passo 7 pendente"**.
- **O caminho do print se resolve no passo 3, nunca no 7.**
- Prints só com **contas de teste dedicadas** do Tiago. Nunca conta de terceiros e nunca dados pessoais de terceiros visíveis sem necessidade (borrar antes de commitar).
- Captura de print padronizada: `adb exec-out screencap -p > NN.png`, noVNC (budtmo) ou `DiagnosticCollector` (bundle com `screenshot.png` + `uiautomator dump`).

---

## 1. Pesquisa consolidada

### 1.1 Duas estratégias "mobile"

| | **A. API privada móvel** | **B. App real em Android/Docker** |
|---|---|---|
| Como | HTTP igual ao app: `i.instagram.com/api/v1/...`, UA `Instagram <ver> Android (...)`, headers `X-IG-*`, body assinado | APK oficial em redroid/emulador, controlado por uiautomator2/ADB |
| Prós | Rápida, JSON estruturado, pouca banda (importante: DataImpulse cobra por GB) | TLS, headers, assinatura e telemetria vêm **do próprio app**, logo são autênticos por construção |
| Contras | Precisa igualar TLS/HTTP2, headers e device com precisão e acompanhar cada versão do app | Lenta, pesada, dados vêm da UI, emulador é detectável |
| Referências | [instagrapi](https://github.com/subzeroid/instagrapi), [molkex/instagram-private-api](https://github.com/molkex/instagram-private-api), [dilame/instagram-private-api](https://github.com/dilame/instagram-private-api) | [GramAddict](https://github.com/GramAddict/bot), [instadroid](https://github.com/ivylikethevine/instadroid) |

**Decisão (híbrida):**
- **B (app real)** faz **login, challenges, aquecimento** e é a **fonte da verdade** (captura TLS, headers e versão do app).
- **A (API móvel)** faz a **extração em volume** usando a **mesma identidade** (cookies `sessionid`/`mid` + device IDs + IP) exportada de B.
- Motivo: o molkex documenta que *"sessões modernas da Meta vinculam cookies (`sessionid`, `mid`) a um identificador de hardware e ao estado da operadora"*. Portanto a sessão só pode ser reaproveitada com o **mesmo device + mesmo IP**. O InstaT já faz *cookie handoff* Selenium → httpx; aqui a ideia é a mesma, só que app → API.

### 1.2 Aparelho em Docker

| Opção | Como roda | Prós | Contras |
|---|---|---|---|
| **[redroid](https://github.com/remote-android/redroid-doc)** | Android nativo em container (sem emular CPU) | Leve, várias instâncias, GPU | **Só Linux**. No Windows, só em **WSL2 com kernel recompilado** ([guia](https://github.com/remote-android/redroid-doc/blob/master/deploy/wsl.md)): `CONFIG_ANDROID_BINDER_IPC`, `CONFIG_ANDROID_BINDERFS`, `CONFIG_DMABUF_HEAPS(_SYSTEM)` ou `CONFIG_ASHMEM`, `CONFIG_IPV6_MULTIPLE_TABLES`. Há issues de [device offline](https://github.com/remote-android/redroid-doc/issues/68) e [boot lento no WSL 6.6](https://github.com/remote-android/redroid-doc/issues/899). Várias instâncias exigem binderfs real |
| **[budtmo/docker-android](https://github.com/budtmo/docker-android)** | Emulador QEMU completo | noVNC (ótimo para o passo 7), gravação de vídeo, perfis de device (Galaxy S10 etc.) | Precisa de `/dev/kvm` em Ubuntu (no Windows, VM com virtualização aninhada), pesado |
| **Celular físico via ADB** (plano B) | Aparelho real no USB/Wi-Fi | Passa na atestação de hardware; fingerprint real | Custo, não escala em container |

**Detecção de emulador** ([gmh5225/Android-Emulator-Detection](https://github.com/gmh5225/Android-Emulator-Detection), [strazzere/anti-emulator](https://github.com/strazzere/anti-emulator)): apps checam propriedades de build (`ro.kernel.qemu`, `ro.product.*`, tags `test-keys`), sensores, telefonia e bateria, e hoje fazem **validação cruzada de coerência** das propriedades contra devices que existem de verdade. Emuladores **não passam na atestação de hardware** (Play Integrity strong), e as listas de fingerprints de software são bloqueadas em ondas ([XDA](https://xdaforums.com/t/how-do-i-pass-the-play-integrity-checker-with-redroid.4721174/)).

**Tradução ARM (crítico):** o APK do Instagram é ARM. O host é x86_64, então o redroid precisa de `libndk_translation`. Imagens **redroid 15+** anunciam ARM sem tradutor, e o app fecha ([#933](https://github.com/remote-android/redroid-doc/issues/933), [#669](https://github.com/remote-android/redroid-doc/issues/669), [#889](https://github.com/remote-android/redroid-doc/issues/889)). Caminho validado: **redroid 11–13 + [redroid-script](https://github.com/ayasa520/redroid-script) `-n`** (tradutor extraído de imagem oficial, **não redistribuível**: gerar localmente, nunca publicar a imagem). Alternativa: host **ARM64** (VPS Ampere/Graviton), que roda o APK nativo sem tradução.

**Modo de GPU** ([issue #705](https://github.com/remote-android/redroid-doc/issues/705), [kasmweb/redroid](https://hub.docker.com/r/kasmweb/redroid)): `androidboot.redroid_gpu_mode=auto|host|guest`. `host` (GPU real) é **frágil**, sobretudo em NVIDIA e dentro de VM — sintoma clássico é `adb` mostrando o device **offline**. Fallback seguro: **`guest` (render por software)**, mais lento porém estável (o instadroid relata a UI da aba "Seguindo" falhando conforme o modo de GPU). Decidir na Fase 1 medindo os dois.

**Dois tipos de container:**
- **slot de produção:** sem root, sem Magisk, sem Frida (quanto mais limpo, menos sinal de ambiente modificado).
- **container de laboratório:** com Magisk/Frida, usado **só** para capturar a verdade (headers, TLS, versão do app), **com conta descartável**, nunca com contas de produção.

**Props de device customizadas** ([issue #481](https://github.com/remote-android/redroid-doc/issues/481)): o redroid aceita sobrescrever `ro.product.*` e `androidboot.redroid_{width,height,dpi,fps}` via `docker run`/compose (`command:`). É por aí que o `DeviceProfile` da Fase 3 vira as build props do container (marca, modelo, fabricante, dpi, resolução) — coerentes com o UA e o modelo real. Algumas props **não fazem efeito** ([#481](https://github.com/remote-android/redroid-doc/issues/481)), então toda prop é conferida com `getprop` em teste.

**Decisão:** o projeto **não depende de burlar atestação**. A Fase 1 **mede** se o app loga e extrai no container (gate); se não logar em ≥ 3 de 5 tentativas, aciona o **plano B** (celular físico via ADB).

### 1.3 Proxy móvel DataImpulse

- **Endpoints** ([docs](https://docs.dataimpulse.com/proxies/types-of-connections)): `gw.dataimpulse.com:823` (HTTP rotativo), `:824` (SOCKS5 rotativo), portas **10000–20000** para sticky (IP fixo por **até 120 min**, média de 30).
- **Duração do sticky:** `sessttl.<minutos>`, p.ex. `login__cr.br;sessttl.60:senha@gw.dataimpulse.com:10001`. `sessttl.1` troca a cada minuto e `sessttl.120` é o máximo. **Regra do projeto:** `sessttl` ≥ duração máxima da sessão + margem, e a sessão termina **antes** do IP expirar.
- **Exemplo completo por slot:** `login__cr.br;asn.<operadora>;sessid.slot01;sessttl.120:senha@gw.dataimpulse.com:10001`.
- **Parâmetros no usuário**: `login__cr.br:senha@...` (país), `login__cr.br;city.saopaulo`, `login__cr.br;asn.<num>` (operadora/ASN), `login__cr.br;sessid.<id>` (sessão sticky). Separadores: `__` inicia os parâmetros, `;` separa tipos, `,` separa valores.
- **Pool móvel** ([mobile proxies](https://dataimpulse.com/mobile-proxies/)): IPs 3G/4G/5G/LTE de operadoras reais, ~16M de IPs em 195 países, **~US$2/GB, sem expiração** ([review](https://github.com/jvetste/dataimpulse-mobile-proxy-review)). A seleção de pool/operadora é por parâmetros de targeting no usuário (`__cr.xx;...`); a sintaxe exata do **pool móvel** não está na doc pública — **confirmar com o suporte na Fase 0**. Como cobra por GB, a estimativa de custo por 1k perfis entra na Fase 0 e é medida de novo na Fase 9.
- **Consenso da comunidade** (instagrapi best practices): *"reutilizar o mesmo país, cidade, ASN/operadora, configurações de device e sessão salva é menos suspeito que um IP diferente"*. Rotação por requisição **só** vale para conteúdo público sem login.
- **Todo o tráfego do Android** precisa sair pelo proxy. O proxy HTTP global do Android não cobre tudo, então a solução é **tun2socks** no namespace de rede do container ([tun2proxy](https://github.com/tun2proxy/tun2proxy), [guia container](https://bigmike.help/en/devops/003/), [sockstun](https://github.com/heiher/sockstun) como alternativa via VpnService).
- **Custo:** cobrança por GB. A UI do app consome muito mais banda (imagens/vídeo) que a API. Isso reforça a estratégia híbrida e pede medir **GB por 1k perfis**.

### 1.4 TLS / HTTP2 fingerprint

- httpx/requests têm JA3/JA4 de Python/OpenSSL, detectáveis na hora. O app Android usa **BoringSSL** (via OkHttp/Liger).
- **[curl_cffi](https://github.com/lexiforest/curl_cffi)** permite fingerprint próprio com `ja3`, `akamai` e `extra_fp` ([customize](https://curl-cffi.readthedocs.io/en/latest/impersonate/customize.html)). ⚠️ **Não existe target pronto de OkHttp/app do IG**: os [targets](https://curl-cffi.readthedocs.io/en/stable/impersonate/targets.html) móveis são só navegadores (`chrome131_android`, `safari260_ios`...). **Não usar `chrome_android` fingindo ser o app** — UA de app + TLS de Chrome é incoerência detectável. O fingerprint do app tem que ser reproduzido à mão via `ja3`+`akamai`+`extra_fp`, a partir da captura real (docs de customização citam OkHttp como exemplo desse método).
- **Alternativa se o `extra_fp` não bastar:** [tls-client](https://github.com/bogdanfinn/tls-client) (Go, perfis customizáveis, bindings Python), comparado com curl_cffi no passo 2 da Fase 4. A doc manda **conferir o resultado** e refinar com `extra_fp`, porque JA3/Akamai não cobrem todos os campos.
- O instagrapi já usa **curl_cffi + HTTP/2** nas requisições móveis privadas. O molkex declara JA4 TLS 1.3 com suites BoringSSL.
- **Fonte da verdade:** capturar o ClientHello do app **no container da Fase 1** (tcpdump) e comparar os JA4 como asserção automatizada.

### 1.5 Identidade: device, User-Agent e headers

- **Modelo de device** (instagrapi): `app_version, version_code, android_version, android_release, dpi, resolution, manufacturer, device, model, cpu`, mais o UA derivado, p.ex. `Instagram 269.0.0.19.301 Android (27/8.1.0; 480dpi; 1080x1776; motorola; Moto G (5S); montana; qcom; ru_RU; 253447809)`.
- **Identificadores persistentes por conta:** `android_device_id` (`android-<hex16>`), `uuid`/`device_id`, `phone_id`, `client_session_id`, `advertising_id`, `mid`, `pigeon_session_id`.
- **Headers usados pelo app** ([dilame request.ts](https://github.com/dilame/instagram-private-api/blob/master/src/core/request.ts), [issue #855](https://github.com/dilame/instagram-private-api/issues/855)): `User-Agent`, `X-IG-App-ID`, `X-IG-Capabilities`, `X-IG-Connection-Type`, `X-IG-Connection-Speed`, `X-IG-Bandwidth-Speed-KBPS`, `X-IG-Bandwidth-TotalBytes-B`, `X-IG-Bandwidth-TotalTime-MS`, `X-IG-App-Locale`, `X-IG-Device-Locale`, `X-IG-Mapped-Locale`, `X-IG-Timezone-Offset`, `X-IG-Android-ID`, `X-IG-Device-ID`, `X-IG-Family-Device-ID`, `X-MID`, `X-Pigeon-Session-Id`, `X-Pigeon-Rawclienttime`, `X-Bloks-Version-Id`, `X-Bloks-Is-Layout-RTL`, `X-FB-HTTP-Engine`, `X-FB-Client-IP`, `X-FB-Server-Cluster`, `IG-U-DS-User-ID`, `IG-INTENDED-USER-ID`, `Authorization: Bearer IGT:2:...`.
  ⚠️ A lista **muda com a versão do app**. Por isso a Fase 3 captura do app real em vez de copiar de repositório antigo.
- **Como capturar a verdade:** o app usa **certificate pinning**, então mitmproxy com CA de sistema **não intercepta**. É necessário *unpinning* via Frida (root + Magisk) **no container de laboratório** ([Instagram-SSL-Pinning-Bypass](https://github.com/0xSHAK1B/Instagram-SSL-Pinning-Bypass)). A captura de TLS (ClientHello) **não** precisa de unpinning: basta tcpdump passivo no slot, porque o ClientHello trafega em claro.
- **Props do aparelho no redroid:** passadas como argumento do container (`androidboot.redroid_width/height/dpi`, `ro.product.system.brand/model/manufacturer`...). Algumas **não fazem efeito** ([#481](https://github.com/remote-android/redroid-doc/issues/481)), e por isso toda prop é conferida com `adb shell getprop` em teste.
- **Princípio:** coerência vale mais que aleatoriedade. UA, locale, timezone, operadora/país do IP, resolução, versão do app e propriedades de build do container contam **a mesma história** e ficam **fixos por conta**. Nada muda durante um challenge ou reset de senha (instagrapi).
- **Não logar do zero a cada execução:** `dump_settings`/`load_settings`. Logins novos repetidos são sinal forte.
- **Assinatura de request** ([dilame #1085](https://github.com/dilame/instagram-private-api/issues/1085), [pavlovdog api.py](https://github.com/pavlovdog/Instagram-private-API/blob/master/api.py)): endpoints de escrita/login usam `signed_body=<hmac-sha256>.<json>` + `ig_sig_key_version` (a chave sai da APK, versão muda pouco). Muitos endpoints de **leitura** hoje dispensam a assinatura, mas o `MobileApiEngine` precisa saber assinar quando exigido — e isso é **manutenção recorrente** (chave/versão viram fixture versionada, checada pelo drift check da Fase 10). É mais uma razão para o app real (estratégia B) ser a fonte da verdade: ele assina sozinho.

### 1.6 Comportamento humano

- GramAddict: *"delays e ações aleatórios human-like"* e *"limites customizáveis para evitar soft ban"* ([config](https://docs.gramaddict.org/#/configuration)), com velocidade, duração de sessão, horários de trabalho e limites totais.
- **Números de referência do GramAddict** ([config.yml](https://github.com/GramAddict/bot/blob/master/config-examples/config.yml)) — úteis para calibrar `human.py` e o `RateGovernor` (todos como **faixa**, sorteada por sessão): interações totais `280–300`/sessão, "watches" e likes `120–150`, follows/unfollows `40–50`, `total-scraped-limit` `100–150`, comments/PMs `3–5`; `working-hours: [10.15-16.40, 18.15-22.46]` (janelas por timezone), `repeat: 280–320` s entre sessões, uma sessão por vez. São **piso de segurança para a UI**, não metas — para nós a extração de leitura substitui a maior parte das ações.
- instadroid: *"toques sempre feitos a partir de um dump de hierarquia tirado imediatamente antes"*, o que evita tocar em elemento velho (erro que denuncia automação).
- Gestos (uiautomator2/Appium [gestures](https://github.com/appium/appium-uiautomator2-driver/blob/master/docs/android-mobile-gestures.md)): velocidade padrão `5000 × density` px/s é **robótica**. Usar duração variável e trajetória curva por `touch down/move/up`.
- molkex: warmup com *"scroll orgânico de feed, dwell aleatório, visualização de stories, ritmo humano"*, além de telemetria Pigeon/Scribe em lote (o app real manda isso sozinho; a API precisa pelo menos não ficar muda por tempo anormal).

### 1.7 Rate limit e erros

- A Graph API oficial documenta **200 chamadas/conta/hora** em janela móvel ([Phyllo](https://www.getphyllo.com/post/instagram-api-rate-limits-explained----and-how-to-scale-beyond-them-2026)). A API privada **não publica limites**, então eles precisam ser **aprendidos**.
- **Política de erros** ([instagrapi errors](https://instagrapi.com/guides/errors), [best practices](https://subzeroid.github.io/instagrapi/latest/usage-guide/best-practices/)):

| Sinal | Natureza | Ação |
|---|---|---|
| HTTP 429 / `PleaseWaitFewMinutes` | Soft limit (5–30 min) | Dormir, **reduzir taxa**, tentar 1×. **Não** trocar proxy nem relogar |
| `FeedbackRequired` | Limite **da conta**, por **horas** | Congelar aquele tipo de ação, ler `feedback_message`, mandar para *dead-letter* + alerta humano |
| `ChallengeRequired` / `checkpoint_required` | Verificação | Resolver pelo app (Fase 1) com os resolvers existentes; senão, alerta humano |
| `LoginRequired` | Sessão expirou | Recarregar a sessão salva; **evitar login do zero** |

- *"A maioria dos times lê isso como problema de sessão e sai trocando proxy ou relogando, o que **piora**, porque o limite é da conta."* Ordem certa: **pausa → ritmo → proxy fixo**.
- Volume: *"aumente ao longo de dias, não de minutos"*, com contas novas começando só em leitura e polling na escala de minutos.
- **Números da comunidade para leitura de listas** ([instagrapi followers](https://instagrapi.com/guides/get-instagram-followers-python/), [instagrapi scraper](https://instagrapi.com/guides/instagram-scraper-python), [instaloader #1285](https://github.com/instaloader/instaloader/issues/1285)):
  - Páginas de **50–100** itens. Sem proxy, *"alguns milhares de seguidores numa janela curta"* já disparam `please_wait`.
  - `delay_range = [1, 3]` s como padrão; em listas grandes, **limite inferior de 2–3 s**.
  - `please_wait_a_few_minutes` pede **30 min** de pausa; se voltar imediatamente, **o problema é o IP**.
  - `feedback_required` pede **horas** paradas e, muitas vezes, login manual no app oficial (aqui: no slot, pela UI).
  - Mais de 100k seguidores exige proxy residencial, retries e **checkpoint de cursor**.
  - **Teto por IP:** um IP residencial aguenta ~**200 req/h** antes de `please_wait` ([scrapfly](https://scrapfly.io/blog/posts/how-to-scrape-instagram)) — daí o paralelismo ser **entre contas/IPs** (Fase 11), não em rajada por conta.
  - **Fragilidade estrutural:** a comunidade relata que um scraper de API privada **quebra a cada 2–4 semanas** quando o IG rotaciona `doc_id`/versão do app — reforça o **drift check** (Fase 10) e fixtures versionadas.
  - **Isso são pontos de partida, não verdades:** a Fase 8 calibra com dados próprios.

### 1.8 Riscos não técnicos

- Automação e API privada **violam os Termos do Instagram**, com risco de ban das contas.
- Listas de seguidores são **dado pessoal (LGPD)**: base legal, minimização, retenção e anonimização nos prints.
- **Política do provedor:** a Bright Data proíbe login automatizado no IG (já registrado no CHANGELOG). **A política da DataImpulse precisa ser verificada na Fase 0.**
- Onde houver cobertura, **preferir a Graph API oficial** (contas Business/Creator próprias).

### 1.9 O gargalo real hoje: login travando e sem paralelismo resiliente

**Problema medido (relato do Tiago, 14/09/2026):** o processo é lento e "cai toda hora"; o Instagram **trava o login**; não há paralelismo de verdade; e quando uma conta cai, o trabalho feito se perde em vez de outra conta **assumir de onde parou**.

**Causa-raiz (pesquisa):** o InstaT hoje **loga do zero a cada execução**. Cada login novo é o evento mais arriscado — o IG vê "novo device" e dispara challenge/checkpoint ([instagrapi best practices](https://subzeroid.github.io/instagrapi/usage-guide/best-practices.html), [login_required](https://instagrapi.com/guides/errors/login-required)). As regras de ouro da comunidade:
- **Não logar toda vez.** Logar **uma vez**, salvar a sessão (`dump_settings`) e **reusar** (`load_settings` **antes** de qualquer login, senão os device IDs são regenerados e perde-se o efeito).
- **Recuperar com `relogin()`, não `login()`.** `login()` descarta o fingerprint e refaz o handshake (→ challenge). `relogin()` reusa o fingerprint e só renova o cookie jar.
- **Sessão + proxy fixo + execução serial *por conta*** derruba a taxa de challenge. O paralelismo vem de **muitas contas em paralelo**, cada uma serial e com IP próprio — nunca uma conta em rajada.
- Persistência: arquivo para scripts, **Redis/SQLite para frota**, mesma dupla `dump/load` por baixo ([session persistence](https://instagrapi.com/guides/instagrapi-session-persistence)).

**Failover "assumir de onde parou" (pesquisa):** o padrão certo é **checkpoint de cursor**, não de resultado. A API privada pagina por `next_max_id` (`user_followers_v1_chunk(user_id, max_id=<cursor>)` devolve a página **+** o próximo cursor) ([instagrapi user.md](https://github.com/subzeroid/instagrapi/blob/master/docs/usage-guide/user.md), [followers guide](https://instagrapi.com/guides/get-instagram-followers-python/)). Persistindo o cursor por `(alvo, list_type)`, quando a conta A é bloqueada a conta B **retoma exatamente na página seguinte** — sem recomeçar. ⚠️ Isso só é exato na **API** (`MobileApiEngine`, Fase 5): o scroll do Selenium é **posicional** e não exporta cursor, então lá o resume é aproximado (retomar por delta do conjunto já coletado, via `PersistentStore.get_delta_since`).

**Modelo de pool de contas (referência: [twscrape](https://github.com/vladkens/twscrape)):** contas num store (SQLite), **estado por conta** (`active` / `cooling` / `blocked` / `challenge` / `logged_out`), **lock por conta e por endpoint** (quando limitada para uma operação, trava só aquela operação até o reset e **tenta outra conta ativa**), `wait_timeout`/`wait_interval` para esperar a próxima conta liberar, e liberação imediata do lock ao interromper cedo. Reclaim estilo fila (visibility timeout, [Redis job queue](https://redis.io/docs/latest/develop/use-cases/job-queue/), [Celery `task_acks_late`/`task_reject_on_worker_lost`](https://dev.to/artemooon/celery-redis-at-scale-designing-a-reliable-and-efficient-task-queue-in-production-27nh)): a fatia de uma conta que caiu volta para a fila e outra pega. Para uma ferramenta de host único, uma **tabela de claims em SQLite** com timeout basta — sem precisar de Celery/Redis.

**Vale para o Selenium também (não só a API):** dá para matar o login do engine padrão reusando **cookies de sessão**. Salvar `driver.get_cookies()` depois do 1º login e, nas execuções seguintes, navegar ao domínio do IG, `add_cookie` cada um e `refresh()` — entra **sem abrir o formulário de login** ([selenium cookies](https://www.selenium.dev/documentation/webdriver/interactions/cookies/), [reuse](https://dev.to/hardiksondagar/reuse-sessions-using-cookies-in-python-selenium-12ca)). O `HttpxEngine.login_with_cookies(...)` já faz esse handoff — falta usá-lo **na entrada**, por conta, e não só no meio da cascata.

**O que o InstaT já tem** (reaproveitar, não reinventar): `ParallelCoordinator` (união + `stop_threshold`), `parallel_extract`/`_parallel_extract_with_pool`, `get_followers_parallel`/`get_following_parallel`, `get_followers_persistent` (`_extract_persistent` com fallback de contas e retries), `ExtractionCheckpoint` (salva **Set**), `PersistentStore.get_delta_since`, `SessionPool.mark_blocked` (cooldown), `WorkerPool` (workers pré-logados), `SessionCache` (cookies), `HttpxEngine.login_with_cookies`. **Faltam:** (a) persistência de sessão **usada na entrada** para evitar o re-login; (b) o **AccountPool scheduler** com estados+locks por endpoint; (c) checkpoint de **cursor** (não só de Set) para failover exato. Isso é a Fase 11.

---

## 2. Arquitetura alvo

```
 InstaExtractor
      │
      ▼
 AccountPool (scheduler)  ── estados: active/cooling/blocked/challenge/logged_out
      │   • pick_next(endpoint): conta ativa cujo lock (conta+endpoint) está livre
      │   • lock por conta E por endpoint; espera wait_timeout/wait_interval
      │   • SessionStore: load_settings ANTES de login; relogin() (nunca login do zero)
      │   • bloqueio/challenge → mark + cooldown → devolve a fatia à fila
      ▼
 WorkQueue (SQLite claims + visibility timeout)   CursorStore: {(alvo,list_type) → next_max_id}
      │   fatia reivindicada por 1 conta; se cai, timeout → outra conta reivindica
      ▼
 EngineManager (cascata, por conta/slot)
      │   1. MobileApiEngine  (curl_cffi, TLS=app) ── EXPORTA next_max_id → failover exato
      │   2. AndroidUiEngine  (uiautomator2 → container) ── resume aproximado (delta)
      │   3. engines web atuais (httpx / Playwright / Selenium)
      │
      │   RateGovernor ◄── block_predictor / backoff / block_detector
      ▼
 N slots em paralelo (1 conta cada) ── host Linux (VPS ou WSL2 kernel custom)
   ┌─ slot-01 ─────────────────────────────────────┐   ┌─ slot-02 ─┐   ┌─ … ─┐
   │ AccountSlot = {conta, DeviceProfile, sessão,   │   │  (idem)   │   │     │
   │   porta sticky, warmth}                        │   └───────────┘   └─────┘
   │ android (redroid|budtmo) ◄─adb─ controller(py) │
   │ netns ─► tun2socks ─► gw.dataimpulse.com:1xxxx │
   └────────────────────────────────────────────────┘
```

**Invariante central:** `1 conta = 1 DeviceProfile = 1 container/volume = 1 porta sticky DataImpulse (país/ASN fixo) = 1 RateGovernor`. Nada é compartilhado entre slots.

**Fluxo do failover ("outro assume de onde parou"):** cada conta processa uma fatia paginada; a cada página grava `next_max_id` no `CursorStore` e os perfis no `PersistentStore` (união, dedupe por `pk`). Se a conta cai (bloqueio/challenge/queda), o `AccountPool` a coloca em cooldown e a fatia volta à `WorkQueue`; a próxima conta ativa reivindica e **retoma pelo cursor salvo** — na API, na página exata; no Selenium, pelo delta do conjunto. O paralelismo é **entre contas**; cada conta é **serial** e nunca faz rajada.

---

## 3. Princípios de projeto

0. **Compatibilidade retroativa é inegociável** (ver [§3.1](#31-contrato-público-imutável)).
1. **Coerência acima de aleatoriedade.** A identidade é fixa por conta e só varia o comportamento.
2. **Medir antes de disfarçar.** Todo "spoof" tem teste comparando com o app real (headers, JA4, props).
3. **Falhar para humano, não para retry.** Challenge e feedback vão para *dead-letter* + alerta.
4. **Evidência visual obrigatória** (passo 7), inclusive em fase "só de backend": renderizar tabelas e gráficos em PNG.
5. **Reaproveitar o InstaT.** Engines, cascata, `persistent_store`, `worker_pool`, `diagnostics` e resolvers já existem.
6. **Segredos fora do git:** `.env` (`DATAIMPULSE_LOGIN`, `DATAIMPULSE_PASSWORD`, contas) no `.gitignore`, e prints com dados pessoais borrados.
7. **Custo visível:** toda corrida registra GB de proxy e perfis/GB.

### 3.1 Contrato público imutável

O mobile entra **só como novos engines na cascata**. **Nenhuma assinatura pública muda.** O código abaixo — que já funciona hoje — precisa continuar funcionando **byte a byte, sem alteração**, em todas as fases:

```python
from instat import InstaExtractor

ext = InstaExtractor(
    username="your_user",
    password="your_pass",
    headless=True,
    engines=["selenium", "httpx"],  # cascade: Selenium → httpx on fail
)

target = ext.get_profile("target_profile")   # 1 page load → cheap metadata
print(f"@{target.username} · {target.full_name} · "
      f"{target.followers_count} followers · {target.posts_count} posts")

followers = target.get_followers()
following = target.get_following()
print(f"Fetched: {len(followers)} followers | {len(following)} following")

ext.quit()
```

O mobile é **opt-in**, ativado apenas por **valores novos** em `engines=` (nunca por mudança de default). A mesma API, agora passando pelo caminho mobile:

```python
ext = InstaExtractor(
    username="your_user",
    password="your_pass",
    engines=["mobile_api", "android_ui", "selenium", "httpx"],  # novos nomes, mesma cascata
)
target = ext.get_profile("target_profile")   # idêntico
followers = target.get_followers()           # idêntico
ext.quit()
```

**Regras do contrato (valem em toda fase):**
- Não remover nem renomear parâmetros de `InstaExtractor.__init__` (`username`, `password`, `headless`, `timeout`, `proxies`, `accounts`, `engines`, `exporter`, `imap_config`, `completion_threshold`, `block_predictor`, `use_stdlib_logging`). Só **acrescentar** parâmetros novos, sempre com default que preserva o comportamento atual.
- Não mudar assinatura nem tipo de retorno de `get_profile`, `Profile.get_followers`, `Profile.get_following`, `get_both`, `get_followers_parallel`, `quit` e afins. `get_followers()`/`get_following()` continuam retornando `List[str]` por padrão (o modo `with_metadata=True` continua opcional).
- Os nomes `"selenium"`, `"playwright"`, `"httpx"` seguem válidos e com o mesmo efeito. `"mobile_api"`/`"android_ui"` são **adição**.
- **Teste de guarda:** o snippet acima vira `tests/test_public_api_contract.py` (mockando a rede), rodado no CI desde a Fase 0. Qualquer PR que o quebre falha o CI. É o critério de aceite nº 1 de **todas** as fases.

### 3.2 Decisão de arquitetura: Selenium × Playwright — migração gradual, sem *big-bang*

**Pergunta (Tiago, 14/09/2026):** vale trocar tudo de Selenium por Playwright?

> **Correção (14/09/2026, após pesquisa).** Uma versão anterior desta seção afirmou que "o stealth do Playwright é mais fraco". **Isso estava errado.** A pesquisa de 2026 mostra o oposto — o texto abaixo substitui aquela conclusão.

**O que a pesquisa diz** ([scrapfly](https://scrapfly.io/blog/posts/playwright-vs-selenium), [decodo](https://decodo.com/blog/playwright-vs-selenium), [ByteTunnels stealth](https://bytetunnels.com/posts/playwright-vs-selenium-stealth-which-evades-detection-better/), [benchmark anti-detect 2026](https://dev.to/ianlpaterson/anti-detect-browser-benchmark-2026-7-stealth-tools-31-cloudflare-targets-651-verdicts-4361), [scrapewise](https://scrapewise.ai/blogs/playwright-stealth-2026)):
- Para scraping, **o Playwright é o preferido em 2026** (auto-wait, interceptação de rede nativa, async, mais rápido).
- **Stealth: vantagem do Playwright**, não do Selenium. O Playwright tem fingerprint de base **mais limpo** (não injeta os marcadores `cdc_` que o Selenium **vaza em todo elemento do DOM**). O **undetected-chromedriver é frágil**: depende de patch binário que **quebra a cada update do Chrome**. As stealth **mais fortes de 2026 são baseadas em Playwright/Chromium** — [Patchright](https://scrapewise.ai/blogs/playwright-stealth-2026), Camoufox, rebrowser-playwright.
- **Multi-conta:** `BrowserContext` dá **dezenas de sessões isoladas num só processo** (cookies/storage próprios), com queda grande de CPU/RAM vs. o "um browser por conta" do Selenium. `storage_state` (JSON de cookies+localStorage) é o mecanismo limpo de persistir/reusar sessão — casa direto com a Fase 11.
- **Porém, o alerta que se repete em toda fonte:** contra alvos sérios, **a escolha do engine importa menos que a infra** (IP/proxy, TLS, comportamento). Fingerprint de browser **não** resolve TLS nem reputação de IP nem análise comportamental — que é o que o Instagram usa. Isso **reforça** a estratégia do roadmap (mobile + proxy residencial + comportamento), não a troca de engine web.

**Análise (acoplamento medido):** o Selenium é o engine **mais completo** e está fundo no projeto — ~15 arquivos. Só-Selenium hoje: login (`login.py`, `login_flow.py`), challenge por IMAP (`challenge_resolvers.py`), `scroll_loop.py`, `modal_interaction.py`, `worker_pool.py`, `get_profile` (exige `_driver`), `diagnostics.py`, undetected-chromedriver (`providers.py`). O `PlaywrightEngine` existe, mas é secundário e mais fino.

**Decisão: migrar para Playwright de forma incremental — NÃO num *big-bang*.**
- **Por que não arrancar tudo de uma vez:** portar login + IMAP + scroll/modal + worker_pool é superfície enorme e risco alto de regressão (princípio nº 5), e o destino estratégico é o **mobile** (todos os engines web viram *fallback tier 3*). Refazer tudo agora, para um tier que vai virar secundário, é ROI ruim.
- **Por que migrar mesmo assim:** o Playwright é o engine web melhor (fingerprint mais limpo, contexts, `storage_state`, interceptação de XHR nativa) e o undetected-chromedriver é frágil. Então o Playwright deve virar o **primário web**, ao longo do tempo, pela cascata.
- **Como:** começar **pela Fase 11** — o caminho paralelo/sessão nasce **em Playwright** (`BrowserContext` por conta + `storage_state` no `SessionStore`), com o Selenium como **fallback** e ainda dono de login/IMAP até serem portados. Depois, migrar login/challenge para Playwright fase a fase, cada passo com o teste de contrato (§3.1) verde. Avaliar [Patchright](https://scrapewise.ai/blogs/playwright-stealth-2026)/Camoufox como camada de stealth quando o primário for Playwright.
- **Não** remover o Selenium enquanto login/IMAP/stealth não estiverem portados e medidos. A cascata `BaseEngine` permite os dois convivendo durante a transição.

---

## 4. Fases

> Formato: cada fase lista os **7 passos** + **critérios de aceite** + **caminho do print** (definido no passo 3).
> Marcadores de teste: `mobile` (precisa de container Android), `e2e` (fake server). O CI roda `-m "not e2e and not mobile"`.
>
> **⏫ Ordem recomendada:** Fase 0 → **Fase 11 (prioridade — resolve a dor atual: login travando e sem paralelismo)** → Fase 1 → … A Fase 11 tem duas camadas: a **Camada 1** (sessão persistente + AccountPool) **não precisa do container Android** e pode entregar valor já; a **Camada 2** (failover por cursor exato) fecha junto com o `MobileApiEngine` (Fase 5).

---

### Fase 0 — Fundação, baseline e decisões de infra

1. **Análise:** medir o baseline dos engines atuais em 3 perfis de teste (tamanhos ~1k, ~10k, ~50k): taxa de sucesso, `coverage_pct`, `rate_limit_hits`, tempo por 1k e bloqueios por 24h.
2. **Pesquisa:** redroid (WSL2 kernel custom) × budtmo (VM KVM) × VPS Linux; política da DataImpulse para login em redes sociais e parâmetro do pool móvel (suporte); ToS do IG + LGPD; Graph API cobre algum dado necessário?
3. **Pré-análise:** decidir o host; estrutura `instat/mobile/` + `docker/mobile/` + extra `instat[mobile]`; `AccountSlot` (dataclass); lista de contas de teste; política de dados (retenção, anonimização). **Print:** gráfico do baseline (matplotlib → PNG) + `docker info`/`wsl --status`. Sem conta real.
4. **TDD:** `tests/test_public_api_contract.py` (o snippet do §3.1 com rede mockada, **verde**) + `tests/mobile/test_contracts.py`: `MobileApiEngine` e `AndroidUiEngine` são `BaseEngine`; `AccountSlot` valida invariantes (sem porta ou conta repetida).
5. **Execução:** esqueleto, `.env.example`, marcador `mobile` no `pyproject.toml`, ajuste do CI.
6. **Testes:** CI verde nas 3 versões de Python, **incluindo o teste de contrato público**.
7. **Prints:** `01-baseline.png`, `02-host.png`, lidos.

**Aceite:** baseline registrado; host decidido; política da DataImpulse confirmada **por escrito**; esqueleto no CI; **teste de contrato público (§3.1) verde e obrigatório no CI**.

---

### Fase 1 — Aparelho Android em Docker (gate de viabilidade)

1. **Análise:** o APK instala e abre? A conta de teste loga? Aparece challenge, "dispositivo não suportado" ou crash? Medir tempo de boot, RAM/CPU, **`gpu_mode=host` × `guest`** (device fica offline?) e taxa de login em 5 tentativas espaçadas (não seguidas).
2. **Pesquisa:** redroid × budtmo na prática (issues abertas); tradução ARM (**redroid 11–13 + redroid-script `-n`**, evitar 15+ sem bridge, [#933](https://github.com/remote-android/redroid-doc/issues/933)); host ARM64 como alternativa; persistência de `/data`; fonte do APK (versão fixada, hash conferido); uiautomator2 × Appium.
3. **Pré-análise:** `docker/mobile/compose.yml` com `android` (imagem gerada localmente com `libndk_translation`, volume `/data` por slot, `privileged`, adb 5555, props do `DeviceProfile` como argumentos do container) + `controller` (Python, adb, uiautomator2) + **perfil `lab`** separado (Magisk + Frida, conta descartável); `scripts/mobile/provision.py` (instala APK por hash, locale, timezone, `wm size/density` iguais ao `DeviceProfile`). **Print:** conta de teste 1 no slot-01 via `adb exec-out screencap -p`; estado preparado = APK instalado; toca conta real **sim**, então aval antes.
4. **TDD** (`mobile`): `adb devices` lista o slot; `u2.connect()` ok; `pm list packages` contém `com.instagram.android` com versão == fixture; `getprop persist.sys.timezone` == perfil.
5. **Execução.**
6. **Testes:** suíte `mobile` local + CI sem regressão.
7. **Prints:** `01-boot`, `02-app-aberto`, `03-login`, `04-pos-login` (ou tela de challenge). Procurar idioma errado, banner de erro, layout cortado, aviso de device.

**Gate:** se o login falhar em ≥ 3 de 5 tentativas por detecção de ambiente, **parar**, registrar e seguir pelo **plano B (celular físico via ADB)**, que usa o mesmo código uiautomator2.

---

### Fase 2 — Proxy móvel DataImpulse, sticky por conta, sem vazamento

1. **Análise:** do container, medir IP de saída, ASN (é operadora móvel?), país/cidade, latência, **duração real do sticky** (IP a cada 5 min por 120 min) e vazamento de DNS/IPv6.
2. **Pesquisa:** parâmetros `cr`/`city`/`asn`/`sessid`; tun2socks/tun2proxy em container × proxy global Android × sockstun; DNS por dentro do túnel; bloqueio de IPv6.
3. **Pré-análise:** `instat/mobile/proxy_pool.py` (o `ProxyPool` atual é round-robin com cooldown por falha, **modelo errado para conta logada**; o sticky é uma classe nova que reutiliza só o health/cooldown) com atribuição **determinística** `conta → porta sticky + cr + asn + sessid + sessttl`, health check **só entre sessões** e renovação controlada ao vencer (nunca no meio de uma extração); sidecar `tun2socks` no netns do slot; **kill-switch** (sem proxy, sem rede). **Print:** navegador do Android numa página de IP/ASN + app do IG aberto; sem login novo.
4. **TDD:** atribuição estável; nenhuma troca de IP durante uma sessão ativa; sticky vencido gera evento `session_renewed` antes da próxima sessão; `mobile`: IP do container == IP do proxy, DNS não vaza, IPv6 desativado, derrubar o proxy derruba a rede.
5. **Execução.**
6. **Testes.**
7. **Prints:** `01-ip-asn`, `02-teste-vazamento`, `03-killswitch`, `04-app-via-proxy`.

**Aceite:** 0 vazamentos; sticky estável ≥ 30 min medido; custo de banda por hora de sessão registrado.

---

### Fase 3 — Identidade do device: User-Agent, headers e build props coerentes

1. **Análise:** capturar com mitmproxy + **Frida unpinning** (só no **container de laboratório**, conta descartável, já que o pinning bloqueia CA de sistema) os headers reais do app logado e comparar campo a campo com o que o `HttpxEngine` envia hoje (diff).
2. **Pesquisa:** modelo de device do instagrapi; headers em dilame/instagrapi/molkex; `X-Bloks-Version-Id` e `version_code` por versão do app; como a comunidade versiona isso; validação cruzada de build props.
3. **Pré-análise:** `DeviceProfile` (frozen dataclass, persistido no `persistent_store`) com modelo real, IDs, dpi/resolução e **locale/timezone derivados do `cr` do proxy**; `HeaderFactory` única, gerada a partir do perfil + fixture da versão do app (`instat/mobile/app_versions/<ver>.json`); checker de coerência das build props do container × perfil. **Print:** diff de headers renderizado em PNG + tela "Sobre o telefone"; sem login novo.
4. **TDD:** mesmo seed/conta → perfil idêntico; locale/timezone ↔ país; UA casa com o regex do app e com o perfil; `HeaderFactory` produz o **mesmo conjunto e ordem** da fixture capturada; props do container coerentes com o perfil.
5. **Execução.**
6. **Testes.**
7. **Prints:** `01-diff-headers`, `02-sobre-telefone`, `03-coerencia-props`.

**Aceite:** diff de headers = vazio (exceto valores dinâmicos, como timestamps, listados explicitamente).

---

### Fase 4 — TLS / HTTP2 fingerprint igual ao do app

1. **Análise:** medir JA4 + fingerprint Akamai H2 de: app real (tcpdump no slot), `HttpxEngine` atual e curl_cffi com `ja3`/`akamai`/`extra_fp` derivados da captura.
2. **Pesquisa:** curl_cffi `ja3`/`akamai`/`extra_fp` (**sem target OkHttp pronto**) × [tls-client](https://github.com/bogdanfinn/tls-client); perfis Android/OkHttp da comunidade; como o instagrapi configura HTTP/2; ferramentas de leitura de JA4 (Wireshark, servidor próprio). Captura do ClientHello por **tcpdump passivo** no slot (não precisa de unpinning).
3. **Pré-análise:** transporte do `MobileApiEngine` sobre `curl_cffi.requests.Session` com fingerprint de `instat/mobile/fingerprints/<app_version>.json`; **servidor TLS local de teste** que devolve o JA4/Akamai recebido (sem depender de site externo). Verificação: comparar contra [tls.peet.ws/api/all](https://tls.peet.ws/) e [browserleaks.com/tls](https://browserleaks.com/tls) — usar **`ja4` e `peetprint_hash`** como âncora (estáveis), **não `ja3_hash`** (muda a cada run pelo *extension shuffle* do Chrome). **Print:** Wireshark lado a lado, app × engine; sem conta.
4. **TDD:** JA4 e Akamai do engine == fixture do app; HTTP/2 negociado; ordem dos pseudo-headers igual.
5. **Execução.**
6. **Testes.**
7. **Prints:** `01-ja4-app`, `02-ja4-engine`, `03-h2-settings`. **Qualquer campo diferente = fase não concluída.**

---

### Fase 5 — Handoff de sessão (app → API) e MobileApiEngine

1. **Análise:** medir se cookies + IDs exportados do app funcionam na API com mesmo IP e mesmo device, e o que acontece com IP diferente (esperado: invalidação).
2. **Pesquisa:** vínculo `sessionid`/`mid` ↔ device (molkex); `dump_settings`/`load_settings` (instagrapi); onde o app guarda a sessão (`/data/data/com.instagram.android/`) e implicações.
3. **Pré-análise:** `SessionBridge` que extrai do slot (exige root no container de lab ou captura via mitm da Fase 3) → `SessionStore` (evolui `session_cache.py`); `MobileApiEngine` implementa `get_followers`/`get_following`/`get_profile`/`get_recent_posts` nos endpoints móveis, com paginação, `should_stop`/`on_batch` como os engines atuais e **assinatura `signed_body` + `ig_sig_key_version`** (chave/versão em fixture) para os endpoints que exigem. **Print:** perfil de teste público pequeno, comparando a contagem do app (screencap) com o JSON da API.
   - **Mapa de endpoints (v1 privada, "obter os valores")** ([instagrapi user.md](https://github.com/subzeroid/instagrapi/blob/master/docs/usage-guide/user.md), [ping/friendships](https://instagram-private-api.readthedocs.io/en/latest/_modules/instagram_private_api/endpoints/friendships.html)): perfil → `users/{pk}/info/` e `users/web_profile_info/?username=` (dão `pk`, `follower_count`, `following_count`, `is_private`, `is_verified`, `biography`, `profile_pic_url`); seguidores → `friendships/{pk}/followers/?rank_token=&max_id=<cursor>`; seguindo → `friendships/{pk}/following/?rank_token=&max_id=<cursor>`; header `X-IG-App-ID` obrigatório (valor extraído do app na Fase 3, **não** hardcode de repo antigo). Cada página devolve `users[]` (com `pk`, `username`, `full_name`, `is_private`, `is_verified`) + `next_max_id` → alimenta o `CursorStore` da Fase 11.
   - ⚠️ [Issue #2797](https://github.com/subzeroid/instagrapi/issues/2797): quando a conta está em checkpoint, os endpoints retornam **0 linhas silenciosamente** — tratar "0 com sessão suspeita" como sinal de bloqueio (vai para o `RateGovernor`/dead-letter), não como "lista vazia".
4. **TDD:** sessão serializa e desserializa sem perda; sessão só é usada com o `AccountSlot` dono (IP e device); `LoginRequired` recarrega a sessão em vez de relogar; paginação e partial preservation (fake server e2e).
5. **Execução:** registrar na cascata do `EngineManager`.
6. **Testes.**
7. **Prints:** `01-contagem-app`, `02-resultado-api`, `03-diferenca`.

**Aceite:** diferença de contagem ≤ 1% no perfil de teste; nenhum login novo durante a fase.

---

### Fase 6 — Comportamento humano (AndroidUiEngine)

1. **Análise:** medir o que hoje é robótico nos engines web: intervalos (desvio padrão ~0), swipes retos de velocidade fixa, sessão que vai direto à lista e sai.
2. **Pesquisa:** GramAddict (velocidade, duração, horários, limites); instadroid (dump antes de tocar); gestos uiautomator2/Appium; distribuições (log-normal para leitura, mistura para scroll); literatura de biometria de toque.
3. **Pré-análise:** `instat/mobile/human.py` (seed injetável) com swipe por Bézier + jitter de início/fim + duração log-normal + "overscroll" e volta ocasional; toque em ponto aleatório **dentro** do bound (gaussiana ao centro); pausas de leitura; **roteiro de sessão** (feed → stories → busca → perfil → lista → saída) com duração mín/máx; horário ativo pela timezone do perfil. `AndroidUiEngine` extrai a lista de seguidores pela UI. **Print:** gravação de uma sessão (budtmo) ou screencap a cada etapa, conta de teste 1.
4. **TDD** (seed fixo): KS-test das pausas vs. distribuição alvo; nenhum swipe repetido em 1000; toques 100% dentro do bound; sessão respeita duração; nenhuma ação fora do horário ativo; toque só após dump fresco.
5. **Execução.**
6. **Testes.**
7. **Prints:** `01-feed`, `02-stories`, `03-busca`, `04-perfil`, `05-modal-seguidores`, `06-fim`, + um heatmap de toques, **lidos um a um**.

---

### Fase 7 — Session warming

1. **Análise:** com as contas de teste, medir challenges/bloqueios de conta "fria" × conta com N dias de uso passivo.
2. **Pesquisa:** instagrapi (*"começar com leitura, subir volume ao longo de dias"*); molkex warmup; GramAddict (limites e horários); relatos de comunidade sobre aquecimento.
3. **Pré-análise:** `instat/mobile/warming.py` com nível por conta (0–N) no `persistent_store`; plano diário crescente (D1: feed e stories curtos; D2–D3: + busca e perfis; D4+: primeira extração pequena; subida gradual); challenge/feedback **rebaixa** o nível; extração em volume só com nível ≥ mínimo; sempre via app real + `human.py`. **Print:** telas de cada etapa do dia + painel de níveis (PNG).
4. **TDD:** conta abaixo do nível não entra na fila; plano monotônico; evento de bloqueio rebaixa; nenhuma sessão fora do horário ativo.
5. **Execução:** integrar com `session_pool.py`/`worker_pool.py`.
6. **Testes.**
7. **Prints:** `01..NN-etapas`, `painel-niveis.png`.

---

### Fase 8 — Rate limiting inteligente (RateGovernor)

1. **Análise:** das telemetrias (`rate_limit_hits`, `block_predictor_score`, erros por tipo), montar a curva requisições/hora × erro **por conta e por IP**.
2. **Pesquisa:** token bucket; AIMD; janela móvel de 1h; política de erros do instagrapi (tabela 1.7); limites reportados pela comunidade.
3. **Pré-análise:** `RateGovernor` com buckets por **conta** e por **IP sticky**, orçamento diário, AIMD (+ lento, × 0.5 em 429), classificação de erro → ação (tabela 1.7), *dead-letter* + alerta para `FeedbackRequired`/`ChallengeRequired`, jitter, horário ativo e integração com `block_predictor.py`/`backoff.py`. Vale para **todos** os engines. **Print:** gráfico de uma corrida controlada (taxa × eventos × pausas).
4. **TDD** (relógio falso): bucket respeita janela; 429 corta a taxa pela metade; `FeedbackRequired` congela e **não** re-tenta; `ChallengeRequired` não troca proxy nem reloga; orçamento diário bloqueia; predictor alto força pausa.
5. **Execução.**
6. **Testes.**
7. **Prints:** `01-taxa-x-eventos`, `02-deadletter`, lidos (picos? cortes coerentes?).

---

### Fase 9 — Orquestração e escala

1. **Análise:** CPU/RAM/banda por slot; quantos slots cabem por host; custo GB/1k perfis.
2. **Pesquisa:** redroid multi-instância (binderfs), compose scale × k8s, pools de ADB.
3. **Pré-análise:** `WorkerPool` estendido para N `AccountSlot`s; health check de container; checkpoint/resume (`checkpoint.py`); `DiagnosticCollector` com `screencap` + `uiautomator dump` do slot. **Print:** grid dos N devices (noVNC ou montagem de screencaps).
4. **TDD:** pool nunca compartilha conta/IP/device; queda de container → resume sem perda (partial preservation); diagnóstico coleta os artefatos do slot.
5. **Execução.**
6. **Testes.**
7. **Prints:** `01-grid`, `02-resume`, `03-relatorio-custos`.

---

### Fase 10 — Validação ponta a ponta, drift e documentação

1. **Análise:** comparar com o baseline da Fase 0 (sucesso, bloqueio, tempo, custo).
2. **Pesquisa:** versão atual do app, mudanças de headers/Bloks/TLS reportadas nos repositórios de referência.
3. **Pré-análise:** job de **drift check** periódico (versão do app, headers, JA4 × fixtures) que abre alerta/issue quando diverge.
4. **TDD:** drift check falha quando a fixture diverge.
5. **Execução:** `docs/USAGE.md` (seção mobile), `TROUBLESHOOTING.md`, `ARCHITECTURE.md`, CHANGELOG.
6. **Testes.**
7. **Prints:** `01-comparativo-baseline`, `02-drift-ok`, `03-drift-falha`.

---

### Fase 11 — Paralelismo resiliente multi-conta ⏫ PRIORIDADE

> **Resolve a dor atual** (§1.9): login travando, processo lento, "cai toda hora", sem paralelismo, e trabalho perdido quando uma conta cai. Roda **logo após a Fase 0**. Duas camadas: a **Camada 1 não precisa do container Android**; a **Camada 2** (cursor exato) fecha com o `MobileApiEngine` (Fase 5).

#### Camada 1 — Sessão persistente + AccountPool (sem container)

1. **Análise:** medir hoje, com os engines atuais: quantas execuções **relogam do zero**, quantos challenges vêm **do login** (vs. da extração), tempo médio perdido por login travado e quanto trabalho se perde quando uma conta cai no meio.
2. **Pesquisa:** `dump_settings`/`load_settings` e `relogin()` vs `login()` ([best practices](https://subzeroid.github.io/instagrapi/usage-guide/best-practices.html), [login_required](https://instagrapi.com/guides/errors/login-required)); persistência de sessão (arquivo/SQLite/Redis, [guia](https://instagrapi.com/guides/instagrapi-session-persistence)); pool de contas com estado+lock ([twscrape](https://github.com/vladkens/twscrape)); reclaim por visibility timeout ([Redis job queue](https://redis.io/docs/latest/develop/use-cases/job-queue/), [Celery acks_late](https://dev.to/artemooon/celery-redis-at-scale-designing-a-reliable-and-efficient-task-queue-in-production-27nh)).
3. **Pré-análise:**
   - `SessionStore` (evolui `session_cache.py`): salva **cookies + IDs de device juntos** por conta em SQLite; **carrega antes** de qualquer login; expõe `relogin_needed()`. No Playwright, persiste `storage_state` (cookies + localStorage) — mecanismo mais limpo de reuso; ver [§3.2](#32-decisão-de-arquitetura-substituir-todo-o-selenium-por-playwright-não).
   - **Engine web do caminho paralelo: nasce em Playwright** (`BrowserContext` por conta + `storage_state`, isolamento e reuso de sessão mais limpos — [§3.2](#32-decisão-de-arquitetura-selenium--playwright--migração-gradual-sem-big-bang)), com Selenium como **fallback** durante a migração. Não remover o Selenium enquanto login/IMAP não forem portados.
   - `AccountPool` (envolve `SessionPool`): estados `active/cooling/blocked/challenge/logged_out`; `acquire(endpoint)` devolve conta ativa com lock **por conta e por endpoint** livre, senão espera (`wait_timeout`/`wait_interval`) ou levanta `NoAccountError`; `release()` sempre (inclusive em interrupção); bloqueio/challenge → `mark_*` + cooldown.
   - `WorkQueue` (SQLite): a lista-alvo vira fatias; cada fatia é **reivindicada** por uma conta com **visibility timeout**; conta que cai → a fatia expira e outra reivindica. Sem Celery/Redis (host único).
   - **Contrato público intacto** (§3.1): tudo isso é **opt-in** por parâmetros novos com default; `get_followers()`/`get_following()` seguem iguais. O caminho paralelo já existe (`get_followers_parallel`) e ganha o pool por baixo.
   - **Caminho do print:** rodar N contas de teste contra 1 alvo; sem tocar produção além das próprias contas de teste (aval já dado para elas).
4. **TDD** (rede mockada, relógio falso): `load_settings` roda antes de `login`; recuperação usa `relogin`, nunca `login`; conta bloqueada sai do pool e outra assume; lock por endpoint não trava outros endpoints; `WorkQueue` reivindica de novo uma fatia expirada; interrupção libera o lock; união dedup por `pk`.
5. **Execução.**
6. **Testes:** unit + um teste de integração simulando "conta A cai na fatia 2 → conta B termina a fatia 2".
7. **Prints:** painel do pool (estados por conta, PNG), linha do tempo de uma corrida com uma queda e o failover, gráfico "logins evitados vs. execuções". **Lidos um a um.**

**Aceite Camada 1:** re-login por execução cai a ~0 quando há sessão salva; nenhuma conta em rajada; queda de uma conta **não perde** trabalho (outra retoma pelo delta); contrato público §3.1 verde.

#### Camada 2 — Failover por cursor exato (com `MobileApiEngine`, depende da Fase 5)

1. **Análise:** medir a perda do resume **aproximado** (Selenium/delta) vs. exato (cursor) — quantos perfis são re-scrolados à toa quando a conta troca.
2. **Pesquisa:** paginação `next_max_id` / `user_followers_v1_chunk(user_id, max_id=cursor)` ([user.md](https://github.com/subzeroid/instagrapi/blob/master/docs/usage-guide/user.md), [followers guide](https://instagrapi.com/guides/get-instagram-followers-python/)); dedupe por `pk`; limites de crawl grande.
3. **Pré-análise:** `CursorStore` (em `PersistentStore`) por `(alvo, list_type)`; o `MobileApiEngine` **exporta** `next_max_id` a cada página e **retoma** de um cursor dado; a `WorkQueue` passa a guardar o cursor na fatia. **Print:** log mostrando conta B continuando no cursor exato onde A parou.
4. **TDD:** engine retoma da página exata de um cursor salvo; troca de conta no meio não repete nem pula perfis (dedupe por `pk`); cursor nulo encerra.
5. **Execução.**
6. **Testes.**
7. **Prints:** `01-cursor-handoff`, `02-sem-reprocesso`, lidos.

**Aceite Camada 2:** ao trocar de conta, o re-processamento de perfis já vistos é ~0 (contra o resume aproximado do Selenium).

#### Desenho concreto (referência de implementação)

Detalhamento pesquisado para tirar a Fase 11 do abstrato — vale para o **engine padrão de hoje (Selenium)**, não só para a API.

**1) Reuso de sessão no Selenium (mata o login sem depender do mobile)** ([selenium cookies](https://www.selenium.dev/documentation/webdriver/interactions/cookies/), [reuse via cookies](https://dev.to/hardiksondagar/reuse-sessions-using-cookies-in-python-selenium-12ca)):
- Após um login bem-sucedido, salvar `driver.get_cookies()` no `SessionStore` (o `SessionCache` já guarda cookies; falta persistir por conta e **usar na entrada**).
- Na execução seguinte: **navegar primeiro para `https://www.instagram.com/`** (obrigatório — só dá pra `add_cookie` no mesmo domínio), injetar cada cookie salvo (`driver.add_cookie(c)`), dar `driver.refresh()` → entra **sem tocar no formulário de login** (o passo que trava). Cookies-chave: `sessionid`, `ds_user_id`, `csrftoken`, `mid`.
- Cookie expirado/inválido → **um** login de recuperação (equivalente ao `relogin`), atualiza o store. O `HttpxEngine.login_with_cookies(...)` **já existe** (handoff de cookies Selenium→httpx) — é a mesma peça, reaproveitada na entrada em vez de só no meio.

**2) Modelo de concorrência (engines são síncronos)** ([threading.Lock](https://realpython.com/python-thread-lock/), [ThreadPoolExecutor](https://superfastpython.com/threadpoolexecutor-thread-safe/)):
- `ThreadPoolExecutor(max_workers=N)` — o InstaT já usa threads no `parallel.py`; **não** migrar para asyncio.
- `AccountPool` protegido por `threading.Lock`; `BoundedSemaphore(min(N, contas_ativas))` limita workers ao nº de contas utilizáveis (1 conta = 1 worker por vez, nunca duas threads na mesma conta).
- `acquire(endpoint)` / `release()` sempre em `try/finally` (liberar o lock mesmo em exceção/interrupção).

**3) `AccountPool` em SQLite (espelha o [twscrape](https://github.com/vladkens/twscrape/blob/main/twscrape/accounts_pool.py))** — desenho já provado em produção:
- Colunas por conta: `active`, `cookies` (JSON), `locks` (JSON `{endpoint → timestamp de expiração}`), `stats`, `last_used`, `error_msg`.
- **Escolha + trava atômica** (`_get_and_lock`): seleciona conta `active` cujo lock daquele endpoint é nulo **ou já expirou** (`json_extract(locks,'$.endpoint') < now`) e grava um lock com validade (ex.: `now + 15min`) — assim uma conta que travou/caiu é **automaticamente reelegível** após o timeout (é o *visibility timeout* da §1.9, sem Redis).
- `acquire_or_wait(wait_timeout, wait_interval)`: se todas travadas, calcula `next_available_at` e espera; senão `NoAccountError`.
- `unlock(endpoint)` libera cedo; `mark_inactive(error_msg)` tira do pool em challenge/ban.

**4) Idempotência e resume** ([dedupe por pk](https://dev.to/marcos_faccindasilva_c3/scraping-150k-instagram-followers-reliably-batching-resume-on-error-and-enrichment-3d3i)): `PersistentStore` com `PRIMARY KEY (alvo, list_type, username)` (dedupe natural na união entre contas); resume por `get_delta_since` (Selenium) ou `CursorStore.next_max_id` (API). Assim recomeçar depois de uma queda **nunca duplica nem reprocessa** o que já foi salvo.

> Levantado lendo as classes reais em 14/09/2026. Cada linha diz **o que existe hoje**, **a lacuna para o mobile** e **em que fase** ela é resolvida.

| Módulo / classe atual | Hoje | Lacuna para o mobile | Fase |
|---|---|---|---|
| `engines/base.py` · `BaseEngine` | `login`, `extract(profile_id, list_type, ...)`, `get_total_count`, `get_recent_posts`, `quit`, `name`, `is_available` | `MobileApiEngine` e `AndroidUiEngine` implementam o mesmo contrato e entram na cascata | 0, 5, 6 |
| `engines/httpx_engine.py` · `HttpxEngine` | `login`, `login_with_cookies`, `extract`, `get_total_count`, `get_recent_posts`; usa httpx | Referência de endpoints/paginação/`on_batch`; transporte migra para `curl_cffi` e headers para o `HeaderFactory` | 3, 4, 5 |
| `proxy.py` · `ProxyPool`/`ProxyState` | Round-robin (`get_next`) com cooldown por falha (`mark_failed`/`mark_success`) | Modelo **errado para conta logada** (rotação por falha). O sticky é classe nova (`StickyProxyPool`) que reusa só o health/cooldown e fixa `conta→porta+cr+asn+sessttl` | 2 |
| `session_cache.py` · `SessionCache` | `save`/`load(max_age)`/`clear` de cookies por usuário | Vira `SessionStore` que guarda cookies **+ `DeviceProfile` + IDs** juntos (senão a sessão invalida) e **evita o re-login** (`load` antes, `relogin`) | 3, 5, **11** |
| `session_pool.py` · `SessionPool`/`Session` | Pool de sessões com `mark_blocked`/`mark_success`/`all_blocked` | Base do `AccountSlot` e do `AccountPool` (estados+locks por conta/endpoint) | 0, 9, **11** |
| `parallel.py` · `ParallelCoordinator` / `parallel_extract` | União de sets + `stop_threshold`; N workers independentes | Passa a ser dirigido pelo `AccountPool` + `WorkQueue` (fatias reivindicáveis, failover) | **11** |
| `checkpoint.py` · `ExtractionCheckpoint` | `save`/`load`/`clear` de um **Set** | Ganha o `CursorStore` (checkpoint de `next_max_id`) p/ failover exato | **11** |
| `persistent_store.py` · `PersistentStore` | `add_batch`/`get_all`/`get_delta_since`/`stats`/`source_breakdown` (SQLite) | Guarda `DeviceProfile`, nível de warming, **cursor** e a `WorkQueue` de claims | 3, 7, **11** |
| `block_predictor.py` · `BlockPredictor` | `record_request`/`record_stale`/`risk_score`/`should_cooldown(0.7)` | Alimenta o `RateGovernor`; ganha os sinais novos (429, `feedback_required`, challenge) | 8 |
| `backoff.py` · `SmartBackoff` | `wait()`/`reset()` exponencial | Vira o componente AIMD do `RateGovernor` (corte ×0.5 em 429) | 8 |
| `worker_pool.py` · `WorkerPool` | `add_worker`/`engines`/`clear` (hoje só `SeleniumEngine`) | Estendido para N `AccountSlot`s (device+conta+proxy), com health de container | 9 |
| `diagnostics.py` · `DiagnosticCollector` | `capture(...)` → bundle com `screenshot.png`, `page_source`, cookies | Ganha `adb screencap` + `uiautomator dump` do slot (evidência do passo 7) | 1, 9 |
| `challenge_resolvers.py`, `email_code.py` | Cadeia de resolvers (email/Bloks) já existente | Reaproveitados nos challenges dentro do app (UI) | 1, 5 |
| `extraction_result.py`, `logging_config.py` | Telemetria (`to_dict()` BigQuery-friendly), logging | + custo de banda (GB) e perfis/GB por corrida | 0, 10 |

**Componentes novos:** `MobileApiEngine`, `AndroidUiEngine`, `DeviceProfile`, `HeaderFactory`, `StickyProxyPool`, `AccountSlot`, `SessionBridge`, `RateGovernor`, `human.py`, `warming.py`, **`SessionStore`, `AccountPool`, `WorkQueue`, `CursorStore`** (Fase 11) — todos em `instat/mobile/`.

---

## 6. Riscos e gates

| Risco | Prob. | Impacto | Mitigação / gate |
|---|---|---|---|
| **Login travando + sem paralelismo (dor atual)** | **Alta** | **Alto** | **Fase 11 (prioritária)**: sessão persistente (sem re-login), AccountPool com failover, cursor resume |
| App não loga em emulador (atestação) | Alta | Alto | **Gate Fase 1** → plano B celular físico |
| redroid instável no WSL2 | Média | Médio | Fase 0 decide VPS Linux se WSL falhar |
| Política DataImpulse proíbe login no IG | ? | Alto | **Gate Fase 0**, confirmação escrita |
| Ban das contas de teste | Alta | Médio | Contas dedicadas, warming, RateGovernor, dead-letter |
| Drift de versão do app (headers/TLS) | Alta | Médio | Fixtures versionadas + drift check (Fase 10) |
| Custo de banda (UI do app) | Média | Médio | API para volume; métrica GB/1k perfis |
| LGPD / ToS | — | Alto | Graph API onde possível; minimização; prints borrados |

---

## 7. Histórico de fases

| Fase | Status | Início | Fim | 1 | 2 | 3 | 4 | 5 | 6 | 7 | Achados nos prints | Commit/PR |
|------|--------|--------|-----|---|---|---|---|---|---|---|--------------------|-----------|
| 0 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 1 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 2 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 3 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 4 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 5 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 6 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 7 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 8 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 9 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 10 | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |
| 11 ⏫ | não iniciada | — | — | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | ☐ | — | — |

Status possíveis: `não iniciada` · `em andamento (passo N)` · `entregue, passo 7 pendente` · `bloqueada (gate)` · `concluída`.

---

## 8. Template de registro de fase

Copiar para `docs/phase-evidence/fase-N/README.md`:

```markdown
# Fase N — <nome>

## 1. Análise (medida)
| Métrica | Valor | Como medi |
|---|---|---|

## 2. Pesquisa
| Opção | Prós | Contras | Fonte |
|---|---|---|---|
**Decisão:** … **Por quê:** …

## 3. Pré-análise
- Desenho / contratos:
- Critérios de aceite:
- Caminho do print: quem loga = … | device/slot = … | estado a preparar = … | toca conta real? … | aval do Tiago: (data/link)

## 4. TDD
- Testes criados: …
- Log vermelho: `01-tdd-red.log`

## 5. Execução
- Arquivos alterados: …

## 6. Testes
- pytest / ruff / mypy / mobile: resultado + log

## 7. Prints (um por linha, TODOS lidos)
| Arquivo | O que mostra | Problema/melhoria encontrada | Ação |
|---|---|---|---|
```

---

## 9. Fontes

**Aparelho / automação:** [redroid-doc](https://github.com/remote-android/redroid-doc) · [redroid no WSL2](https://github.com/remote-android/redroid-doc/blob/master/deploy/wsl.md) · [redroid-script (ndk/magisk/gapps)](https://github.com/ayasa520/redroid-script) · [ARM sem bridge #933](https://github.com/remote-android/redroid-doc/issues/933) · [props #481](https://github.com/remote-android/redroid-doc/issues/481) · [gpu_mode #705](https://github.com/remote-android/redroid-doc/issues/705) · [GramAddict config.yml](https://github.com/GramAddict/bot/blob/master/config-examples/config.yml) · [budtmo/docker-android](https://github.com/budtmo/docker-android) · [instadroid](https://github.com/ivylikethevine/instadroid) · [GramAddict](https://github.com/GramAddict/bot) · [GramAddict config](https://docs.gramaddict.org/#/configuration) · [uiautomator2 gestures](https://github.com/appium/appium-uiautomator2-driver/blob/master/docs/android-mobile-gestures.md)

**Detecção de emulador:** [Android-Emulator-Detection](https://github.com/gmh5225/Android-Emulator-Detection) · [strazzere/anti-emulator](https://github.com/strazzere/anti-emulator) · [XDA redroid/Play Integrity](https://xdaforums.com/t/how-do-i-pass-the-play-integrity-checker-with-redroid.4721174/)

**API privada / identidade:** [instagrapi](https://github.com/subzeroid/instagrapi) · [instagrapi best practices](https://subzeroid.github.io/instagrapi/latest/usage-guide/best-practices/) · [instagrapi errors](https://instagrapi.com/guides/errors) · [feedback_required](https://instagrapi.com/guides/errors/feedback-required) · [challenge_required](https://instagrapi.com/guides/errors/challenge-required) · [molkex/instagram-private-api](https://github.com/molkex/instagram-private-api) · [dilame request.ts](https://github.com/dilame/instagram-private-api/blob/master/src/core/request.ts) · [dilame headers #855](https://github.com/dilame/instagram-private-api/issues/855) · [assinatura #1085](https://github.com/dilame/instagram-private-api/issues/1085) · [pavlovdog api.py](https://github.com/pavlovdog/Instagram-private-API/blob/master/api.py)

**TLS:** [curl_cffi](https://github.com/lexiforest/curl_cffi) · [fingerprint customizado](https://curl-cffi.readthedocs.io/en/latest/impersonate/customize.html) · [targets](https://curl-cffi.readthedocs.io/en/stable/impersonate/targets.html) · [tls-client](https://github.com/bogdanfinn/tls-client) · **verificação:** [tls.peet.ws](https://tls.peet.ws/) · [browserleaks/tls](https://browserleaks.com/tls) · [scrapfly JA3/JA4](https://scrapfly.io/web-scraping-tools/ja3-fingerprint) · [JA4+ 2026](https://proxylabs.app/blog/ja4-tls-fingerprinting-2026)

**Endpoints da API privada ("obter os valores"):** [instagrapi user.md](https://github.com/subzeroid/instagrapi/blob/master/docs/usage-guide/user.md) · [ping/friendships](https://instagram-private-api.readthedocs.io/en/latest/_modules/instagram_private_api/endpoints/friendships.html) · [issue #2797 (0 linhas em checkpoint)](https://github.com/subzeroid/instagrapi/issues/2797) · [melhores scrapers open-source 2026](https://scrapfly.io/blog/posts/best-open-source-instagram-scrapers)

**Captura de tráfego:** [Instagram-SSL-Pinning-Bypass](https://github.com/0xSHAK1B/Instagram-SSL-Pinning-Bypass) · [tópico instagram-ssl-pinning-bypass](https://github.com/topics/instagram-ssl-pinning-bypass)

**Proxy:** [DataImpulse tipos de conexão](https://docs.dataimpulse.com/proxies/types-of-connections) · [DataImpulse mobile](https://dataimpulse.com/mobile-proxies/) · [sticky × rotating](https://dataimpulse.com/blog/rotating-or-sticky-proxies-how-to-make-the-right-choice/) · [tun2proxy](https://github.com/tun2proxy/tun2proxy) · [tun2socks em container](https://bigmike.help/en/devops/003/) · [sockstun](https://github.com/heiher/sockstun) · [DataImpulse mobile review (~US$2/GB)](https://github.com/jvetste/dataimpulse-mobile-proxy-review)

**Selenium × Playwright (§3.2):** [scrapfly](https://scrapfly.io/blog/posts/playwright-vs-selenium) · [decodo](https://decodo.com/blog/playwright-vs-selenium) · [ByteTunnels stealth](https://bytetunnels.com/posts/playwright-vs-selenium-stealth-which-evades-detection-better/) · [benchmark anti-detect 2026](https://dev.to/ianlpaterson/anti-detect-browser-benchmark-2026-7-stealth-tools-31-cloudflare-targets-651-verdicts-4361) · [Patchright/Camoufox/noDriver](https://scrapewise.ai/blogs/playwright-stealth-2026) · [multi-account contexts](https://medium.com/@proxiesthatwork/playwright-vs-selenium-for-scraping-proxies-36edf0434a5b)

**Rate limit:** [Phyllo — limites 2026](https://www.getphyllo.com/post/instagram-api-rate-limits-explained----and-how-to-scale-beyond-them-2026) · [SMTasker warming](https://smtasker.com/automate-instagram-engagement-without-ban/) · [instagrapi followers](https://instagrapi.com/guides/get-instagram-followers-python/) · [instagrapi scraper](https://instagrapi.com/guides/instagram-scraper-python) · [scrapfly — scraping IG 2026](https://scrapfly.io/blog/posts/how-to-scrape-instagram) · [instaloader #1285](https://github.com/instaloader/instaloader/issues/1285)

**Paralelismo resiliente / sessão / failover (Fase 11):** [instagrapi best practices](https://subzeroid.github.io/instagrapi/usage-guide/best-practices.html) · [login_required (relogin vs login)](https://instagrapi.com/guides/errors/login-required) · [session persistence (file/Redis/Postgres)](https://instagrapi.com/guides/instagrapi-session-persistence) · [instagrapi user.md (next_max_id chunk)](https://github.com/subzeroid/instagrapi/blob/master/docs/usage-guide/user.md) · [twscrape (account pool)](https://github.com/vladkens/twscrape) · [twscrape accounts_pool.py (desenho SQLite/lock)](https://github.com/vladkens/twscrape/blob/main/twscrape/accounts_pool.py) · [Scweet (multi-account pooling)](https://github.com/Altimis/Scweet) · [Redis job queue](https://redis.io/docs/latest/develop/use-cases/job-queue/) · [Celery acks_late/reject_on_worker_lost](https://dev.to/artemooon/celery-redis-at-scale-designing-a-reliable-and-efficient-task-queue-in-production-27nh) · [reuso de sessão Selenium](https://dev.to/hardiksondagar/reuse-sessions-using-cookies-in-python-selenium-12ca) · [selenium cookies](https://www.selenium.dev/documentation/webdriver/interactions/cookies/) · [threading.Lock](https://realpython.com/python-thread-lock/) · [resume-on-error + dedupe por pk](https://dev.to/marcos_faccindasilva_c3/scraping-150k-instagram-followers-reliably-batching-resume-on-error-and-enrichment-3d3i)
