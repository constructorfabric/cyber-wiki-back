import base64
from unittest.mock import Mock

from git_provider.providers.github import GitHubProvider


class TestGitHubProvider:
    def test_parse_repository_url_strips_terminal_dot_git(self):
        parsed = GitHubProvider.parse_repository_url('git@github.com:octo/repo.git')

        assert parsed['git_base_url'] == 'https://api.github.com'
        assert parsed['git_repository_id'] == 'octo/repo'
        assert parsed['git_repository_name'] == 'repo'

    def test_get_file_content_decodes_base64_and_uses_owner_repo_endpoint(self):
        provider = GitHubProvider(base_url='https://github.com', token='token')
        provider._request = Mock()
        provider._request.return_value.json.return_value = {
            'content': base64.b64encode(b'---\ntitle: Hello\n---\n# Heading\n').decode('ascii'),
            'encoding': 'base64',
            'sha': 'abc123',
            'size': 30,
            'path': 'README.md',
        }

        result = provider.get_file_content('', 'octo/repo', 'README.md', branch='main')

        provider._request.assert_called_once_with(
            'GET',
            '/repos/octo/repo/contents/README.md',
            params={'ref': 'main'},
        )
        assert result['content'] == '---\ntitle: Hello\n---\n# Heading\n'
        assert result['encoding'] == 'utf-8'

    def test_get_directory_tree_avoids_double_slash_for_owner_repo_ids(self):
        provider = GitHubProvider(base_url='https://api.github.com', token='token')
        provider._request = Mock()
        provider._request.return_value.json.return_value = {'tree': []}

        provider.get_directory_tree('', 'octo/repo', branch='main', recursive=True)

        provider._request.assert_called_once_with(
            'GET',
            '/repos/octo/repo/git/trees/main',
            params={'recursive': '1'},
        )

    def test_get_directory_tree_encodes_recursive_branch_treeish(self):
        provider = GitHubProvider(base_url='https://api.github.com', token='token')
        provider._request = Mock()
        provider._request.return_value.json.return_value = {'tree': []}

        provider.get_directory_tree('', 'octo/repo', branch='feature/new-ui', recursive=True)

        provider._request.assert_called_once_with(
            'GET',
            '/repos/octo/repo/git/trees/feature%2Fnew-ui',
            params={'recursive': '1'},
        )

    def test_runtime_coordinates_strip_terminal_dot_git_from_persisted_ids(self):
        project_key, repo_slug = GitHubProvider.split_repository_coordinates('', 'octo/repo.git')

        assert project_key == 'octo'
        assert repo_slug == 'repo'
        assert GitHubProvider.build_repo_id('', 'octo/repo.git') == 'octo/repo'

    def test_parse_repository_url_coerces_ssh_ghe_clone_urls_to_https_api_origin(self):
        parsed = GitHubProvider.parse_repository_url('ssh://git@ghe.example.com/octo/repo.git')

        assert parsed['git_base_url'] == 'https://ghe.example.com/api/v3'
        assert parsed['git_repository_id'] == 'octo/repo'
        assert parsed['git_repository_name'] == 'repo'

    def test_recursive_tree_entries_normalize_github_git_tree_types(self):
        provider = GitHubProvider(base_url='https://api.github.com', token='token')
        provider._request = Mock()
        provider._request.return_value.json.return_value = {
            'tree': [
                {'path': 'docs', 'type': 'tree', 'sha': 'dirsha'},
                {'path': 'docs/readme.md', 'type': 'blob', 'sha': 'filesha', 'size': 42},
            ]
        }

        tree = provider.get_directory_tree('', 'octo/repo', branch='main', recursive=True)

        assert tree == [
            {'path': 'docs', 'type': 'dir', 'size': 0, 'sha': 'dirsha'},
            {'path': 'docs/readme.md', 'type': 'file', 'size': 42, 'sha': 'filesha'},
        ]

    def test_recursive_tree_scopes_results_to_requested_subtree(self):
        provider = GitHubProvider(base_url='https://api.github.com', token='token')
        provider._request = Mock()
        provider._request.return_value.json.return_value = {
            'tree': [
                {'path': 'docs', 'type': 'tree', 'sha': 'dirsha'},
                {'path': 'docs/readme.md', 'type': 'blob', 'sha': 'filesha', 'size': 42},
                {'path': 'src/app.py', 'type': 'blob', 'sha': 'othersha', 'size': 12},
            ]
        }

        tree = provider.get_directory_tree('', 'octo/repo', path='docs', branch='main', recursive=True)

        assert tree == [
            {'path': 'docs', 'type': 'dir', 'size': 0, 'sha': 'dirsha'},
            {'path': 'docs/readme.md', 'type': 'file', 'size': 42, 'sha': 'filesha'},
        ]
