# 🔥 InstaT - Intelligent Instagram Data Extractor

**InstaT** is a powerful and easy-to-use Python tool to automatically log in to Instagram and extract followers and following lists. Built with Selenium, it supports mobile user-agent emulation, dynamic scrolling, and strong logging. Perfect for marketers, data analysts, and developers who want fast insights from Instagram profiles.

Extract Instagram data reliably for marketing analysis, influencer targeting, or competitive research — all while minimizing the risk of detection.

---

## 🚀 Features

- 🔐 **Automated Login**: Logs into Instagram with robust retry logic.
- 📱 **Mobile Emulation**: Uses a mobile user-agent to reduce detection.
- 🔁 **Smart Scrolling**: Scrolls dynamically with logic to retry or refresh.
- 🕒 **Timeout Controls**: Supports `timeout` and `max_duration` to limit operations.
- ⚙️ **External Selector Configuration**: Cleanly handles changes in Instagram's UI via JSON.
- 📂 **Advanced Logging**: Integrated with Loguru for colorized logs and persistent files.
- 🧪 **Built-in Testing**: Unit tests validate login, extraction, and selector loading.
- 🧠 **Business Ready**: Ideal for marketing agencies, influencer tracking, and client prospecting.
- 🛡️ **Anti-Ban Friendly**: Designed with human-like delays and retries.

---

## 🛠️ Step-by-Step Installation

### 1. Clone the repository
```bash
git clone https://github.com/tiagopsilv/InstaT.git
cd InstaT
```

### 2. (Optional) Create and activate a virtual environment
Using a virtual environment helps avoid dependency conflicts with other projects.
```bash
python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate
```

### 3. Install dependencies
Installs the necessary libraries (Selenium, Loguru, etc.) to run the tool.
```bash
pip install -r requirements.txt
```

### 4. Python Version
Ensure you are using **Python 3.8 or higher**.

---

## ⚙️ Configuration Notes

The `selectors.json` file contains all XPaths and selectors used to interact with Instagram's interface. If Instagram updates its UI, just update the JSON file — no need to change the code.

Ensure it exists at:
```
InstaT/config/selectors.json
```

<details>
<summary>📄 Example selectors.json structure</summary>

```json
{
  "LOGIN_USERNAME_INPUT": "input[name='username']",
  "LOGIN_PASSWORD_INPUT": "input[name='password']",
  "LOGIN_BUTTON_CANDIDATE": "//button",
  "FOLLOWERS_LINK": "//a[contains(@href, '/followers')]/span",
  "FOLLOWING_LINK": "//a[contains(@href, '/following')]/span",
  "CLOSE_MODAL_BUTTON": "//div[@role='dialog']//button",
  "PROFILE_USERNAME_SPAN": "span._ap3a",
  "IGNORE_BUTTON": "//button[contains(text(), 'Agora n\u00e3o')]",
  "SAVE_LOGIN_INFO_BUTTON": "//div[@role='dialog']//button",
  "SAVE_LOGIN_INFO_DIALOG": "//div[@role='dialog']",
  "LOADING_SPINNER": "//div[@aria-label='Carregando...']"
}
```
</details>

---

## 📸 Simple Example

```python
from InstaT.extractor import InstaExtractor

try:
    # Initialize with login credentials and browser configuration
    extractor = InstaExtractor(
        username="your_username",
        password="your_password",
        headless=True,     # Run browser in background
        timeout=10         # Max seconds to wait for page elements
    )

    # Fine-tune scroll logic to behave like a human
    extractor.max_refresh_attempts = 10
    extractor.wait_interval = 0.4
    extractor.additional_scroll_attempts = 2
    extractor.pause_time = 0.4
    extractor.max_attempts = 3

    # Optional timeout for the entire extraction session
    followers = extractor.get_followers("target_profile", max_duration=30.0)
    following = extractor.get_following("target_profile")

    print("Followers:", followers)
    print("Following:", following)

except Exception as e:
    print("Error during extraction:", e)

finally:
    extractor.quit()
```

➡️ For a complete example with additional insights and explanations, see [`examples/example_usage.py`](examples/example_usage.py).

---

## 🆕 New Utility: `get_total_count()`

You can now retrieve just the **total number of followers or following** without opening the Instagram modal or loading the full list.

### ✅ Example usage:

