import json
from unittest.mock import Mock, patch

import pytest
from rest_framework.test import APIRequestFactory

from enrichment_provider.views import _get_recursive_enrichments, _get_space_enrichments, get_enrichments, stream_enrichments
from wiki.models import FileComment
from service_tokens.models import ServiceToken, ServiceType
from unit_tests.test_helpers import create_test_space


@pytest.mark.django_db
def test_space_enrichments_strip_terminal_git_for_github_repo_ids(user):
    space = create_test_space(
        user,
        slug='github-space',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='octo/repo.git',
        git_default_branch='main',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider):
        response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 200
    provider.list_pull_requests.assert_called_once_with(
        repo_id='octo/repo',
        state='open',
        page=1,
        per_page=1000,
    )


@pytest.mark.django_db
def test_space_enrichments_preserve_bitbucket_repo_ids(user):
    space = create_test_space(
        user,
        slug='bitbucket-space',
        git_provider='bitbucket_server',
        git_base_url='https://bitbucket.example.com',
        git_project_key='PRJ',
        git_repository_id='repo.git',
        git_default_branch='main',
    )
    token = ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.BITBUCKET_SERVER,
        base_url='https://bitbucket.example.com',
    )
    token.set_username('bb-user')
    token.set_token('bb-token')
    token.save()

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider):
        response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 200
    provider.list_pull_requests.assert_called_once_with(
        repo_id='PRJ_repo.git',
        state='open',
        page=1,
        per_page=1000,
    )


@pytest.mark.django_db
def test_space_enrichments_read_legacy_github_comment_prefixes(user):
    space = create_test_space(
        user,
        slug='github-legacy-comments',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='octo/repo',
        git_default_branch='main',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    FileComment.objects.create(
        source_uri='git://github/octo_repo/main/docs/readme.md',
        line_start=1,
        line_end=1,
        text='Legacy comment',
        author=user,
    )

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider):
        response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 200
    assert 'docs/readme.md' in response.data
    assert response.data['docs/readme.md']['comments'][0]['text'] == 'Legacy comment'


@pytest.mark.django_db
def test_space_enrichments_match_canonical_github_comments_by_semantic_branch(user):
    space = create_test_space(
        user,
        slug='github-slash-branch-comments',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='octo/repo',
        git_default_branch='feature/new-ui',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    matching_comment = FileComment.objects.create(
        source_uri='git://github/octo/repo/docs/readme.md?ref=feature%2Fnew-ui',
        line_start=3,
        line_end=3,
        text='Slash branch comment',
        author=user,
    )
    FileComment.objects.create(
        source_uri='git://github/octo/repo/docs/readme.md?ref=feature%2Fanother-branch',
        line_start=4,
        line_end=4,
        text='Wrong branch comment',
        author=user,
    )

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider):
        response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 200
    comments = response.data['docs/readme.md']['comments']
    assert len(comments) == 1
    assert comments[0]['id'] == str(matching_comment.id)
    assert comments[0]['source_uri'] == matching_comment.source_uri
    assert comments[0]['text'] == 'Slash branch comment'


@pytest.mark.django_db
def test_space_enrichments_private_space_denies_unrelated_user(user, another_user):
    another_user.userprofile.role = 'commenter'
    another_user.userprofile.save()
    space = create_test_space(
        user,
        slug='private-enrichments',
        visibility='private',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='octo/repo',
        git_default_branch='main',
    )

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = another_user

    response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 404
    assert response.data == {'error': 'Space not found'}


@pytest.mark.django_db
def test_recursive_enrichments_supports_canonical_github_owner_repo_source_uri(user):
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.get_directory_tree.return_value = [{'path': 'docs/readme.md', 'type': 'file'}]

    registry = Mock()
    registry.get_enrichments_by_type.return_value = [{'id': 'c1'}]

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider), patch(
        'enrichment_provider.views.get_registry',
        return_value=registry,
    ):
        response = _get_recursive_enrichments(
            request,
            'git://github/octo/repo/docs?ref=main',
            'comments',
            start_time=0.0,
        )

    assert response.status_code == 200
    provider.get_directory_tree.assert_called_once_with('octo', 'repo', 'docs', 'main', recursive=True)
    registry.get_enrichments_by_type.assert_called_once_with(
        'git://github/octo/repo/docs/readme.md?ref=main',
        user,
        'comments',
    )


