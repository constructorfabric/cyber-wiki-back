from unittest.mock import Mock, patch

import pytest

from enrichment_provider.pr_enrichment import PREnrichmentProvider
from service_tokens.models import ServiceToken, ServiceType


def _create_service_token(*, user, service_type, base_url, token):
    service_token = ServiceToken(
        user=user,
        service_type=service_type,
        base_url=base_url,
        encrypted_token='placeholder',
    )
    service_token.set_token(token)
    service_token.save()
    return service_token


@pytest.mark.django_db
def test_pr_enrichment_fails_closed_when_github_source_tokens_are_ambiguous(user):
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
        PREnrichmentProvider().get_enrichments(
            'git://github/octo/repo/docs/readme.md?ref=main',
            user,
        )


@pytest.mark.django_db
def test_pr_enrichment_uses_public_github_token_for_source_uris_when_unambiguous(user):
    public_token = ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
    )

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.pr_enrichment.GitProviderFactory.create_from_service_token', return_value=provider) as create_provider:
        enrichments = PREnrichmentProvider().get_enrichments(
            'git://github/octo/repo/docs/readme.md?ref=main',
            user,
        )

    assert enrichments == []
    assert create_provider.call_args.args[0] == public_token


@pytest.mark.django_db
def test_pr_enrichment_uses_ghe_token_for_source_uris_when_unambiguous(user):
    ghe_token = ServiceToken.objects.create(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://ghe.example.com/api/v3',
    )

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.pr_enrichment.GitProviderFactory.create_from_service_token', return_value=provider) as create_provider:
        enrichments = PREnrichmentProvider().get_enrichments(
            'git://github/octo/repo/docs/readme.md?ref=main&base_url=https%3A%2F%2Fghe.example.com',
            user,
        )

    assert enrichments == []
    assert create_provider.call_args.args[0] == ghe_token


@pytest.mark.django_db
def test_pr_enrichment_uses_explicit_public_github_base_url_context(user):
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

    provider = Mock()
    provider.list_pull_requests.return_value = {'pull_requests': []}

    with patch('enrichment_provider.pr_enrichment.GitProviderFactory.create_from_service_token', return_value=provider) as create_provider:
        enrichments = PREnrichmentProvider().get_enrichments(
            'git://github/octo/repo/docs/readme.md?ref=main&base_url=https%3A%2F%2Fgithub.com',
            user,
        )

    assert enrichments == []
    assert create_provider.call_args.args[0] == public_token


@pytest.mark.django_db
def test_pr_enrichment_normalizes_legacy_github_owner_repo_before_provider_calls(user):
    _create_service_token(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='ghp-token',
    )
    provider = Mock()
    provider.list_pull_requests.return_value = {
        'pull_requests': [
            {
                'number': 7,
                'title': 'Update docs',
                'author': 'octocat',
                'state': 'open',
                'url': 'https://github.com/octo/repo/pull/7',
                'created_at': '2026-07-23T00:00:00Z',
                'reviewers': [],
            }
        ]
    }
    provider.get_pull_request_diff.return_value = '\n'.join([
        'diff --git a/docs/readme.md b/docs/readme.md',
        '--- a/docs/readme.md',
        '+++ b/docs/readme.md',
        '@@ -1 +1 @@',
        '-old line',
        '+new line',
    ])

    with patch('enrichment_provider.pr_enrichment.GitProviderFactory.create_from_service_token', return_value=provider):
        enrichments = PREnrichmentProvider().get_enrichments(
            'git://github/octo_repo/main/docs/readme.md',
            user,
        )

    assert len(enrichments) == 1
    provider.list_pull_requests.assert_called_once_with(
        repo_id='octo/repo',
        state='open',
        page=1,
        per_page=1000,
    )
    provider.get_pull_request_diff.assert_called_once_with(
        repo_id='octo/repo',
        pr_number=7,
    )


@pytest.mark.django_db
def test_pr_enrichment_stream_returns_matching_pr_diff_for_unambiguous_github_token(user):
    _create_service_token(
        user=user,
        service_type=ServiceType.GITHUB,
        base_url='https://api.github.com',
        token='ghp-stream-token',
    )
    provider = Mock()
    provider.list_pull_requests.return_value = {
        'pull_requests': [
            {
                'number': 7,
                'title': 'Update docs',
                'author': 'octocat',
                'state': 'open',
                'url': 'https://github.com/octo/repo/pull/7',
                'created_at': '2026-07-23T00:00:00Z',
                'reviewers': ['alice'],
            }
        ]
    }
    provider.get_pull_request_diff.return_value = '\n'.join([
        'diff --git a/docs/readme.md b/docs/readme.md',
        '--- a/docs/readme.md',
        '+++ b/docs/readme.md',
        '@@ -1 +1 @@',
        '-old line',
        '+new line',
    ])

    with patch('enrichment_provider.pr_enrichment.GitProviderFactory.create_from_service_token', return_value=provider):
        events = list(PREnrichmentProvider().get_enrichments_stream(
            'git://github/octo/repo/docs/readme.md?ref=main',
            user,
        ))

    assert events[0] == {'type': 'progress', 'message': 'Checking 1 open PRs for readme.md…'}
    assert events[1] == {'type': 'progress', 'message': 'PR #7 (1/1): Update docs'}
    assert events[2]['type'] == 'result'
    assert events[2]['data'] == [{
        'type': 'pr_diff',
        'pr_number': 7,
        'pr_title': 'Update docs',
        'pr_author': 'octocat',
        'pr_state': 'open',
        'pr_url': 'https://github.com/octo/repo/pull/7',
        'from_branch': '',
        'created_at': '2026-07-23T00:00:00Z',
        'reviewers': ['alice'],
        'diff_hunks': [{
            'old_start': 1,
            'old_count': 1,
            'new_start': 1,
            'new_count': 1,
            'lines': ['-old line', '+new line'],
        }],
    }]


@pytest.mark.django_db
def test_pr_enrichment_stream_fails_closed_when_github_source_tokens_are_ambiguous(user):
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

    events = list(PREnrichmentProvider().get_enrichments_stream(
        'git://github/octo/repo/docs/readme.md?ref=main',
        user,
    ))

    assert events == [
        {
            'type': 'error',
            'message': 'Ambiguous service token configuration for provider: github',
        },
        {'type': 'result', 'data': []},
    ]