```python
count = extractor.get_total_count("target_profile", list_type="followers")
print("Total followers:", count)

---

## ❓ Common Issues & Fixes

- **Login fails**: Disable `headless=True` if you have 2FA or login challenges.
- **Empty results**: Instagram may throttle large lists — reduce `max_duration`.
- **Selectors not working**: Update `selectors.json` if Instagram's layout changed.

---

## 🏗️ Architecture Overview

This project is designed around separation of concerns:

- `login.py`: Handles login logic and WebDriver setup using headless Firefox and mobile emulation.
- `extractor.py`: Core logic for scrolling, modal handling, and profile extraction.
- `utils.py`: Reliable utility methods to reduce fragile DOM interactions.
- `selectors.json`: Clean external management of XPaths and selectors.
- `tests/`: Modular tests covering login, extraction, and selector loading.

**Why this architecture?**
- ✔️ **Maintainable**: Easy to update selectors and logic independently.
- ✔️ **Robust**: Exception handling, retries, and clear logging improve stability.
- ✔️ **Scalable**: Easy to expand to support stories, posts, or messages in the future.
- ✔️ **Business-Driven**: Built for real-world use cases like influencer analysis, campaign tracking, or follower insights.
- ✔️ **Testable**: Includes pre-written test cases simulating login flows, extraction behaviors, and JSON config validation.

---

## ✅ Tests & Validation

Unit tests ensure code reliability. All core functions are covered:

- `test_login.py`: Simulates login scenarios including success, timeout, invalid credentials, and fallback login button clicks.
- `test_extractor.py`: Tests modal extraction, scrolling logic, edge cases with slow internet, and profile uniqueness.
- `test_selector_loader.py`: Validates loading of selectors from JSON and fallback behavior in case of missing or corrupted files.


📦 **How to run the tests:**
```bash
pytest tests/
```

📢 We welcome suggestions and improvements! You can contribute:
- New test scenarios (e.g., multi-factor login, network throttling)
- Handling of Instagram's UI updates
- Enhanced performance with asyncio or undetected webdriver

⚠️ *Note*: While implementing asyncio or using undetected webdriver can improve performance and stealth, it may increase maintenance effort and risk breaking with future Instagram updates.

---

## ▶️ Running the Tool

```bash
python example_usage.py
```

This script logs into Instagram and prints followers/following of a profile.

📂 For a full instructional example with comments, check:
```
examples/example_usage.py
```

---

## 📁 Project Structure

```
InstaT/
├── extractor.py              # Core extractor logic
├── login.py                  # Login & browser management
├── utils.py                  # Helper functions
├── config/
│   └── selectors.json        # UI selectors in JSON
├── tests/
│   ├── test_login.py
│   ├── test_extractor.py
│   └── test_selector_loader.py
└── examples/
    └── example_usage.py      # Real-world usage sample
```

---

## 💼 Use It for Business

This tool is ideal for:
- 🔍 Influencer discovery
- 📈 Campaign tracking
- 🤝 Partnership evaluation
- 🛍️ Market research for competitors

---

## 👨‍💻 About the Developer

**Tiago Pereira da Silva** is a skilled Python developer and automation expert with deep experience in scraping, process optimization, and systems integration.

- Over 10 years working in systems development and data engineering.
- Solid knowledge in automation, ETL, .NET, SQL Server, Python and web scraping.
- Passionate about building smart solutions for businesses and scaling data insights.

🧠 Open to freelance jobs, consulting, partnerships, or part-time tech roles.

📧 [tiagosilv@gmail.com](mailto:tiagosilv@gmail.com)  
🔗 [linkedin.com/in/tiagopsilvatec](https://www.linkedin.com/in/tiagopsilvatec)

---

## 🌟 Support & Contributions

If this project helped you, consider giving it a star ⭐ on GitHub.

You’re welcome to contribute by improving tests, performance, or features.

---

## 📄 License

MIT License — see `LICENSE` file for terms.

---

## 📬 Contact for Freelance Projects

Tiago Pereira da Silva is available for freelance jobs, web scraping, Python automation, or data extraction consulting.

📧 [tiagosilv@gmail.com](mailto:tiagosilv@gmail.com)  
🔗 [linkedin.com/in/tiagopsilvatec](https://www.linkedin.com/in/tiagopsilvatec)
