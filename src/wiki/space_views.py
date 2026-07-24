"""
Space-centric API views for the redesigned architecture.
Handles spaces, permissions, configuration, shortcuts, attributes, and user preferences.
"""
from django.db import models
from django.db.models import Max
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse

from .models import (
    Space, SpacePermission, SpaceConfiguration, SpaceShortcut,
    UserSpacePreference, SpaceAttribute
)
from .serializers import (
    SpaceDetailSerializer,
    SpacePermissionSerializer, SpaceConfigurationSerializer,
    SpaceShortcutSerializer, UserSpacePreferenceSerializer,
    SpaceAttributeSerializer
)


class SpaceViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing spaces with full CRUD operations.
    """
    permission_classes = [IsAuthenticated]
    serializer_class = SpaceDetailSerializer
    queryset = Space.objects.all()
    lookup_field = 'slug'
    
    def get_queryset(self):
        """Filter spaces based on visibility and permissions."""
        user = self.request.user
        
        # Spaces visible to user:
        # 1. Owned by user
        # 2. Public visibility
        # 3. Team visibility (all authenticated users)
        # 4. Private with explicit permission
        return Space.objects.filter(
            models.Q(owner=user) |
            models.Q(visibility='public') |
            models.Q(visibility='team') |
            models.Q(permissions__user=user)
        ).distinct()
    
    @extend_schema(
        operation_id='spaces_list',
        summary='List spaces',
        description='List all spaces accessible to the user based on visibility and permissions.',
        responses={200: SpaceDetailSerializer(many=True)},
        tags=['spaces'],
    )
    def list(self, request):
        queryset = self.get_queryset()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_create',
        summary='Create space',
        description='Create a new space. The creator becomes the owner.',
        request=SpaceDetailSerializer,
        responses={201: SpaceDetailSerializer},
        tags=['spaces'],
    )
    def create(self, request):
        serializer = SpaceDetailSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        
        # Set owner and created_by to current user
        space = serializer.save(
            owner=request.user,
            created_by=request.user
        )
        
        # Create default configuration
        SpaceConfiguration.objects.create(space=space)
        
        response_serializer = SpaceDetailSerializer(space, context={'request': request})
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    @extend_schema(
        operation_id='spaces_retrieve',
        summary='Get space details',
        description='Get detailed information about a space.',
        responses={200: SpaceDetailSerializer},
        tags=['spaces'],
    )
    def retrieve(self, request, *args, **kwargs):
        space = self.get_object()
        serializer = self.get_serializer(space)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_update',
        summary='Update space',
        description='Update space details. Requires owner or admin permission.',
        request=SpaceDetailSerializer,
        responses={200: SpaceDetailSerializer},
        tags=['spaces'],
    )
    def update(self, request, *args, **kwargs):
        partial = kwargs.pop('partial', False)
        space = self.get_object()
        
        # Check permission
        if not self._has_admin_permission(request.user, space):
            return Response(
                {'error': 'Only space owner or admins can update space settings'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = SpaceDetailSerializer(space, data=request.data, partial=partial, context={'request': request})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_partial_update',
        summary='Partially update space',
        description='Partially update space details. Requires owner or admin permission.',
        request=SpaceDetailSerializer,
        responses={200: SpaceDetailSerializer},
        tags=['spaces'],
    )
    def partial_update(self, request, *args, **kwargs):
        """Handle PATCH requests."""
        kwargs['partial'] = True
        return self.update(request, *args, **kwargs)
    
    @extend_schema(
        operation_id='spaces_delete',
        summary='Delete space',
        description='Delete a space. Requires owner permission.',
        responses={204: None},
        tags=['spaces'],
    )
    def destroy(self, request, *args, **kwargs):
        space = self.get_object()
        
        # Only owner can delete
        if space.owner != request.user:
            return Response(
                {'error': 'Only space owner can delete the space'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        space.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    # ========================================================================
    # Permissions
    # ========================================================================
    
    @extend_schema(
        operation_id='spaces_permissions_list',
        summary='List space permissions',
        description='List all user permissions for a space.',
        responses={200: SpacePermissionSerializer(many=True)},
        tags=['spaces'],
    )
    @action(detail=True, methods=['get'], url_path='permissions')
    def list_permissions(self, request, slug=None):
        space = self.get_object()
        permissions = space.permissions.all()
        serializer = SpacePermissionSerializer(permissions, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_permissions_grant',
        summary='Grant permission',
        description='Grant permission to a user. Requires owner or admin permission.',
        request=SpacePermissionSerializer,
        responses={201: SpacePermissionSerializer},
        tags=['spaces'],
    )
    @action(detail=True, methods=['post'], url_path='permissions/grant')
    def grant_permission(self, request, slug=None):
        space = self.get_object()
        
        # Check permission
        if not self._has_admin_permission(request.user, space):
            return Response(
                {'error': 'Only space owner or admins can grant permissions'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = SpacePermissionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        permission = serializer.save(
            space=space,
            granted_by=request.user
        )
        
        response_serializer = SpacePermissionSerializer(permission)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    @extend_schema(
        operation_id='spaces_permissions_revoke',
        summary='Revoke permission',
        description='Revoke a user\'s permission. Requires owner or admin permission.',
        parameters=[
            OpenApiParameter(name='user_id', type=int, required=True, description='User ID'),
        ],
        responses={204: None},
        tags=['spaces'],
    )
    @action(detail=True, methods=['delete'], url_path='permissions/revoke/(?P<user_id>[0-9]+)')
    def revoke_permission(self, request, slug=None, user_id=None):
        space = self.get_object()
        
        # Check permission
        if not self._has_admin_permission(request.user, space):
            return Response(
                {'error': 'Only space owner or admins can revoke permissions'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        try:
            permission = space.permissions.get(user_id=user_id)
            permission.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except SpacePermission.DoesNotExist:
            return Response(
                {'error': 'Permission not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    # ========================================================================
    # Configuration
    # ========================================================================
    
    @extend_schema(
        operation_id='spaces_configuration_get',
        summary='Get space configuration',
        description='Get configuration settings for a space.',
        responses={200: SpaceConfigurationSerializer},
        tags=['spaces'],
    )
    @action(detail=True, methods=['get'], url_path='configuration')
    def get_configuration(self, request, slug=None):
        space = self.get_object()
        
        # Get or create configuration
        config, created = SpaceConfiguration.objects.get_or_create(space=space)
        
        serializer = SpaceConfigurationSerializer(config)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_configuration_update',
        summary='Update space configuration',
        description='Update configuration settings. Requires owner or admin permission.',
        request=SpaceConfigurationSerializer,
        responses={200: SpaceConfigurationSerializer},
        tags=['spaces'],
    )
    @action(detail=True, methods=['patch'], url_path='configuration')
    def update_configuration(self, request, slug=None):
        space = self.get_object()
        
        # Check permission
        if not self._has_admin_permission(request.user, space):
            return Response(
                {'error': 'Only space owner or admins can update configuration'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        config, created = SpaceConfiguration.objects.get_or_create(space=space)
        
        serializer = SpaceConfigurationSerializer(config, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)
    
    # ========================================================================
    # Shortcuts
    # ========================================================================
    
    @extend_schema(
        operation_id='spaces_shortcuts_list',
        summary='List space shortcuts',
        description='List all shortcuts for a space.',
        responses={200: SpaceShortcutSerializer(many=True)},
        tags=['spaces'],
    )
    @action(detail=True, methods=['get'], url_path='shortcuts')
    def list_shortcuts(self, request, slug=None):
        space = self.get_object()
        shortcuts = space.shortcuts.all()
        serializer = SpaceShortcutSerializer(shortcuts, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_shortcuts_create',
        summary='Create shortcut',
        description='Create a new shortcut.',
        request=SpaceShortcutSerializer,
        responses={201: SpaceShortcutSerializer},
        tags=['spaces'],
    )
    @action(detail=True, methods=['post'], url_path='shortcuts')
    def create_shortcut(self, request, slug=None):
        space = self.get_object()
        
        serializer = SpaceShortcutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        shortcut = serializer.save(
            space=space,
            created_by=request.user
        )
        
        response_serializer = SpaceShortcutSerializer(shortcut)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    @extend_schema(
        operation_id='spaces_shortcuts_delete',
        summary='Delete shortcut',
        description='Delete a shortcut.',
        parameters=[
            OpenApiParameter(name='shortcut_id', type=int, required=True, description='Shortcut ID'),
        ],
        responses={204: None},
        tags=['spaces'],
    )
    @action(detail=True, methods=['delete'], url_path='shortcuts/(?P<shortcut_id>[0-9]+)')
    def delete_shortcut(self, request, slug=None, shortcut_id=None):
        space = self.get_object()
        
        try:
            shortcut = space.shortcuts.get(id=shortcut_id)
            shortcut.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except SpaceShortcut.DoesNotExist:
            return Response(
                {'error': 'Shortcut not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    # ========================================================================
    # Attributes
    # ========================================================================
    
    @extend_schema(
        operation_id='spaces_attributes_list',
        summary='List space attributes',
        description='List all attributes for a space (latest versions).',
        parameters=[
            OpenApiParameter(name='field_id', type=str, required=False, description='Filter by field ID'),
            OpenApiParameter(name='data_source', type=str, required=False, description='Filter by data source'),
        ],
        responses={200: SpaceAttributeSerializer(many=True)},
        tags=['spaces'],
    )
    @action(detail=True, methods=['get'], url_path='attributes')
    def list_attributes(self, request, slug=None):
        space = self.get_object()
        
        # Get latest version of each attribute
        latest_versions = space.attributes.values('field_id').annotate(
            max_version=Max('version')
        )
        
        # Build list of (field_id, version) tuples
        filters = [
            models.Q(field_id=item['field_id'], version=item['max_version'])
            for item in latest_versions
        ]
        
        if filters:
            queryset = space.attributes.filter(models.Q(*filters, _connector=models.Q.OR))
        else:
            queryset = space.attributes.none()
        
        # Apply filters
        field_id = request.query_params.get('field_id')
        if field_id:
            queryset = queryset.filter(field_id=field_id)
        
        data_source = request.query_params.get('data_source')
        if data_source:
            queryset = queryset.filter(data_source=data_source)
        
        serializer = SpaceAttributeSerializer(queryset, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_attributes_create',
        summary='Create or update attribute',
        description='Create a new attribute or update existing (creates new version).',
        request=SpaceAttributeSerializer,
        responses={201: SpaceAttributeSerializer},
        tags=['spaces'],
    )
    @action(detail=True, methods=['post'], url_path='attributes')
    def create_attribute(self, request, slug=None):
        space = self.get_object()
        
        serializer = SpaceAttributeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Get latest version for this field_id
        field_id = serializer.validated_data['field_id']
        latest = space.attributes.filter(field_id=field_id).order_by('-version').first()
        version = (latest.version + 1) if latest else 1
        
        attribute = serializer.save(
            space=space,
            version=version
        )
        
        response_serializer = SpaceAttributeSerializer(attribute)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    @extend_schema(
        operation_id='spaces_attributes_get',
        summary='Get specific attribute',
        description='Get the latest version of a specific attribute.',
        parameters=[
            OpenApiParameter(name='field_id', type=str, required=True, description='Field ID'),
        ],
        responses={200: SpaceAttributeSerializer},
        tags=['spaces'],
    )
    @action(detail=True, methods=['get'], url_path='attributes/(?P<field_id>[^/]+)')
    def get_attribute(self, request, slug=None, field_id=None):
        space = self.get_object()
        
        attribute = space.attributes.filter(field_id=field_id).order_by('-version').first()
        
        if not attribute:
            return Response(
                {'error': 'Attribute not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = SpaceAttributeSerializer(attribute)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_attributes_history',
        summary='Get attribute history',
        description='Get all versions of a specific attribute.',
        parameters=[
            OpenApiParameter(name='field_id', type=str, required=True, description='Field ID'),
        ],
        responses={200: SpaceAttributeSerializer(many=True)},
        tags=['spaces'],
    )
    @action(detail=True, methods=['get'], url_path='attributes/(?P<field_id>[^/]+)/history')
    def get_attribute_history(self, request, slug=None, field_id=None):
        space = self.get_object()
        
        attributes = space.attributes.filter(field_id=field_id).order_by('-version')
        
        serializer = SpaceAttributeSerializer(attributes, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='spaces_attributes_delete',
        summary='Delete attribute',
        description='Delete all versions of a specific attribute.',
        parameters=[
            OpenApiParameter(name='field_id', type=str, required=True, description='Field ID'),
        ],
        responses={204: None},
        tags=['spaces'],
    )
    @action(detail=True, methods=['delete'], url_path='attributes/(?P<field_id>[^/]+)')
    def delete_attribute(self, request, slug=None, field_id=None):
        space = self.get_object()
        
        deleted_count, _ = space.attributes.filter(field_id=field_id).delete()
        
        if deleted_count == 0:
            return Response(
                {'error': 'Attribute not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    # ========================================================================
    # Helper Methods
    # ========================================================================
    
    def _has_admin_permission(self, user, space):
        """Check if user has admin permission for space."""
        if space.owner == user:
            return True
        
        try:
            permission = space.permissions.get(user=user)
            return permission.role == 'admin'
        except SpacePermission.DoesNotExist:
            return False


class UserSpacePreferenceViewSet(viewsets.ViewSet):
    """
    ViewSet for managing user space preferences (favorites, recent).
    """
    permission_classes = [IsAuthenticated]
    
    @extend_schema(
        operation_id='preferences_favorites_list',
        summary='List favorite spaces',
        description='Get all spaces marked as favorite by the current user.',
        responses={200: UserSpacePreferenceSerializer(many=True)},
        tags=['preferences'],
    )
    @action(detail=False, methods=['get'], url_path='favorites')
    def list_favorites(self, request):
        preferences = UserSpacePreference.objects.filter(
            user=request.user,
            is_favorite=True
        ).select_related('space')
        
        serializer = UserSpacePreferenceSerializer(preferences, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='preferences_favorites_toggle',
        summary='Add or remove a favorite',
        description='POST to mark a space as favorite, DELETE to unmark.',
        parameters=[
            OpenApiParameter(name='space_slug', type=str, required=True, description='Space slug'),
        ],
        responses={200: UserSpacePreferenceSerializer, 204: None},
        tags=['preferences'],
    )
    @action(detail=False, methods=['post', 'delete'], url_path='favorites/(?P<space_slug>[^/]+)')
    def toggle_favorite(self, request, space_slug=None):
        if request.method == 'DELETE':
            try:
                preference = UserSpacePreference.objects.get(
                    user=request.user,
                    space__slug=space_slug
                )
                preference.is_favorite = False
                preference.save()
                return Response(status=status.HTTP_204_NO_CONTENT)
            except UserSpacePreference.DoesNotExist:
                return Response(status=status.HTTP_204_NO_CONTENT)

        # POST — add to favorites
        try:
            space = Space.objects.get(slug=space_slug)
        except Space.DoesNotExist:
            return Response(
                {'error': 'Space not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        preference, created = UserSpacePreference.objects.get_or_create(
            user=request.user,
            space=space
        )
        preference.is_favorite = True
        preference.save()
        
        serializer = UserSpacePreferenceSerializer(preference)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='preferences_recent_list',
        summary='List recent spaces',
        description='Get recently visited spaces for the current user.',
        parameters=[
            OpenApiParameter(name='limit', type=int, required=False, description='Number of results (default 10)'),
        ],
        responses={200: UserSpacePreferenceSerializer(many=True)},
        tags=['preferences'],
    )
    @action(detail=False, methods=['get'], url_path='recent')
    def list_recent(self, request):
        limit = int(request.query_params.get('limit', 10))
        
        preferences = UserSpacePreference.objects.filter(
            user=request.user
        ).select_related('space').order_by('-last_visited_at')[:limit]
        
        serializer = UserSpacePreferenceSerializer(preferences, many=True)
        return Response(serializer.data)
    
    @extend_schema(
        operation_id='preferences_mark_visited',
        summary='Mark space as visited',
        description='Update last visited timestamp for a space.',
        parameters=[
            OpenApiParameter(name='space_slug', type=str, required=True, description='Space slug'),
        ],
        responses={200: UserSpacePreferenceSerializer},
        tags=['preferences'],
    )
    @action(detail=False, methods=['post'], url_path='visited/(?P<space_slug>[^/]+)')
    def mark_visited(self, request, space_slug=None):
        from django.utils import timezone
        try:
            space = Space.objects.get(slug=space_slug)
        except Space.DoesNotExist:
            return Response(
                {'error': 'Space not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        preference, created = UserSpacePreference.objects.get_or_create(
            user=request.user,
            space=space
        )
        preference.last_visited_at = timezone.now()
        preference.save()
        
        serializer = UserSpacePreferenceSerializer(preference)
        return Response(serializer.data)
