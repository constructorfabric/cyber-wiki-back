"""
Abstract base class for Git provider implementations.
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional


class BaseGitProvider(ABC):
    """
    Abstract interface for Git provider implementations.
    
    All Git providers (GitHub, Bitbucket Server, etc.) must implement this interface.
    """
    
    def __init__(self, base_url: str, token: str, username: Optional[str] = None, user=None):
        """
        Initialize the Git provider.
        
        Args:
            base_url: Base URL for the Git provider API
            token: Access token for authentication
            username: Username (required for some providers like Bitbucket Server)
            user: Django user instance for caching (optional)
        """
        self.base_url = base_url
        self.token = token
        self.username = username
        self.user = user  # Store user for caching
    
    @property
    def capabilities(self) -> Dict[str, bool]:
        """
        Report which features this provider supports.
        
        Returns:
            Dict mapping feature names to support status (True/False)
        """
        return {
            'list_repositories': True,
            'get_repository': True,
            'get_file_content': True,
            'get_directory_tree': True,
            'list_pull_requests': True,
            'get_pull_request': True,
            'get_pull_request_diff': True,
            'list_commits': True,
            'create_commit': True,
            'requires_authentication': True,
            'supports_webhooks': False,
            'supports_projects': False,
            # Edit workflow capabilities
            'list_branches': True,
            'create_branch': True,
            'delete_branch': True,
            'create_pull_request': True,
            'get_pull_request_status': True,
        }
    
    @property
    def provider_type(self) -> str:
        """
        Return the provider type identifier.
        
        Returns:
            Provider type string (e.g., 'github', 'bitbucket_server', 'local_git')
        """
        return 'unknown'
    
    @abstractmethod
    def list_repositories(self, page: int = 1, per_page: int = 30) -> Dict[str, Any]:
        """
        List repositories accessible to the authenticated user.
        
        Args:
            page: Page number (1-indexed)
            per_page: Number of repositories per page
        
        Returns:
            Dict with 'repositories' list and pagination metadata
        """
        pass
    
    @abstractmethod
    def get_repository(self, repo_id: str) -> Dict[str, Any]:
        """
        Get details of a specific repository.
        
        Args:
            repo_id: Repository identifier (format varies by provider)
        
        Returns:
            Repository details
        """
        pass
    
    @abstractmethod
    def get_file_content(self, project_key: str, repo_slug: str, file_path: str, branch: str = 'main') -> Dict[str, Any]:
        """
        Get content of a specific file.

        Args:
            project_key: Project key (for Bitbucket) or owner (for GitHub)
            repo_slug: Repository slug/name
            file_path: Path to the file within the repository
            branch: Branch name (default: 'main')

        Returns:
            Dict with 'content', 'encoding', 'sha', etc.
        """
        pass

    def get_file_blame(
        self,
        project_key: str,
        repo_slug: str,
        file_path: str,
        branch: str = 'main',
    ) -> List[Dict[str, Any]]:
        """Per-line blame for `file_path` at `branch`.

        Optional capability — only providers with direct git access (LocalGit,
        worktree-backed setups) override this. Remote-only providers return
        an empty list and the caller surfaces a friendly "blame not supported
        for this provider" message.

        Returns a list of dicts, one per line, in source order:
          {
            'line_no':       1-based line number in the current file,
            'content':       text of the line (no trailing newline),
            'commit_sha':    abbreviated/long sha of the commit that
                              introduced this version of the line,
            'author_name':   author display name,
            'author_email':  author email (best-effort),
            'author_date':   ISO-8601 timestamp,
            'summary':       commit message subject (single line),
          }
        """
        return []
    
    @abstractmethod
    def get_directory_tree(self, project_key: str, repo_slug: str, path: str = '', branch: str = 'main', recursive: bool = False) -> List[Dict[str, Any]]:
        """
        Get directory tree/listing.
        
        Args:
            project_key: Project key (for Bitbucket) or owner (for GitHub)
            repo_slug: Repository slug/name
            path: Directory path (empty for root)
            branch: Branch name
            recursive: Whether to recursively list all files
        
        Returns:
            List of file/directory entries
        """
        pass
    
    @abstractmethod
    def list_pull_requests(self, repo_id: str, state: str = 'open', page: int = 1, per_page: int = 30, reviewer: Optional[str] = None) -> Dict[str, Any]:
        """
        List pull requests for a repository.
        
        Args:
            repo_id: Repository identifier
            state: PR state ('open', 'closed', 'merged', 'all')
            page: Page number
            per_page: Number of PRs per page
            reviewer: Filter by reviewer username
        
        Returns:
            Dict with 'pull_requests' list and pagination metadata
        """
        pass
    
    @abstractmethod
    def get_pull_request(self, repo_id: str, pr_number: int) -> Dict[str, Any]:
        """
        Get details of a specific pull request.
        
        Args:
            repo_id: Repository identifier
            pr_number: Pull request number
        
        Returns:
            Pull request details
        """
        pass
    
    @abstractmethod
    def get_pull_request_diff(self, repo_id: str, pr_number: int) -> str:
        """
        Get diff for a pull request.
        
        Args:
            repo_id: Repository identifier
            pr_number: Pull request number
        
        Returns:
            Diff content as string
        """
        pass
    
    @abstractmethod
    def list_commits(self, repo_id: str, branch: str = 'main', page: int = 1, per_page: int = 30) -> Dict[str, Any]:
        """
        List commits for a repository.
        
        Args:
            repo_id: Repository identifier
            branch: Branch name
            page: Page number
            per_page: Number of commits per page
        
        Returns:
            Dict with 'commits' list and pagination metadata
        """
        pass
    
    @abstractmethod
    def create_commit(self, repo_id: str, branch: str, message: str, files: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        Create a new commit with file changes.
        
        Args:
            repo_id: Repository identifier
            branch: Branch name
            message: Commit message
            files: List of file changes [{'path': '...', 'content': '...', 'action': 'create|update|delete'}]
        
        Returns:
            Commit details
        """
        pass
    
    def get_pr_comment_authors(self, repo_id: str, pr_number: int) -> List[str]:
        """Return a list of comment author usernames for a pull request.

        Each entry corresponds to one comment — the same author may appear
        multiple times.  Providers override this to supply real data; the
        default returns an empty list (meaning "not available").
        """
        return []

    def normalize_repository_id(self, repo_data: Dict[str, Any]) -> str:
        """
        Normalize repository identifier across providers.
        
        Args:
            repo_data: Repository data from provider
        
        Returns:
            Normalized repository ID (e.g., "owner_repo" or "projectkey_reposlug")
        """
        # Default implementation - can be overridden
        return repo_data.get('id', '')
    
    # Edit workflow methods (optional - not all providers support these)
    
    def list_branches(
        self,
        project_key: str,
        repo_slug: str,
        filter_text: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        List branches in a repository.
        
        Args:
            project_key: Project key
            repo_slug: Repository slug
            filter_text: Optional filter for branch names
            
        Returns:
            List of branch info dicts
        """
        raise NotImplementedError("list_branches not implemented for this provider")
    
    def create_branch(
        self,
        project_key: str,
        repo_slug: str,
        branch_name: str,
        start_point: str = 'master',
    ) -> Dict[str, Any]:
        """
        Create a new branch.
        
        Args:
            project_key: Project key
            repo_slug: Repository slug
            branch_name: Name for the new branch
            start_point: Branch or commit to branch from
            
        Returns:
            Branch info dict
        """
        raise NotImplementedError("create_branch not implemented for this provider")
    
    def delete_branch(
        self,
        project_key: str,
        repo_slug: str,
        branch_name: str,
    ) -> bool:
        """
        Delete a branch.
        
        Args:
            project_key: Project key
            repo_slug: Repository slug
            branch_name: Branch to delete
            
        Returns:
            True if deleted successfully
        """
        raise NotImplementedError("delete_branch not implemented for this provider")
    
    def create_pull_request(
        self,
        from_project: str,
        from_repo: str,
        from_branch: str,
        to_project: str,
        to_repo: str,
        to_branch: str,
        title: str,
        description: str = '',
    ) -> Dict[str, Any]:
        """
        Create a pull request.
        
        Args:
            from_project: Source project key
            from_repo: Source repository slug
            from_branch: Source branch
            to_project: Target project key
            to_repo: Target repository slug
            to_branch: Target branch
            title: PR title
            description: PR description
            
        Returns:
            PR info dict with 'id', 'url', etc.
        """
        raise NotImplementedError("create_pull_request not implemented for this provider")
    
    def get_pull_request_status(
        self,
        project_key: str,
        repo_slug: str,
        pr_id: int,
    ) -> str:
        """
        Get the status of a pull request.
        
        Args:
            project_key: Project key
            repo_slug: Repository slug
            pr_id: Pull request ID
            
        Returns:
            Status string (e.g., 'OPEN', 'MERGED', 'DECLINED')
        """
        raise NotImplementedError("get_pull_request_status not implemented for this provider")
