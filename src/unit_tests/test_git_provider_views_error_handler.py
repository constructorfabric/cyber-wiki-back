"""
Unit tests for GitProviderViewSet._handle_provider_error.

Verifies the static helper that maps upstream HTTPError responses to DRF
Response objects with appropriate status codes (replaces 3 duplicated try
blocks in views.py).
"""
from unittest.mock import Mock

import pytest
import requests
from rest_framework import status

from git_provider.views import GitProviderViewSet


def _http_error(code: int, url: str = 'https://upstream.example/x') -> requests.exceptions.HTTPError:
    """Build a requests.HTTPError carrying a response with the given code."""
    response = Mock(spec=requests.Response)
    response.status_code = code
    response.url = url
    err = requests.exceptions.HTTPError(f'{code} Client Error', response=response)
    return err


class TestHandleProviderError:
    def test_401_returns_bad_gateway(self):
        resp = GitProviderViewSet._handle_provider_error(_http_error(401), 'op')
        assert resp.status_code == status.HTTP_502_BAD_GATEWAY
        assert resp.data['code'] == 'GIT_PROVIDER_AUTH_FAILED'

    def test_403_returns_forbidden(self):
        resp = GitProviderViewSet._handle_provider_error(_http_error(403), 'op')
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert resp.data['code'] == 'FORBIDDEN'

    def test_404_returns_not_found_with_url(self):
        resp = GitProviderViewSet._handle_provider_error(
            _http_error(404, 'https://git/host/file?at=master'), 'op'
        )
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert resp.data['code'] == 'NOT_FOUND'
        assert 'master' in resp.data['detail']

    def test_other_http_status_falls_through_to_500(self):
        # 500 from upstream is propagated as 500 (not mapped specially).
        resp = GitProviderViewSet._handle_provider_error(_http_error(500), 'op')
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert resp.data['code'] == 'INTERNAL_ERROR'

    def test_non_http_exception_returns_500(self):
        resp = GitProviderViewSet._handle_provider_error(RuntimeError('boom'), 'op')
        assert resp.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
        assert resp.data['code'] == 'INTERNAL_ERROR'
        assert 'boom' in resp.data['detail']
