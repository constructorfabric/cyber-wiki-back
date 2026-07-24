from types import SimpleNamespace
from unittest.mock import patch

import pytest

from git_provider.worktree_manager import GitError, GitWorktreeManager


def test_run_git_sync_redacts_credentials_in_error_message_and_logs(caplog):
    manager = GitWorktreeManager(cache_dir='/tmp/cw-cache-test', worktree_dir='/tmp/cw-worktree-test')
    token = 'super-secret-token'
    url = f'https://oauth2:{token}@github.com/octo/repo.git'

    with patch(
        'subprocess.run',
        return_value=SimpleNamespace(
            returncode=1,
            stdout=b'',
            stderr=f'fatal: could not read from {url}'.encode('utf-8'),
        ),
    ):
        with pytest.raises(GitError) as exc_info:
            manager._run_git_sync(['clone', '--bare', url, '/tmp/repo.git'])

    assert token not in str(exc_info.value)
    assert token not in exc_info.value.stderr
    assert token not in caplog.text
    assert '***' in str(exc_info.value)
