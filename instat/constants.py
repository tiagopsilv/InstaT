"""
Constantes de timing centralizadas para o InstaT.
Cada constante documenta onde é usada e seu propósito.
Substituem magic numbers hardcoded para facilitar tuning
e evitar padrões mecânicos detectáveis pelo Instagram.
"""
import random
import time

# === Login ===
LOGIN_POST_CLICK_DELAY = 3.0        # login.py: após click do botão de login
IGNORE_BUTTON_PRE_CLICK = 3.0       # utils.py: wait_before_click em click_ignore_button_if_present
DISMISS_MODAL_TIMEOUT = 6           # utils.py: timeout em dismiss_save_login_modal

# === Scrolling ===
SCROLL_PAUSE = 0.5                  # extractor.py: default de self.pause_time
SCROLL_INNER_PAUSE = 0.4            # utils.py: pause_time dentro de wait_for_new_profiles
PROFILE_WAIT_INTERVAL = 0.5         # extractor.py: default de self.wait_interval

# === Retry ===
ELEMENT_RETRY_DELAY = 1.0           # utils.py: entre retries em find_element_safe
ELEMENTS_RETRY_WAIT = 0.3           # utils.py: wait_time default em find_elements_safe
ELEMENTS_RETRY_WAIT_LONG = 0.7      # utils.py: wait_time em wait_for_new_profiles
SPINNER_WAIT_TIMEOUT = 5            # utils.py: WebDriverWait para spinner desaparecer

# === Performance (PERF-01) ===
LOADING_SPINNER_WAIT = 1.0          # utils.py: max wait para spinner desaparecer (antes: 5.0)
COOKIE_RESTORE_REFRESH_TIMEOUT = 5  # login.py: timeout após refresh no session restore


def human_delay(base: float, variance: float = 0.3) -> float:
    """
    Delay gaussiano humanizado. Evita padrões mecânicos detectáveis.
    Gera valores variados em torno de `base` (ex: 0.3s, 0.6s, 0.4s, 0.7s).
    Floor de 0.1s para nunca ser instantâneo.
    Retorna o delay efetivamente aplicado.
    """
    delay = max(0.1, random.gauss(base, variance))
    time.sleep(delay)
    return delay
