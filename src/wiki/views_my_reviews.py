"""
Pull Requests — lists open PRs across all visible spaces.
Supports optional filtering by author and/or reviewer username.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from django.db.models import Q
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from drf_spectacular.utils import extend_schema, OpenApiParameter

from service_tokens.models import ServiceToken
from .models import Space
from git_provider.factory import GitProviderFactory

logger = logging.getLogger(__name__)

# Bound concurrent provider calls. 10 is enough to mask network latency
# without DoSing upstream Bitbucket/GitHub when a user has many spaces.
_PR_FETCH_MAX_WORKERS = 10


def _visible_spaces(user):
    """Return queryset of spaces visible to the given user."""
    return Space.objects.filter(
        Q(owner=user)
        | Q(visibility='public')
        | Q(visibility='team')
        | Q(permissions__user=user)
    ).distinct()


@extend_schema(
    operation_id='wiki_my_reviews_list',
    summary='List open PRs across all visible spaces',
    description=(
        'Iterates all spaces visible to the authenticated user, queries the '
        'Git provider of each space for open PRs. Optionally filters by author '
        'and/or reviewer username.'
    ),
    parameters=[
        OpenApiParameter(name='author', type=str, required=False, description='Filter by PR author username'),
        OpenApiParameter(name='reviewer', type=str, required=False, description='Filter by reviewer username'),
    ],
    responses={200: dict},
    tags=['wiki'],
)
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_reviews(request):
    """
    GET /api/wiki/v1/my-reviews/
    GET /api/wiki/v1/my-reviews/?author=USERNAME
    GET /api/wiki/v1/my-reviews/?reviewer=USERNAME

    Returns:
        {
            "pull_requests": [
                {
                    "space_slug": "...",
                    "space_name": "...",
                    ...pr fields + reviewers...
                }
            ]
        }
    """
    user = request.user
    author_filter = request.query_params.get('author', '').strip().lower() or None
    reviewer_filter = request.query_params.get('reviewer', '').strip().lower() or None
    spaces = list(
        _visible_spaces(user)
        .exclude(git_provider__isnull=True)
        .exclude(git_provider='')
    )

    # Build a {(provider_type, base_url): (provider, token_username)} cache so
    # we don't recreate the same provider for every space on the same server.
    # Built sequentially before fanning out so worker threads share the cache
    # without locking.
    provider_cache: dict = {}
    fetch_targets = []  # list of (space, provider, repo_id)

    # SaaS providers where there's only one instance (no self-hosted URL
    # distinction), so base_url filtering would cause false negatives.
    _SAAS_PROVIDERS = {'github'}

    for space in spaces:
        repo_slug = space.git_repository_id
        if not repo_slug:
            continue

        key = (space.git_provider, space.git_base_url or '')
        if key not in provider_cache:
            try:
                qs = ServiceToken.objects.filter(
                    user=user,
                    service_type=space.git_provider,
                )
                if space.git_base_url and space.git_provider not in _SAAS_PROVIDERS:
                    qs = qs.filter(base_url=space.git_base_url)
                token = qs.first()
                if not token:
                    logger.debug(
                        'No service token for space %s (provider=%s, base_url=%s)',
                        space.slug, space.git_provider, space.git_base_url,
                    )
                    provider_cache[key] = None
                else:
                    token_username = token.get_username() or user.username or ''
                    provider_cache[key] = (
                        GitProviderFactory.create_from_service_token(token),
                        token_username,
                    )
            except Exception:
                logger.exception('Failed to create provider for space %s', space.slug)
                provider_cache[key] = None

        cached = provider_cache[key]
        if cached is None:
            continue
        provider, _token_username = cached

        # Bitbucket provider expects `PROJECT_REPO` format; GitHub uses
        # `owner/repo` which is already stored in git_repository_id.
        if space.git_project_key:
            repo_id = f'{space.git_project_key}_{repo_slug}'
        else:
            repo_id = repo_slug

        fetch_targets.append((space, provider, repo_id))

    server_reviewer = reviewer_filter if reviewer_filter else None

    def _is_bot(username, prefixes):
        lower = username.lower()
        return any(lower.startswith(p.lower()) for p in prefixes)

    def _fetch(space, provider, repo_id):
        """Fetch PRs for one space; return list of result dicts (filtered)."""
        try:
            prs_data = provider.list_pull_requests(
                repo_id=repo_id,
                state='open',
                page=1,
                per_page=100,
                reviewer=server_reviewer,
            )
        except Exception:
            logger.warning('Failed to fetch PRs for space %s', space.slug, exc_info=True)
            return []

        prs = prs_data.get('pull_requests', [])
        bot_prefixes = space.bot_usernames or []

        # If the space has bot prefixes configured, enrich PRs that have
        # comments with separate bot/human counts (fetched in parallel).
        if bot_prefixes:
            prs_with_comments = [pr for pr in prs if pr.get('comment_count', 0) > 0]
            if prs_with_comments:
                def _count_for_pr(pr):
                    try:
                        authors = provider.get_pr_comment_authors(repo_id, pr['number'])
                    except Exception:
                        authors = []
                    bot = sum(1 for a in authors if _is_bot(a, bot_prefixes))
                    return pr['number'], len(authors) - bot, bot

                # Cap inner workers to 3 so nested pools stay bounded.
                # With up to 10 outer space-workers this gives ≤30 total
                # concurrent upstream calls (vs 80 with the previous cap of 8).
                max_w = min(3, len(prs_with_comments))
                with ThreadPoolExecutor(max_workers=max_w) as pool:
                    futs = {pool.submit(_count_for_pr, pr): pr for pr in prs_with_comments}
                    counts = {}
                    for fut in as_completed(futs):
                        pr_num, human, bot = fut.result()
                        counts[pr_num] = (human, bot)
                for pr in prs:
                    if pr['number'] in counts:
                        human, bot = counts[pr['number']]
                        pr['human_comment_count'] = human
                        pr['bot_comment_count'] = bot

        out = []
        for pr in prs:
            if reviewer_filter and not any(
                r.get('username', '').lower() == reviewer_filter
                for r in pr.get('reviewers', [])
            ):
                continue
            if author_filter and (pr.get('author') or '').lower() != author_filter:
                continue
            out.append({
                'space_slug': space.slug,
                'space_name': space.name,
                **pr,
            })
        return out

    result = []
    if fetch_targets:
        # Cap workers at the actual target count so we don't spawn 10 threads
        # for a single space.
        max_workers = min(_PR_FETCH_MAX_WORKERS, len(fetch_targets))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_fetch, *t) for t in fetch_targets]
            for fut in as_completed(futures):
                result.extend(fut.result())

    # Collect all unique git usernames encountered for the current user so
    # the frontend can offer a "My reviews" shortcut without guessing.
    git_usernames = sorted({
        cached[1] for cached in provider_cache.values() if cached is not None
    })

    # Collect bot username prefixes from all spaces so the frontend can
    # exclude them from reviewer filters and separate bot comments.
    bot_usernames_by_space = {}
    all_bot_usernames = set()
    for space in spaces:
        prefixes = space.bot_usernames or []
        if prefixes:
            bot_usernames_by_space[space.slug] = prefixes
            all_bot_usernames.update(prefixes)

    return Response({
        'pull_requests': result,
        'current_git_usernames': git_usernames,
        'bot_usernames': sorted(all_bot_usernames),
        'bot_usernames_by_space': bot_usernames_by_space,
    }, status=status.HTTP_200_OK)
