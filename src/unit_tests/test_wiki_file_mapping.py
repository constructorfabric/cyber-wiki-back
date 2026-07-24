"""
Unit tests for file mapping inheritance and effective value computation.

Tested Scenarios:
- File inherits from space default when no parent folder
- File inherits from parent folder's children_display_name_source
- File overrides parent folder with explicit setting
- Nested folder inheritance (nearest parent wins)
- Folders always use filename or custom (never inherit)
- Visibility inheritance from parent folder
- Effective values recompute after parent changes
- Effective values recompute after space default changes

Untested Scenarios / Gaps:
- Multi-level nested inheritance (> 3 levels)
- Circular inheritance detection
- Inheritance with deleted parent folders
- Concurrent updates to parent and child
- Bulk recomputation performance
- Cache invalidation on inheritance changes
- Cross-space inheritance (should not happen)

Test Strategy:
- Model tests with database fixtures
- Use shared fixtures from conftest.py
- Test inheritance chain logic
- Test effective value computation
"""
import pytest
from unittest.mock import Mock, patch
from rest_framework.test import APIRequestFactory, force_authenticate
from wiki.models import Space, FileMapping
from wiki.serializers import FileMappingCreateSerializer
from wiki.views_file_mapping import FileMappingViewSet


@pytest.mark.django_db
class TestFileMappingInheritance:
    """Test inheritance chain: space → folder → file"""
    
    def test_file_inherits_from_space_default(self, space):
        """File with no mapping should inherit from space default."""
        mapping = FileMapping.objects.create(
            space=space,
            file_path='docs/readme.md',
            is_folder=False,
        )
        
        # Should inherit from space default (first_h1)
        assert mapping.effective_display_name_source == 'first_h1'
        assert mapping.effective_is_visible == True
    
    def test_file_inherits_from_parent_folder(self, space):
        """File should inherit from parent folder's children_display_name_source."""
        # Create parent folder with children rule
        folder = FileMapping.objects.create(
            space=space,
            file_path='api',
            is_folder=True,
            display_name_source='filename',
            children_display_name_source='first_h2',
        )
        
        # Create file in folder
        file_mapping = FileMapping.objects.create(
            space=space,
            file_path='api/endpoints.md',
            is_folder=False,
        )
        
        # Should inherit H2 from parent folder
        assert file_mapping.effective_display_name_source == 'first_h2'
    
    def test_file_overrides_parent_folder(self, space):
        """File with explicit setting should override parent folder."""
        # Create parent folder
        FileMapping.objects.create(
            space=space,
            file_path='api',
            is_folder=True,
            children_display_name_source='first_h2',
        )
        
        # Create file with explicit setting
        file_mapping = FileMapping.objects.create(
            space=space,
            file_path='api/auth.md',
            is_folder=False,
            display_name_source='custom',
            display_name='Authentication',
        )
        
        # Should use own setting, not parent
        assert file_mapping.effective_display_name_source == 'custom'
    
    def test_nested_folder_inheritance(self, space):
        """File should inherit from nearest parent with children_display_name_source."""
        # Create grandparent folder
        FileMapping.objects.create(
            space=space,
            file_path='docs',
            is_folder=True,
            children_display_name_source='first_h1',
        )
        
        # Create parent folder with different rule
        FileMapping.objects.create(
            space=space,
            file_path='docs/api',
            is_folder=True,
            children_display_name_source='first_h2',
        )
        
        # Create file
        file_mapping = FileMapping.objects.create(
            space=space,
            file_path='docs/api/endpoints.md',
            is_folder=False,
        )
        
        # Should inherit from nearest parent (api/ → H2)
        assert file_mapping.effective_display_name_source == 'first_h2'
    
    def test_folder_always_uses_filename_or_custom(self, space):
        """Folders should always use filename or custom, never inherit."""
        # Create parent folder
        FileMapping.objects.create(
            space=space,
            file_path='docs',
            is_folder=True,
            children_display_name_source='first_h1',
        )
        
        # Create child folder without explicit setting
        child_folder = FileMapping.objects.create(
            space=space,
            file_path='docs/api',
            is_folder=True,
        )
        
        # Should use filename, not inherit from parent
        assert child_folder.effective_display_name_source == 'filename'
    
    def test_visibility_inheritance(self, space):
        """Test visibility inheritance from parent folder."""
        # Create hidden parent folder
        FileMapping.objects.create(
            space=space,
            file_path='internal',
            is_folder=True,
            is_visible=False,
            children_display_name_source='first_h1',
        )
        
        # Create file in hidden folder
        file_mapping = FileMapping.objects.create(
            space=space,
            file_path='internal/secret.md',
            is_folder=False,
        )
        
        # Should inherit hidden status from parent
        assert file_mapping.effective_is_visible == False


