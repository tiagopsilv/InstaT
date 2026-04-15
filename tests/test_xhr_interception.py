import unittest
from unittest.mock import MagicMock, patch

from instat.engines.playwright_engine import PlaywrightEngine


def make_response(url, status=200, json_data=None, json_raises=False):
    """Mock de Response do Playwright."""
    resp = MagicMock()
    resp.url = url
    resp.status = status
    if json_raises or json_data is None:
        resp.json.side_effect = Exception("not JSON")
    else:
        resp.json.return_value = json_data
    return resp


class TestXhrInterception(unittest.TestCase):

    def _make_engine_with_mocked_page(self):
        e = PlaywrightEngine()
        e._page = MagicMock()
        e._page.query_selector_all.return_value = []
        return e

    def _setup_handler_capture(self, engine, fire_callback=None):
        """Captures the handler registered via page.on('response', handler).
        fire_callback(handler) is invoked inside page.evaluate to simulate XHR firing."""
        captured = {'handler': None}

        def fake_on(event, handler):
            captured['handler'] = handler
        engine._page.on.side_effect = fake_on

        def evaluate_then_fire(*args, **kw):
            if fire_callback and captured['handler']:
                fire_callback(captured['handler'])
        engine._page.evaluate.side_effect = evaluate_then_fire
        return captured

    def test_intercept_extracts_usernames_from_json(self):
        e = self._make_engine_with_mocked_page()

        def fire_xhr(handler):
            resp = make_response(
                'https://i.instagram.com/api/v1/friendships/12345/followers/',
                json_data={'users': [
                    {'username': 'alice'},
                    {'username': 'bob'},
                    {'username': 'carol'},
                ]}
            )
            handler(resp)
        self._setup_handler_capture(e, fire_xhr)

        with patch('instat.engines.playwright_engine.human_delay'):
            result = e.extract('target', 'followers', max_duration=0.5)

        self.assertIn('alice', result)
        self.assertIn('bob', result)
        self.assertIn('carol', result)

    def test_handles_malformed_json_gracefully(self):
        e = self._make_engine_with_mocked_page()

        def fire_bad(handler):
            # Response that throws on .json()
            bad = make_response(
                'https://i.instagram.com/api/v1/friendships/x/followers/',
                json_raises=True
            )
            handler(bad)
            # Response with non-dict data
            weird = make_response(
                'https://i.instagram.com/api/v1/friendships/y/followers/',
                json_data="not a dict"
            )
            handler(weird)
            # Response with users not being a list
            also_weird = make_response(
                'https://i.instagram.com/api/v1/friendships/z/followers/',
                json_data={'users': 'not a list'}
            )
            handler(also_weird)
        self._setup_handler_capture(e, fire_bad)

        with patch('instat.engines.playwright_engine.human_delay'):
            result = e.extract('target', 'followers', max_duration=0.3)
        self.assertIsInstance(result, set)

    def test_ignores_non_friendship_urls(self):
        e = self._make_engine_with_mocked_page()

        def fire_unrelated(handler):
            resp = make_response(
                'https://i.instagram.com/api/v1/feed/timeline/',
                json_data={'users': [{'username': 'should_not_appear'}]}
            )
            handler(resp)
        self._setup_handler_capture(e, fire_unrelated)

        with patch('instat.engines.playwright_engine.human_delay'):
            result = e.extract('target', 'followers', max_duration=0.3)
        self.assertNotIn('should_not_appear', result)

    def test_ignores_non_200_status(self):
        e = self._make_engine_with_mocked_page()

        def fire_429(handler):
            resp = make_response(
                'https://i.instagram.com/api/v1/friendships/x/followers/',
                status=429,
                json_data={'users': [{'username': 'rate_limited'}]}
            )
            handler(resp)
        self._setup_handler_capture(e, fire_429)

        with patch('instat.engines.playwright_engine.human_delay'):
            result = e.extract('target', 'followers', max_duration=0.3)
        self.assertNotIn('rate_limited', result)

    def test_merge_xhr_and_dom_results(self):
        e = self._make_engine_with_mocked_page()

        # DOM returns 'dom_user'
        dom_link = MagicMock()
        dom_link.get_attribute.return_value = '/dom_user/'
        e._page.query_selector_all.return_value = [dom_link]

        def fire_xhr(handler):
            resp = make_response(
                'https://i.instagram.com/api/v1/friendships/x/followers/',
                json_data={'users': [{'username': 'xhr_user'}]}
            )
            handler(resp)
        self._setup_handler_capture(e, fire_xhr)

        with patch('instat.engines.playwright_engine.human_delay'):
            result = e.extract(
                'target', 'followers',
                existing_profiles={'cached_user'},
                max_duration=0.5
            )
        self.assertIn('cached_user', result)
        self.assertIn('dom_user', result)
        self.assertIn('xhr_user', result)

    def test_listener_removed_in_finally(self):
        e = self._make_engine_with_mocked_page()
        self._setup_handler_capture(e, fire_callback=None)

        with patch('instat.engines.playwright_engine.human_delay'):
            e.extract('target', 'followers', max_duration=0.2)
        e._page.remove_listener.assert_called()


if __name__ == "__main__":
    unittest.main()
