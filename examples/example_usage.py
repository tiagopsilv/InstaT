"""
    Example script demonstrating how to use InstaExtractor to log in and extract followers or following from Instagram.

    This script is designed for instructional purposes and shows the full range of capabilities
    available in the InstaExtractor class. It supports custom configuration of scroll timing,
    login behavior, extraction limits, and session handling.

    This example demonstrates how to:
    - Initialize the extractor with custom parameters.
    - Configure scroll behavior and retry strategies.
    - Extract followers and following lists with a defined timeout.
    - Handle the extractor session with proper cleanup.

    Requirements:
    -------------
    - Ensure that the `selectors.json` file exists and includes all necessary keys (see SelectorLoader).
    - You must provide a valid Instagram username and password.
    - The script uses a mobile user-agent and headless Firefox for automation.

    Parameters you can tweak:
    --------------------------
    - headless (bool): Whether to run the browser in headless mode (no GUI).
    - timeout (int): Maximum wait time (in seconds) for elements to load.
    - max_refresh_attempts (int): Max page refreshes if profiles are not loading.
    - wait_interval (float): Time to wait between scroll checks.
    - additional_scroll_attempts (int): Extra scroll attempts to load more profiles.
    - pause_time (float): Pause between each scroll.
    - max_attempts (int): Scroll retries when no new profiles are found.
    - max_retry_without_new_profiles (int): Max retries allowed with no new data before refreshing.

    --------------------------------------------------------------------------
    About the developer:
    This solution was created by Tiago Pereira da Silva, a passionate and highly skilled 
    Data & Automation Specialist with experience in financial systems, Python development, 
    and web scraping at scale. 

    Tiago is currently open to new freelance opportunities and job offers (remote or hybrid),
    especially in the fields of data engineering, automation, and digital intelligence.

    🔗 LinkedIn: https://www.linkedin.com/in/tiagopsilvatec/
    💻 GitHub: https://github.com/tiagopsilv
    📧 Contact: tiagosilv@gmail.com
    --------------------------------------------------------------------------
"""

from InstaT.extractor import InstaExtractor

# === BASIC USAGE ===
# Login and extract both followers and following from a target profile
username = "your_username"  # Replace with your Instagram username
password = "your_password"  # Replace with your Instagram password

# Optional: Toggle headless mode (True means no browser window will open)
headless_mode = True

# Optional: Set timeout for loading elements (in seconds)
timeout = 12

# Initialize the extractor
extractor = InstaExtractor(username=username, password=password, headless=headless_mode, timeout=timeout)

try:
    # === CONFIGURATION ===
    # You can fine-tune the behavior of the extractor:
    extractor.max_refresh_attempts = 10  # How many times to refresh if no new profiles are loaded
    extractor.wait_interval = 0.4        # Delay between checks for new profiles (in seconds)
    extractor.additional_scroll_attempts = 2  # Extra scrolls to ensure full list capture
    extractor.pause_time = 0.4           # Pause between each scroll (in seconds)
    extractor.max_attempts = 3           # Max scroll attempts before checking for new profiles

    # === EXTRACTION EXAMPLES ===

    # 1. Extract followers from a target profile
    # Optional parameter `max_duration` sets a time limit (in seconds) for the scroll loop.
    # If this time is exceeded, scrolling will stop even if not all profiles are captured.
    # If `max_duration` is None (default), the extractor will continue until the expected count
    # is reached or max_refresh_attempts is exceeded.
    followers = extractor.get_followers("target_profile_username", max_duration=30.0)
    print(f"Followers found: {len(followers)}")
    print(followers)

    # 2. Extract following accounts from a target profile
    following = extractor.get_following("target_profile_username")
    print(f"Following found: {len(following)}")
    print(following)

    # === BULK USAGE ===
    # To loop over multiple profiles and save results:
    # profiles = ["runner_001", "brand_athlete", "coach_maria"]
    # for profile in profiles:
    #     followers = extractor.get_followers(profile, max_duration=20.0)
    #     print(f"{profile} has {len(followers)} followers")

finally:
    # Always quit the browser session properly
    extractor.quit()
