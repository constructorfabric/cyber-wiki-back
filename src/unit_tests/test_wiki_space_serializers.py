from unittest.mock import Mock, patch

import pytest
from rest_framework.test import APIRequestFactory

from git_provider.factory import GitProviderFactory
from wiki.serializers import EditSessionCreateSerializer, SpaceDetailSerializer
from unit_tests.test_helpers import create_test_space


def _request_with_user(user):
    request = APIRequestFactory().post('/api/spaces/')
    request.user = user
    return request


@pytest.mark.django_db
def test_space_create_autodetects_github_default_branch(user):
    provider = Mock()
    provider.get_repository.return_value = {'default_branch': 'trunk'}

    filter_result = Mock()
    filter_result.first.return_value = object()

    serializer = SpaceDetailSerializer(
        data={
            'slug': 'gh-space',
            'name': 'GitHub Space',
            'git_provider': 'github',
            'git_repository_url': 'https://github.com/octo/repo.git',
        },
        context={'request': _request_with_user(user)},
    )

    with patch('service_tokens.models.ServiceToken.objects.filter', return_value=filter_result), patch(
        'git_provider.factory.GitProviderFactory.create_from_service_token',
        return_value=provider,
    ):
        assert serializer.is_valid(), serializer.errors
        space = serializer.save(owner=user, created_by=user)

    assert space.git_repository_id == 'octo/repo'
    assert space.git_repository_name == 'repo'
    assert space.git_default_branch == 'trunk'
    provider.get_repository.assert_called_once_with('octo/repo')


@pytest.mark.django_db
def test_space_update_autodetects_github_default_branch(user):
    space = create_test_space(
        user,
        slug='existing-space',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='legacy/repo',
        git_default_branch='',
    )
    provider = Mock()
    provider.get_repository.return_value = {'default_branch': 'develop'}

    filter_result = Mock()
    filter_result.first.return_value = object()

    serializer = SpaceDetailSerializer(
        space,
        data={
            'git_repository_url': 'https://github.com/octo/repo.git',
            'git_default_branch': '',
        },
        partial=True,
        context={'request': _request_with_user(user)},
    )

    with patch('service_tokens.models.ServiceToken.objects.filter', return_value=filter_result), patch(
        'git_provider.factory.GitProviderFactory.create_from_service_token',
        return_value=provider,
    ):
        assert serializer.is_valid(), serializer.errors
        updated_space = serializer.save()

    assert updated_space.git_repository_id == 'octo/repo'
    assert updated_space.git_default_branch == 'develop'
    provider.get_repository.assert_called_once_with('octo/repo')


def test_detect_default_branch_does_not_silently_fallback_to_master_for_github(user):
    serializer = SpaceDetailSerializer(context={'request': _request_with_user(user)})

    filter_result = Mock()
    filter_result.first.return_value = object()
    provider = Mock()
    provider.get_repository.side_effect = RuntimeError('boom')

    with patch('service_tokens.models.ServiceToken.objects.filter', return_value=filter_result), patch(
        'git_provider.factory.GitProviderFactory.create_from_service_token',
        return_value=provider,
    ):
        detected = serializer._detect_default_branch(
            provider='github',
            base_url='https://github.com',
            project_key=None,
            repo_id='octo/repo',
        )

    assert detected == 'main'


def test_parse_and_verify_git_url_uses_github_provider_parser_for_ssh_urls():
    serializer = SpaceDetailSerializer()

    parsed = serializer._parse_and_verify_git_url(
        'git@github.com:octo/repo.git',
        'github',
    )

    assert parsed == {
        'git_base_url': 'https://api.github.com',
        'git_project_key': None,
        'git_repository_id': 'octo/repo',
        'git_repository_name': 'repo',
    }


def test_detect_default_branch_keeps_bitbucket_master_fallback(user):
    serializer = SpaceDetailSerializer(context={'request': _request_with_user(user)})

    filter_result = Mock()
    filter_result.first.return_value = object()
    provider = Mock()
    provider.get_repository.return_value = {}
    provider.list_branches.return_value = []

    with patch('service_tokens.models.ServiceToken.objects.filter', return_value=filter_result), patch(
        'git_provider.factory.GitProviderFactory.create_from_service_token',
        return_value=provider,
    ):
        detected = serializer._detect_default_branch(
            provider='bitbucket_server',
            base_url='https://bitbucket.example.com',
            project_key='PRJ',
            repo_id='repo',
        )

    assert detected == 'master'


@pytest.mark.django_db
def test_edit_session_create_uses_provider_aware_branch_fallback_for_blank_github_default(user):
    space = create_test_space(
        user,
        slug='edit-session-github',
        git_provider='github',
        git_default_branch='',
    )
    serializer = EditSessionCreateSerializer(
        data={'title': 'Review docs'},
        context={'space': space},
    )

    assert serializer.is_valid(), serializer.errors
    session = serializer.save(user=user, space=space)

    assert session.base_branch == GitProviderFactory.default_branch_fallback('github')