@pytest.mark.django_db
def test_recursive_enrichments_supports_slash_branch_and_repo_root(user):
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.get_directory_tree.return_value = [{'path': 'docs/readme.md', 'type': 'file'}]

    registry = Mock()
    registry.get_all_enrichments.return_value = {'comments': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider), patch(
        'enrichment_provider.views.get_registry',
        return_value=registry,
    ):
        response = _get_recursive_enrichments(
            request,
            'git://github/octo/repo?ref=feature%2Fnew-ui',
            None,
            start_time=0.0,
        )

    assert response.status_code == 200
    provider.get_directory_tree.assert_called_once_with('octo', 'repo', '', 'feature/new-ui', recursive=True)
    registry.get_all_enrichments.assert_called_once_with(
        'git://github/octo/repo/docs/readme.md?ref=feature%2Fnew-ui',
        user,
    )


@pytest.mark.django_db
def test_get_enrichments_returns_400_for_missing_github_token_with_actionable_message(user):
    request = APIRequestFactory().get(
        '/api/enrichments/v1/enrichments/',
        {'source_uri': 'git://github/octo/repo/docs/readme.md?ref=main'},
    )
    request.user = user

    response = get_enrichments(request)

    assert response.status_code == 400
    assert response.data == {
        'error': (
            'No service token found for provider: github. '
            'Add a matching service token or include the correct base_url in the source URI.'
        )
    }


@pytest.mark.django_db
def test_stream_enrichments_surfaces_missing_github_token_and_does_not_complete(user):
    request = APIRequestFactory().get(
        '/api/enrichments/v1/enrichments/stream/',
        {'source_uri': 'git://github/octo/repo/docs/readme.md?ref=main'},
    )
    request.user = user

    response = stream_enrichments(request)
    events = [json.loads(chunk) for chunk in response.streaming_content]

    assert events == [
        {
            'type': 'error',
            'message': (
                'No service token found for provider: github. '
                'Add a matching service token or include the correct base_url in the source URI.'
            ),
        }
    ]


@pytest.mark.django_db
def test_recursive_enrichments_only_returns_requested_subtree_files(user):
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.get_directory_tree.return_value = [
        {'path': 'docs/readme.md', 'type': 'file'},
        {'path': 'docs/guide.md', 'type': 'file'},
    ]

    registry = Mock()
    registry.get_all_enrichments.side_effect = lambda source_uri, *_: {'source_uri': source_uri}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider), patch(
        'enrichment_provider.views.get_registry',
        return_value=registry,
    ):
        response = _get_recursive_enrichments(
            request,
            'git://github/octo/repo/docs?ref=main',
            None,
            start_time=0.0,
        )

    assert response.status_code == 200
    assert set(response.data) == {
        'git://github/octo/repo/docs/readme.md?ref=main',
        'git://github/octo/repo/docs/guide.md?ref=main',
    }


@pytest.mark.django_db
def test_recursive_enrichments_fail_closed_when_provider_tokens_are_ambiguous(user):
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
    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    response = _get_recursive_enrichments(
        request,
        'git://github/octo/repo/docs?ref=main',
        'comments',
        start_time=0.0,
    )

    assert response.status_code == 400
    assert response.data == {'error': 'Ambiguous service token configuration for provider: github'}


@pytest.mark.django_db
def test_file_enrichments_match_persisted_old_canonical_github_comments_for_anybranch(user):
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    FileComment.objects.create(
        source_uri='git://github/octo/repo/release2026/docs/readme.md',
        line_start=7,
        line_end=7,
        text='Persisted old canonical comment',
        author=user,
    )
    request = APIRequestFactory().get(
        '/api/enrichments/v1/enrichments/',
        {'source_uri': 'git://github/octo/repo/docs/readme.md?ref=release2026', 'type': 'comments'},
    )
    request.user = user

    response = get_enrichments(request)

    assert response.status_code == 200
    comment = response.data['comments'][0]
    assert comment['source_uri'] == 'git://github/octo/repo/docs/readme.md?ref=release2026'
    assert comment['line_start'] == 7
    assert comment['line_end'] == 7
    assert comment['text'] == 'Persisted old canonical comment'
    assert comment['author'] == user.username
    assert comment['parent_id'] is None
    assert comment['is_resolved'] is False
    assert comment['anchoring_status'] == 'anchored'
    assert comment['replies'] == []


@pytest.mark.django_db
def test_file_enrichments_return_400_for_ambiguous_pr_token_configuration(user):
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
        '/api/enrichments/v1/enrichments/',
        {'source_uri': 'git://github/octo/repo/docs/readme.md?ref=main', 'type': 'pr_diff'},
    )
    request.user = user

    response = get_enrichments(request)

    assert response.status_code == 400
    assert response.data == {'error': 'Ambiguous service token configuration for provider: github'}