@pytest.mark.django_db
class TestFileMappingSync:
    """Test sync functionality - removing outdated mappings."""
    
    def test_recompute_effective_values_after_parent_change(self, space):
        """When parent folder changes, child effective values should update."""
        # Create parent folder
        folder = FileMapping.objects.create(
            space=space,
            file_path='api',
            is_folder=True,
            children_display_name_source='first_h1',
        )
        
        # Create file
        file_mapping = FileMapping.objects.create(
            space=space,
            file_path='api/endpoints.md',
            is_folder=False,
        )
        
        assert file_mapping.effective_display_name_source == 'first_h1'
        
        # Change parent folder's children rule
        folder.children_display_name_source = 'first_h2'
        folder.save()
        
        # Recompute file's effective values
        file_mapping.refresh_from_db()
        effective_source, _ = file_mapping.compute_effective_values()
        file_mapping.effective_display_name_source = effective_source
        file_mapping.save()
        
        # Should now use H2
        file_mapping.refresh_from_db()
        assert file_mapping.effective_display_name_source == 'first_h2'
    
    def test_recompute_after_space_default_change(self, space):
        """When space default changes, files without parent should update."""
        # Create file with no parent folder
        file_mapping = FileMapping.objects.create(
            space=space,
            file_path='readme.md',
            is_folder=False,
        )
        
        assert file_mapping.effective_display_name_source == 'first_h1'
        
        # Change space default
        space.default_display_name_source = 'first_h2'
        space.save()
        
        # Recompute
        effective_source, _ = file_mapping.compute_effective_values()
        file_mapping.effective_display_name_source = effective_source
        file_mapping.save()
        
        file_mapping.refresh_from_db()
        assert file_mapping.effective_display_name_source == 'first_h2'

    def test_sync_uses_provider_directory_tree_api(self, user, space):
        """Sync should call get_directory_tree with provider-aware coordinates."""
        user.userprofile.role = 'commenter'
        user.userprofile.save()
        space.git_provider = 'github'
        space.git_base_url = 'https://api.github.com'
        space.git_repository_id = 'octo/repo'
        space.git_default_branch = 'main'
        space.save()
        FileMapping.objects.create(space=space, file_path='docs/old.md', is_folder=False)

        provider = Mock()
        provider.get_directory_tree.return_value = [{'path': 'docs/current.md', 'type': 'file'}]

        request = APIRequestFactory().post('/sync')
        force_authenticate(request, user=user)
        view = FileMappingViewSet.as_view({'post': 'sync'})

        with patch.object(FileMappingViewSet, '_get_git_provider', return_value=provider):
            response = view(request, space_slug=space.slug)

        assert response.status_code == 200
        provider.get_directory_tree.assert_called_once_with(
            project_key='octo',
            repo_slug='repo',
            path='',
            branch='main',
            recursive=True,
        )
        assert response.data['deleted_count'] == 1

    def test_sync_uses_master_fallback_for_blank_bitbucket_default_branch(self, user, space):
        user.userprofile.role = 'commenter'
        user.userprofile.save()
        space.git_provider = 'bitbucket_server'
        space.git_base_url = 'https://bitbucket.example.com'
        space.git_project_key = 'PRJ'
        space.git_repository_id = 'repo'
        space.git_default_branch = ''
        space.save()

        provider = Mock()
        provider.get_directory_tree.return_value = [{'path': 'docs/current.md', 'type': 'file'}]

        request = APIRequestFactory().post('/sync')
        force_authenticate(request, user=user)
        view = FileMappingViewSet.as_view({'post': 'sync'})

        with patch.object(FileMappingViewSet, '_get_git_provider', return_value=provider):
            response = view(request, space_slug=space.slug)

        assert response.status_code == 200
        provider.get_directory_tree.assert_called_once_with(
            project_key='PRJ',
            repo_slug='repo',
            path='',
            branch='master',
            recursive=True,
        )


