from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path
import os
import pytest
from django.contrib.auth.models import User

from git_provider.views import GitProviderViewSet, _BlameClone
from service_tokens.models import ServiceToken, ServiceType


def _request(user, query):
    return SimpleNamespace(user=user, query_params=query)


def _accessible_space_queryset(space):
    return SimpleNamespace(get=lambda **kwargs: space)


def _create_service_token(*, user, service_type, base_url, token, username=None):
    service_token = ServiceToken(
        user=user,
        service_type=service_type,
        base_url=base_url,
        encrypted_token='placeholder',
    )
    service_token.set_token(token)
    if username:
        service_token.set_username(username)
    service_token.save()
    return service_token


@pytest.mark.django_db
def test_get_provider_honors_explicit_public_github_base_url(user):
    public_token = ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    ghe_token = ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://ghe.example.com/api/v3',
    )

    with patch('git_provider.views.GitProviderFactory.create_from_service_token', return_value=Mock()) as create_provider:
        GitProviderViewSet()._get_provider(
            _request(user, {'provider': 'github', 'base_url': 'https://github.com'})
        )

    assert create_provider.call_args.args[0] == public_token
    assert create_provider.call_args.args[0] != ghe_token


@pytest.mark.django_db
def test_get_provider_honors_explicit_ghe_base_url_for_github(user):
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

    with patch('git_provider.views.GitProviderFactory.create_from_service_token', return_value=Mock()) as create_provider:
        GitProviderViewSet()._get_provider(
            _request(user, {'provider': 'github', 'base_url': 'https://ghe.example.com'})
        )

    assert create_provider.call_args.args[0] == ghe_token


@pytest.mark.django_db
def test_get_provider_requires_unambiguous_github_token_when_base_url_omitted(user):
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

    with pytest.raises(ValueError, match='Ambiguous service token configuration for provider: github'):
        GitProviderViewSet()._get_provider(_request(user, {'provider': 'github'}))


@pytest.mark.django_db
def test_blame_clone_with_auth_uses_request_user_token_for_canonical_base_url(user):
    other_user = User.objects.create_user(username='other-user')
    _create_service_token(
        user=other_user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='other-token',
    )
    _create_service_token(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='request-user-token',
    )

    space = SimpleNamespace(
        git_repository_url='https://github.com/octo/repo.git',
        git_provider=ServiceType.GITHUB,
        git_base_url='https://github.com',
    )

    auth_url = _BlameClone.with_auth(space, user)

    assert auth_url == 'https://oauth2:request-user-token@github.com/octo/repo.git'


@pytest.mark.django_db
def test_blame_clone_with_auth_fails_closed_without_matching_user_token(user):
    other_user = User.objects.create_user(username='other-user')
    _create_service_token(
        user=other_user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='other-token',
    )
    space = SimpleNamespace(
        git_repository_url='https://github.com/octo/repo.git',
        git_provider=ServiceType.GITHUB,
        git_base_url='https://github.com',
    )

    assert _BlameClone.with_auth(space, user) is None


@pytest.mark.django_db
def test_blame_clone_resolve_https_credentials_uses_saved_bitbucket_server_username(user):
    _create_service_token(
        user=user,
        service_type=ServiceType.BITBUCKET_SERVER,
        base_url='https://bitbucket.example.com',
        token='bb-token',
        username='saved-user',
    )
    space = SimpleNamespace(
        git_repository_url='https://bitbucket.example.com/scm/proj/repo.git',
        git_provider=ServiceType.BITBUCKET_SERVER,
        git_base_url='https://bitbucket.example.com',
    )

    credentials = _BlameClone.resolve_https_credentials(space, user)

    assert credentials is not None
    assert credentials['username'] == 'saved-user'


@pytest.mark.django_db
def test_blame_clone_resolve_https_credentials_fails_closed_without_bitbucket_server_username(user):
    _create_service_token(
        user=user,
        service_type=ServiceType.BITBUCKET_SERVER,
        base_url='https://bitbucket.example.com',
        token='bb-token',
    )
    space = SimpleNamespace(
        git_repository_url='https://bitbucket.example.com/scm/proj/repo.git',
        git_provider=ServiceType.BITBUCKET_SERVER,
        git_base_url='https://bitbucket.example.com',
    )

    assert _BlameClone.resolve_https_credentials(space, user) is None


