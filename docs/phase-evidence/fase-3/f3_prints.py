"""F3 passo 7 — relatório com execuções reais do código, adapters fake, sem rede nem conta.

Gera 01-contrato-legado.png, 02-perfil-sem-driver.png, 03-capacidades.png e prints.json.
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

from loguru import logger  # noqa: E402

logger.remove()

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import test_f3_profile_capability as T  # noqa: E402
import test_public_api_contract as C  # noqa: E402

from instat import InstaExtractor  # noqa: E402
from instat.engines.httpx_engine import HttpxEngine  # noqa: E402
from instat.engines.playwright_engine import PlaywrightEngine  # noqa: E402
from instat.engines.selenium_engine import SeleniumEngine  # noqa: E402
from instat.exceptions import ChallengeError, ExtractionStoppedError  # noqa: E402
from instat.mobile.engines import AndroidUiEngine, MobileApiEngine  # noqa: E402
from instat.profile_info import ProfileInfo  # noqa: E402

FIELDS = ("username", "full_name", "followers_count", "following_count", "posts_count", "is_private",
          "is_verified", "profile_pic_url")
OUT = {}


def row(p):
    return [getattr(p, f) for f in FIELDS]


# ------------------------------------------------------------ 01 contrato
fake = C._FakeEngine(followers=["alice", "bob", "carol"], following=["dave", "erin"],
                     info=ProfileInfo(username="target", url="https://www.instagram.com/target/",
                                      full_name="Target Name", followers_count=1894, following_count=1892,
                                      posts_count=123, profile_pic_url="https://example.test/pic.jpg"))
ext = InstaExtractor(username="your_user", password="your_pass", headless=True, engines=[fake])
target = ext.get_profile("target")
snippet = {"tem__driver": hasattr(fake, "_driver"), "chamadas_get_profile_info": list(fake.profile_info_calls),
           "perfil": row(target), "followers": target.get_followers(), "following": target.get_following()}

legacy_ext = InstaExtractor.__new__(InstaExtractor)
legacy_ext._engine = MagicMock()
legacy_ext._engine._driver = T._fake_driver()
legacy_profile = legacy_ext.get_profile("target")
OUT["contrato"] = {"sem_driver": snippet, "legado_com_driver": row(legacy_profile)}

# ------------------------------------------------------------ 02 perfil sem driver
httpx_eng = T._httpx_with(200, T.WEB_PROFILE_INFO)
httpx_eng.login = lambda *a, **k: True
ext_h = InstaExtractor(username="u", password="p", headless=True, engines=[httpx_eng])
p_h = ext_h.get_profile("alvo")
legacy, capable = T.LegacyEngine("primaria-sem-capacidade"), T.CapableEngine("secundaria-capaz")
p_c = InstaExtractor(username="u", password="p", headless=True, engines=[legacy, capable]).get_profile("alvo")
c1, c2 = T.CapableEngine("c1", exc=ChallengeError("checkpoint_required")), T.CapableEngine("c2")
try:
    InstaExtractor(username="u", password="p", headless=True, engines=[c1, c2]).get_profile("alvo")
    stop = "sem parada (ERRO)"
except ExtractionStoppedError as e:
    stop = f"ExtractionStoppedError reason={e.reason}"
OUT["sem_driver"] = {
    "httpx_web_profile_info": {"perfil": row(p_h), "bio": p_h.bio,
                               "url_requisitada": str(httpx_eng._client.get.call_args)},
    "cascata": {"perfil": row(p_c), "chamadas_secundaria": capable.info_calls},
    "challenge": {"resultado": stop, "chamadas_c2": c2.info_calls},
}

# ------------------------------------------------------------ 03 capacidades e mensagens
engines = [SeleniumEngine(headless=True), PlaywrightEngine(), HttpxEngine(), AndroidUiEngine(), MobileApiEngine(),
           T.LegacyEngine("subclasse-legada")]
matrix = [[e.name, sorted(e.capabilities)] for e in engines]
msgs = {}
try:
    InstaExtractor(username="u", password="p", headless=True,
                   engines=[T.LegacyEngine("minha-engine")]).get_profile("alvo")
except RuntimeError as e:
    msgs["só engine legada"] = str(e)
with patch("instat.engines.httpx_engine.HttpxEngine.is_available", new=property(lambda self: False)):
    ext_x = InstaExtractor(username="u", password="p", headless=True, engines=[T.LegacyEngine("primaria"), "httpx"])
try:
    ext_x.get_profile("alvo")
except RuntimeError as e:
    msgs["httpx sem extra"] = str(e)
OUT["capacidades"] = {"matriz": matrix, "mensagens": msgs}

with open(os.path.join(HERE, "prints.json"), "w", encoding="utf-8") as f:
    json.dump(OUT, f, ensure_ascii=False, indent=2, default=str)

# ------------------------------------------------------------ render
GRN, YEL, HEAD = "#d9f2d9", "#fff2cc", "#dde3ea"


def table(ax, header, rows, colors=None, widths=None, fs=10, title=None):
    ax.axis("off")
    if title:
        ax.set_title(title, loc="left", fontsize=11, fontweight="bold")
    t = ax.table(cellText=[[str(c) for c in r] for r in rows], colLabels=header, loc="upper left",
                 cellLoc="left", colWidths=widths)
    t.auto_set_font_size(False)
    t.set_fontsize(fs)
    t.scale(1, 1.45)
    for j in range(len(header)):
        t[0, j].set_facecolor(HEAD)
        t[0, j].set_text_props(weight="bold")
    for i, c in enumerate(colors or [], start=1):
        if c:
            for j in range(len(header)):
                t[i, j].set_facecolor(c)


def wrap(s, n=150):
    import textwrap
    return "\n".join(textwrap.wrap(s, n, break_long_words=False, break_on_hyphens=False))


HEADERS = ("user", "full_name", "seguidores", "seguindo", "posts", "privado", "verificado", "profile_pic_url")


c = OUT["contrato"]
fig, axes = plt.subplots(2, 1, figsize=(15, 6.5), gridspec_kw={"height_ratios": [1.3, 1.6]})
fig.suptitle("F3 · 01 contrato — snippet público com engine SEM _driver × caminho legado com _driver", x=0.01,
             ha="left")
table(axes[0], ["origem", *HEADERS],
      [["engine fake sem _driver", *c["sem_driver"]["perfil"]], ["legado: _driver (mock)", *c["legado_com_driver"]]],
      [GRN, GRN], [0.16, 0.05, 0.09, 0.07, 0.07, 0.05, 0.06, 0.07, 0.19], fs=9)
axes[0].text(0, -0.05, "is_private/is_verified: a engine fake não informa esses campos (None); o leitor DOM "
             "legado devolve False quando o seletor não acha nada.", transform=axes[0].transAxes, fontsize=9)
s = c["sem_driver"]
table(axes[1], ["verificação", "resultado"],
      [["hasattr(engine, '_driver')", s["tem__driver"]],
       ["engine.get_profile_info chamada (delegação real)", s["chamadas_get_profile_info"]],
       ["target.get_followers()", s["followers"]], ["target.get_following()", s["following"]]],
      [GRN, GRN, GRN, GRN], [0.4, 0.6], fs=10)
fig.savefig(os.path.join(HERE, "01-contrato-legado.png"), dpi=110, bbox_inches="tight")
plt.close(fig)

d = OUT["sem_driver"]
fig, axes = plt.subplots(2, 1, figsize=(15, 6))
fig.suptitle("F3 · 02 perfil sem driver — HttpxEngine com resposta SIMULADA de web_profile_info; cascata e challenge",
             x=0.01, ha="left")
table(axes[0], ["caminho", *HEADERS],
      [["httpx (resposta simulada)", *d["httpx_web_profile_info"]["perfil"]],
       ["cascata: legada → capaz", *d["cascata"]["perfil"]]], [GRN, GRN],
      [0.17, 0.05, 0.09, 0.07, 0.07, 0.05, 0.06, 0.07, 0.19], fs=9)
table(axes[1], ["verificação", "resultado"],
      [["bio lida do JSON", repr(d["httpx_web_profile_info"]["bio"])],
       ["requisição feita pelo httpx", wrap(d["httpx_web_profile_info"]["url_requisitada"], 110)],
       ["chamadas à engine secundária", d["cascata"]["chamadas_secundaria"]],
       ["challenge na 1ª engine capaz", d["challenge"]["resultado"]],
       ["chamadas à 2ª engine após challenge", d["challenge"]["chamadas_c2"]]],
      [GRN, None, GRN, GRN, GRN], [0.3, 0.7], fs=10)
fig.savefig(os.path.join(HERE, "02-perfil-sem-driver.png"), dpi=110, bbox_inches="tight")
plt.close(fig)

k = OUT["capacidades"]
fig, axes = plt.subplots(2, 1, figsize=(15, 8), gridspec_kw={"height_ratios": [1.5, 1.6]})
fig.suptitle("F3 · 03 capacidades explícitas e mensagens de erro", x=0.01, ha="left")
table(axes[0], ["engine", "capabilities"], k["matriz"], None, [0.25, 0.75], fs=10)
table(axes[1], ["situação", "mensagem de RuntimeError"],
      [[name, wrap(msg, 115)] for name, msg in k["mensagens"].items()], [YEL, YEL], [0.15, 0.85], fs=9)
axes[1].tables[0].scale(1, 3.2)
fig.savefig(os.path.join(HERE, "03-capacidades.png"), dpi=110, bbox_inches="tight")
plt.close(fig)
print("ok")