@pytest.mark.django_db
class TestFileMappingCreateSerializerBranchFallback:
    def test_create_serializer_uses_master_fallback_for_blank_bitbucket_default_branch(self, user, space):
        space.git_provider = 'bitbucket_server'
        space.git_base_url = 'https://bitbucket.example.com'
        space.git_project_key = 'PRJ'
        space.git_repository_id = 'repo'
        space.git_default_branch = ''
        space.save()

        request = APIRequestFactory().post('/mappings')
        request.user = user

        provider = Mock()
        provider.get_file_content.return_value = {'content': '# Heading'}

        serializer = FileMappingCreateSerializer(
            data={'file_path': 'docs/readme.md', 'is_folder': False, 'display_name_source': 'first_h1'},
            context={'request': request},
        )

        with patch('wiki.serializers.GitProviderFactory.get_service_token', return_value=object()), patch(
            'wiki.serializers.GitProviderFactory.create_from_service_token',
            return_value=provider,
        ):
            assert serializer.is_valid(), serializer.errors
            serializer.save(space=space, created_by=user)

        provider.get_file_content.assert_called_once_with(
            project_key='PRJ',
            repo_slug='repo',
            file_path='docs/readme.md',
            branch='master',
        )

    def test_create_serializer_keeps_github_main_fallback(self, user, space):
        space.git_provider = 'github'
        space.git_base_url = 'https://api.github.com'
        space.git_project_key = ''
        space.git_repository_id = 'octo/repo'
        space.git_repository_name = 'repo'
        space.git_default_branch = ''
        space.save()

        request = APIRequestFactory().post('/mappings')
        request.user = user

        provider = Mock()
        provider.get_file_content.return_value = {'content': '# Heading'}

        serializer = FileMappingCreateSerializer(
            data={'file_path': 'docs/readme.md', 'is_folder': False, 'display_name_source': 'first_h1'},
            context={'request': request},
        )

        with patch('wiki.serializers.GitProviderFactory.get_service_token', return_value=object()), patch(
            'wiki.serializers.GitProviderFactory.create_from_service_token',
            return_value=provider,
        ):
            assert serializer.is_valid(), serializer.errors
            serializer.save(space=space, created_by=user)

        provider.get_file_content.assert_called_once_with(
            project_key='octo',
            repo_slug='repo',
            file_path='docs/readme.md',
            branch='main',
        )


@pytest.mark.django_db
class TestFileMappingAccessControl:
    def test_private_space_list_denies_unrelated_commenter(self, user, another_user):
        another_user.userprofile.role = 'commenter'
        another_user.userprofile.save()
        space = Space.objects.create(
            slug='private-space',
            name='Private Space',
            owner=user,
            visibility='private',
            git_provider='local_git',
            git_base_url='/tmp/test-repo',
            git_repository_id='test-repo',
        )
        FileMapping.objects.create(space=space, file_path='docs/readme.md', is_folder=False)

        request = APIRequestFactory().get('/file-mappings/')
        force_authenticate(request, user=another_user)
        response = FileMappingViewSet.as_view({'get': 'list'})(request, space_slug=space.slug)

        assert response.status_code == 404

    def test_private_space_sync_denies_unrelated_commenter(self, user, another_user):
        another_user.userprofile.role = 'commenter'
        another_user.userprofile.save()
        space = Space.objects.create(
            slug='private-sync-space',
            name='Private Sync Space',
            owner=user,
            visibility='private',
            git_provider='github',
            git_base_url='https://github.com',
            git_repository_id='octo/repo',
        )

        request = APIRequestFactory().post('/sync')
        force_authenticate(request, user=another_user)
        response = FileMappingViewSet.as_view({'post': 'sync'})(request, space_slug=space.slug)

        assert response.status_code == 404

    def test_private_space_mapping_update_denies_unrelated_commenter(self, user, another_user):
        another_user.userprofile.role = 'commenter'
        another_user.userprofile.save()
        space = Space.objects.create(
            slug='private-mapping-space',
            name='Private Mapping Space',
            owner=user,
            visibility='private',
            git_provider='local_git',
            git_base_url='/tmp/test-repo',
            git_repository_id='test-repo',
        )
        mapping = FileMapping.objects.create(space=space, file_path='docs/readme.md', is_folder=False)

        request = APIRequestFactory().put(
            f'/file-mappings/{mapping.pk}/',
            {'file_path': 'docs/readme.md', 'is_folder': False},
            format='json',
        )
        force_authenticate(request, user=another_user)
        response = FileMappingViewSet.as_view({'put': 'update'})(request, space_slug=space.slug, pk=mapping.pk)

        assert response.status_code == 404
