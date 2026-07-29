"""
Unit tests for service token models.

Tested Scenarios:
- ServiceToken creation with encryption
- Token encryption and decryption
- Username encryption and decryption
- String representation for different service types
- Unique constraints for service tokens
- Unique constraints for custom header tokens
- Default ZTA header name
- ServiceType choices validation

Untested Scenarios / Gaps:
- Signal handlers (post_save, pre_delete logging)
- Encryption key rotation
- Invalid encryption key handling
- Concurrent token creation conflicts
- Token expiration (not implemented)
- Token revocation workflows
- Bulk token operations
- Performance with many tokens

Test Strategy:
- Model tests with database using @pytest.mark.django_db
- Test encryption/decryption round-trip
- Test model constraints and validation
- Use shared fixtures from conftest.py
- Verify string representations
"""
import importlib
import pytest
from django.apps import apps as django_apps
from django.db import IntegrityError
from django.utils import timezone
from service_tokens.models import ServiceToken, ServiceType

normalize_github_base_urls_migration = importlib.import_module(
    'service_tokens.migrations.0008_normalize_github_base_urls'
)


@pytest.mark.django_db
class TestServiceToken:
    """Tests for ServiceToken model."""
    
    def test_create_github_token(self, user):
        """Test creating a GitHub service token."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com'
        )
        token.set_token('ghp_test_token_123')
        token.set_username('testuser@github')
        token.save()
        
        assert token.user == user
        assert token.service_type == ServiceType.GITHUB
        assert token.base_url == 'https://api.github.com'
        assert token.get_token() == 'ghp_test_token_123'
        assert token.get_username() == 'testuser@github'
    
    def test_create_bitbucket_token(self, user):
        """Test creating a Bitbucket Server service token."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.BITBUCKET_SERVER,
            base_url='https://bitbucket.example.com'
        )
        token.set_token('bb_token_456')
        token.set_username('admin')
        token.save()
        
        assert token.service_type == ServiceType.BITBUCKET_SERVER
        assert token.get_token() == 'bb_token_456'
        assert token.get_username() == 'admin'
    
    def test_create_jira_token(self, user):
        """Test creating a JIRA service token."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.JIRA,
            base_url='https://jira.example.com'
        )
        token.set_token('jira_api_token_789')
        token.set_username('jira.user@example.com')
        token.save()
        
        assert token.service_type == ServiceType.JIRA
        assert token.get_token() == 'jira_api_token_789'
        assert token.get_username() == 'jira.user@example.com'
    
    def test_create_custom_header_token(self, user):
        """Test creating a custom header token."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-API-Key',
            name='My Custom API'
        )
        token.set_token('custom_api_key_abc')
        token.save()
        
        assert token.service_type == ServiceType.CUSTOM_HEADER
        assert token.header_name == 'X-API-Key'
        assert token.name == 'My Custom API'
        assert token.get_token() == 'custom_api_key_abc'
        assert token.base_url is None
    
    def test_token_encryption_decryption(self, user):
        """Test token encryption and decryption round-trip."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com'
        )
        
        original_token = 'super_secret_token_xyz'
        token.set_token(original_token)
        token.save()
        
        # Verify encrypted token is different from original
        assert token.encrypted_token != original_token

        # Verify decryption returns original
        assert token.get_token() == original_token

    def test_save_normalizes_github_enterprise_root_to_api_v3(self, user):
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://ghe.example.com'
        )
        token.set_token('ghp-enterprise-token')
        token.save()

        assert token.base_url == 'https://ghe.example.com/api/v3'

    def test_save_normalizes_github_api_host_casing_and_trailing_slash(self, user):
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='HTTPS://API.GITHUB.COM/',
        )
        token.set_token('ghp-public-token')
        token.save()

        assert token.base_url == 'https://api.github.com'
    
    def test_username_encryption_decryption(self, user):
        """Test username encryption and decryption round-trip."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com'
        )
        token.set_token('token')
        
        original_username = 'user@example.com'
        token.set_username(original_username)
        token.save()
        
        # Verify encrypted username is different from original
        assert token.encrypted_username != original_username
        
        # Verify decryption returns original
        assert token.get_username() == original_username
    
    def test_username_optional(self, user):
        """Test that username is optional."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com'
        )
        token.set_token('token')
        token.save()
        
        assert token.get_username() is None
    
    def test_string_representation_standard_token(self, user):
        """Test string representation for standard service tokens."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com'
        )
        token.set_token('token')
        token.save()
        
        assert str(token) == 'testuser - github - https://api.github.com'
    
    def test_string_representation_custom_header_with_name(self, user):
        """Test string representation for custom header token with name."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-API-Key',
            name='My API'
        )
        token.set_token('token')
        token.save()
        
        assert str(token) == 'testuser - custom_header - My API'
    
    def test_string_representation_custom_header_without_name(self, user):
        """Test string representation for custom header token without name."""
        token = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-Auth-Token'
        )
        token.set_token('token')
        token.save()
        
        assert str(token) == 'testuser - custom_header - Unnamed'
    
    def test_unique_constraint_standard_token(self, user):
        """Test unique constraint for standard service tokens."""
        # Create first token
        token1 = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com'
        )
        token1.set_token('token1')
        token1.save()
        
        # Attempt to create duplicate should raise error
        with pytest.raises(IntegrityError):
            token2 = ServiceToken.objects.create(
                user=user,
                service_type=ServiceType.GITHUB,
                base_url='https://api.github.com'
            )
            token2.set_token('token2')
            token2.save()
    
    def test_unique_constraint_custom_header_token(self, user):
        """Test unique constraint for custom header tokens with same base_url."""
        # Create first custom header token with a base_url
        token1 = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-API-Key',
            name='API 1',
            base_url='https://api.example.com'
        )
        token1.set_token('token1')
        token1.save()
        
        # Attempt to create duplicate with same header_name and base_url should raise error
        # The unique constraint is: user + service_type + base_url + header_name
        # Note: NULL values in SQL don't enforce uniqueness, so we use a non-NULL base_url
        with pytest.raises(IntegrityError):
            token2 = ServiceToken.objects.create(
                user=user,
                service_type=ServiceType.CUSTOM_HEADER,
                header_name='X-API-Key',
                name='API 2',
                base_url='https://api.example.com'  # Same as token1
            )
            token2.set_token('token2')
            token2.save()
    
    def test_multiple_custom_headers_different_names(self, user):
        """Test that multiple custom header tokens with different header names are allowed."""
        # Create first custom header token
        token1 = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-API-Key',
            name='API 1'
        )
        token1.set_token('token1')
        token1.save()
        
        # Create second custom header token with different header_name
        token2 = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.CUSTOM_HEADER,
            header_name='X-Auth-Token',
            name='API 2'
        )
        token2.set_token('token2')
        token2.save()
        
        # Both should exist
        assert ServiceToken.objects.filter(user=user, service_type=ServiceType.CUSTOM_HEADER).count() == 2
    
    def test_different_users_same_service(self, user, another_user):
        """Test that different users can have tokens for the same service."""
        # User 1 creates token
        token1 = ServiceToken.objects.create(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com'
        )
        token1.set_token('token1')
        token1.save()
        
        # User 2 creates token for same service
        token2 = ServiceToken.objects.create(
            user=another_user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com'
        )
        token2.set_token('token2')
        token2.save()
        
        # Both should exist
        assert ServiceToken.objects.filter(service_type=ServiceType.GITHUB).count() == 2
    
    def test_get_default_zta_header(self):
        """Test default ZTA header name."""
        assert ServiceToken.get_default_zta_header() == 'X-Zero-Trust-Token'
    
    def test_service_type_choices(self):
        """Test all service type choices are valid."""
        expected_types = ['github', 'bitbucket_server', 'jira', 'custom_header']
        actual_types = [choice[0] for choice in ServiceType.choices]
        
        for expected in expected_types:
            assert expected in actual_types

    def test_migration_normalizes_existing_github_web_urls_and_deduplicates_collisions(self, user):
        root_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com',
            encrypted_token='placeholder',
        )
        root_token.set_token('ghp-shared-token')
        api_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://api.github.com',
            encrypted_token='placeholder',
        )
        api_token.set_token('ghp-shared-token')
        ServiceToken.objects.bulk_create([root_token, api_token])

        normalize_github_base_urls_migration.forwards(apps=django_apps, schema_editor=None)

        tokens = list(
            ServiceToken.objects.filter(user=user, service_type=ServiceType.GITHUB).order_by('pk')
        )
        assert len(tokens) == 1
        assert tokens[0].base_url == 'https://api.github.com'

    def test_migration_prefers_validated_populated_survivor_over_empty_canonical_row(self, user):
        canonical_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://api.github.com',
            encrypted_token='placeholder',
            last_validation_valid=False,
        )
        canonical_token.set_token('ghp-working-token')
        root_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com',
            encrypted_token='placeholder',
            last_validation_valid=True,
            last_validation_message='ok',
        )
        root_token.set_token('ghp-working-token')
        root_token.set_username('octocat')
        ServiceToken.objects.bulk_create([canonical_token, root_token])

        normalize_github_base_urls_migration.forwards(apps=django_apps, schema_editor=None)

        tokens = list(ServiceToken.objects.filter(user=user, service_type=ServiceType.GITHUB))
        assert len(tokens) == 1
        survivor = tokens[0]
        assert survivor.base_url == 'https://api.github.com'
        assert survivor.get_token() == 'ghp-working-token'
        assert survivor.get_username() == 'octocat'
        assert survivor.last_validation_valid is True
        assert survivor.last_validation_message == 'ok'

    def test_migration_fails_closed_on_materially_different_duplicate_tokens(self, user):
        root_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com',
            encrypted_token='placeholder',
        )
        root_token.set_token('ghp-root-token')

        api_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://api.github.com',
            encrypted_token='placeholder',
        )
        api_token.set_token('ghp-api-token')
        ServiceToken.objects.bulk_create([root_token, api_token])

        with pytest.raises(RuntimeError, match='Manual ServiceToken deduplication required'):
            normalize_github_base_urls_migration.forwards(apps=django_apps, schema_editor=None)

    def test_migration_normalizes_existing_ghe_root_url_to_api_v3(self, user):
        token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://ghe.example.com',
            encrypted_token='placeholder',
        )
        token.set_token('ghp-ghe-token')
        ServiceToken.objects.bulk_create([token])

        normalize_github_base_urls_migration.forwards(apps=django_apps, schema_editor=None)

        token.refresh_from_db()
        assert token.base_url == 'https://ghe.example.com/api/v3'

    def test_migration_handles_mixed_last_validated_at_values_without_type_errors(self, user):
        stale_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://github.com',
            encrypted_token='placeholder',
        )
        stale_token.set_token('ghp-shared-token')
        fresh_token = ServiceToken(
            user=user,
            service_type=ServiceType.GITHUB,
            base_url='https://api.github.com',
            encrypted_token='placeholder',
        )
        fresh_token.set_token('ghp-shared-token')
        fresh_token.last_validation_valid = True
        ServiceToken.objects.bulk_create([stale_token, fresh_token])

        fresh_token.last_validated_at = timezone.now()
        fresh_token.save(update_fields=['last_validated_at'])

        normalize_github_base_urls_migration.forwards(apps=django_apps, schema_editor=None)

        tokens = list(ServiceToken.objects.filter(user=user, service_type=ServiceType.GITHUB))
        assert len(tokens) == 1
        assert tokens[0].base_url == 'https://api.github.com'
