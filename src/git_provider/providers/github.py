"""
GitHub provider implementation.
"""
import requests
from typing import List, Dict, Any, Optional
from ..base import BaseGitProvider


class GitHubProvider(BaseGitProvider):
    """
    GitHub REST API v3 implementation.
    
    Documentation: https://docs.github.com/en/rest
    """
    
    def __init__(self, base_url: str = 'https://api.github.com', token: str = '', username: Optional[str] = None, user=None):
        super().__init__(base_url, token, username, user)
        self.headers = {
            'Authorization': f'Bearer {token}',
            'Accept': 'application/vnd.github.v3+json',
        }
    
    @property
    def capabilities(self) -> Dict[str, bool]:
        """
        Report which features this provider supports.
        GitHub has full API support.
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
            'supports_webhooks': True,
            'supports_projects': False,  # GitHub uses orgs, not projects
            'supports_organizations': True,  # Unique to GitHub
            'supports_actions': True,  # Unique to GitHub
        }
    
    @property
    def provider_type(self) -> str:
        """Return the provider type identifier."""
        return 'github'
    
    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """Make HTTP request to GitHub API."""
        url = f"{self.base_url}{endpoint}"
        response = requests.request(method, url, headers=self.headers, **kwargs)
        response.raise_for_status()
        return response
    
    def list_repositories(self, page: int = 1, per_page: int = 30) -> Dict[str, Any]:
        """List repositories accessible to the authenticated user."""
        response = self._request('GET', '/user/repos', params={
            'page': page,
            'per_page': per_page,
            'sort': 'updated',
            'affiliation': 'owner,collaborator,organization_member'
        })
        
        repos = response.json()
        return {
            'repositories': [self._normalize_repo(repo) for repo in repos],
            'page': page,
            'per_page': per_page,
            'total': len(repos),  # GitHub doesn't provide total count easily
        }
    
    def get_repository(self, repo_id: str) -> Dict[str, Any]:
        """Get repository details. repo_id format: 'owner/repo'"""
        response = self._request('GET', f'/repos/{repo_id}')
        return self._normalize_repo(response.json())
    
    def get_file_content(self, project_key: str, repo_slug: str, file_path: str, branch: str = 'main') -> Dict[str, Any]:
        """Get file content from repository."""
        repo_id = f"{project_key}/{repo_slug}"
        response = self._request('GET', f'/repos/{repo_id}/contents/{file_path}', params={'ref': branch})
        data = response.json()
        
        return {
            'content': data.get('content', ''),
            'encoding': data.get('encoding', 'base64'),
            'sha': data.get('sha', ''),
            'size': data.get('size', 0),
            'path': data.get('path', file_path),
        }
    
    def get_directory_tree(self, project_key: str, repo_slug: str, path: str = '', branch: str = 'main', recursive: bool = False) -> List[Dict[str, Any]]:
        """Get directory tree."""
        repo_id = f"{project_key}/{repo_slug}"
        if recursive:
            # Use Git Trees API for recursive listing
            response = self._request('GET', f'/repos/{repo_id}/git/trees/{branch}', params={'recursive': '1'})
            tree = response.json().get('tree', [])
            return [self._normalize_tree_entry(entry) for entry in tree]
        else:
            # Use Contents API for single directory
            endpoint = f'/repos/{repo_id}/contents/{path}' if path else f'/repos/{repo_id}/contents'
            response = self._request('GET', endpoint, params={'ref': branch})
            contents = response.json()
            
            if isinstance(contents, list):
                return [self._normalize_tree_entry(entry) for entry in contents]
            else:
                return [self._normalize_tree_entry(contents)]
    
    def list_pull_requests(self, repo_id: str, state: str = 'open', page: int = 1, per_page: int = 30, reviewer: Optional[str] = None) -> Dict[str, Any]:
        """List pull requests.

        ``reviewer`` filtering is applied **client-side over the current page only**.
        GitHub's `/repos/.../pulls` endpoint has no native reviewer filter, and
        ``requested_reviewers`` only lists reviewers who have not yet submitted a
        review (after approve/request-changes the reviewer is removed from that
        array). For an authoritative cross-page listing of "PRs awaiting my
        review", callers should use the search API
        (``/search/issues?q=is:pr+is:open+review-requested:<user>``) instead.
        """
        response = self._request('GET', f'/repos/{repo_id}/pulls', params={
            'state': state if state != 'merged' else 'closed',
            'page': page,
            'per_page': per_page,
        })

        prs = response.json()
        normalized = [self._normalize_pr(pr) for pr in prs]

        # The /pulls list endpoint doesn't populate comment counts.
        # Enrich from /issues which returns PRs with a `comments` field.
        if normalized:
            try:
                issues_resp = self._request('GET', f'/repos/{repo_id}/issues', params={
                    'state': state if state != 'merged' else 'closed',
                    'per_page': per_page,
                    'page': page,
                })
                comment_map = {
                    issue['number']: issue.get('comments', 0)
                    for issue in issues_resp.json()
                    if 'pull_request' in issue
                }
                for pr in normalized:
                    pr['comment_count'] = comment_map.get(pr['number'], pr.get('comment_count', 0))
            except Exception:
                pass  # Non-critical — keep whatever _normalize_pr produced

        # The list endpoint never includes `mergeable`; fetch from the detail
        # endpoint concurrently so we can populate merge_status.
        if normalized:
            from concurrent.futures import ThreadPoolExecutor, as_completed

            def _fetch_merge_status(pr):
                try:
                    detail = self._request('GET', f'/repos/{repo_id}/pulls/{pr["number"]}').json()
                    return pr['number'], detail.get('mergeable'), detail.get('draft', False)
                except Exception:
                    return pr['number'], None, None

            merge_map: dict = {}
            max_w = min(5, len(normalized))
            with ThreadPoolExecutor(max_workers=max_w) as pool:
                futs = [pool.submit(_fetch_merge_status, pr) for pr in normalized]
                for fut in as_completed(futs):
                    num, mergeable, draft = fut.result()
                    if mergeable is not None or draft is not None:
                        merge_map[num] = (mergeable, draft)

            for pr in normalized:
                info = merge_map.get(pr['number'])
                if info:
                    mergeable, draft = info
                    if draft:
                        pr['merge_status'] = 'draft'
                    elif mergeable is True:
                        pr['merge_status'] = 'clean'
                    elif mergeable is False:
                        pr['merge_status'] = 'conflict'

        if reviewer:
            reviewer_lower = reviewer.lower()
            normalized = [
                pr for pr in normalized
                if any(r['username'].lower() == reviewer_lower for r in pr.get('reviewers', []))
            ]

        return {
            'pull_requests': normalized,
            'page': page,
            'per_page': per_page,
        }
    
    def get_pull_request(self, repo_id: str, pr_number: int) -> Dict[str, Any]:
        """Get pull request details."""
        response = self._request('GET', f'/repos/{repo_id}/pulls/{pr_number}')
        return self._normalize_pr(response.json())
    
    def get_pull_request_diff(self, repo_id: str, pr_number: int) -> str:
        """Get pull request diff."""
        headers = {**self.headers, 'Accept': 'application/vnd.github.v3.diff'}
        diff_response = requests.get(
            f"{self.base_url}/repos/{repo_id}/pulls/{pr_number}",
            headers=headers
        )
        diff_response.raise_for_status()
        return diff_response.text
    
    def get_pr_comment_authors(self, repo_id: str, pr_number: int) -> List[str]:
        """Return author usernames for all comments on a GitHub PR."""
        authors: List[str] = []

        def _paginate(endpoint: str):
            """Fetch all pages for a GitHub list endpoint."""
            url = endpoint
            params = {'per_page': 100}
            while url:
                try:
                    resp = self._request('GET', url, params=params)
                    for c in resp.json():
                        login = (c.get('user') or {}).get('login', '')
                        if login:
                            authors.append(login)
                    # Follow Link: <...>; rel="next" header for subsequent pages
                    next_url = None
                    link_header = resp.headers.get('Link', '')
                    for part in link_header.split(','):
                        if 'rel="next"' in part:
                            next_url = part.split(';')[0].strip().strip('<>')
                            break
                    if next_url:
                        # next_url is absolute; strip base_url so _request can prepend it
                        url = next_url.replace(self.base_url, '')
                        params = {}  # params are already embedded in next_url
                    else:
                        url = None
                except Exception:
                    break

        # Issue comments (conversation-level)
        _paginate(f'/repos/{repo_id}/issues/{pr_number}/comments')
        # Review comments (inline on diff)
        _paginate(f'/repos/{repo_id}/pulls/{pr_number}/comments')
        return authors

    def list_commits(self, repo_id: str, branch: str = 'main', page: int = 1, per_page: int = 30) -> Dict[str, Any]:
        """List commits."""
        response = self._request('GET', f'/repos/{repo_id}/commits', params={
            'sha': branch,
            'page': page,
            'per_page': per_page,
        })
        
        commits = response.json()
        return {
            'commits': [self._normalize_commit(commit) for commit in commits],
            'page': page,
            'per_page': per_page,
        }
    
    def create_commit(self, repo_id: str, branch: str, message: str, files: List[Dict[str, str]]) -> Dict[str, Any]:
        """Create a commit with file changes."""
        # This is a simplified implementation
        # Full implementation would use GitHub's Git Data API
        raise NotImplementedError("Commit creation not yet implemented for GitHub")
    
    def _normalize_repo(self, repo: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize repository data."""
        return {
            'id': repo.get('full_name', ''),
            'name': repo.get('name', ''),
            'full_name': repo.get('full_name', ''),
            'description': repo.get('description', ''),
            'private': repo.get('private', False),
            'default_branch': repo.get('default_branch', 'main'),
            'url': repo.get('html_url', ''),
            'clone_url': repo.get('clone_url', ''),
            'updated_at': repo.get('updated_at', ''),
        }
    
    def _normalize_tree_entry(self, entry: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize tree entry."""
        return {
            'path': entry.get('path', ''),
            'type': entry.get('type', 'file'),
            'size': entry.get('size', 0),
            'sha': entry.get('sha', ''),
        }
    
    def _normalize_pr(self, pr: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize pull request data."""
        reviewers = []
        for r in pr.get('requested_reviewers', []):
            reviewers.append({
                'username': r.get('login', ''),
                'display_name': r.get('login', ''),
                'avatar_url': r.get('avatar_url', ''),
                'role': 'REVIEWER',
                'status': 'UNAPPROVED',
            })
        comment_count = (pr.get('comments', 0) or 0) + (pr.get('review_comments', 0) or 0)

        # merge_status: 'clean' | 'conflict' | 'unknown' | 'draft'
        # GitHub's list endpoint includes `draft` but not `mergeable` (only
        # the detail endpoint returns that). When `mergeable` is present
        # (detail call), map it; otherwise fall back to 'unknown'.
        draft = bool(pr.get('draft', False))
        if draft:
            merge_status = 'draft'
        elif pr.get('mergeable') is True:
            merge_status = 'clean'
        elif pr.get('mergeable') is False:
            merge_status = 'conflict'
        else:
            merge_status = 'unknown'

        return {
            'number': pr.get('number', 0),
            'title': pr.get('title', ''),
            'state': pr.get('state', ''),
            'author': pr.get('user', {}).get('login', ''),
            'created_at': pr.get('created_at', ''),
            'updated_at': pr.get('updated_at', ''),
            'merged': pr.get('merged', False),
            'url': pr.get('html_url', ''),
            'from_branch': pr.get('head', {}).get('ref', ''),
            'reviewers': reviewers,
            'comment_count': comment_count,
            'draft': draft,
            'merge_status': merge_status,
        }
    
    def _normalize_commit(self, commit: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize commit data."""
        return {
            'sha': commit.get('sha', ''),
            'message': commit.get('commit', {}).get('message', ''),
            'author': commit.get('commit', {}).get('author', {}).get('name', ''),
            'date': commit.get('commit', {}).get('author', {}).get('date', ''),
            'url': commit.get('html_url', ''),
        }
    
    def normalize_repository_id(self, repo_data: Dict[str, Any]) -> str:
        """Normalize repository ID to 'owner_repo' format."""
        full_name = repo_data.get('full_name', '')
        return full_name.replace('/', '_')
