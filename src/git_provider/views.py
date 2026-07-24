"""
Views for Git provider management and operations.
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiExample
from drf_spectacular.types import OpenApiTypes
from service_tokens.models import ServiceToken, ServiceType
from service_tokens.url import canonical_base_url
from .factory import GitProviderFactory
from .serializers import (
    RepositorySerializer,
    FileContentSerializer, TreeEntrySerializer, PullRequestSerializer,
    CommitSerializer
)
from users.decorators import cached_api_response
from wiki.access import accessible_spaces_for_user
import base64
import hashlib
import logging
import os
import stat
import subprocess
import tempfile
import shutil
from urllib.parse import urlparse, urlunparse

logger = logging.getLogger(__name__)


class _BlameClone:
    """Helpers for the lazy on-demand HTTPS bare clone used by `get_blame`.

    Kept on its own (rather than inlined) so the URL-rewriting logic can be
    unit-tested without standing up the whole view set.
    """

    @staticmethod
    def resolve_https_credentials(space, user) -> dict | None:
        """Return the current user's usable HTTPS clone credentials, or None.

        The result is bound to the current user's matching `(provider, base_url)`
        token so cache reuse across users or revoked/rotated tokens fails closed.
        """
        from urllib.parse import urlparse

        repo_url = (space.git_repository_url or '').strip()
        if not repo_url:
            return None
        parsed = urlparse(repo_url)
        if parsed.scheme not in ('http', 'https'):
            return None

        service_token = GitProviderFactory.get_service_token(
            user=user,
            provider=space.git_provider,
            base_url=canonical_base_url(space.git_base_url),
        )
        if not service_token:
            return None

        token = service_token.get_token()
        if not token:
            return None

        if space.git_provider == ServiceType.BITBUCKET_SERVER:
            username = service_token.get_username()
            if not username:
                return None
        else:
            username = 'oauth2'

        custom_header_name = None
        custom_header_token = None
        if space.git_provider == ServiceType.BITBUCKET_SERVER:
            custom_header = GitProviderFactory.get_custom_header_token(
                user=user,
                base_url=canonical_base_url(space.git_base_url),
            )
            if custom_header:
                custom_header_name = custom_header.header_name
                custom_header_token = custom_header.get_token()

        return {
            'repo_url': repo_url,
            'provider': space.git_provider,
            'base_url': canonical_base_url(space.git_base_url),
            'username': username,
            'token': token,
            'custom_header_name': custom_header_name,
            'custom_header_token': custom_header_token,
        }

    @staticmethod
    def with_auth(space, user) -> str | None:
        """Backward-compatible helper retained for existing unit tests."""
        from urllib.parse import quote

        credentials = _BlameClone.resolve_https_credentials(space, user)
        if not credentials:
            return None

        parsed = urlparse(credentials['repo_url'])
        netloc = (
            f"{quote(credentials['username'], safe='')}:"
            f"{quote(credentials['token'], safe='')}@{parsed.netloc}"
        )
        return urlunparse(parsed._replace(netloc=netloc))

    @staticmethod
    def cache_key(credentials: dict) -> str:
        """Return a stable cache suffix bound to the active credential tuple."""
        parsed_repo_url = urlparse(credentials['repo_url'] or '')
        canonical_repo_url = urlunparse((
            parsed_repo_url.scheme.lower(),
            parsed_repo_url.netloc.lower(),
            parsed_repo_url.path.rstrip('/'),
            '',
            '',
            '',
        ))
        digest = hashlib.sha256(
            '||'.join(
                [
                    credentials['provider'] or '',
                    credentials['base_url'] or '',
                    canonical_repo_url,
                    credentials['username'] or '',
                    credentials['token'] or '',
                ]
            ).encode('utf-8')
        ).hexdigest()
        return digest[:16]

    @staticmethod
    def cache_path(manager, space, user) -> str | None:
        """Return the cache path for the active credential tuple, or None."""
        credentials = _BlameClone.resolve_https_credentials(space, user)
        if not credentials:
            return None
        return os.path.join(
            manager.cache_dir,
            'spaces',
            str(space.id),
            f"blame-{_BlameClone.cache_key(credentials)}.git",
        )

    @staticmethod
    def build_git_env(manager, credentials: dict) -> tuple[dict, str]:
        """Build a one-shot askpass environment for HTTPS git auth."""
        env = manager._get_git_env().copy()
        fd, askpass_path = tempfile.mkstemp(prefix='cyberwiki-git-askpass-', suffix='.sh')
        try:
            os.write(
                fd,
                (
                    '#!/bin/sh\n'
                    'case "$1" in\n'
                    '  *sername*) printf "%s" "$CYBERWIKI_GIT_USERNAME" ;;\n'
                    '  *) printf "%s" "$CYBERWIKI_GIT_PASSWORD" ;;\n'
                    'esac\n'
                ).encode('utf-8'),
            )
        finally:
            os.close(fd)
        os.chmod(askpass_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        env.update(
            {
                'GIT_TERMINAL_PROMPT': '0',
                'GIT_ASKPASS': askpass_path,
                'CYBERWIKI_GIT_USERNAME': credentials['username'],
                'CYBERWIKI_GIT_PASSWORD': credentials['token'],
            }
        )
        if credentials.get('custom_header_name') and credentials.get('custom_header_token'):
            _BlameClone._append_git_config_env(
                env,
                'http.extraHeader',
                f"{credentials['custom_header_name']}: {credentials['custom_header_token']}",
            )
        return env, askpass_path

    @staticmethod
    def _append_git_config_env(env: dict, key: str, value: str) -> None:
        """Append a git -c equivalent via environment variables."""
        count = int(env.get('GIT_CONFIG_COUNT', '0'))
        env[f'GIT_CONFIG_KEY_{count}'] = key
        env[f'GIT_CONFIG_VALUE_{count}'] = value
        env['GIT_CONFIG_COUNT'] = str(count + 1)

    @staticmethod
    def clone_bare_repo(manager, credentials: dict, blame_path: str) -> str:
        """Clone to a temp directory and atomically publish the cache path."""
        parent_dir = os.path.dirname(blame_path)
        os.makedirs(parent_dir, exist_ok=True)
        temp_path = tempfile.mkdtemp(
            prefix=f".{os.path.basename(blame_path)}.",
            dir=parent_dir,
        )
        env, askpass_path = _BlameClone.build_git_env(manager, credentials)
        try:
            subprocess.run(
                ['git', 'clone', '--bare', credentials['repo_url'], temp_path],
                env=env,
                capture_output=True,
                text=True,
                check=True,
                timeout=180,
            )
        finally:
            try:
                os.unlink(askpass_path)
            except OSError:
                pass

        try:
            os.rename(temp_path, blame_path)
            temp_path = None
            return blame_path
        except OSError:
            if os.path.exists(blame_path):
                return blame_path
            raise
        finally:
            if temp_path and os.path.exists(temp_path):
                shutil.rmtree(temp_path, ignore_errors=True)


class GitProviderViewSet(viewsets.ViewSet):
    """
    ViewSet for Git provider operations.
    """
    permission_classes = [IsAuthenticated]
    
    # Note: Git credentials are now managed via /api/service-tokens/v1/tokens/
    # This ViewSet only handles repository operations using those credentials
    
    def _get_provider(self, request):
        """Get Git provider instance for the user.

        Token lookup is by exact `(user, service_type, base_url)` tuple.
        `base_url` is canonicalised on save (see service_tokens/url.py) and
        Space.git_base_url is canonicalised in lock-step, so the frontend
        can pass `space.git_base_url` straight through without massaging.
        """
        provider_type = request.query_params.get('provider')
        base_url = request.query_params.get('base_url')

        if not provider_type:
            raise ValueError('provider is required')

        if not base_url and provider_type != 'github':
            raise ValueError('provider and base_url are required')

        if base_url:
            service_token = GitProviderFactory.get_service_token(
                user=request.user,
                provider=provider_type,
                base_url=canonical_base_url(base_url),
            )
            if service_token is None:
                raise ValueError('Git credentials not found')
        else:
            service_token = GitProviderFactory.get_source_service_token(
                user=request.user,
                provider=provider_type,
            )
        return GitProviderFactory.create_from_service_token(service_token)

    @staticmethod
    def _handle_provider_error(exc, log_prefix):
        """Map upstream HTTPError to a DRF Response with a meaningful status.

        Falls through to 500 for non-HTTPError exceptions (caller logs them).
        """
        import requests
        logger.exception(f"{log_prefix}: {str(exc)}")

        if isinstance(exc, requests.exceptions.HTTPError):
            code = exc.response.status_code
            if code == 401:
                return Response(
                    {
                        'error': 'Git provider authentication failed',
                        'code': 'GIT_PROVIDER_AUTH_FAILED',
                        'detail': 'Invalid credentials. Please check your tokens in the Configuration page and ensure they are valid and not expired.',
                        'help': 'Verify: 1) Git provider token is valid, 2) Username is correct, 3) Custom header token (if required) is valid and not expired',
                    },
                    status=status.HTTP_502_BAD_GATEWAY,
                )
            if code == 403:
                return Response(
                    {
                        'error': 'Access forbidden',
                        'code': 'FORBIDDEN',
                        'detail': 'You do not have permission to access this resource. Check your token permissions.',
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            if code == 404:
                return Response(
                    {
                        'error': 'Resource not found',
                        'code': 'NOT_FOUND',
                        'detail': f'The requested resource does not exist on the remote ({exc.response.url}).',
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

        return Response(
            {'error': 'Internal server error', 'code': 'INTERNAL_ERROR', 'detail': str(exc)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    
    @extend_schema(
        operation_id='git_provider_repositories_list',
        summary='List repositories',
        description='List repositories accessible to the authenticated user.',
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='page', type=int, required=False),
            OpenApiParameter(name='per_page', type=int, required=False),
        ],
        responses={200: RepositorySerializer(many=True)},
        tags=['git-provider'],
    )
    @action(detail=False, methods=['get'], url_path='projects')
    def list_projects(self, request):
        """List projects."""
        try:
            provider = self._get_provider(request)
            page = int(request.query_params.get('page', 1))
            per_page = int(request.query_params.get('per_page', 100))
            
            # Check if provider supports list_projects
            if not hasattr(provider, 'list_projects'):
                return Response(
                    {'error': 'Provider does not support project listing', 'code': 'NOT_SUPPORTED'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            result = provider.list_projects(page=page, per_page=per_page)
            return Response(result)
        except Exception as e:
            return self._handle_provider_error(e, 'Error listing projects')
    
    @action(detail=False, methods=['get'], url_path='repositories')
    def list_repositories(self, request):
        """List repositories. Optionally filter by project_key."""
        try:
            provider = self._get_provider(request)
            page = int(request.query_params.get('page', 1))
            per_page = int(request.query_params.get('per_page', 30))
            project_key = request.query_params.get('project_key')
            
            result = provider.list_repositories(page=page, per_page=per_page, project_key=project_key)
            return Response(result)
        except ValueError as e:
            logger.error(f"Invalid request for list_repositories: {str(e)}")
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return self._handle_provider_error(e, 'Error listing repositories')

    @extend_schema(
        operation_id='git_provider_repository_get',
        summary='Get repository details',
        description='Get details of a specific repository.',
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='repo_id', type=str, required=True, location=OpenApiParameter.PATH),
        ],
        responses={200: RepositorySerializer},
        tags=['git-provider'],
    )
    @action(detail=True, methods=['get'], url_path='')
    def get_repository(self, request, pk=None):
        """Get repository details."""
        try:
            provider = self._get_provider(request)
            repo = provider.get_repository(pk)
            return Response(repo)
        except ValueError as e:
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @extend_schema(
        operation_id='git_provider_file_get',
        summary='Get file content',
        description='Get content of a specific file.',
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='project_key', type=str, required=True, description='Project key (for Bitbucket Server) or owner (for GitHub)'),
            OpenApiParameter(name='repo_slug', type=str, required=True, description='Repository slug/name'),
            OpenApiParameter(name='file_path', type=str, required=True, description='Path to the file'),
            OpenApiParameter(name='branch', type=str, required=False),
        ],
        responses={200: FileContentSerializer},
        tags=['git-provider'],
    )
    @cached_api_response(
        provider_type_param='provider',
        endpoint_func=lambda view, **kwargs: '/file'
    )
    @action(detail=False, methods=['get'], url_path='file')
    def get_file(self, request):
        """Get file content."""
        try:
            provider = self._get_provider(request)
            project_key = request.query_params.get('project_key')
            repo_slug = request.query_params.get('repo_slug')
            file_path = request.query_params.get('file_path')
            branch = request.query_params.get('branch', 'main')
            
            if not project_key or not repo_slug or not file_path:
                return Response(
                    {'error': 'project_key, repo_slug, and file_path are required', 'code': 'MISSING_PARAMETERS'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            file_data = provider.get_file_content(project_key, repo_slug, file_path, branch)
            
            # Decode base64 content if needed
            if file_data.get('encoding') == 'base64':
                try:
                    file_data['content'] = base64.b64decode(file_data['content']).decode('utf-8')
                    file_data['encoding'] = 'utf-8'
                except Exception:
                    pass  # Keep as base64 if decode fails
            
            return Response(file_data)
        except ValueError as e:
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return self._handle_provider_error(e, 'Error getting file content')

    @extend_schema(
        operation_id='git_provider_file_blame_get',
        summary='Get per-line blame for a file',
        description=(
            'Return per-line blame info: commit_sha, author_name, '
            'author_email, author_date, summary. Only providers with direct '
            'git access (LocalGit + worktree-backed setups) implement this; '
            'remote-only providers return an empty array.'
        ),
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='project_key', type=str, required=True),
            OpenApiParameter(name='repo_slug', type=str, required=True),
            OpenApiParameter(name='file_path', type=str, required=True),
            OpenApiParameter(name='branch', type=str, required=False),
        ],
        responses={200: OpenApiTypes.OBJECT},
        tags=['git-provider'],
    )
    @action(detail=False, methods=['get'], url_path='blame')
    def get_blame(self, request):
        """Get per-line blame for a file.

        Resolution order:
          1. The space's local clone (worktree-manager bare repo or
             `edit_fork_local_path`) — works for any provider as long as the
             user has committed at least once and the bare repo is cached.
          2. `provider.get_file_blame()` — the provider's own implementation
             (only LocalGit currently). Returns `supported=False` otherwise.
        """
        try:
            project_key = request.query_params.get('project_key')
            repo_slug = request.query_params.get('repo_slug')
            file_path = request.query_params.get('file_path')
            branch = request.query_params.get('branch', 'main')
            space_id = request.query_params.get('space_id')

            if not project_key or not repo_slug or not file_path:
                return Response(
                    {
                        'error': 'project_key, repo_slug, and file_path are required',
                        'code': 'MISSING_PARAMETERS',
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            blame: list = []
            provider_type = ''

            # Path 1 — local clone backed by GitWorktreeManager. Only available
            # once the bare repo has been cached (first commit or fetch).
            local_blame_used = False
            if space_id:
                local = self._blame_from_local_clone(space_id, file_path, branch, request.user)
                if local is not None:
                    blame = local
                    local_blame_used = True
                    provider_type = 'local_clone'

            # Path 2 — defer to the upstream provider when we have no clone.
            if not local_blame_used:
                provider = self._get_provider(request)
                provider_type = provider.provider_type
                blame = provider.get_file_blame(
                    project_key, repo_slug, file_path, branch,
                )

            supported = local_blame_used or bool(blame)
            return Response({
                'lines': blame,
                'supported': supported,
                'provider': provider_type,
            })
        except ValueError as e:
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return self._handle_provider_error(e, 'Error getting file blame')

    @staticmethod
    def _blame_from_local_clone(space_id: str, file_path: str, branch: str, user=None):
        """Try to blame against a locally-cached bare clone of the space.

        Resolution order — first one that exists / can be created wins:
          1. `space.edit_fork_local_path` (filesystem path the user configured),
          2. The worktree-manager edit-fork bare clone (created on first commit),
          3. Lazy bare clone of `space.edit_fork_ssh_url` (SSH-key auth),
          4. Lazy bare clone of `space.git_repository_url` over HTTPS using the
             user's saved git ServiceToken — gives blame to remote-only flows
             (GitHub / Bitbucket Cloud) before any commit has been made.

        Returns parsed blame lines on success, None if no usable clone could
        be obtained. Any git failure (file not in branch, etc.) is raised as
        ValueError so the view returns 400.
        """
        from wiki.models import Space
        from git_provider.worktree_manager import GitWorktreeManager
        from git_provider.providers.local_git import LocalGitProvider as _LG
        import logging
        import subprocess

        log = logging.getLogger(__name__)

        try:
            if user is None:
                return None
            space = accessible_spaces_for_user(user).get(id=space_id)
        except (Space.DoesNotExist, ValueError):
            return None

        manager = GitWorktreeManager()
        local_path = None

        if space.edit_fork_local_path and os.path.exists(space.edit_fork_local_path):
            local_path = space.edit_fork_local_path
        else:
            edit_bare = manager.get_bare_repo_path(str(space.id))
            if os.path.exists(edit_bare):
                local_path = edit_bare
            elif space.edit_fork_ssh_url:
                # First-blame for a space that hasn't seen a commit yet —
                # ensure_bare_repo_sync clones the fork over SSH.
                try:
                    local_path = manager.ensure_bare_repo_sync(
                        str(space.id), space.edit_fork_ssh_url,
                    )
                except Exception as e:
                    log.warning(
                        f"[Blame] Lazy SSH clone failed for space {space_id}: {e}"
                    )
                    local_path = None

            # Final fallback — clone the upstream over HTTPS using the user's
            # active credential tuple. This prevents reuse across users and
            # token rotation/revocation.
            if not local_path and space.git_repository_url:
                credentials = _BlameClone.resolve_https_credentials(space, user)
                blame_path = _BlameClone.cache_path(manager, space, user) if credentials else None
                if blame_path and os.path.exists(blame_path):
                    try:
                        env, askpass_path = _BlameClone.build_git_env(manager, credentials)
                        try:
                            subprocess.run(
                                ['git', 'fetch', '--all', '--prune'],
                                cwd=blame_path,
                                env=env,
                                capture_output=True,
                                text=True,
                                check=True,
                                timeout=180,
                            )
                        finally:
                            try:
                                os.unlink(askpass_path)
                            except OSError:
                                pass
                    except Exception as e:
                        log.warning(
                            f"[Blame] Refresh of {blame_path} failed: {e}"
                        )
                        local_path = None
                    else:
                        local_path = blame_path
                else:
                    if credentials and blame_path:
                        try:
                            local_path = _BlameClone.clone_bare_repo(
                                manager,
                                credentials,
                                blame_path,
                            )
                        except Exception as e:
                            log.warning(
                                f"[Blame] HTTPS clone for {space_id} failed: {e}"
                            )

        if not local_path or not os.path.exists(local_path):
            return None

        try:
            output = subprocess.run(
                ['git', '-C', local_path, 'blame', '--line-porcelain', branch, '--', file_path],
                capture_output=True,
                text=True,
                check=True,
                env=manager._get_git_env(),
            ).stdout
        except subprocess.CalledProcessError as e:
            raise ValueError(
                f"Could not blame {file_path} in {branch}: {e.stderr.strip()}"
            )

        return _LG._parse_blame_porcelain(output)

    @extend_schema(
        operation_id='git_provider_tree_get',
        summary='Get directory tree',
        description='Get directory tree for a repository.',
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='project_key', type=str, required=True, description='Project key (for Bitbucket Server) or owner (for GitHub)'),
            OpenApiParameter(name='repo_slug', type=str, required=True, description='Repository slug/name'),
            OpenApiParameter(name='path', type=str, required=False),
            OpenApiParameter(name='branch', type=str, required=False),
            OpenApiParameter(name='recursive', type=bool, required=False),
        ],
        responses={200: TreeEntrySerializer(many=True)},
        tags=['git-provider'],
    )
    @cached_api_response(
        provider_type_param='provider',
        endpoint_func=lambda view, **kwargs: '/tree'
    )
    @action(detail=False, methods=['get'], url_path='tree')
    def get_tree(self, request):
        """Get directory tree."""
        try:
            provider = self._get_provider(request)
            project_key = request.query_params.get('project_key')
            repo_slug = request.query_params.get('repo_slug')
            path = request.query_params.get('path', '')
            branch = request.query_params.get('branch', 'main')
            recursive = request.query_params.get('recursive', 'false').lower() == 'true'
            
            if not project_key or not repo_slug:
                return Response(
                    {'error': 'project_key and repo_slug are required', 'code': 'MISSING_PARAMETERS'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            tree = provider.get_directory_tree(project_key, repo_slug, path, branch, recursive)
            return Response(tree)
        except ValueError as e:
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return self._handle_provider_error(e, 'Error getting directory tree')

    @extend_schema(
        operation_id='git_provider_pull_requests_list',
        summary='List pull requests',
        description='List pull requests for a repository.',
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='repo_id', type=str, required=True, location=OpenApiParameter.PATH),
            OpenApiParameter(name='state', type=str, required=False, description='PR state (open, closed, merged)'),
            OpenApiParameter(name='page', type=int, required=False),
            OpenApiParameter(name='per_page', type=int, required=False),
            OpenApiParameter(name='reviewer', type=str, required=False, description='Filter by reviewer username'),
        ],
        responses={200: PullRequestSerializer(many=True)},
        tags=['git-provider'],
    )
    @action(detail=True, methods=['get'], url_path='pull-requests')
    def list_pull_requests(self, request, pk=None):
        """List pull requests."""
        try:
            provider = self._get_provider(request)
            state = request.query_params.get('state', 'open')
            page = int(request.query_params.get('page', 1))
            per_page = int(request.query_params.get('per_page', 30))
            reviewer = request.query_params.get('reviewer', None)
            
            result = provider.list_pull_requests(pk, state, page, per_page, reviewer=reviewer)
            return Response(result)
        except ValueError as e:
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @extend_schema(
        operation_id='git_provider_pull_request_get',
        summary='Get pull request details',
        description='Get details of a specific pull request.',
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='repo_id', type=str, required=True, location=OpenApiParameter.PATH),
            OpenApiParameter(name='number', type=int, required=True, location=OpenApiParameter.PATH),
        ],
        responses={200: PullRequestSerializer},
        tags=['git-provider'],
    )
    @action(detail=True, methods=['get'], url_path='pull-requests/(?P<number>[0-9]+)')
    def get_pull_request(self, request, pk=None, number=None):
        """Get pull request details."""
        try:
            provider = self._get_provider(request)
            pr = provider.get_pull_request(pk, int(number))
            return Response(pr)
        except ValueError as e:
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @extend_schema(
        operation_id='git_provider_pull_request_diff_get',
        summary='Get pull request diff',
        description='Get diff for a pull request.',
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='repo_id', type=str, required=True, location=OpenApiParameter.PATH),
            OpenApiParameter(name='number', type=int, required=True, location=OpenApiParameter.PATH),
        ],
        responses={200: str},
        tags=['git-provider'],
    )
    @action(detail=True, methods=['get'], url_path='pull-requests/(?P<number>[0-9]+)/diff')
    def get_pull_request_diff(self, request, pk=None, number=None):
        """Get pull request diff."""
        try:
            provider = self._get_provider(request)
            diff = provider.get_pull_request_diff(pk, int(number))
            return Response({'diff': diff})
        except ValueError as e:
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST
            )
    
    @extend_schema(
        operation_id='git_provider_commits_list',
        summary='List commits',
        description='List commits for a repository.',
        parameters=[
            OpenApiParameter(name='provider', type=str, required=True),
            OpenApiParameter(name='base_url', type=str, required=True),
            OpenApiParameter(name='repo_id', type=str, required=True, location=OpenApiParameter.PATH),
            OpenApiParameter(name='branch', type=str, required=False),
            OpenApiParameter(name='page', type=int, required=False),
            OpenApiParameter(name='per_page', type=int, required=False),
        ],
        responses={200: CommitSerializer(many=True)},
        tags=['git-provider'],
    )
    @action(detail=True, methods=['get'], url_path='commits')
    def list_commits(self, request, pk=None):
        """List commits."""
        try:
            provider = self._get_provider(request)
            branch = request.query_params.get('branch', 'main')
            page = int(request.query_params.get('page', 1))
            per_page = int(request.query_params.get('per_page', 30))
            
            result = provider.list_commits(pk, branch, page, per_page)
            return Response(result)
        except ValueError as e:
            return Response(
                {'error': str(e), 'code': 'INVALID_REQUEST'},
                status=status.HTTP_400_BAD_REQUEST
            )
