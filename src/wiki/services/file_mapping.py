"""
Service for file mapping business logic.
"""
import logging
import os
from typing import Optional, Dict, List
from django.db.models import Q
from django.utils import timezone
from wiki.models import Space, FileMapping
from wiki.services.name_extraction import NameExtractionService

logger = logging.getLogger(__name__)

EXTRACTABLE_EXTS = {'.md', '.markdown', '.mdx', '.xml', '.drawio'}
EXTRACTION_SOURCES = {'first_h1', 'first_h2', 'title_frontmatter'}


class FileMappingService:
    """Business logic for file mappings."""
    
    @staticmethod
    def get_effective_mapping(space: Space, file_path: str) -> Optional[FileMapping]:
        """
        Get effective mapping for a file, considering inheritance.
        
        Args:
            space: Space instance
            file_path: File path
        
        Returns:
            FileMapping instance or None
        """
        # 1. Check for direct mapping
        try:
            mapping = FileMapping.objects.get(space=space, file_path=file_path)
            if mapping.is_override or not mapping.parent_rule:
                return mapping
        except FileMapping.DoesNotExist:
            pass
        
        # 2. Check parent folder rules
        path_parts = file_path.split('/')
        for i in range(len(path_parts) - 1, 0, -1):
            parent_path = '/'.join(path_parts[:i]) + '/'
            try:
                parent_rule = FileMapping.objects.get(
                    space=space,
                    file_path=parent_path,
                    apply_to_children=True
                )
                return parent_rule
            except FileMapping.DoesNotExist:
                continue
        
        # 3. Return None (use space defaults)
        return None

    @staticmethod
    def _resolve_effective_source(
        space: Space,
        file_path: str,
        direct_mapping: Optional[FileMapping],
        is_folder: bool,
        mappings_by_path: Dict[str, FileMapping],
        space_default_source: str,
    ) -> str:
        """Compute the effective display_name_source for a single file/folder.

        Order of precedence:
          1. Explicit `display_name_source` on the file's own mapping.
          2. `children_display_name_source` from the nearest folder ancestor that
             has one set (and is configured to apply to children).
          3. The space's `default_display_name_source`.
        Folders always use filename (no extraction).
        """
        if is_folder:
            if direct_mapping and direct_mapping.display_name_source:
                return direct_mapping.display_name_source
            return 'filename'

        if direct_mapping and direct_mapping.display_name_source:
            return direct_mapping.display_name_source

        # Walk parent folders looking for a children_display_name_source rule.
        path_parts = file_path.split('/')
        for i in range(len(path_parts) - 1, 0, -1):
            parent_prefix = '/'.join(path_parts[:i])
            parent = mappings_by_path.get(parent_prefix) or mappings_by_path.get(parent_prefix + '/')
            if parent and parent.is_folder and parent.children_display_name_source:
                return parent.children_display_name_source

        return space_default_source

    @staticmethod
    def _resolve_effective_visibility(
        file_path: str,
        direct_mapping: Optional[FileMapping],
        mappings_by_path: Dict[str, FileMapping],
    ) -> bool:
        """If any parent folder mapping is hidden, this item is hidden."""
        if direct_mapping and not direct_mapping.is_visible:
            return False
        path_parts = file_path.split('/')
        for i in range(len(path_parts) - 1, 0, -1):
            parent_prefix = '/'.join(path_parts[:i])
            parent = mappings_by_path.get(parent_prefix) or mappings_by_path.get(parent_prefix + '/')
            if parent and parent.is_folder and not parent.is_visible:
                return False
        return True

    @staticmethod
    def _resolve_display_name(
        space: Space,
        git_provider,
        file_path: str,
        name: str,
        is_folder: bool,
        direct_mapping: Optional[FileMapping],
        effective_source: str,
    ) -> str:
        """Resolve the display name for one tree entry.

        Performs on-the-fly extraction for files whose effective source needs
        file content (H1/H2/frontmatter) and caches the result on a (possibly
        new) FileMapping row so subsequent loads are cheap.
        """
        if is_folder:
            if direct_mapping and direct_mapping.display_name_source == 'custom' and direct_mapping.display_name:
                return direct_mapping.display_name
            return name

        if effective_source == 'custom':
            if direct_mapping and direct_mapping.display_name:
                return direct_mapping.display_name
            return name

        if effective_source == 'filename' or not effective_source:
            return name

        # Extraction-based source. Use cached value when it matches the current source.
        if (
            direct_mapping
            and direct_mapping.extracted_name
            and (direct_mapping.effective_display_name_source or effective_source) == effective_source
        ):
            return direct_mapping.extracted_name

        ext = os.path.splitext(file_path)[1].lower()
        if ext not in EXTRACTABLE_EXTS or effective_source not in EXTRACTION_SOURCES:
            return name

        try:
            file_data = git_provider.get_file_content(
                project_key=space.git_project_key or '',
                repo_slug=space.git_repository_id or space.git_repository_name or '',
                file_path=file_path,
                branch=space.git_default_branch or 'main',
            )
            content = file_data.get('content', '') if isinstance(file_data, dict) else ''
            extracted = NameExtractionService.extract_name(file_path, content, effective_source)
        except Exception as exc:
            logger.info(f'tree-extract: skip {file_path} ({exc})')
            return name

        if not extracted:
            return name

        # Persist cache via stub mapping
        mapping = direct_mapping
        if mapping is None:
            mapping = FileMapping(
                space=space,
                file_path=file_path,
                is_folder=False,
                is_visible=True,
            )
        mapping.extracted_name = extracted
        mapping.extracted_at = timezone.now()
        try:
            mapping.save()
        except Exception as exc:
            logger.warning(f'tree-extract: failed to cache {file_path}: {exc}')
        return extracted

    @staticmethod
    def apply_folder_rule(
        space: Space,
        folder_path: str,
        rule: Dict,
        apply_to_children: bool = True,
        user=None
    ) -> FileMapping:
        """
        Apply a rule to a folder and optionally its children.
        
        Args:
            space: Space instance
            folder_path: Folder path
            rule: Rule configuration dict
            apply_to_children: Apply to children
            user: User creating the rule
        
        Returns:
            Created/updated FileMapping
        """
        # Ensure folder_path ends with /
        if not folder_path.endswith('/'):
            folder_path += '/'
        
        # Create or update folder mapping
        folder_mapping, created = FileMapping.objects.update_or_create(
            space=space,
            file_path=folder_path,
            defaults={
                'is_folder': True,
                'apply_to_children': apply_to_children,
                'created_by': user if created else None,
                **rule
            }
        )
        
        if apply_to_children:
            # Update all children that don't have overrides
            children = FileMapping.objects.filter(
                space=space,
                file_path__startswith=folder_path,
                is_override=False
            ).exclude(file_path=folder_path)
            
            children.update(parent_rule=folder_mapping)
        
        return folder_mapping
    
    @staticmethod
    def bulk_update_mappings(
        space: Space,
        mappings: List[Dict],
        user=None
    ) -> List[FileMapping]:
        """
        Bulk update or create file mappings.
        
        Args:
            space: Space instance
            mappings: List of mapping dicts with file_path and config
            user: User creating mappings
        
        Returns:
            List of created/updated FileMappings
        """
        results = []
        
        for mapping_data in mappings:
            file_path = mapping_data.pop('file_path')
            is_folder = mapping_data.get('is_folder', False)
            
            # Ensure folder paths end with /
            if is_folder and not file_path.endswith('/'):
                file_path += '/'
            
            mapping, created = FileMapping.objects.update_or_create(
                space=space,
                file_path=file_path,
                defaults={
                    'created_by': user if created else None,
                    **mapping_data
                }
            )
            results.append(mapping)
        
        return results
    
    @staticmethod
    def get_visible_files(
        space: Space,
        file_tree: List[Dict],
        mode: str = 'dev'
    ) -> List[Dict]:
        """
        Filter file tree based on visibility settings.
        
        Args:
            space: Space instance
            file_tree: List of file/folder dicts from git provider
            mode: 'dev' or 'documents'
        
        Returns:
            Filtered file tree
        """
        if mode == 'dev':
            # In dev mode, show everything
            return file_tree
        
        # In documents mode, filter by visibility
        mappings = {
            m.file_path: m
            for m in FileMapping.objects.filter(space=space)
        }
        
        visible_files = []
        for item in file_tree:
            path = item['path']
            
            # Get effective mapping
            mapping = FileMappingService.get_effective_mapping(space, path)
            
            # Check visibility
            if mapping and not mapping.is_visible:
                continue
            
            # Add display name if available
            if mapping:
                item['display_name'] = mapping.get_display_name()
                item['icon'] = mapping.icon
                item['sort_order'] = mapping.sort_order
            
            visible_files.append(item)
        
        return visible_files
    
    @staticmethod
    def build_tree_with_mappings(
        space: Space,
        git_provider,
        path: str = '',
        mode: str = 'dev',
        filters: List[str] = None
    ) -> List[Dict]:
        """
        Build file tree with mappings applied.
        
        Args:
            space: Space instance
            git_provider: Git provider instance
            path: Root path to start from
            mode: 'dev' or 'documents'
            filters: List of file extensions to filter
        
        Returns:
            File tree with mappings
        """
        # Get raw file tree from git provider
        # For Bitbucket: project_key and repo_slug
        # For GitHub: project_key is owner, repo_slug is repo name
        
        # Handle repository identification
        if space.git_project_key:
            # Explicit project key (Bitbucket Server)
            project_key = space.git_project_key
            repo_slug = space.git_repository_id or space.git_repository_name or ''
        elif space.git_repository_id and '/' in space.git_repository_id:
            # GitHub format "owner/repo" in repository_id
            parts = space.git_repository_id.split('/', 1)
            project_key = parts[0]
            repo_slug = parts[1]
        elif space.git_repository_id and '_' in space.git_repository_id:
            # Repository ID in format "project_repo" (legacy)
            parts = space.git_repository_id.split('_', 1)
            project_key = parts[0]
            repo_slug = parts[1]
        elif space.git_repository_name and '/' in space.git_repository_name:
            # GitHub format "owner/repo"
            parts = space.git_repository_name.split('/', 1)
            project_key = parts[0]
            repo_slug = parts[1]
        else:
            # Fallback
            project_key = space.git_project_key or ''
            repo_slug = space.git_repository_id or space.git_repository_name or ''
        
        branch = space.git_default_branch or 'main'
        
        raw_tree = git_provider.get_directory_tree(
            project_key=project_key,
            repo_slug=repo_slug,
            path=path,
            branch=branch,
            recursive=False  # Load only current level, not recursively
        )
        
        # Get all mappings for this space, indexed both with and without trailing slash
        # so folder lookups by either form succeed.
        all_mappings = list(FileMapping.objects.filter(space=space))
        mappings_by_path: Dict[str, FileMapping] = {}
        for m in all_mappings:
            mappings_by_path[m.file_path] = m
            if m.is_folder:
                mappings_by_path[m.file_path.rstrip('/')] = m

        space_default_source = space.default_display_name_source or 'first_h1'

        # Build tree with mappings
        result = []
        for item in raw_tree:
            file_path = item.get('path', '')
            if not file_path:
                continue

            # Extract name from path
            name = file_path.split('/')[-1] if file_path else ''
            item['name'] = name

            # Apply filters - but always show folders (they may contain matching files)
            is_folder = item.get('type') == 'dir'
            if filters and not is_folder:
                matches_filter = any(file_path.endswith(f) for f in filters)
                if not matches_filter:
                    if mode == 'documents':
                        # Skip in documents mode
                        continue
                    else:
                        # Mark as filtered in dev mode
                        item['filtered'] = True

            # Direct mapping for this exact path (file or folder)
            direct_mapping = mappings_by_path.get(file_path)

            # Resolve effective display-name source for this item
            effective_source = FileMappingService._resolve_effective_source(
                space=space,
                file_path=file_path,
                direct_mapping=direct_mapping,
                is_folder=is_folder,
                mappings_by_path=mappings_by_path,
                space_default_source=space_default_source,
            )

            # Apply visibility (parent-folder hidden also hides the file)
            visible = FileMappingService._resolve_effective_visibility(
                file_path=file_path,
                direct_mapping=direct_mapping,
                mappings_by_path=mappings_by_path,
            )
            if mode == 'documents' and not visible:
                continue

            # Resolve display name (extracts + caches when source needs file content)
            display_name = FileMappingService._resolve_display_name(
                space=space,
                git_provider=git_provider,
                file_path=file_path,
                name=name,
                is_folder=is_folder,
                direct_mapping=direct_mapping,
                effective_source=effective_source,
            )
            # _resolve_display_name may have created a stub mapping; refresh ref
            direct_mapping = mappings_by_path.get(file_path) or direct_mapping

            item['is_visible'] = visible
            item['display_name'] = display_name
            item['display_name_source'] = effective_source
            item['extracted_name'] = direct_mapping.extracted_name if direct_mapping else None
            item['icon'] = direct_mapping.icon if direct_mapping else None
            item['sort_order'] = direct_mapping.sort_order if direct_mapping else None
            item['has_mapping'] = direct_mapping is not None

            result.append(item)
        
        # Sort: directories first, then by sort_order/name
        result.sort(key=lambda x: (
            0 if x.get('type') == 'dir' else 1,  # Directories first
            x.get('sort_order') if x.get('sort_order') is not None else 999999,
            x['name']
        ))
        
        return result
