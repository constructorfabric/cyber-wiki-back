"""
Factory for creating Git provider instances.
"""
from typing import Optional
from .base import BaseGitProvider
from .providers.github import GitHubProvider
from .providers.bitbucket_server import BitbucketServerProvider
from .providers.local_git import LocalGitProvider
from service_tokens.models import ServiceType, ServiceToken
from service_tokens.url import canonical_base_url, normalize_service_base_url, service_base_url_lookup_candidates


class GitProviderFactory:
    """
    Factory class for creating Git provider instances.
    """

    @staticmethod
    def normalize_base_url(provider: str, base_url: str) -> str:
        """Normalize provider base URLs before lookup/provider construction."""
        return normalize_service_base_url(provider, base_url)

    @staticmethod
    def _get_single_matching_token(queryset, provider: str, candidates: list[str]):
        """Return one exact-match token or fail closed on semantic duplicates."""
        if not candidates:
            return None

        matches = list(
            queryset.filter(base_url__in=candidates).order_by('pk')
        )
        if not matches:
            return None
        if len(matches) > 1:
            raise ValueError(f'Ambiguous service token configuration for provider: {provider}')
        return matches[0]

    @staticmethod
    def get_service_token(user, provider: str, base_url: Optional[str] = None):
        """Look up the matching provider token using provider-aware semantics."""
        queryset = ServiceToken.objects.filter(user=user, service_type=provider)
        candidates = service_base_url_lookup_candidates(provider, base_url)
        return GitProviderFactory._get_single_matching_token(queryset, provider, candidates)

    @staticmethod
    def get_source_service_token(user, provider: str, base_url: Optional[str] = None):
        """
        Resolve the sole usable token for source URIs that do not include a base URL.

        Fail closed when credentials are missing or ambiguous instead of selecting an
        arbitrary token row.
        """
        if base_url:
            service_token = GitProviderFactory.get_service_token(
                user=user,
                provider=provider,
                base_url=base_url,
            )
            if service_token is not None:
                return service_token
            raise ValueError(f'No service token found for provider: {provider}')

        queryset = ServiceToken.objects.filter(user=user, service_type=provider).order_by('base_url', 'pk')
        count = queryset.count()
        if count == 1:
            return queryset.first()
        if count == 0:
            raise ValueError(f'No service token found for provider: {provider}')
        raise ValueError(f'Ambiguous service token configuration for provider: {provider}')

    @staticmethod
    def default_branch_fallback(provider: str) -> str:
        """Return the provider-aware default branch fallback."""
        return 'main' if provider == ServiceType.GITHUB else 'master'

    @staticmethod
    def get_custom_header_token(user, base_url: Optional[str] = None):
        """Return the preferred custom-header token for the user/base URL."""
        queryset = ServiceToken.objects.filter(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
        )
        candidates = []
        canonical = canonical_base_url(base_url)
        if canonical:
            candidates.append(canonical)
        candidates.append('')

        for candidate in candidates:
            service_token = queryset.filter(base_url=candidate).order_by('pk').first()
            if service_token is not None:
                return service_token
        return None

    @staticmethod
    def get_repository_coordinates(
        provider: str,
        project_key: Optional[str],
        repository_id: Optional[str],
        repository_name: Optional[str] = None,
    ) -> tuple[str, str]:
        """Resolve provider-aware repository coordinates for provider calls."""
        repo_identifier = repository_id or repository_name or ''
        if provider == ServiceType.GITHUB:
            return GitHubProvider.split_repository_coordinates(project_key or '', repo_identifier)
        return project_key or '', repo_identifier

    @staticmethod
    def build_repository_identity(provider: str, project_key: Optional[str], repository_id: Optional[str]) -> str:
        """Build the canonical repository identity used by reviewed paths."""
        if provider == ServiceType.GITHUB:
            owner, repo = GitHubProvider.split_repository_coordinates(project_key or '', repository_id or '')
            return GitHubProvider.build_repo_id(owner, repo)
        if project_key and repository_id:
            return f'{project_key}_{repository_id}'
        return repository_id or ''
    
    @staticmethod
    def create(provider: str, base_url: str, token: str, username: Optional[str] = None, custom_header: Optional[str] = None, custom_header_token: Optional[str] = None, user=None) -> BaseGitProvider:
        """
        Create a Git provider instance.
        
        Args:
            provider: Provider type ('github' or 'bitbucket_server')
            base_url: Base URL for the provider API
            token: Access token
            username: Username (required for Bitbucket Server)
            custom_header: Custom header name for authentication (e.g., 'X-Custom-Token')
            custom_header_token: Token value for custom header
            user: Django user instance for caching (optional)
        
        Returns:
            BaseGitProvider instance
        
        Raises:
            ValueError: If provider type is not supported
        """
        if provider == ServiceType.GITHUB:
            return GitHubProvider(
                base_url=GitProviderFactory.normalize_base_url(provider, base_url),
                token=token,
                username=username,
                user=user,
            )
        elif provider == ServiceType.BITBUCKET_SERVER:
            if not username:
                raise ValueError("Username is required for Bitbucket Server")
            return BitbucketServerProvider(base_url=base_url, token=token, username=username, custom_header=custom_header, custom_header_token=custom_header_token, user=user)
        elif provider == 'local_git':
            # For local Git, base_url is the filesystem path
            return LocalGitProvider(base_path=base_url, token=token, username=username, user=user)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    
    @staticmethod
    def create_from_service_token(service_token):
        """
        Create a Git provider instance from a ServiceToken model.
        
        Args:
            service_token: ServiceToken model instance
        
        Returns:
            BaseGitProvider instance
        """
        import logging
        logger = logging.getLogger(__name__)

        custom_header = None
        custom_header_name = None
        
        # For Bitbucket Server, also fetch custom header token if available
        if service_token.service_type == ServiceType.BITBUCKET_SERVER:
            logger.info(f"Looking for custom header token for user {service_token.user.username}")
            try:
                custom_token = GitProviderFactory.get_custom_header_token(
                    service_token.user,
                    base_url=service_token.base_url,
                )
                if custom_token:
                    custom_header = custom_token.get_token()
                    custom_header_name = custom_token.header_name
                    logger.info(f"Using custom header token: {custom_header_name} (base_url: '{custom_token.base_url}', token length: {len(custom_header) if custom_header else 0})")
                else:
                    logger.warning(f"No custom header token found for user {service_token.user.username}")
            except Exception as e:
                logger.error(f"Error fetching custom header token: {e}", exc_info=True)
                pass  # Custom header token not configured, continue without it
        
        return GitProviderFactory.create(
            provider=service_token.service_type,
            base_url=GitProviderFactory.normalize_base_url(service_token.service_type, service_token.base_url),
            token=service_token.get_token(),
            username=service_token.get_username(),
            custom_header=custom_header_name,
            custom_header_token=custom_header,
            user=service_token.user  # Pass user for caching
        )
