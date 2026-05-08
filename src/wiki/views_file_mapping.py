"""
Views for file mapping API.
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter
from django.shortcuts import get_object_or_404

from .models import Space, FileMapping
from .serializers import FileMappingSerializer, FileMappingCreateSerializer
from .services.file_mapping import FileMappingService
from .services.name_extraction import NameExtractionService
from users.permissions import IsCommenterOrAbove
from git_provider.factory import GitProviderFactory
from service_tokens.models import ServiceToken


class FileMappingViewSet(viewsets.ModelViewSet):
    """
    ViewSet for file mappings.
    """
    permission_classes = [IsAuthenticated, IsCommenterOrAbove]
    serializer_class = FileMappingSerializer
    
    def _get_git_provider(self, space: Space):
        """Get Git provider instance for the space."""
        import logging
        logger = logging.getLogger(__name__)
        
        # Get the git provider from space's git config
        if not space.git_provider or not space.git_repository_id:
            raise ValueError("Space does not have Git configuration")
        
        logger.info(f"Looking for service token: provider={space.git_provider}, base_url={space.git_base_url}, user={self.request.user.username}")
        
        # Find service token for this provider and base_url
        # This is important for getting the correct token with custom headers
        service_token = ServiceToken.objects.filter(
            user=self.request.user,
            service_type=space.git_provider,
            base_url=space.git_base_url
        ).first()
        
        if not service_token:
            logger.warning(f"No token found with base_url={space.git_base_url}, trying without base_url")
            # Fallback: try without base_url filter
            service_token = ServiceToken.objects.filter(
                user=self.request.user,
                service_type=space.git_provider
            ).first()
        
        if not service_token:
            raise ValueError(f"No credentials found for provider: {space.git_provider}")
        
        logger.info(f"Found service token: id={service_token.id}, base_url={service_token.base_url}")
        return GitProviderFactory.create_from_service_token(service_token)
    
    def get_queryset(self):
        space_slug = self.kwargs.get('space_slug')
        if space_slug:
            return FileMapping.objects.filter(space__slug=space_slug).select_related('space', 'parent_rule')
        return FileMapping.objects.none()
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return FileMappingCreateSerializer
        return FileMappingSerializer
    
    @extend_schema(
        operation_id='file_mappings_list',
        summary='List file mappings for a space',
        description='Get all file mappings configured for a space.',
        parameters=[
            OpenApiParameter(name='space_slug', type=str, location=OpenApiParameter.PATH, required=True),
        ],
        responses={200: FileMappingSerializer(many=True)},
        tags=['file-mappings'],
    )
    def list(self, request, space_slug=None):
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='file_mappings_create',
        summary='Create a file mapping',
        description='Create a new file mapping for a space.',
        request=FileMappingCreateSerializer,
        responses={201: FileMappingSerializer},
        tags=['file-mappings'],
    )
    def create(self, request, space_slug=None):
        space = get_object_or_404(Space, slug=space_slug)
        serializer = FileMappingCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)

        # Update existing mapping if one already exists for this space + file_path
        file_path = serializer.validated_data.get('file_path', '')
        existing = FileMapping.objects.filter(space=space, file_path=file_path).first()
        if existing:
            update_serializer = FileMappingCreateSerializer(
                existing, data=request.data, context={'request': request}
            )
            update_serializer.is_valid(raise_exception=True)
            mapping = update_serializer.save()
            response_serializer = FileMappingSerializer(mapping)
            return Response(response_serializer.data, status=status.HTTP_200_OK)

        mapping = serializer.save(space=space, created_by=request.user)
        response_serializer = FileMappingSerializer(mapping)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    @extend_schema(
        operation_id='file_mappings_update',
        summary='Update a file mapping',
        description='Update an existing file mapping.',
        request=FileMappingCreateSerializer,
        responses={200: FileMappingSerializer},
        tags=['file-mappings'],
    )
    def update(self, request, pk=None, space_slug=None):
        mapping = self.get_object()
        serializer = FileMappingCreateSerializer(mapping, data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        response_serializer = FileMappingSerializer(mapping)
        return Response(response_serializer.data)
    
    @extend_schema(
        operation_id='file_mappings_delete',
        summary='Delete a file mapping',
        description='Delete a file mapping.',
        responses={204: None},
        tags=['file-mappings'],
    )
    def destroy(self, request, pk=None, space_slug=None):
        mapping = self.get_object()
        mapping.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @extend_schema(
        operation_id='file_mappings_bulk_update',
        summary='Bulk update file mappings',
        description='Create or update multiple file mappings at once.',
        request={
            'type': 'object',
            'properties': {
                'mappings': {
                    'type': 'array',
                    'items': {'$ref': '#/components/schemas/FileMappingCreate'}
                }
            }
        },
        responses={200: FileMappingSerializer(many=True)},
        tags=['file-mappings'],
    )
    @action(detail=False, methods=['post'])
    def bulk_update(self, request, space_slug=None):
        space = get_object_or_404(Space, slug=space_slug)
        mappings_data = request.data.get('mappings', [])
        
        results = FileMappingService.bulk_update_mappings(
            space=space,
            mappings=mappings_data,
            user=request.user
        )
        
        serializer = FileMappingSerializer(results, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='file_mappings_apply_folder_rule',
        summary='Apply folder rule',
        description='Apply a configuration rule to a folder and optionally its children.',
        request={
            'type': 'object',
            'properties': {
                'folder_path': {'type': 'string'},
                'apply_to_children': {'type': 'boolean'},
                'rule': {'type': 'object'}
            }
        },
        responses={200: FileMappingSerializer},
        tags=['file-mappings'],
    )
    @action(detail=False, methods=['post'])
    def apply_folder_rule(self, request, space_slug=None):
        space = get_object_or_404(Space, slug=space_slug)
        folder_path = request.data.get('folder_path')
        apply_to_children = request.data.get('apply_to_children', True)
        rule = request.data.get('rule', {})
        
        mapping = FileMappingService.apply_folder_rule(
            space=space,
            folder_path=folder_path,
            rule=rule,
            apply_to_children=apply_to_children,
            user=request.user
        )
        
        serializer = FileMappingSerializer(mapping)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='file_mappings_extract_names',
        summary='Extract display names from files',
        description='Extract display names from file content for multiple files.',
        request={
            'type': 'object',
            'properties': {
                'file_paths': {
                    'type': 'array',
                    'items': {'type': 'string'}
                },
                'source': {'type': 'string', 'enum': ['first_h1', 'first_h2', 'title_frontmatter', 'filename']}
            }
        },
        responses={200: {
            'type': 'object',
            'properties': {
                'extracted': {
                    'type': 'array',
                    'items': {
                        'type': 'object',
                        'properties': {
                            'file_path': {'type': 'string'},
                            'extracted_name': {'type': 'string'},
                            'source': {'type': 'string'}
                        }
                    }
                }
            }
        }},
        tags=['file-mappings'],
    )
    @action(detail=False, methods=['post'])
    def extract_names(self, request, space_slug=None):
        space = get_object_or_404(Space, slug=space_slug)
        file_paths = request.data.get('file_paths', [])
        source = request.data.get('source', 'first_h1')
        
        # Get git provider
        git_provider = self._get_git_provider(space)
        
        # Get repository info
        project_key = space.git_project_key or ''
        repo_slug = space.git_repository_id or space.git_repository_name or ''
        branch = space.git_default_branch or 'main'
        
        # Extract names
        results = []
        for file_path in file_paths:
            try:
                file_data = git_provider.get_file_content(
                    project_key=project_key,
                    repo_slug=repo_slug,
                    file_path=file_path,
                    branch=branch
                )
                content = file_data.get('content', '')
                extracted_name = NameExtractionService.extract_name(file_path, content, source)
                
                results.append({
                    'file_path': file_path,
                    'extracted_name': extracted_name or file_path.split('/')[-1],
                    'source': source
                })
            except Exception as e:
                results.append({
                    'file_path': file_path,
                    'extracted_name': file_path.split('/')[-1],
                    'source': 'filename',
                    'error': str(e)
                })
        
        return Response({'extracted': results})
    
    @extend_schema(
        operation_id='file_mappings_get_tree',
        summary='Get file tree with mappings',
        description='Get the file tree with all mappings applied.',
        parameters=[
            OpenApiParameter(name='mode', type=str, description='View mode: dev or documents'),
            OpenApiParameter(name='filters', type=str, description='Comma-separated file extensions'),
        ],
        responses={200: {
            'type': 'object',
            'properties': {
                'tree': {'type': 'array'}
            }
        }},
        tags=['file-mappings'],
    )
    @action(detail=False, methods=['get'])
    def get_tree(self, request, space_slug=None):
        space = get_object_or_404(Space, slug=space_slug)
        mode = request.query_params.get('mode', 'dev')
        filters_str = request.query_params.get('filters', '')
        filters = [f.strip() for f in filters_str.split(',') if f.strip()]
        
        # Get git provider
        git_provider = self._get_git_provider(space)
        
        # Build tree with mappings
        try:
            tree = FileMappingService.build_tree_with_mappings(
                space=space,
                git_provider=git_provider,
                mode=mode,
                filters=filters if filters else None
            )
        except Exception as exc:
            import requests as _requests
            if isinstance(exc, _requests.exceptions.HTTPError) and exc.response is not None:
                code = exc.response.status_code
                if code == 401:
                    return Response(
                        {'error': 'Git provider authentication failed', 'code': 'GIT_PROVIDER_AUTH_FAILED',
                         'detail': 'Git provider token is invalid or expired. Update it in Configuration → Tokens.'},
                        status=status.HTTP_502_BAD_GATEWAY,
                    )
                if code == 403:
                    return Response(
                        {'error': 'Access forbidden', 'detail': 'Insufficient permissions for this repository.'},
                        status=status.HTTP_403_FORBIDDEN,
                    )
            raise
        
        return Response({'tree': tree})
    
    @extend_schema(
        operation_id='file_mappings_sync',
        summary='Sync file mappings with repository',
        description='Remove mappings for deleted files and recompute effective values.',
        responses={200: {
            'type': 'object',
            'properties': {
                'deleted_count': {'type': 'integer'},
                'updated_count': {'type': 'integer'},
                'message': {'type': 'string'}
            }
        }},
        tags=['file-mappings'],
    )
    @action(detail=False, methods=['post'])
    def sync(self, request, space_slug=None):
        """Sync file mappings - remove deleted files, recompute effective values."""
        space = get_object_or_404(Space, slug=space_slug)
        
        try:
            # Get git provider
            git_provider = self._get_git_provider(space)
            
            # Get actual files from repository
            tree = git_provider.get_tree(space.git_repository_id, recursive=True)
            actual_files = {item['path'] for item in tree}
            
            # Find and delete mappings for files that no longer exist
            mappings = FileMapping.objects.filter(space=space)
            deleted_count = 0
            
            for mapping in mappings:
                if not mapping.is_folder and mapping.file_path not in actual_files:
                    mapping.delete()
                    deleted_count += 1
            
            # Recompute effective values for remaining mappings
            updated_count = 0
            for mapping in FileMapping.objects.filter(space=space):
                old_source = mapping.effective_display_name_source
                old_visible = mapping.effective_is_visible
                
                effective_source, effective_visible = mapping.compute_effective_values()
                
                if old_source != effective_source or old_visible != effective_visible:
                    mapping.effective_display_name_source = effective_source
                    mapping.effective_is_visible = effective_visible
                    mapping.save(update_fields=['effective_display_name_source', 'effective_is_visible'])
                    updated_count += 1
            
            return Response({
                'deleted_count': deleted_count,
                'updated_count': updated_count,
                'message': f'Sync complete. Deleted {deleted_count} outdated mappings, updated {updated_count} effective values.'
            })
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @extend_schema(
        operation_id='file_mappings_refresh',
        summary='Refresh effective mappings',
        description='Extract fresh content from files and update effective display names.',
        responses={200: {
            'type': 'object',
            'properties': {
                'updated_count': {'type': 'integer'},
                'message': {'type': 'string'}
            }
        }},
        tags=['file-mappings'],
    )
    @action(detail=False, methods=['post'])
    def refresh(self, request, space_slug=None):
        """Refresh effective mappings - extract fresh content and update display names."""
        space = get_object_or_404(Space, slug=space_slug)
        
        try:
            # Get git provider
            git_provider = self._get_git_provider(space)
            
            # Get all file mappings (not folders)
            mappings = FileMapping.objects.filter(space=space, is_folder=False)
            updated_count = 0
            
            for mapping in mappings:
                # Skip custom names - they don't need extraction
                if mapping.display_name_source == 'custom':
                    continue
                
                # Skip filename source - no extraction needed
                if mapping.effective_display_name_source == 'filename':
                    continue
                
                try:
                    # Get file content
                    file_data = git_provider.get_file_content(
                        space.git_repository_id,
                        mapping.file_path,
                        ref=space.git_default_branch or 'main'
                    )
                    content = file_data.get('content', '')
                    
                    # Extract name based on effective source
                    source = mapping.effective_display_name_source or 'first_h1'
                    extracted_name = NameExtractionService.extract_name(
                        mapping.file_path,
                        content,
                        source
                    )
                    
                    # Update if changed
                    if extracted_name and extracted_name != mapping.extracted_name:
                        mapping.extracted_name = extracted_name
                        mapping.extracted_at = None  # Will be set by save
                        mapping.save(update_fields=['extracted_name', 'extracted_at'])
                        updated_count += 1
                        
                except Exception as e:
                    # Log error but continue with other files
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f'Failed to refresh {mapping.file_path}: {str(e)}')
                    continue
            
            return Response({
                'updated_count': updated_count,
                'message': f'Refresh complete. Updated {updated_count} display names.'
            })
            
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
