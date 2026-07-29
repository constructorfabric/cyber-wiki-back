"""
Unit tests for Git provider factory.

Tested Scenarios:
- GitHub provider creation with basic auth
- Bitbucket Server provider creation with username
- Bitbucket Server provider without username (error)
- Local Git provider creation
- Unsupported provider type (error)
- Provider creation from ServiceToken (GitHub)
- Provider creation from ServiceToken (Bitbucket with custom header)
- Provider creation from ServiceToken (Bitbucket without custom header)

Untested Scenarios / Gaps:
- Provider creation with invalid tokens
- Provider creation with malformed URLs
- Custom header token lookup with multiple tokens
- Custom header token lookup failures
- Provider instance validation
- Thread safety of factory methods

Test Strategy:
- Pure unit tests with mocks (no database for basic factory tests)
- Database tests for ServiceToken integration
- Mock external dependencies
- Test error conditions and edge cases
"""
import pytest
from unittest.mock import patch
from git_provider.factory import GitProviderFactory
from git_provider.providers.github import GitHubProvider
from git_provider.providers.bitbucket_server import BitbucketServerProvider
from git_provider.providers.local_git import LocalGitProvider
from service_tokens.models import ServiceType
from unit_tests.test_helpers import create_mock_user


class TestGitProviderFactory:
    """Tests for GitProviderFactory.create() method."""
    
    def test_create_github_provider(self):
        """Test creating a GitHub provider instance."""
        provider = GitProviderFactory.create(
            provider=ServiceType.GITHUB,
            base_url='https://api.github.com',
            token='ghp_test_token',
            username='testuser'
        )
        
        assert isinstance(provider, GitHubProvider)
        assert provider.base_url == 'https://api.github.com'
        assert provider.token == 'ghp_test_token'
    
    def test_create_bitbucket_provider_with_username(self):
        """Test creating a Bitbucket Server provider with username."""
        provider = GitProviderFactory.create(
            provider=ServiceType.BITBUCKET_SERVER,
            base_url='https://bitbucket.example.com',
            token='bb_token',
            username='admin'
        )
        
        assert isinstance(provider, BitbucketServerProvider)
        assert provider.base_url == 'https://bitbucket.example.com'
        assert provider.token == 'bb_token'
        assert provider.username == 'admin'
    
    def test_create_bitbucket_provider_without_username_raises_error(self):
        """Test that Bitbucket Server requires username."""
        with pytest.raises(ValueError, match="Username is required for Bitbucket Server"):
            GitProviderFactory.create(
                provider=ServiceType.BITBUCKET_SERVER,
                base_url='https://bitbucket.example.com',
                token='bb_token',
                username=None
            )
    
    def test_create_bitbucket_provider_with_custom_header(self):
        """Test creating Bitbucket Server provider with custom header."""
        provider = GitProviderFactory.create(
            provider=ServiceType.BITBUCKET_SERVER,
            base_url='https://bitbucket.example.com',
            token='bb_token',
            username='admin',
            custom_header='X-Custom-Token',
            custom_header_token='custom_token_value'
        )
        
        assert isinstance(provider, BitbucketServerProvider)
        # Custom headers are stored in the headers dict
        assert 'X-Custom-Token' in provider.headers
        assert provider.headers['X-Custom-Token'] == 'custom_token_value'
    
    def test_create_local_git_provider(self, tmp_path):
        """Test creating a local Git provider instance."""
        # Create a temporary directory for the test
        repo_path = tmp_path / "test_repo"
        repo_path.mkdir()
        
        provider = GitProviderFactory.create(
            provider='local_git',
            base_url=str(repo_path),
            token='dummy_token',
            username='user'
        )
        
        assert isinstance(provider, LocalGitProvider)
        assert provider.base_path == repo_path
    
    def test_create_unsupported_provider_raises_error(self):
        """Test that unsupported provider type raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported provider: invalid_provider"):
            GitProviderFactory.create(
                provider='invalid_provider',
                base_url='https://example.com',
                token='token'
            )
    
    def test_create_github_with_user_parameter(self):
        """Test creating GitHub provider with user parameter for caching."""
        mock_user = create_mock_user('testuser')
        
        provider = GitProviderFactory.create(
            provider=ServiceType.GITHUB,
            base_url='https://api.github.com',
            token='token',
            user=mock_user
        )
        
        assert isinstance(provider, GitHubProvider)
        assert provider.user == mock_user
    
    def test_create_bitbucket_with_user_parameter(self):
        """Test creating Bitbucket provider with user parameter for caching."""
        mock_user = create_mock_user('testuser')
        
        provider = GitProviderFactory.create(
            provider=ServiceType.BITBUCKET_SERVER,
            base_url='https://bitbucket.example.com',
            token='token',
            username='admin',
            user=mock_user
        )
        
        assert isinstance(provider, BitbucketServerProvider)
        assert provider.user == mock_user

    def test_get_repository_coordinates_github_owner_repo(self):
        """GitHub coordinates should resolve from canonical owner/repo IDs."""
        project_key, repo_slug = GitProviderFactory.get_repository_coordinates(
            ServiceType.GITHUB,
            None,
            'octo/repo',
        )

        assert project_key == 'octo'
        assert repo_slug == 'repo'

    def test_build_repository_identity_github(self):
        """GitHub repository identities stay in owner/repo form."""
        repo_id = GitProviderFactory.build_repository_identity(
            ServiceType.GITHUB,
            None,
            'octo/repo',
        )

        assert repo_id == 'octo/repo'


@pytest.mark.django_db
class TestGitProviderFactoryFromServiceToken:
    """Tests for GitProviderFactory.create_from_service_token() method."""
    
    def test_create_from_github_service_token(self, user):
        """Test creating GitHub provider from ServiceToken."""
        from service_tokens.models import ServiceToken
        
        # Create GitHub service token
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://api.github.com'
        )
        token.set_token('ghp_test_token')
        token.set_username('github_user')
        token.save()
        
        # Create provider from token
        provider = GitProviderFactory.create_from_service_token(token)
        
        assert isinstance(provider, GitHubProvider)
        assert provider.base_url == 'https://api.github.com'
        assert provider.token == 'ghp_test_token'

    def test_create_from_github_service_token_normalizes_public_web_url(self, user):
        """A GitHub token stored against github.com should still create an API client."""
        from service_tokens.models import ServiceToken

        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com',
        )
        token.set_token('ghp_test_token')
        token.save()

        provider = GitProviderFactory.create_from_service_token(token)

        assert isinstance(provider, GitHubProvider)
        assert provider.base_url == 'https://api.github.com'

    def test_get_service_token_matches_github_public_and_api_urls(self, user):
        """GitHub token lookup should match public repo URLs to API-token rows."""
        from service_tokens.models import ServiceToken

        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://api.github.com',
        )
        token.set_token('ghp_test_token')
        token.save()

        service_token = GitProviderFactory.get_service_token(
            user=user,
            provider=ServiceType.GITHUB,
            base_url='https://github.com',
        )

        assert service_token == token

    def test_get_service_token_fails_closed_when_github_url_candidates_miss(self, user):
        """GitHub token lookup must not fall back to an arbitrary token row."""
        from service_tokens.models import ServiceToken

        matched_token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://ghe.example.com/api/v3',
        )
        matched_token.set_token('ghe-token')
        matched_token.save()

        other_token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://api.github.com',
        )
        other_token.set_token('public-token')
        other_token.save()

        service_token = GitProviderFactory.get_service_token(
            user=user,
            provider=ServiceType.GITHUB,
            base_url='https://another.example.com',
        )

        assert service_token is None

    def test_get_service_token_fails_closed_for_semantically_duplicate_ghe_tokens(self, user):
        """Root and /api/v3 GitHub Enterprise rows must not resolve arbitrarily."""
        from service_tokens.models import ServiceToken

        first_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://ghe.example.com',
            encrypted_token='first-encrypted',
        )
        second_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://ghe.example.com/api/v3',
            encrypted_token='second-encrypted',
        )
        ServiceToken.objects.bulk_create([first_token, second_token])

        with pytest.raises(ValueError, match='Ambiguous service token configuration for provider: github'):
            GitProviderFactory.get_service_token(
                user=user,
                provider=ServiceType.GITHUB,
                base_url='https://ghe.example.com',
            )
    
    def test_create_from_bitbucket_service_token_without_custom_header(self, user):
        """Test creating Bitbucket provider from ServiceToken without custom header."""
        from service_tokens.models import ServiceToken
        
        # Create Bitbucket service token
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.BITBUCKET_SERVER,
            base_url='https://bitbucket.example.com'
        )
        token.set_token('bb_token')
        token.set_username('bb_user')
        token.save()
        
        # Create provider from token
        provider = GitProviderFactory.create_from_service_token(token)
        
        assert isinstance(provider, BitbucketServerProvider)
        assert provider.base_url == 'https://bitbucket.example.com'
        assert provider.token == 'bb_token'
        # Custom headers not added when not provided
        assert 'X-ZTA-Token' not in provider.headers
    
    def test_create_from_bitbucket_service_token_with_custom_header(self, user):
        """Test creating Bitbucket provider from ServiceToken with custom header."""
        from service_tokens.models import ServiceToken
        
        # Create Bitbucket service token
        bb_token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.BITBUCKET_SERVER,
            base_url='https://bitbucket.example.com'
        )
        bb_token.set_token('bb_token')
        bb_token.set_username('bb_user')
        bb_token.save()
        
        # Create custom header token
        custom_token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-ZTA-Token',
            name='ZTA Token',
            base_url='',
        )
        custom_token.set_token('zta_token_value')
        custom_token.save()
        
        # Create provider from Bitbucket token
        provider = GitProviderFactory.create_from_service_token(bb_token)
        
        assert isinstance(provider, BitbucketServerProvider)
        # Custom header should be in headers dict
        assert 'X-ZTA-Token' in provider.headers
        assert provider.headers['X-ZTA-Token'] == 'zta_token_value'
    
    def test_create_from_bitbucket_with_multiple_custom_headers(self, user):
        """Test that first custom header token is used when multiple exist."""
        from service_tokens.models import ServiceToken
        
        # Create Bitbucket service token
        bb_token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.BITBUCKET_SERVER,
            base_url='https://bitbucket.example.com'
        )
        bb_token.set_token('bb_token')
        bb_token.set_username('bb_user')
        bb_token.save()
        
        # Create multiple custom header tokens (empty base_url sorts first)
        custom_token1 = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-First-Token',
            name='First Token',
            base_url=''  # Empty string
        )
        custom_token1.set_token('first_token')
        custom_token1.save()
        
        custom_token2 = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-Second-Token',
            name='Second Token',
            base_url='https://example.com'
        )
        custom_token2.set_token('second_token')
        custom_token2.save()
        
        # Create provider - should use first token (empty base_url)
        provider = GitProviderFactory.create_from_service_token(bb_token)
        
        assert 'X-First-Token' in provider.headers
        assert provider.headers['X-First-Token'] == 'first_token'

    def test_get_custom_header_token_does_not_fall_back_to_unrelated_base_url(self, user):
        from service_tokens.models import ServiceToken

        custom_token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-Other-Token',
            name='Other Token',
            base_url='https://another.example.com'
        )
        custom_token.set_token('other-token')
        custom_token.save()

        assert GitProviderFactory.get_custom_header_token(
            user,
            base_url='https://bitbucket.example.com',
        ) is None
    
    def test_create_from_service_token_handles_custom_header_error(self, user):
        """Test that custom header lookup errors are handled gracefully."""
        from service_tokens.models import ServiceToken
        
        # Create Bitbucket service token
        bb_token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.BITBUCKET_SERVER,
            base_url='https://bitbucket.example.com'
        )
        bb_token.set_token('bb_token')
        bb_token.set_username('bb_user')
        bb_token.save()
        
        # Mock ServiceToken.objects.filter to raise an exception
        with patch('service_tokens.models.ServiceToken.objects.filter') as mock_filter:
            mock_filter.side_effect = Exception("Database error")
            
            # Should still create provider without custom header
            provider = GitProviderFactory.create_from_service_token(bb_token)
            
            assert isinstance(provider, BitbucketServerProvider)
            # Should have basic headers but no custom header
            assert 'Content-Type' in provider.headers
            # No custom header should be added
            assert len([k for k in provider.headers.keys() if k.startswith('X-')]) == 0