@pytest.mark.django_db
def test_blame_clone_cache_path_is_bound_to_current_user_token(user, another_user, tmp_path):
    _create_service_token(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='user-token',
    )
    _create_service_token(
        user=another_user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='other-token',
    )
    space = SimpleNamespace(
        id='cache-path-space',
        git_provider=ServiceType.GITHUB,
        git_base_url='https://github.com',
        git_repository_url='https://github.com/octo/repo.git',
    )
    manager = SimpleNamespace(cache_dir=str(tmp_path))

    user_path = _BlameClone.cache_path(manager, space, user)
    other_path = _BlameClone.cache_path(manager, space, another_user)

    assert user_path != other_path


@pytest.mark.django_db
def test_blame_clone_cache_path_changes_when_repository_origin_changes(user, tmp_path):
    _create_service_token(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='user-token',
    )
    manager = SimpleNamespace(cache_dir=str(tmp_path))
    first_space = SimpleNamespace(
        id='cache-path-space',
        git_provider=ServiceType.GITHUB,
        git_base_url='https://github.com',
        git_repository_url='https://github.com/octo/repo.git',
    )
    second_space = SimpleNamespace(
        id='cache-path-space',
        git_provider=ServiceType.GITHUB,
        git_base_url='https://github.com',
        git_repository_url='https://github.com/octo/other-repo.git',
    )

    assert _BlameClone.cache_path(manager, first_space, user) != _BlameClone.cache_path(manager, second_space, user)


@pytest.mark.django_db
def test_blame_from_local_clone_fails_closed_without_current_user_token(user, another_user, tmp_path):
    _create_service_token(
        user=another_user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='other-token',
    )
    space = SimpleNamespace(
        id='blame-no-token-space',
        edit_fork_local_path='',
        edit_fork_ssh_url='',
        git_provider=ServiceType.GITHUB,
        git_base_url='https://github.com',
        git_repository_url='https://github.com/octo/repo.git',
    )
    fake_manager = SimpleNamespace(
        cache_dir=str(tmp_path),
        _get_git_env=lambda: {},
        get_bare_repo_path=lambda _space_id: str(tmp_path / 'missing-edit-fork.git'),
    )

    with patch('git_provider.views.accessible_spaces_for_user', return_value=_accessible_space_queryset(space)), patch(
        'git_provider.worktree_manager.GitWorktreeManager', return_value=fake_manager
    ), patch('subprocess.run') as run_git:
        result = GitProviderViewSet._blame_from_local_clone(
            str(space.id), 'README.md', 'main', user
        )

    assert result is None
    run_git.assert_not_called()


@pytest.mark.django_db
def test_blame_from_local_clone_does_not_serve_stale_cache_after_refresh_failure(user, tmp_path):
    _create_service_token(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='active-token',
    )
    space = SimpleNamespace(
        id='blame-refresh-space',
        edit_fork_local_path='',
        edit_fork_ssh_url='',
        git_provider=ServiceType.GITHUB,
        git_base_url='https://github.com',
        git_repository_url='https://github.com/octo/repo.git',
    )
    fake_manager = SimpleNamespace(
        cache_dir=str(tmp_path),
        _get_git_env=lambda: {},
        get_bare_repo_path=lambda _space_id: str(tmp_path / 'missing-edit-fork.git'),
    )
    blame_path = _BlameClone.cache_path(fake_manager, space, user)
    assert blame_path is not None
    tmp_path.joinpath(f'spaces/{space.id}').mkdir(parents=True, exist_ok=True)
    tmp_path.joinpath(Path(blame_path).relative_to(tmp_path)).mkdir(parents=True)

    with patch('git_provider.views.accessible_spaces_for_user', return_value=_accessible_space_queryset(space)), patch(
        'git_provider.worktree_manager.GitWorktreeManager', return_value=fake_manager
    ), patch(
        'subprocess.run',
        side_effect=Exception('refresh failed'),
    ) as run_git:
        result = GitProviderViewSet._blame_from_local_clone(
            str(space.id), 'README.md', 'main', user
        )

    assert result is None
    assert run_git.call_count == 1