@pytest.mark.django_db
def test_file_enrichments_all_return_400_for_ambiguous_pr_token_configuration(user):
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
        '/api/enrichments/v1/enrichments/',
        {'source_uri': 'git://github/octo/repo/docs/readme.md?ref=main'},
    )
    request.user = user

    response = get_enrichments(request)

    assert response.status_code == 400
    assert response.data == {'error': 'Ambiguous service token configuration for provider: github'}


@pytest.mark.django_db
def test_recursive_enrichments_uses_explicit_ghe_base_url_context(user):
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
    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.get_directory_tree.return_value = [{'path': 'docs/readme.md', 'type': 'file'}]

    registry = Mock()
    registry.get_all_enrichments.return_value = {'comments': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider) as create_provider, patch(
        'enrichment_provider.views.get_registry',
        return_value=registry,
    ):
        response = _get_recursive_enrichments(
            request,
            'git://github/octo/repo/docs?ref=main&base_url=https%3A%2F%2Fghe.example.com',
            None,
            start_time=0.0,
        )

    assert response.status_code == 200
    assert create_provider.call_args.args[0] == ghe_token
    registry.get_all_enrichments.assert_called_once_with(
        'git://github/octo/repo/docs/readme.md?ref=main&base_url=https%3A%2F%2Fghe.example.com',
        user,
    )


@pytest.mark.django_db
def test_space_enrichments_filter_comments_by_explicit_github_base_url(user):
    space = create_test_space(
        user,
        slug='github-base-url-comments',
        git_provider='github',
        git_base_url='https://ghe.example.com',
        git_repository_id='octo/repo',
        git_default_branch='main',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://ghe.example.com/api/v3',
    )
    matching_comment = FileComment.objects.create(
        source_uri='git://github/octo/repo/docs/readme.md?ref=main&base_url=https%3A%2F%2Fghe.example.com',
        line_start=1,
        line_end=1,
        text='Matching GHE comment',
        author=user,
    )
    FileComment.objects.create(
        source_uri='git://github/octo/repo/docs/readme.md?ref=main&base_url=https%3A%2F%2Fgithub.com',
        line_start=2,
        line_end=2,
        text='Wrong origin comment',
        author=user,
    )

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider):
        response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 200
    comments = response.data['docs/readme.md']['comments']
    assert len(comments) == 1
    assert comments[0]['id'] == str(matching_comment.id)


@pytest.mark.django_db
def test_space_enrichments_match_github_comments_with_terminal_dot_git_repo_ids(user):
    space = create_test_space(
        user,
        slug='github-dot-git-comments',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='octo/repo',
        git_default_branch='main',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    FileComment.objects.create(
        source_uri='git://github/octo/repo.git/docs/readme.md?ref=main',
        line_start=1,
        line_end=1,
        text='Dot git comment',
        author=user,
    )

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider):
        response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 200
    assert response.data['docs/readme.md']['comments'][0]['text'] == 'Dot git comment'


@pytest.mark.django_db
def test_space_enrichments_match_pre_query_canonical_github_comment_uris(user):
    space = create_test_space(
        user,
        slug='github-pre-query-comments',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='octo/repo',
        git_default_branch='main',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )
    FileComment.objects.create(
        source_uri='git://github/octo/repo/main/docs/readme.md',
        line_start=1,
        line_end=1,
        text='Pre-query canonical comment',
        author=user,
    )

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider):
        response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 200
    assert response.data['docs/readme.md']['comments'][0]['text'] == 'Pre-query canonical comment'


@pytest.mark.django_db
def test_space_enrichments_return_actionable_400_for_ambiguous_github_tokens(user):
    space = create_test_space(
        user,
        slug='github-ambiguous-space',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='octo/repo',
        git_default_branch='main',
    )
    ServiceToken.objects.bulk_create([
        ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com',
            encrypted_token='root-encrypted',
        ),
        ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://api.github.com',
            encrypted_token='api-encrypted',
        ),
    ])

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 400
    assert response.data == {
        'error': (
            'Multiple GitHub tokens match this space. Remove duplicate GitHub tokens '
            'or set the space Git base URL to the intended GitHub host.'
        ),
    }


@pytest.mark.django_db
def test_space_enrichments_github_blank_default_branch_falls_back_to_main(user):
    space = create_test_space(
        user,
        slug='github-blank-branch',
        git_provider='github',
        git_base_url='https://github.com',
        git_repository_id='octo/repo',
        git_default_branch='',
    )
    ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )

    request = APIRequestFactory().get('/api/enrichments/v1/enrichments/')
    request.user = user

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.views.GitProviderFactory.create_from_service_token', return_value=provider):
        response = _get_space_enrichments(request, space.slug, start_time=0.0)

    assert response.status_code == 200
    provider.list_pull_requests.assert_called_once_with(
        repo_id='octo/repo',
        state='open',
        page=1,
        per_page=1000,
    )
