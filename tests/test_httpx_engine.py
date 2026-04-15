import sys
import unittest
from unittest.mock import MagicMock, patch

from instat.engines.base import BaseEngine
from instat.engines.httpx_engine import HttpxEngine
from instat.exceptions import BlockedError, ProfileNotFoundError, RateLimitError


def make_response(status_code=200, json_body=None, json_raises=False):
    r = MagicMock()
    r.status_code = status_code
    if json_raises or json_body is None:
        r.json.side_effect = Exception("no json")
    else:
        r.json.return_value = json_body
    return r


class TestHttpxEngineBasics(unittest.TestCase):

    def test_implements_base_engine(self):
        self.assertTrue(issubclass(HttpxEngine, BaseEngine))

    def test_name_is_httpx(self):
        self.assertEqual(HttpxEngine().name, 'httpx')

    def test_is_available_false_without_httpx(self):
        original = sys.modules.get('httpx')
        sys.modules['httpx'] = None
        try:
            e = HttpxEngine()
            self.assertFalse(e.is_available)
        finally:
            if original is None:
                sys.modules.pop('httpx', None)
            else:
                sys.modules['httpx'] = original

    def test_is_available_true_when_httpx_present(self):
        mock_httpx = MagicMock()
        with patch.dict(sys.modules, {'httpx': mock_httpx}):
            self.assertTrue(HttpxEngine().is_available)

    def test_quit_closes_client(self):
        e = HttpxEngine()
        e._client = MagicMock()
        e.quit()
        e._client.close.assert_called_once()

    def test_quit_no_client_is_safe(self):
        e = HttpxEngine()
        e._client = None
        e.quit()

    def test_extract_invalid_list_type_raises(self):
        e = HttpxEngine()
        e._client = MagicMock()
        with self.assertRaises(ValueError):
            e.extract('p', 'bogus')


class TestHttpxEngineLogin(unittest.TestCase):

    def _setup_client_mock(self, get_responses=None, post_response=None,
                           cookies=None):
        client = MagicMock()
        if get_responses is not None:
            client.get.side_effect = get_responses
        if post_response is not None:
            client.post.return_value = post_response
        cookies_obj = MagicMock()
        cookies_obj.get.side_effect = (cookies or {}).get
        cookies_obj.clear = MagicMock()
        cookies_obj.jar = []
        cookies_obj.set = MagicMock()
        client.cookies = cookies_obj
        return client

    def test_login_extracts_session_cookies(self):
        e = HttpxEngine()
        client_mock = self._setup_client_mock(
            get_responses=[make_response(200)],
            post_response=make_response(200, {
                'authenticated': True,
                'user': True,
                'userId': '123',
                'status': 'ok'
            }),
            cookies={'csrftoken': 'abc123', 'sessionid': 'sid456'},
        )
        with patch.object(e, '_build_client', return_value=client_mock):
            e._session_cache = MagicMock()
            e._session_cache.load.return_value = None
            result = e.login('user', 'pass')
        self.assertTrue(result)
        self.assertEqual(e._sessionid, 'sid456')
        self.assertEqual(e._csrftoken, 'abc123')

    def test_login_checkpoint_raises_blocked(self):
        e = HttpxEngine()
        client_mock = self._setup_client_mock(
            get_responses=[make_response(200)],
            post_response=make_response(400, {
                'message': 'checkpoint_required'
            }),
            cookies={'csrftoken': 'abc123'},
        )
        with patch.object(e, '_build_client', return_value=client_mock):
            e._session_cache = MagicMock()
            e._session_cache.load.return_value = None
            with self.assertRaises(BlockedError):
                e.login('user', 'pass')

    def test_login_429_raises_rate_limit(self):
        e = HttpxEngine()
        client_mock = self._setup_client_mock(
            get_responses=[make_response(200)],
            post_response=make_response(429),
            cookies={'csrftoken': 'abc'},
        )
        with patch.object(e, '_build_client', return_value=client_mock):
            e._session_cache = MagicMock()
            e._session_cache.load.return_value = None
            with self.assertRaises(RateLimitError):
                e.login('user', 'pass')

    def test_login_2fa_raises_blocked(self):
        e = HttpxEngine()
        client_mock = self._setup_client_mock(
            get_responses=[make_response(200)],
            post_response=make_response(200, {
                'authenticated': False,
                'two_factor_required': True
            }),
            cookies={'csrftoken': 'abc'},
        )
        with patch.object(e, '_build_client', return_value=client_mock):
            e._session_cache = MagicMock()
            e._session_cache.load.return_value = None
            with self.assertRaises(BlockedError):
                e.login('user', 'pass')