@pytest.mark.django_db
def test_blame_clone_build_git_env_includes_bitbucket_custom_header(user, tmp_path):
    _create_service_token(
        user=user,
        service_type=ServiceType.BITBUCKET_SERVER,
        base_url='https://bitbucket.example.com',
        token='bb-token',
        username='saved-user',
    )
    custom_token = ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.CUSTOM_HEADER,
        base_url='https://bitbucket.example.com',
        header_name='X-ZTA-Token',
        encrypted_token='placeholder',
    )
    custom_token.set_token('zta-token')
    custom_token.save()
    space = SimpleNamespace(
        git_repository_url='https://bitbucket.example.com/scm/proj/repo.git',
        git_provider=ServiceType.BITBUCKET_SERVER,
        git_base_url='https://bitbucket.example.com',
    )
    manager = SimpleNamespace(cache_dir=str(tmp_path), _get_git_env=lambda: {})

    credentials = _BlameClone.resolve_https_credentials(space, user)

    assert credentials is not None
    env, askpass_path = _BlameClone.build_git_env(manager, credentials)
    try:
        assert env['GIT_CONFIG_COUNT'] == '1'
        assert env['GIT_CONFIG_KEY_0'] == 'http.extraHeader'
        assert env['GIT_CONFIG_VALUE_0'] == 'X-ZTA-Token: zta-token'
    finally:
        os.unlink(askpass_path)


@pytest.mark.django_db
def test_blame_from_local_clone_https_clone_race_keeps_winner_cache(user, tmp_path):
    _create_service_token(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='active-token',
    )
    space = SimpleNamespace(
        id='blame-race-space',
        edit_fork_local_path='',
        edit_fork_ssh_url='',
        git_provider=ServiceType.GITHUB,
        git_base_url='https://github.com',
        git_repository_url='https://github.com/octo/repo.git',
    )
    fake_manager = SimpleNamespace(
        cache_dir=str(tmp_path),
        _get_git_env=lambda: {},
        get_bare_repo_path=lambda _space_id: str(tmp_path / 'missing-edit-fork.git'),
    )
    blame_path = _BlameClone.cache_path(fake_manager, space, user)
    assert blame_path is not None
    winner_marker = Path(blame_path) / 'winner.txt'
    porcelain = (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa 1 1 1\n"
        "author Example User\n"
        "author-mail <user@example.com>\n"
        "author-time 1700000000\n"
        "author-tz +0000\n"
        "summary Initial commit\n"
        "filename README.md\n"
        "\tline 1\n"
    )

    def fake_run(args, **kwargs):
        if args[:3] == ['git', 'clone', '--bare']:
            Path(args[-1]).joinpath('temp.txt').write_text('temp', encoding='utf-8')
            winner_marker.parent.mkdir(parents=True, exist_ok=True)
            winner_marker.write_text('winner', encoding='utf-8')
            return Mock(stdout='', returncode=0)
        if args[:3] == ['git', '-C', blame_path]:
            return Mock(stdout=porcelain, returncode=0)
        raise AssertionError(f'unexpected git invocation: {args}')

    with patch('git_provider.views.accessible_spaces_for_user', return_value=_accessible_space_queryset(space)), patch(
        'git_provider.worktree_manager.GitWorktreeManager', return_value=fake_manager
    ), patch('subprocess.run', side_effect=fake_run):
        result = GitProviderViewSet._blame_from_local_clone(
            str(space.id), 'README.md', 'main', user
        )

    assert winner_marker.read_text(encoding='utf-8') == 'winner'
    assert result[0]['commit_sha'] == 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
    leftovers = list((Path(blame_path).parent).glob('.*blame-*.git.*'))
    assert leftovers == []


@pytest.mark.django_db
def test_blame_from_local_clone_denies_inaccessible_space_before_clone_or_cache(user, tmp_path):
    denied_queryset = SimpleNamespace(get=Mock(side_effect=ValueError('forbidden')))
    fake_manager = SimpleNamespace(
        cache_dir=str(tmp_path),
        _get_git_env=lambda: {},
        get_bare_repo_path=lambda _space_id: str(tmp_path / 'missing-edit-fork.git'),
    )

    with patch('git_provider.views.accessible_spaces_for_user', return_value=denied_queryset), patch(
        'git_provider.worktree_manager.GitWorktreeManager', return_value=fake_manager
    ), patch('subprocess.run') as run_git:
        result = GitProviderViewSet._blame_from_local_clone(
            'private-space', 'README.md', 'main', user
        )

    assert result is None
    run_git.assert_not_called()
