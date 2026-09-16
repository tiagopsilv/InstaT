# Fase F6a — Spike de viabilidade Android, sem conta Instagram

Status: **bloqueada (passo 3 — pré-análise): fonte do APK e uso de imagem com tradução ARM dependem de decisão do Tiago**
Base: roadmap v13.7; host Windows 11 da sessão de 16/09/2026
Nenhuma imagem baixada, nenhum container iniciado, nenhum APK obtido, nenhuma conta usada, nenhum tráfego pago.

## 1. Análise (medições do host, 16/09/2026)

| Item | Observado | Como |
|---|---|---|
| Virtualização | habilitada no firmware; hypervisor presente | `Win32_Processor.VirtualizationFirmwareEnabled`, `Win32_ComputerSystem.HypervisorPresent` |
| WSL | Ubuntu-24.04 (WSL 2, parado) e `docker-desktop` (parado) | `wsl -l -v` |
| Kernel WSL | `6.6.114.1-microsoft-standard-WSL2` | `uname -r` |
| `/dev/kvm` no WSL | **presente** (`crw-rw---- root kvm 10, 232`) | `ls -l /dev/kvm` |
| Binder no kernel | **ausente**: `# CONFIG_ANDROID_BINDER_IPC is not set`, `# CONFIG_DMABUF_HEAPS is not set`, sem `binder` em `/proc/filesystems` | `/proc/config.gz`, `/proc/filesystems` |
| Recursos no WSL | 24 CPUs, 15,5 GB RAM, 952 GB livres | `nproc`, `free -m`, `df -h` |
| Docker | Docker Desktop instalado; engine **não em execução** (`dockerDesktopLinuxEngine` indisponível) | `docker version` |

Conclusão da análise: **redroid é inviável neste host sem kernel WSL customizado** (binder ausente). **budtmo/docker-android é tecnicamente possível** (KVM presente), dependendo do engine Docker.

## 2. Pesquisa

| Fonte | O que sustenta | O que não comprova |
|---|---|---|
| redroid — deploy WSL [S6] (16/09/2026) | redroid em WSL2 exige **kernel compilado** com `CONFIG_ANDROID_BINDER_IPC`, `CONFIG_ANDROID_BINDERFS`, `CONFIG_DMABUF_HEAPS` e apontado em `.wslconfig` | estabilidade do kernel customizado neste host |
| budtmo/docker-android README [S13] | imagens `emulator_9.0` a `emulator_14.0`; exemplo com `--device /dev/kvm`, noVNC em 6080; passos para WSL2 no Windows 11 | tipo de imagem de sistema (Google APIs ou não) e presença de tradução ARM em cada tag |
| Docker Hub, tags budtmo [S46] | `emulator_11.0`–`emulator_14.0` amd64, 2,77–3,03 GB, atualizadas em 01/09/2026 | funcionamento com o APK do Instagram |
| Android Developers Blog [S45] | imagens **Android 11 x86** (Google APIs/Play) executam binários ARM por tradução; uso declarado: "can only be used for application development and debug purposes" | suporte explícito em x86_64; compatibilidade de apps com checagem de ABI |
| redroid README e #933 [S5, S25] | conflito já registrado sobre tradutor por tag (redroid) | — |

**Decisão técnica proposta:** neste host, F6a com **budtmo em WSL2 + KVM**, matriz mínima `emulator_11.0` e `emulator_14.0`; redroid só se o Tiago autorizar compilar e instalar kernel WSL customizado.

## 3. Pré-análise — bloqueios concretos

| # | Bloqueio | Por que impede | Decisão necessária |
|---|---|---|---|
| B1 | **Fonte do APK do Instagram** | o critério de aceite exige APK com hash, certificado e ABIs registrados; budtmo padrão não tem Play Store | escolher: (a) extrair do próprio celular do Tiago via ADB (`pm path` + `adb pull` dos splits), com certificado conferido; (b) imagem com Play Store e login de conta Google (uso de conta → aval); (c) espelho de terceiros — **não recomendado** (integridade e termos) |
| B2 | **Uso da tradução ARM fora de "development and debug"** | a fonte oficial restringe o uso declarado da tradução nas imagens do emulador [S45] | decisão de conformidade do Tiago antes de usar essas imagens para o projeto |
| B3 | **redroid exige kernel WSL customizado** | alteração de sistema no host (kernel do WSL) | autorizar ou manter redroid fora deste host |
| B4 | Engine Docker parado; imagens de ~3 GB cada | operacional, sem necessidade de aval | executar após B1 e B2 |

Caminho do print (definido, não executado): `adb exec-out screencap -p` e noVNC em loopback; sem conta Instagram; sem proxy pago.

Critérios de aceite: os da §8/F6a do roadmap v13.7, sem alteração.

## 4–7
Não iniciados. A fase não pode escrever testes de provisionamento (passo 4) antes de resolver a fonte do APK e a conformidade (passo 3).

## F6b
Depende do F6a aprovado → **bloqueada por dependência**.
