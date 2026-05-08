import json
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from django.http import StreamingHttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework import status
from .registry import get_registry
from wiki.models import Space

logger = logging.getLogger(__name__)


def _empty_file_enrichments():
    return {'pr_diff': [], 'comments': [], 'diff': [], 'local_changes': [], 'edit': [], 'commit': []}


def _ensure_file(file_enrichments, file_path):
    if file_path not in file_enrichments:
        file_enrichments[file_path] = _empty_file_enrichments()


def _get_space_enrichments(request, space_slug, start_time):
    """
    Get all enrichments for a space.
    Returns: {file_path: {pr_diff: [...], comments: [...], edit: [...], local_changes: [...]}}
    """
    from git_provider.factory import GitProviderFactory
    from service_tokens.models import ServiceToken
    from wiki.models import FileComment, UserDraftChange, UserChange

    try:
        space = Space.objects.get(slug=space_slug)
        repo_id = f"{space.git_project_key}_{space.git_repository_id}"
        branch = space.git_default_branch or 'master'

        logger.info(f"[SpaceEnrichments] Space: {space.name}, Provider: {space.git_provider}, Repo: {repo_id}")

        service_token = ServiceToken.objects.filter(
            user=request.user,
            service_type=space.git_provider
        ).first()

        if not service_token:
            logger.warning(f"[SpaceEnrichments] No service token for {space.git_provider}")
            return Response({'error': 'No service token found'}, status=status.HTTP_400_BAD_REQUEST)

        provider = GitProviderFactory.create_from_service_token(service_token)

        # ── PR diffs ──────────────────────────────────────────────────────────
        # Clear only stale PR cache entries (older than 30s) instead of wiping
        # everything. This preserves cache hits for rapid re-opens while still
        # reflecting external changes within a reasonable window.
        from users.models import APIResponseCache
        from django.utils import timezone
        from datetime import timedelta
        stale_cutoff = timezone.now() - timedelta(seconds=30)
        deleted_pr_cache = APIResponseCache.objects.filter(
            user=request.user,
            endpoint__contains='pull-request',
            created_at__lt=stale_cutoff,
        ).delete()
        logger.info(f"[SpaceEnrichments] Cleared {deleted_pr_cache[0]} stale PR cache entries")

        prs_start = time.time()
        prs_response = provider.list_pull_requests(
            repo_id=repo_id, state='open', page=1, per_page=1000
        )
        prs = prs_response.get('pull_requests', [])
        logger.info(f"[SpaceEnrichments] Fetched {len(prs)} PRs in {time.time() - prs_start:.3f}s")

        file_enrichments = {}

        # Fetch diffs in parallel to hide network latency.
        _SPACE_DIFF_MAX_WORKERS = 8

        def _fetch_space_pr_diff(pr):
            """Return (pr, diff_text) or (pr, None) on failure."""
            try:
                return pr, provider.get_pull_request_diff(repo_id=repo_id, pr_number=pr['number'])
            except Exception as e:
                logger.warning(f"[SpaceEnrichments] Failed to fetch diff for PR #{pr.get('number')}: {e}")
                return pr, None

        if prs:
            max_workers = min(_SPACE_DIFF_MAX_WORKERS, len(prs))
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for pr, diff_text in pool.map(_fetch_space_pr_diff, prs):
                    if not diff_text:
                        continue
                    for file_path in _extract_files_from_diff(diff_text):
                        hunks = _parse_diff_hunks_for_file(diff_text, file_path)
                        if hunks:
                            _ensure_file(file_enrichments, file_path)
                            file_enrichments[file_path]['pr_diff'].append({
                                'type': 'pr_diff',
                                'pr_number': pr['number'],
                                'pr_title': pr['title'],
                                'pr_author': pr['author'],
                                'pr_state': pr['state'],
                                'pr_url': pr['url'],
                                'from_branch': pr.get('from_branch', ''),
                                'created_at': pr['created_at'],
                                'diff_hunks': hunks,
                            })

        # ── Comments ──────────────────────────────────────────────────────────
        source_uri_prefix = f"git://{space.git_provider}/{repo_id}/{branch}/"
        comments_qs = (
            FileComment.objects
            .filter(source_uri__startswith=source_uri_prefix, parent_comment=None)
            .select_related('author')
            .prefetch_related('replies')
            .order_by('line_start', 'created_at')
        )

        def _serialize_comment(comment):
            data = {
                'type': 'comment',
                'id': str(comment.id),
                'source_uri': comment.source_uri,
                'line_start': comment.line_start,
                'line_end': comment.line_end,
                'text': comment.text,
                'author': comment.author.username,
                'thread_id': str(comment.thread_id),
                'parent_id': str(comment.parent_comment.id) if comment.parent_comment else None,
                'is_resolved': comment.is_resolved,
                'anchoring_status': comment.anchoring_status,
                'created_at': comment.created_at.isoformat(),
                'updated_at': comment.updated_at.isoformat(),
                'replies': [_serialize_comment(r) for r in comment.replies.all()],
            }
            return data

        for comment in comments_qs:
            file_path = comment.source_uri[len(source_uri_prefix):]
            _ensure_file(file_enrichments, file_path)
            file_enrichments[file_path]['comments'].append(_serialize_comment(comment))

        logger.info(f"[SpaceEnrichments] Loaded {comments_qs.count()} comment threads")

        # ── Draft edits (UserDraftChange) ─────────────────────────────────────
        draft_changes = UserDraftChange.objects.filter(
            user=request.user, space=space
        ).select_related('space', 'user_branch')

        for change in draft_changes:
            diff_hunks = change.generate_diff_hunks()
            # Skip stale drafts with no actual content difference
            if change.change_type == 'modify' and not diff_hunks:
                continue
            _ensure_file(file_enrichments, change.file_path)
            file_enrichments[change.file_path]['edit'].append({
                'type': 'edit',
                'id': str(change.id),
                'space_id': str(change.space.id),
                'space_slug': change.space.slug,
                'file_path': change.file_path,
                'change_type': change.change_type,
                'description': change.description or '',
                'user': request.user.username,
                'user_full_name': request.user.get_full_name() or request.user.username,
                'branch_id': str(change.user_branch_id) if change.user_branch_id else None,
                'task_name': change.user_branch.name if change.user_branch_id else None,
                'created_at': change.created_at.isoformat(),
                'updated_at': change.updated_at.isoformat(),
                'diff_hunks': diff_hunks,
                'actions': ['commit', 'discard'],
            })

        logger.info(f"[SpaceEnrichments] Loaded {draft_changes.count()} draft edits")

        # ── Local changes (UserChange) ─────────────────────────────────────────
        local_changes = UserChange.objects.filter(
            user=request.user, repository_full_name=repo_id, status='pending'
        ).order_by('-created_at')

        for change in local_changes:
            _ensure_file(file_enrichments, change.file_path)
            file_enrichments[change.file_path]['local_changes'].append({
                'type': 'local_change',
                'id': change.id,
                'file_path': change.file_path,
                'commit_message': change.commit_message,
                'status': change.status,
                'created_at': change.created_at.isoformat(),
                'updated_at': change.updated_at.isoformat(),
            })

        logger.info(f"[SpaceEnrichments] Loaded {local_changes.count()} local changes")

        # ── Committed changes (all ACTIVE tasks — PR_OPEN uses PR enrichments) ──
        from wiki.models import UserBranch
        from git_provider.worktree_manager import GitWorktreeManager
        import os as _os

        user_branches = UserBranch.objects.filter(
            user=request.user,
            space=space,
            status=UserBranch.Status.ACTIVE,
        )

        commit_enrichment_count = 0
        manager = GitWorktreeManager()
        if space.edit_fork_local_path and _os.path.exists(space.edit_fork_local_path):
            repo_path = space.edit_fork_local_path
        else:
            repo_path = manager.get_bare_repo_path(str(space.id))

        if _os.path.exists(repo_path):
            for user_branch in user_branches:
                if not user_branch.last_commit_sha:
                    continue
                try:
                    base_ref = manager._resolve_base_ref(repo_path, user_branch.base_branch)
                    changed_files = manager.list_changed_files_sync(
                        repo_path,
                        branch_name=user_branch.branch_name,
                        base_branch=base_ref,
                    )
                    for fp in changed_files:
                        diff_result = manager.get_file_diff_sync(
                            repo_path=repo_path,
                            branch_name=user_branch.branch_name,
                            base_branch=base_ref,
                            file_path=fp,
                        )
                        if diff_result:
                            _ensure_file(file_enrichments, fp)
                            file_enrichments[fp]['commit'].append({
                                'type': 'commit',
                                'id': str(user_branch.id),
                                'space_id': str(space.id),
                                'space_slug': space.slug,
                                'file_path': fp,
                                'branch_name': user_branch.branch_name,
                                'base_branch': user_branch.base_branch,
                                'task_name': user_branch.name or None,
                                'commit_sha': user_branch.last_commit_sha,
                                'user': request.user.username,
                                'user_full_name': request.user.get_full_name() or request.user.username,
                                'created_at': user_branch.created_at.isoformat(),
                                'updated_at': user_branch.updated_at.isoformat(),
                                'diff_hunks': diff_result.get('hunks', []),
                                'additions': diff_result.get('additions', 0),
                                'deletions': diff_result.get('deletions', 0),
                                'pr_id': user_branch.pr_id,
                                'pr_url': user_branch.pr_url,
                                'actions': ['unstage', 'create_pr'],
                            })
                            commit_enrichment_count += 1
                except Exception as e:
                    logger.warning(f"[SpaceEnrichments] Failed to load commit enrichments for {user_branch.branch_name}: {e}")

        logger.info(f"[SpaceEnrichments] Loaded {commit_enrichment_count} commit enrichments")

        # When a commit's branch already has an open PR, suppress the duplicate commit enrichment.
        for fp, fe in file_enrichments.items():
            if fe.get('pr_diff') and fe.get('commit'):
                pr_branches = {e.get('from_branch', '') for e in fe['pr_diff'] if e.get('from_branch')}
                if pr_branches:
                    fe['commit'] = [e for e in fe['commit'] if e.get('branch_name') not in pr_branches]

        total_duration = time.time() - start_time
        logger.info(f"[SpaceEnrichments] Total time: {total_duration:.3f}s, files with enrichments: {len(file_enrichments)}")

        return Response(file_enrichments)

    except Space.DoesNotExist:
        return Response({'error': 'Space not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        logger.error(f"[SpaceEnrichments] Failed: {e}")
        logger.error(traceback.format_exc())
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _extract_files_from_diff(diff_text):
    """Extract all file paths from a diff."""
    files = set()
    for line in diff_text.split('\n'):
        if line.startswith('---') or line.startswith('+++'):
            # Extract filename from diff marker
            marker_file = line[4:].strip()
            if marker_file.startswith('a/') or marker_file.startswith('b/'):
                marker_file = marker_file[2:]
            if marker_file and marker_file != '/dev/null':
                files.add(marker_file)
    return files


def _parse_diff_hunks_for_file(diff_text, file_path):
    """Parse diff hunks for a specific file."""
    import re
    hunks = []
    in_file = False
    current_hunk = None
    
    for line in diff_text.split('\n'):
        # Check if we're entering the section for our file
        if line.startswith('---') or line.startswith('+++'):
            marker_file = line[4:].strip()
            if marker_file.startswith('a/') or marker_file.startswith('b/'):
                marker_file = marker_file[2:]
            
            is_match = (marker_file == file_path)
            
            if is_match:
                in_file = True
            elif marker_file:
                in_file = False
            continue
        
        # Parse hunk header
        if in_file and line.startswith('@@'):
            if current_hunk:
                hunks.append(current_hunk)
            
            match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
            if match:
                current_hunk = {
                    'old_start': int(match.group(1)),
                    'old_count': int(match.group(2)) if match.group(2) else 1,
                    'new_start': int(match.group(3)),
                    'new_count': int(match.group(4)) if match.group(4) else 1,
                    'lines': []
                }
        
        # Collect hunk lines
        elif in_file and current_hunk is not None:
            if line.startswith('+') or line.startswith('-') or line.startswith(' '):
                current_hunk['lines'].append(line)
    
    if current_hunk:
        hunks.append(current_hunk)
    
    return hunks


def _get_recursive_enrichments(request, source_uri, enrichment_type, start_time):
    """
    Get enrichments for all files in a directory.
    """
    from source_provider.base import SourceAddress
    from git_provider.factory import GitProviderFactory
    from service_tokens.models import ServiceToken
    
    try:
        # Strip trailing slash for root directory
        original_uri = source_uri
        source_uri = source_uri.rstrip('/')
        logger.debug(f"[Enrichments] Original URI: {original_uri}")
        logger.debug(f"[Enrichments] After rstrip: {source_uri}")
        logger.debug(f"[Enrichments] Slash count: {source_uri.count('/')}")
        
        # For root directory, add a placeholder path for parser
        # git://provider/repo/branch has 4 slashes (git:// = 2, then 2 more)
        # git://provider/repo/branch/path has 5+ slashes
        if source_uri.count('/') == 4:  # No path component (root directory)
            source_uri += '/.'
            logger.debug(f"[Enrichments] Added placeholder: {source_uri}")
        
        # Parse source address to get provider and repository info
        address = SourceAddress.parse(source_uri)
        
        # Get Git provider
        service_token = ServiceToken.objects.filter(
            user=request.user,
            service_type=address.provider
        ).first()
        
        if not service_token:
            return Response(
                {'error': 'No service token found for provider'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        provider = GitProviderFactory.create_from_service_token(service_token)
        
        # Get directory tree recursively to get all files in subdirectories
        tree_start = time.time()
        # Convert '.' placeholder to empty string for root directory
        tree_path = '' if address.path == '.' else (address.path or '')
        # Split repository into project_key and repo_slug
        project_key, repo_slug = address.repository.split('_', 1)
        tree_entries = provider.get_directory_tree(project_key, repo_slug, tree_path, address.branch, recursive=True)
        tree_duration = time.time() - tree_start
        logger.info(f"[Enrichments] Recursive tree fetch took {tree_duration:.3f}s, found {len(tree_entries)} entries")
        
        # Filter only files (not directories)
        files = [entry for entry in tree_entries if entry.get('type') == 'file']
        logger.info(f"[Enrichments] Processing {len(files)} files")
        
        # Get enrichments for each file
        registry = get_registry()
        results = {}
        
        for file_entry in files:
            file_path = file_entry.get('path', '')
            # Build source URI for this file
            file_source_uri = f"git://{address.provider}/{address.repository}/{address.branch}/{file_path}"
            
            try:
                if enrichment_type:
                    enrichments = registry.get_enrichments_by_type(file_source_uri, request.user, enrichment_type)
                    results[file_source_uri] = {enrichment_type: enrichments}
                else:
                    enrichments = registry.get_all_enrichments(file_source_uri, request.user)
                    results[file_source_uri] = enrichments
            except Exception as e:
                logger.error(f"[Enrichments] Failed to get enrichments for {file_source_uri}: {e}")
                results[file_source_uri] = {}
        
        total_duration = time.time() - start_time
        logger.info(f"[Enrichments] Recursive request completed in {total_duration:.3f}s for {len(files)} files")
        
        return Response(results)
        
    except Exception as e:
        logger.error(f"[Enrichments] Recursive enrichment failed: {e}")
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_enrichments(request):
    """
    Get enrichments for a source URI or entire space.
    
    Query Parameters:
        source_uri: Universal source address for specific file (optional)
        space_slug: Space slug for all enrichments in space (optional)
        type: Filter by enrichment type (optional)
        recursive: If true, get enrichments for all files in directory (optional, only with source_uri)
    
    Returns:
        - If source_uri: Dictionary mapping enrichment types to lists of enrichments
        - If space_slug: Dictionary mapping file paths to their enrichments
    """
    start_time = time.time()
    source_uri = request.query_params.get('source_uri')
    space_slug = request.query_params.get('space_slug')
    enrichment_type = request.query_params.get('type')
    recursive = request.query_params.get('recursive', 'false').lower() == 'true'
    
    # Handle space-level enrichments
    if space_slug:
        logger.info(f"[Enrichments] Space request for: {space_slug}")
        return _get_space_enrichments(request, space_slug, start_time)
    
    # Handle file-level enrichments
    if not source_uri:
        return Response(
            {'error': 'Either source_uri or space_slug parameter is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    logger.info(f"[Enrichments] File request for: {source_uri} (type: {enrichment_type or 'all'}, recursive: {recursive})")
    
    # Handle recursive directory enrichments
    if recursive:
        return _get_recursive_enrichments(request, source_uri, enrichment_type, start_time)
    
    registry = get_registry()
    
    if enrichment_type:
        # Get enrichments of specific type
        type_start = time.time()
        enrichments = registry.get_enrichments_by_type(source_uri, request.user, enrichment_type)
        type_duration = time.time() - type_start
        logger.info(f"[Enrichments] Type '{enrichment_type}' took {type_duration:.3f}s")
        result = {enrichment_type: enrichments}
    else:
        # Get all enrichments
        all_start = time.time()
        enrichments = registry.get_all_enrichments(source_uri, request.user)
        all_duration = time.time() - all_start
        
        # Log individual provider times
        for enrich_type, enrich_list in enrichments.items():
            count = len(enrich_list) if enrich_list else 0
            logger.info(f"[Enrichments]   - {enrich_type}: {count} items")
        
        logger.info(f"[Enrichments] All enrichments took {all_duration:.3f}s")
        result = enrichments

        # When a commit's branch already has an open PR, show only the PR enrichment.
        # The commit diff is a subset of the PR diff, so showing both is redundant and confusing.
        if result.get('pr_diff') and result.get('commit'):
            pr_branches = {e.get('from_branch', '') for e in result['pr_diff'] if e.get('from_branch')}
            if pr_branches:
                result['commit'] = [e for e in result['commit'] if e.get('branch_name') not in pr_branches]
    
    total_duration = time.time() - start_time
    logger.info(f"[Enrichments] Total request time: {total_duration:.3f}s for {source_uri}")
    
    return Response(result)


def stream_enrichments(request):
    """
    Streaming NDJSON endpoint for file enrichments with live progress events.

    Returns newline-delimited JSON:
      {"type": "progress", "message": "..."}   -- emitted for each PR checked
      {"type": "complete", "data": {...}}       -- final EnrichmentsResponse payload

    Auth: session cookie (same as all other API endpoints).
    nginx / gunicorn buffering is disabled via X-Accel-Buffering header.
    """
    # Plain Django view — manually support Bearer token auth in addition to
    # session auth, since DRF's authentication classes don't run here.
    if not request.user.is_authenticated:
        auth_header = request.META.get('HTTP_AUTHORIZATION', '')
        if auth_header.startswith('Bearer '):
            from users.models import ApiToken
            try:
                api_token = ApiToken.objects.select_related('user').get(token=auth_header[7:])
                request.user = api_token.user
            except ApiToken.DoesNotExist:
                pass
    if not request.user.is_authenticated:
        from django.http import HttpResponse
        return HttpResponse('Authentication required', status=401)

    source_uri = request.GET.get('source_uri', '').strip()
    if not source_uri:
        from django.http import HttpResponse
        return HttpResponse('source_uri required', status=400)

    def _generate():
        from .pr_enrichment import PREnrichmentProvider
        from .comment_enrichment import CommentEnrichmentProvider
        from .edit_session_enrichment import EditEnrichmentProvider, CommitEnrichmentProvider

        # Fast enrichments first (DB / local git — typically < 0.5s total).
        yield json.dumps({'type': 'progress', 'message': 'Loading annotations…'}) + '\n'
        comments, edits, commits = [], [], []
        try:
            comments = CommentEnrichmentProvider().get_enrichments(source_uri, request.user)
        except Exception as e:
            logger.warning(f"[StreamEnrichments] comments failed: {e}")
        try:
            edits = EditEnrichmentProvider().get_enrichments(source_uri, request.user)
        except Exception as e:
            logger.warning(f"[StreamEnrichments] edit failed: {e}")
        try:
            commits = CommitEnrichmentProvider().get_enrichments(source_uri, request.user)
        except Exception as e:
            logger.warning(f"[StreamEnrichments] commit failed: {e}")

        # Slow: stream PR enrichments with per-PR progress events.
        pr_enrichments = []
        for event in PREnrichmentProvider().get_enrichments_stream(source_uri, request.user):
            if event['type'] == 'result':
                pr_enrichments = event['data']
            elif event['type'] == 'error':
                logger.warning(f"[StreamEnrichments] PR stream error: {event.get('message')}")
            else:
                yield json.dumps(event) + '\n'

        # Suppress commit enrichment when the branch already has an open PR
        # (the PR diff is a superset of the commit diff).
        if pr_enrichments and commits:
            pr_branches = {e.get('from_branch', '') for e in pr_enrichments if e.get('from_branch')}
            if pr_branches:
                commits = [e for e in commits if e.get('branch_name') not in pr_branches]

        result = {
            'pr_diff': pr_enrichments,
            'comments': comments,
            'edit': edits,
            'commit': commits,
        }
        yield json.dumps({'type': 'complete', 'data': result}) + '\n'

    resp = StreamingHttpResponse(_generate(), content_type='application/x-ndjson')
    resp['X-Accel-Buffering'] = 'no'
    resp['Cache-Control'] = 'no-cache'
    return resp


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_enrichment_types(request):
    """
    Get list of available enrichment types.
    
    Returns:
        List of enrichment type strings
    """
    registry = get_registry()
    types = registry.get_enrichment_types()
    return Response({'types': types})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_enrichment_metadata(request):
    """
    Get metadata for all enrichment types including categories.
    
    Returns:
        Dictionary mapping enrichment types to their metadata
    """
    registry = get_registry()
    metadata = registry.get_enrichment_metadata()
    return Response(metadata)