class TestHttpxEngineResolveUserId(unittest.TestCase):

    def test_resolve_user_id_success(self):
        e = HttpxEngine()
        e._client = MagicMock()
        e._client.get.return_value = make_response(200, {
            'data': {'user': {'id': '99999', 'username': 'u'}}
        })
        self.assertEqual(e._resolve_user_id('u'), '99999')

    def test_resolve_user_id_404_raises_not_found(self):
        e = HttpxEngine()
        e._client = MagicMock()
        e._client.get.return_value = make_response(404)
        with self.assertRaises(ProfileNotFoundError):
            e._resolve_user_id('u')

    def test_resolve_user_id_429_raises_rate_limit(self):
        e = HttpxEngine()
        e._client = MagicMock()
        e._client.get.return_value = make_response(429)
        with self.assertRaises(RateLimitError):
            e._resolve_user_id('u')

    def test_resolve_user_id_not_logged_in_raises(self):
        e = HttpxEngine()
        e._client = None
        with self.assertRaises(BlockedError):
            e._resolve_user_id('u')


class TestHttpxEngineExtract(unittest.TestCase):

    def test_extract_paginates_with_max_id(self):
        e = HttpxEngine()
        e._client = MagicMock()
        page1 = make_response(200, {
            'users': [{'username': 'u1'}, {'username': 'u2'}],
            'next_max_id': 'cursor1'
        })
        page2 = make_response(200, {
            'users': [{'username': 'u3'}],
        })
        resolve_resp = make_response(200, {
            'data': {'user': {'id': '555'}}
        })
        e._client.get.side_effect = [resolve_resp, page1, page2]

        with patch('instat.engines.httpx_engine.human_delay'):
            result = e.extract('target', 'followers')

        self.assertEqual(result, {'u1', 'u2', 'u3'})
        # 2nd pagination call carries max_id=cursor1
        second_call = e._client.get.call_args_list[2]
        self.assertEqual(second_call.kwargs.get('params', {}).get('max_id'), 'cursor1')

    def test_extract_rate_limit_429_raises(self):
        e = HttpxEngine()
        e._client = MagicMock()
        resolve_resp = make_response(200, {'data': {'user': {'id': '1'}}})
        e._client.get.side_effect = [resolve_resp, make_response(429)]
        with self.assertRaises(RateLimitError):
            e.extract('target', 'followers')

    def test_extract_blocked_400_raises(self):
        e = HttpxEngine()
        e._client = MagicMock()
        resolve_resp = make_response(200, {'data': {'user': {'id': '1'}}})
        e._client.get.side_effect = [resolve_resp, make_response(400)]
        with self.assertRaises(BlockedError):
            e.extract('target', 'followers')

    def test_extract_on_batch_is_called(self):
        e = HttpxEngine()
        e._client = MagicMock()
        resolve_resp = make_response(200, {'data': {'user': {'id': '1'}}})
        page = make_response(200, {'users': [{'username': 'x'}]})
        e._client.get.side_effect = [resolve_resp, page]
        batches = []
        with patch('instat.engines.httpx_engine.human_delay'):
            e.extract('t', 'followers', on_batch=lambda b: batches.append(set(b)))
        self.assertEqual(batches, [{'x'}])

    def test_extract_respects_existing_profiles(self):
        e = HttpxEngine()
        e._client = MagicMock()
        resolve_resp = make_response(200, {'data': {'user': {'id': '1'}}})
        page = make_response(200, {'users': [{'username': 'new'}]})
        e._client.get.side_effect = [resolve_resp, page]
        with patch('instat.engines.httpx_engine.human_delay'):
            result = e.extract('t', 'followers',
                               existing_profiles={'cached'})
        self.assertEqual(result, {'cached', 'new'})


class TestHttpxEngineTotalCount(unittest.TestCase):

    def test_get_total_count_followers(self):
        e = HttpxEngine()
        e._client = MagicMock()
        e._client.get.return_value = make_response(200, {
            'data': {'user': {
                'edge_followed_by': {'count': 1234},
                'edge_follow': {'count': 567}
            }}
        })
        self.assertEqual(e.get_total_count('u', 'followers'), 1234)

    def test_get_total_count_following(self):
        e = HttpxEngine()
        e._client = MagicMock()
        e._client.get.return_value = make_response(200, {
            'data': {'user': {
                'edge_followed_by': {'count': 1234},
                'edge_follow': {'count': 567}
            }}
        })
        self.assertEqual(e.get_total_count('u', 'following'), 567)

    def test_get_total_count_invalid_type_returns_none(self):
        e = HttpxEngine()
        e._client = MagicMock()
        self.assertIsNone(e.get_total_count('u', 'invalid'))

    def test_get_total_count_no_client_returns_none(self):
        e = HttpxEngine()
        e._client = None
        self.assertIsNone(e.get_total_count('u', 'followers'))


if __name__ == "__main__":
    unittest.main()
