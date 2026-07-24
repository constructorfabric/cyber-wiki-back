from unittest.mock import Mock, patch

import pytest
from rest_framework.test import APIRequestFactory

from service_tokens.models import ServiceToken, ServiceType
from source_provider.views import get_content, get_tree


@pytest.mark.django_db
def test_get_content_supports_canonical_github_owner_repo_source_uri(user):
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    request = APIRequestFactory().get(
        '/api/source/v1/content/',
        {'uri': 'git://github/octo/repo/docs/readme.md?ref=main'},
    )
    request.user = user

    provider = Mock()
    provider.get_file_content.return_value = {'content': 'hello', 'encoding': 'utf-8'}

    with patch('source_provider.git_source.GitProviderFactory.create_from_service_token', return_value=provider):
        response = get_content(request)

    assert response.status_code == 200
    assert response.data['content'] == 'hello'
    assert response.data['source_uri'] == 'git://github/octo/repo/docs/readme.md?ref=main'
    provider.get_file_content.assert_called_once_with(
        project_key='octo',
        repo_slug='repo',
        file_path='docs/readme.md',
        branch='main',
    )


@pytest.mark.django_db
def test_get_tree_supports_github_slash_branch_source_uri(user):
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    request = APIRequestFactory().get(
        '/api/source/v1/tree/',
        {
            'uri': 'git://github/octo/repo/docs?ref=feature%2Fnew-ui',
            'recursive': 'true',
        },
    )
    request.user = user

    provider = Mock()
    provider.get_directory_tree.return_value = [{'path': 'docs/readme.md', 'type': 'file'}]

    with patch('source_provider.git_source.GitProviderFactory.create_from_service_token', return_value=provider):
        response = get_tree(request)

    assert response.status_code == 200
    provider.get_directory_tree.assert_called_once_with(
        project_key='octo',
        repo_slug='repo',
        path='docs',
        branch='feature/new-ui',
        recursive=True,
    )
    assert response.data[0]['source_uri'] == 'git://github/octo/repo/docs/readme.md?ref=feature%2Fnew-ui'


@pytest.mark.django_db
def test_get_content_fails_closed_when_github_source_tokens_are_ambiguous(user):
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://ghe.example.com/api/v3',
    )
    request = APIRequestFactory().get(
        '/api/source/v1/content/',
        {'uri': 'git://github/octo/repo/docs/readme.md?ref=main'},
    )
    request.user = user

    response = get_content(request)

    assert response.status_code == 400
    assert response.data == {
        'error': 'Ambiguous service token configuration for provider: github',
        'code': 'INVALID_URI',
    }


@pytest.mark.django_db
def test_get_content_uses_explicit_ghe_base_url_context(user):
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    ghe_token = ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://ghe.example.com/api/v3',
    )
    request = APIRequestFactory().get(
        '/api/source/v1/content/',
        {'uri': 'git://github/octo/repo/docs/readme.md?ref=main&base_url=https%3A%2F%2Fghe.example.com'},
    )
    request.user = user

    provider = Mock()
    provider.get_file_content.return_value = {'content': 'hello', 'encoding': 'utf-8'}

    with patch('source_provider.git_source.GitProviderFactory.create_from_service_token', return_value=provider) as create_provider:
        response = get_content(request)

    assert response.status_code == 200
    assert create_provider.call_args.args[0] == ghe_token


@pytest.mark.django_db
def test_get_tree_preserves_explicit_public_github_base_url_context(user):
    public_token = ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://ghe.example.com/api/v3',
    )
    request = APIRequestFactory().get(
        '/api/source/v1/tree/',
        {
            'uri': 'git://github/octo/repo/docs?ref=feature%2Fnew-ui&base_url=https%3A%2F%2Fgithub.com',
            'recursive': 'true',
        },
    )
    request.user = user

    provider = Mock()
    provider.get_directory_tree.return_value = [{'path': 'docs/readme.md', 'type': 'file'}]

    with patch('source_provider.git_source.GitProviderFactory.create_from_service_token', return_value=provider) as create_provider:
        response = get_tree(request)

    assert response.status_code == 200
    assert create_provider.call_args.args[0] == public_token
    assert response.data[0]['source_uri'] == (
        'git://github/octo/repo/docs/readme.md'
        '?ref=feature%2Fnew-ui&base_url=https%3A%2F%2Fgithub.com'
    )
