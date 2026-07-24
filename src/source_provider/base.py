"""
Universal source addressing and base provider interface.
"""
from typing import Dict, Any, Optional, List
from dataclasses import dataclass
import re
from urllib.parse import parse_qs, quote, unquote

_LIKELY_GITHUB_BRANCH_NAMES = {
    'main', 'master', 'develop', 'development', 'dev', 'staging',
    'stage', 'production', 'prod', 'release', 'hotfix', 'fix',
    'bugfix', 'trunk', 'qa', 'test',
}


@dataclass
class SourceAddress:
    """
    Universal source address for files and content.
    
    Format: git://{provider}/{repo}/{branch}/{path}#{line_start}-{line_end}
    
    Examples:
        - git://github/facebook_react/main/README.md
        - git://github/facebook_react/main/src/index.js#10-20
        - git://bitbucket_server/PROJECT_repo/main/docs/api.md#5
    """
    provider: str  # 'github' or 'bitbucket_server'
    repository: str  # Repository identifier (e.g., 'owner_repo' or 'projectkey_reposlug')
    branch: str  # Branch name
    path: str  # File path within repository
    base_url: Optional[str] = None  # Optional provider base URL context
    line_start: Optional[int] = None  # Starting line number (1-indexed)
    line_end: Optional[int] = None  # Ending line number (1-indexed, inclusive)

    @staticmethod
    def _looks_like_legacy_canonical_github_branch(value: str) -> bool:
        """Heuristic for pre-query `owner/repo/branch/path` GitHub URIs."""
        if not value:
            return False

        lowered = value.lower()
        if lowered in _LIKELY_GITHUB_BRANCH_NAMES:
            return True

        return lowered.startswith((
            'release-', 'hotfix-', 'bugfix-', 'feature-', 'feat-',
            'fix-', 'chore-', 'docs-', 'refactor-', 'test-',
        ))

    @staticmethod
    def _looks_like_arbitrary_legacy_canonical_github_uri(parts: List[str]) -> bool:
        """
        Safely detect persisted pre-query `owner/repo/branch/path` GitHub URIs.

        Requiring an extra path segment preserves compatibility with the legacy
        `repo/branch/path` form, while still rescuing the stored canonical
        `owner/repo/arbitrary-branch/nested/path` shape.
        """
        return len(parts) >= 6

    @staticmethod
    def _parse_line_fragment(uri: str) -> tuple[str, Optional[int], Optional[int]]:
        """Split `#start[-end]` from a source URI."""
        line_start = None
        line_end = None
        base_uri, separator, line_fragment = uri.partition('#')
        if separator:
            line_match = re.match(r'^(\d+)(?:-(\d+))?$', line_fragment)
            if not line_match:
                raise ValueError(f"Invalid source URI format: {uri}")
            line_start = int(line_match.group(1))
            line_end = int(line_match.group(2)) if line_match.group(2) else None
        return base_uri, line_start, line_end

    @classmethod
    def parse_for_github_context(
        cls,
        uri: str,
        *,
        repository: Optional[str] = None,
        branch: Optional[str] = None,
    ) -> 'SourceAddress':
        """
        Parse a GitHub URI using known repository/branch context when needed.

        This is the safe compatibility path for persisted pre-query canonical
        URIs of the form `git://github/{owner}/{repo}/{branch}/{path}` where the
        branch name is not distinguishable from the legacy
        `git://github/{repo}/{branch}/{path}` format without external context.
        """
        address = cls.parse(uri)
        if address.provider != 'github' or not repository or not branch:
            return address
        if address.repository == repository and address.branch == branch:
            return address

        owner, sep, repo = repository.partition('/')
        if not sep or '/' in branch:
            return address

        base_uri, line_start, line_end = cls._parse_line_fragment(uri)
        prefix = f'git://github/{owner}/{repo}/{branch}'
        if base_uri == prefix:
            path = ''
        elif base_uri.startswith(f'{prefix}/'):
            path = base_uri[len(prefix) + 1:]
        else:
            return address

        return cls(
            provider='github',
            repository=repository,
            branch=branch,
            path=path,
            line_start=line_start,
            line_end=line_end,
        )

    def equivalent_comment_uris(self) -> List[str]:
        """Return source URI variants that should be treated as the same file."""
        variants = [self.to_uri()]
        if self.provider == 'github' and '/' in self.repository:
            owner, repo = self.repository.split('/', 1)
            if '/' not in self.branch:
                legacy_uri = f"git://github/{owner}/{repo}/{self.branch}"
                if self.path:
                    legacy_uri += f"/{self.path}"
                variants.append(legacy_uri)
            underscore_uri = f"git://github/{owner}_{repo}/{self.branch}"
            if self.path:
                underscore_uri += f"/{self.path}"
            variants.append(underscore_uri)
        return list(dict.fromkeys(variants))
    
    @classmethod
    def parse(cls, uri: str) -> 'SourceAddress':
        """
        Parse a source URI into a SourceAddress.
        
        Args:
            uri: Source URI string
        
        Returns:
            SourceAddress instance
        
        Raises:
            ValueError: If URI format is invalid
        """
        base_uri, line_start, line_end = cls._parse_line_fragment(uri)

        prefix = 'git://'
        if not base_uri.startswith(prefix):
            raise ValueError(f"Invalid source URI format: {uri}")

        path_and_query = base_uri[len(prefix):]
        path_part, query_separator, query_string = path_and_query.partition('?')
        parts = path_part.split('/')
        if len(parts) < 3:
            raise ValueError(f"Invalid source URI format: {uri}")

        query = parse_qs(query_string, keep_blank_values=True) if query_separator else {}
        provider = parts[0]
        base_url = query.get('base_url', [None])[0] if query else None
        if provider == 'github' and len(parts) >= 3:
            if 'ref' in query:
                repository = '/'.join(parts[1:3])
                branch = unquote(query['ref'][0])
                path = '/'.join(parts[3:])
            elif cls._looks_like_arbitrary_legacy_canonical_github_uri(parts):
                repository = '/'.join(parts[1:3])
                branch = parts[3]
                path = '/'.join(parts[4:])
            elif len(parts) >= 4 and cls._looks_like_legacy_canonical_github_branch(parts[3]):
                repository = '/'.join(parts[1:3])
                branch = parts[3]
                path = '/'.join(parts[4:])
            else:
                repository = parts[1]
                branch = parts[2]
                path = '/'.join(parts[3:])
        else:
            if len(parts) < 3:
                raise ValueError(f"Invalid source URI format: {uri}")
            repository = parts[1]
            branch = parts[2]
            path = '/'.join(parts[3:])

        if not repository or not branch:
            raise ValueError(f"Invalid source URI format: {uri}")
        if provider == 'github' and '/' not in repository and not path:
            raise ValueError(f"Invalid source URI format: {uri}")
        
        return cls(
            provider=provider,
            repository=repository,
            branch=branch,
            path=path,
            base_url=base_url,
            line_start=line_start,
            line_end=line_end
        )
    
    def to_uri(self) -> str:
        """
        Convert SourceAddress to URI string.
        
        Returns:
            URI string
        """
        query_params = []
        if self.provider == 'github' and '/' in self.repository:
            owner, repo = self.repository.split('/', 1)
            uri = f"git://{self.provider}/{owner}/{repo}"
            if self.path:
                uri += f"/{self.path}"
            query_params.append(f"ref={quote(self.branch, safe='')}")
        else:
            uri = f"git://{self.provider}/{self.repository}/{self.branch}"
            if self.path:
                uri += f"/{self.path}"

        if self.base_url:
            query_params.append(f"base_url={quote(self.base_url, safe='')}")
        if query_params:
            uri += '?' + '&'.join(query_params)
        
        if self.line_start is not None:
            if self.line_end is not None and self.line_end != self.line_start:
                uri += f"#{self.line_start}-{self.line_end}"
            else:
                uri += f"#{self.line_start}"
        
        return uri
    
    def __str__(self) -> str:
        return self.to_uri()


class BaseSourceProvider:
    """
    Base interface for source content providers.
    """
    
    def get_content(self, address: SourceAddress) -> Dict[str, Any]:
        """
        Get content from a source address.
        
        Args:
            address: SourceAddress instance
        
        Returns:
            Dict with content, encoding, metadata
        """
        raise NotImplementedError
    
    def get_tree(self, address: SourceAddress, recursive: bool = False) -> List[Dict[str, Any]]:
        """
        Get directory tree from a source address.
        
        Args:
            address: SourceAddress instance (path should be directory)
            recursive: Whether to recursively list all files
        
        Returns:
            List of tree entries
        """
        raise NotImplementedError
