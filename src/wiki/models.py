"""
Wiki models for spaces, documents, comments, tags, and change management.
"""
from django.db import models
from django.contrib.auth.models import User
import uuid


class Space(models.Model):
    """
    A Space is a container for Pages, similar to Confluence spaces.
    Each space is linked to a Git repository and has its own configuration.
    """
    # Primary Key
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Basic Information
    slug = models.SlugField(max_length=100, unique=True, db_index=True, help_text='URL-friendly identifier')
    name = models.CharField(max_length=200, help_text='Display name')
    description = models.TextField(blank=True, help_text='Space description')
    
    # Ownership
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='owned_spaces',
        null=True,  # Nullable for migration, will be populated from created_by
        help_text='Space owner with full control'
    )
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='created_spaces',
        help_text='User who created the space (legacy field)'
    )
    
    # Visibility
    VISIBILITY_CHOICES = [
        ('private', 'Private - Only owner and explicitly shared users'),
        ('team', 'Team - All authenticated users'),
        ('public', 'Public - Anyone with link'),
    ]
    visibility = models.CharField(
        max_length=20,
        choices=VISIBILITY_CHOICES,
        default='private',
        help_text='Space visibility level'
    )
    is_public = models.BooleanField(
        default=False,
        help_text='Legacy field - use visibility instead'
    )
    
    # Git Integration
    git_provider = models.CharField(
        max_length=50,
        choices=[
            ('bitbucket_server', 'Bitbucket Server'),
            ('github', 'GitHub'),
            ('local_git', 'Local Git Repository'),
        ],
        null=True,
        blank=True,
        help_text='Git provider type'
    )
    git_base_url = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text='Git provider base URL or filesystem path for local Git'
    )
    git_project_key = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='Git project key (for Bitbucket)'
    )
    git_repository_id = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text='Git repository identifier'
    )
    git_repository_name = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text='Git repository name'
    )
    git_default_branch = models.CharField(
        max_length=100,
        default='',
        blank=True,
        help_text='Default Git branch (auto-detected if empty)'
    )
    
    # Edit Fork Configuration (for edit workflow with git worktrees)
    edit_fork_project_key = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text='Project key for edit fork (e.g., ~doclab-service)'
    )
    edit_fork_repo_slug = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text='Repository slug for edit fork'
    )
    edit_fork_ssh_url = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text='SSH clone URL for edit fork (e.g., ssh://git@git.example.com/~service/repo.git)'
    )
    edit_fork_local_path = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text='Local path to pre-cloned edit fork repo (for development, overrides ssh_url)'
    )
    
    # Bot filtering
    bot_usernames = models.JSONField(
        default=list,
        blank=True,
        help_text='Bot username prefixes to exclude from reviewer filters (e.g., ["coderabbitai", "dependabot"])'
    )

    # File Mapping Configuration
    filters = models.JSONField(
        default=list,
        blank=True,
        help_text='File extension/pattern filters (e.g., [".md", ".xml"])'
    )
    default_display_name_source = models.CharField(
        max_length=50,
        default='first_h1',
        choices=[
            ('first_h1', 'First H1'),
            ('first_h2', 'First H2'),
            ('title_frontmatter', 'Title from Frontmatter'),
            ('filename', 'Filename'),
            ('custom', 'Custom'),
        ],
        help_text='Default display name source for all files in this space'
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last Git sync timestamp'
    )
    
    # Counters (denormalized for performance)
    page_count = models.IntegerField(
        default=0,
        help_text='Number of pages in this space'
    )

    class Meta:
        verbose_name = 'Space'
        verbose_name_plural = 'Spaces'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['owner', '-updated_at']),
            models.Index(fields=['visibility']),
        ]

    def __str__(self):
        return self.name
    
    @property
    def edit_enabled(self) -> bool:
        """Check if editing is configured for this space."""
        # Local path takes precedence (for development)
        if self.edit_fork_local_path:
            return True
        # Otherwise need full SSH config
        return bool(
            self.edit_fork_project_key and
            self.edit_fork_repo_slug and
            self.edit_fork_ssh_url
        )


class Document(models.Model):
    """
    Document within a space, linked to Git repository content.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    unique_id = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='documents')
    title = models.CharField(max_length=500, help_text='Document title')
    path = models.CharField(max_length=1000, help_text='File path in repository')
    content = models.TextField(blank=True, help_text='Cached document content')
    repository_id = models.CharField(max_length=255, default='', help_text='Repository identifier')
    branch = models.CharField(max_length=255, default='main', help_text='Git branch')
    doc_type = models.CharField(max_length=20, default='markdown', help_text='Document type')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_documents')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Document'
        verbose_name_plural = 'Documents'
        ordering = ['-updated_at']
        unique_together = [['space', 'repository_id', 'path']]

    def __str__(self):
        return self.title


class GitSyncConfig(models.Model):
    """
    Configuration for Git synchronization.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    class SyncDirection(models.TextChoices):
        PULL_ONLY = 'pull_only', 'Pull Only'
        PUSH_ONLY = 'push_only', 'Push Only'
        BIDIRECTIONAL = 'bidirectional', 'Bidirectional'
    
    class SyncStatus(models.TextChoices):
        ACTIVE = 'active', 'Active'
        PAUSED = 'paused', 'Paused'
        ERROR = 'error', 'Error'
    
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='sync_configs')
    repository_url = models.URLField(max_length=500, help_text='Git repository URL')
    branch = models.CharField(max_length=255, default='main', help_text='Branch to sync')
    direction = models.CharField(
        max_length=20,
        choices=SyncDirection.choices,
        default=SyncDirection.PULL_ONLY
    )
    status = models.CharField(
        max_length=20,
        choices=SyncStatus.choices,
        default=SyncStatus.ACTIVE
    )
    last_sync_at = models.DateTimeField(null=True, blank=True)
    last_sync_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Git Sync Config'
        verbose_name_plural = 'Git Sync Configs'

    def __str__(self):
        return f"{self.space.name} - {self.repository_url}"


class Tag(models.Model):
    """
    Tag for categorizing documents.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    class TagType(models.TextChoices):
        AUTO = 'auto', 'Auto-generated'
        CUSTOM = 'custom', 'Custom'
    
    name = models.CharField(max_length=100, unique=True, db_index=True)
    tag_type = models.CharField(
        max_length=10,
        choices=TagType.choices,
        default=TagType.CUSTOM
    )
    usage_count = models.IntegerField(default=0, help_text='Number of documents using this tag')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Tag'
        verbose_name_plural = 'Tags'
        ordering = ['-usage_count', 'name']

    def __str__(self):
        return self.name


class DocumentTag(models.Model):
    """
    Association between documents and tags with relevance scoring.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='document_tags')
    tag = models.ForeignKey(Tag, on_delete=models.CASCADE, related_name='document_tags')
    relevance_score = models.FloatField(default=1.0, help_text='TF-IDF or manual relevance score')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [['document', 'tag']]
        verbose_name = 'Document Tag'
        verbose_name_plural = 'Document Tags'
        ordering = ['-relevance_score']

    def __str__(self):
        return f"{self.document.title} - {self.tag.name}"


class DocumentLink(models.Model):
    """
    Links between documents for navigation and discovery.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    class LinkType(models.TextChoices):
        INTERNAL = 'internal', 'Internal Link'
        EXTERNAL = 'external', 'External Link'
        REFERENCE = 'reference', 'Reference'
    
    source_document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name='outgoing_links')
    target_document = models.ForeignKey(Document, on_delete=models.CASCADE, null=True, blank=True, related_name='incoming_links')
    target_url = models.URLField(max_length=1000, blank=True, help_text='External URL if not internal link')
    link_type = models.CharField(max_length=20, choices=LinkType.choices, default=LinkType.INTERNAL)
    is_valid = models.BooleanField(default=True, help_text='Whether link target exists')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Document Link'
        verbose_name_plural = 'Document Links'

    def __str__(self):
        if self.target_document:
            return f"{self.source_document.title} → {self.target_document.title}"
        return f"{self.source_document.title} → {self.target_url}"


class FileComment(models.Model):
    """
    Inline comment on source files with line anchoring.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    class AnchoringStatus(models.TextChoices):
        ANCHORED = 'anchored', 'Anchored'
        MOVED = 'moved', 'Moved'
        OUTDATED = 'outdated', 'Outdated'
    
    source_uri = models.CharField(max_length=1000, db_index=True, help_text='Universal source address')
    line_start = models.IntegerField(null=True, blank=True, help_text='Starting line number (1-indexed)')
    line_end = models.IntegerField(null=True, blank=True, help_text='Ending line number (1-indexed)')
    text = models.TextField(help_text='Comment text (supports Markdown)')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='file_comments')
    thread_id = models.UUIDField(default=uuid.uuid4, db_index=True, help_text='Thread identifier for grouping')
    parent_comment = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='replies')
    is_resolved = models.BooleanField(default=False, help_text='Whether comment is resolved')
    anchoring_status = models.CharField(
        max_length=20,
        choices=AnchoringStatus.choices,
        default=AnchoringStatus.ANCHORED
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'File Comment'
        verbose_name_plural = 'File Comments'
        ordering = ['source_uri', 'line_start', 'created_at']

    def __str__(self):
        return f"Comment by {self.author.username} on {self.source_uri}"


class UserChange(models.Model):
    """
    Pending user changes for approval workflow.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    class ChangeStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        COMMITTED = 'committed', 'Committed'
    
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='changes')
    repository_full_name = models.CharField(max_length=255, help_text='Repository identifier')
    file_path = models.CharField(max_length=1000, help_text='File path in repository')
    original_content = models.TextField(help_text='Original file content')
    modified_content = models.TextField(help_text='Modified file content')
    commit_message = models.TextField(blank=True, help_text='Commit message')
    status = models.CharField(
        max_length=20,
        choices=ChangeStatus.choices,
        default=ChangeStatus.PENDING
    )
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='approved_changes')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'User Change'
        verbose_name_plural = 'User Changes'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} - {self.file_path} ({self.status})"


class EditSession(models.Model):
    """
    Tracks a user's editing session for a space.
    Multiple file changes can be grouped into one session → one PR.
    
    This implements the edit workflow with git worktrees:
    - Users edit files in DocLab
    - Changes are stored in pending_changes JSON
    - On submit, DocLab creates a branch on the edit fork, commits, and creates PR
    - All commits use --author to preserve user attribution
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Ownership
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='edit_sessions')
    space = models.ForeignKey(Space, on_delete=models.CASCADE, related_name='edit_sessions')
    
    # Session state
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft - Changes pending'
        SUBMITTING = 'submitting', 'Submitting - PR being created'
        SUBMITTED = 'submitted', 'Submitted - PR created'
        MERGED = 'merged', 'Merged'
        CLOSED = 'closed', 'Closed without merge'
        ABANDONED = 'abandoned', 'Abandoned by user'
        ERROR = 'error', 'Error during submission'
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    error_message = models.TextField(
        blank=True,
        help_text='Error message if status is ERROR'
    )
    
    # Pending changes (before PR creation)
    pending_changes = models.JSONField(
        default=list,
        help_text='''
        List of pending file changes:
        [
            {
                "file_path": "docs/README.md",
                "original_content": "...",
                "modified_content": "...",
                "change_type": "modify",
                "description": "Fixed typo"
            }
        ]
        '''
    )
    
    # Git branch info (after changes are pushed)
    branch_name = models.CharField(
        max_length=200,
        null=True,
        blank=True,
        help_text='Branch name on edit fork (e.g., doclab/maxim-cherey/edit-abc123)'
    )
    base_branch = models.CharField(
        max_length=100,
        default='master',
        help_text='Base branch to create PR against'
    )
    commit_sha = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        help_text='SHA of the commit on the edit fork'
    )
    
    # PR info (after PR creation)
    pr_id = models.IntegerField(
        null=True,
        blank=True,
        help_text='Pull request ID in Bitbucket'
    )
    pr_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text='URL to the pull request'
    )
    
    # Metadata
    title = models.CharField(
        max_length=200,
        help_text='Session title / PR title'
    )
    description = models.TextField(
        blank=True,
        help_text='Session description / PR description'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    submitted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the PR was created'
    )
    
    class Meta:
        db_table = 'wiki_edit_session'
        verbose_name = 'Edit Session'
        verbose_name_plural = 'Edit Sessions'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['space', 'status']),
            models.Index(fields=['user', 'space', 'status']),
        ]
    
    def __str__(self):
        return f"{self.user.username} - {self.space.name} - {self.title} ({self.status})"
    
    def get_branch_name(self) -> str:
        """Generate unique branch name for this session."""
        if self.branch_name:
            return self.branch_name
        short_id = str(self.id)[:8]
        safe_username = self.user.username.lower().replace('.', '-')
        return f"doclab/{safe_username}/edit-{short_id}"
    
    def add_change(self, file_path: str, original_content: str, modified_content: str,
                   change_type: str = 'modify', description: str = ''):
        """Add or update a file change in this session."""
        changes = list(self.pending_changes)
        
        # Check if file already has a change
        for i, change in enumerate(changes):
            if change['file_path'] == file_path:
                # Update existing change
                changes[i] = {
                    'file_path': file_path,
                    'original_content': original_content,
                    'modified_content': modified_content,
                    'change_type': change_type,
                    'description': description,
                }
                self.pending_changes = changes
                return
        
        # Add new change
        changes.append({
            'file_path': file_path,
            'original_content': original_content,
            'modified_content': modified_content,
            'change_type': change_type,
            'description': description,
        })
        self.pending_changes = changes
    
    def remove_change(self, file_path: str):
        """Remove a file change from this session."""
        self.pending_changes = [
            c for c in self.pending_changes if c['file_path'] != file_path
        ]
    
    def get_change(self, file_path: str):
        """Get a specific file change."""
        for change in self.pending_changes:
            if change['file_path'] == file_path:
                return change
        return None
    
    @property
    def change_count(self) -> int:
        """Number of pending changes (both JSON and model-based)."""
        return len(self.pending_changes) + self.changes.count()


class EditSessionChange(models.Model):
    """
    Individual file change within an edit session.
    
    Lifecycle:
    - draft: User saved changes, not yet committed to fork
    - staged: Changes committed to fork branch (has commit_sha)
    - submitted: Part of a PR (session has pr_id)
    
    This replaces the JSON-based pending_changes for new changes.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    session = models.ForeignKey(
        EditSession,
        on_delete=models.CASCADE,
        related_name='changes'
    )
    
    # File info
    file_path = models.CharField(max_length=500, help_text='Path relative to repo root')
    
    class ChangeType(models.TextChoices):
        MODIFY = 'modify', 'Modify existing file'
        CREATE = 'create', 'Create new file'
        DELETE = 'delete', 'Delete file'
    
    change_type = models.CharField(
        max_length=20,
        choices=ChangeType.choices,
        default=ChangeType.MODIFY
    )
    
    # Content
    original_content = models.TextField(
        blank=True,
        help_text='Original file content (empty for create)'
    )
    modified_content = models.TextField(
        blank=True,
        help_text='Modified file content (empty for delete)'
    )
    description = models.CharField(
        max_length=500,
        blank=True,
        help_text='Description of this change'
    )
    
    # Status tracking
    class Status(models.TextChoices):
        DRAFT = 'draft', 'Draft - Not yet committed'
        STAGED = 'staged', 'Staged - Committed to fork'
        SUBMITTED = 'submitted', 'Submitted - In PR'
        MERGED = 'merged', 'Merged'
        DISCARDED = 'discarded', 'Discarded'
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT
    )
    
    # Git info (populated when staged)
    commit_sha = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        help_text='SHA of the commit containing this change'
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    staged_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the change was committed to fork'
    )
    
    class Meta:
        db_table = 'wiki_edit_session_change'
        verbose_name = 'Edit Session Change'
        verbose_name_plural = 'Edit Session Changes'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['session', 'status']),
            models.Index(fields=['file_path']),
            models.Index(fields=['status']),
        ]
        unique_together = [['session', 'file_path']]
    
    def __str__(self):
        return f"{self.file_path} ({self.status})"
    
    def generate_diff(self) -> str:
        """Generate unified diff for this change."""
        import difflib
        
        if self.change_type == 'delete':
            original_lines = self.original_content.split('\n')
            return '\n'.join(difflib.unified_diff(
                original_lines, [],
                fromfile=f'a/{self.file_path}',
                tofile=f'b/{self.file_path}',
                lineterm=''
            ))
        elif self.change_type == 'create':
            modified_lines = self.modified_content.split('\n')
            return '\n'.join(difflib.unified_diff(
                [], modified_lines,
                fromfile=f'a/{self.file_path}',
                tofile=f'b/{self.file_path}',
                lineterm=''
            ))
        else:
            original_lines = self.original_content.split('\n')
            modified_lines = self.modified_content.split('\n')
            return '\n'.join(difflib.unified_diff(
                original_lines, modified_lines,
                fromfile=f'a/{self.file_path}',
                tofile=f'b/{self.file_path}',
                lineterm=''
            ))


class UserDraftChange(models.Model):
    """
    Draft change - saved in DocLab but not yet committed to git.
    
    This is shown as 'edit' enrichment.
    Actions: Stage (commit to fork), Discard
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='draft_changes'
    )
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name='draft_changes'
    )
    
    # File info
    file_path = models.CharField(max_length=500, help_text='Path relative to repo root')
    
    class ChangeType(models.TextChoices):
        MODIFY = 'modify', 'Modify existing file'
        CREATE = 'create', 'Create new file'
        DELETE = 'delete', 'Delete file'
    
    change_type = models.CharField(
        max_length=20,
        choices=ChangeType.choices,
        default=ChangeType.MODIFY
    )
    
    # Content
    original_content = models.TextField(
        blank=True,
        help_text='Original file content (empty for create)'
    )
    modified_content = models.TextField(
        blank=True,
        help_text='Modified file content (empty for delete)'
    )
    description = models.CharField(
        max_length=500,
        blank=True,
        help_text='Description of this change'
    )
    
    # Task association (which branch/task this draft belongs to)
    user_branch = models.ForeignKey(
        'UserBranch',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='draft_changes',
        help_text='Task (branch) this draft is staged for'
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'wiki_user_draft_change'
        verbose_name = 'User Draft Change'
        verbose_name_plural = 'User Draft Changes'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'space']),
            models.Index(fields=['file_path']),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'user_branch', 'file_path'],
                condition=models.Q(user_branch__isnull=False),
                name='unique_draft_per_branch_file',
            ),
        ]
    
    def __str__(self):
        return f"{self.user.username}: {self.file_path}"
    
    def generate_diff_hunks(self):
        """Generate diff hunks for this change."""
        import difflib
        import re
        
        original_lines = self.original_content.split('\n')
        modified_lines = self.modified_content.split('\n')
        
        diff = list(difflib.unified_diff(
            original_lines,
            modified_lines,
            lineterm='',
            n=3
        ))
        
        if len(diff) < 3:
            return []
        
        hunks = []
        current_hunk = None
        
        for line in diff[2:]:
            if line.startswith('@@'):
                if current_hunk:
                    hunks.append(current_hunk)
                
                match = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
                if match:
                    current_hunk = {
                        'old_start': int(match.group(1)),
                        'old_count': int(match.group(2) or 1),
                        'new_start': int(match.group(3)),
                        'new_count': int(match.group(4) or 1),
                        'lines': []
                    }
            elif current_hunk is not None:
                current_hunk['lines'].append(line)
        
        if current_hunk:
            hunks.append(current_hunk)
        
        return hunks


class UserBranch(models.Model):
    """
    Tracks user's edit branch in the fork.
    
    Staged changes are commits in this branch.
    Shown as 'edit_staged' enrichment (changes read from git).
    Actions: Create PR, Unstage
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='edit_branches'
    )
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name='edit_branches'
    )
    
    # Task info
    name = models.CharField(
        max_length=200,
        blank=True,
        help_text='Human-readable task name (e.g. "Requirements documents fix")'
    )
    is_selected = models.BooleanField(
        default=False,
        help_text='Whether this is the currently active task for this user+space'
    )

    # Branch info
    branch_name = models.CharField(
        max_length=200,
        help_text='Branch name on edit fork'
    )
    base_branch = models.CharField(
        max_length=100,
        default='master',
        help_text='Base branch to create PR against'
    )
    last_commit_sha = models.CharField(
        max_length=40,
        null=True,
        blank=True,
        help_text='SHA of the last commit on this branch'
    )
    
    # PR info (set when PR is created)
    pr_id = models.IntegerField(
        null=True,
        blank=True,
        help_text='Pull request ID'
    )
    pr_url = models.URLField(
        max_length=500,
        null=True,
        blank=True,
        help_text='URL to the pull request'
    )

    # Conflict state (populated when rebase fails)
    conflict_files = models.JSONField(
        default=list,
        blank=True,
        help_text='Files with unresolved rebase conflicts'
    )

    # Status
    class Status(models.TextChoices):
        ACTIVE = 'active', 'Active - Has staged changes'
        PR_OPEN = 'pr_open', 'PR Open'
        PR_MERGED = 'pr_merged', 'PR Merged'
        PR_CLOSED = 'pr_closed', 'PR Closed'
        ABANDONED = 'abandoned', 'Abandoned'
    
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.ACTIVE
    )
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'wiki_user_branch'
        verbose_name = 'User Branch'
        verbose_name_plural = 'User Branches'
        ordering = ['-updated_at']
        indexes = [
            models.Index(fields=['user', 'space']),
            models.Index(fields=['status']),
        ]
        unique_together = [['user', 'space', 'branch_name']]
    
    def __str__(self):
        return f"{self.user.username}: {self.branch_name}"
    
    @classmethod
    def get_selected_for_user(cls, user, space):
        """Return the currently selected branch for user+space, or None."""
        return cls.objects.filter(
            user=user, space=space, is_selected=True,
            status__in=[cls.Status.ACTIVE, cls.Status.PR_OPEN],
        ).first()

    @classmethod
    def set_selected(cls, branch):
        """Mark branch as selected; deselect all others for same user+space."""
        cls.objects.filter(
            user=branch.user, space=branch.space, is_selected=True
        ).exclude(pk=branch.pk).update(is_selected=False)
        branch.is_selected = True
        branch.save(update_fields=['is_selected'])

    @staticmethod
    def generate_branch_name(user, task_name=''):
        """Generate a git branch name from a task name (or random)."""
        import uuid as uuid_module
        import re
        short_id = str(uuid_module.uuid4())[:8]
        safe_username = re.sub(r'[^a-z0-9]', '-', user.username.lower()).strip('-')[:20]
        if task_name:
            slug = re.sub(r'[^a-z0-9]+', '-', task_name.lower()).strip('-')[:30]
            return f"doclab/{safe_username}/{slug}-{short_id}"
        return f"doclab/{safe_username}/task-{short_id}"


class SpacePermission(models.Model):
    """
    Defines user-specific permissions for a space.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name='permissions'
    )
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='space_permissions'
    )
    
    ROLE_CHOICES = [
        ('viewer', 'Viewer - Read only'),
        ('editor', 'Editor - Can edit pages'),
        ('admin', 'Admin - Full control'),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='viewer')
    
    created_at = models.DateTimeField(auto_now_add=True)
    granted_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        related_name='granted_permissions'
    )
    
    class Meta:
        db_table = 'space_permissions'
        unique_together = [['space', 'user']]
        indexes = [
            models.Index(fields=['user', 'role']),
        ]
        verbose_name = 'Space Permission'
        verbose_name_plural = 'Space Permissions'
    
    def __str__(self):
        return f"{self.user.username} - {self.space.name} ({self.role})"


class SpaceConfiguration(models.Model):
    """
    Stores configuration settings for a space.
    Uses JSON field for flexible schema.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.OneToOneField(
        Space,
        on_delete=models.CASCADE,
        related_name='configuration'
    )
    
    # File tree to page mapping configuration
    file_tree_config = models.JSONField(
        default=dict,
        help_text="""
        Configuration for mapping Git file tree to pages.
        Example: {
            "root_path": "/docs",
            "file_extensions": [".md", ".mdx"],
            "ignore_patterns": ["node_modules", ".git"],
            "title_from_frontmatter": true,
            "title_field": "title"
        }
        """
    )
    
    # Page display configuration
    page_display_config = models.JSONField(
        default=dict,
        help_text="""
        Configuration for page display.
        Example: {
            "show_breadcrumbs": true,
            "show_toc": true,
            "show_last_updated": true,
            "default_view_mode": "document"
        }
        """
    )
    
    # Sync configuration
    sync_config = models.JSONField(
        default=dict,
        help_text="""
        Configuration for Git sync.
        Example: {
            "auto_sync": true,
            "sync_interval_minutes": 15,
            "sync_on_webhook": true,
            "conflict_resolution": "git_wins"
        }
        """
    )
    
    # Custom settings (extensible)
    custom_settings = models.JSONField(
        default=dict,
        help_text="Additional custom settings"
    )
    
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        db_table = 'space_configurations'
        verbose_name = 'Space Configuration'
        verbose_name_plural = 'Space Configurations'
    
    def __str__(self):
        return f"Configuration for {self.space.name}"


class SpaceShortcut(models.Model):
    """
    User-defined shortcuts to pages within a space.
    Similar to Confluence space shortcuts.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name='shortcuts'
    )
    # Note: Will reference Page model once it's created
    page_id = models.IntegerField(
        help_text='ID of the target page'
    )
    
    label = models.CharField(max_length=200)
    order = models.IntegerField(default=0)
    
    created_by = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='created_shortcuts'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        db_table = 'space_shortcuts'
        ordering = ['order', 'label']
        verbose_name = 'Space Shortcut'
        verbose_name_plural = 'Space Shortcuts'
    
    def __str__(self):
        return f"{self.space.name} - {self.label}"


class UserSpacePreference(models.Model):
    """
    User-specific preferences for spaces (favorites, recent, etc.)
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='space_preferences'
    )
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name='user_preferences'
    )
    
    is_favorite = models.BooleanField(default=False)
    last_visited_at = models.DateTimeField(auto_now=True)
    visit_count = models.IntegerField(default=0)
    
    # Last viewed page in this space (will be FK to Page once created)
    last_viewed_page_id = models.IntegerField(
        null=True,
        blank=True,
        help_text='ID of last viewed page'
    )
    
    class Meta:
        db_table = 'user_space_preferences'
        unique_together = [['user', 'space']]
        indexes = [
            models.Index(fields=['user', '-last_visited_at']),
            models.Index(fields=['user', 'is_favorite']),
        ]
        verbose_name = 'User Space Preference'
        verbose_name_plural = 'User Space Preferences'
    
    def __str__(self):
        return f"{self.user.username} - {self.space.name}"


class SpaceAttribute(models.Model):
    """
    Extended attributes for spaces using EAV pattern.
    Allows flexible addition of custom properties without schema changes.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name='attributes'
    )
    
    # Attribute identification
    field_id = models.CharField(
        max_length=100,
        db_index=True,
        help_text='Machine identifier (e.g., total_pages, health_score)'
    )
    field_name = models.CharField(
        max_length=200,
        help_text='Human-readable label (e.g., "Total Pages", "Health Score")'
    )
    
    # Multi-type value storage
    field_value_str = models.TextField(
        null=True,
        blank=True,
        help_text='String/JSON/enum value'
    )
    field_value_int = models.BigIntegerField(
        null=True,
        blank=True,
        help_text='Integer or boolean (0/1) value'
    )
    field_value_float = models.FloatField(
        null=True,
        blank=True,
        help_text='Fractional numeric value'
    )
    
    # Metadata
    collected_at = models.DateTimeField(
        auto_now_add=True,
        help_text='When this attribute was collected/computed'
    )
    data_source = models.CharField(
        max_length=100,
        default='',
        blank=True,
        help_text='Source discriminator (e.g., git_sync, manual, computed)'
    )
    
    # Versioning for deduplication
    version = models.BigIntegerField(
        default=0,
        help_text='Version number for deduplication'
    )
    
    class Meta:
        db_table = 'space_attributes'
        unique_together = [['space', 'field_id', 'version']]
        indexes = [
            models.Index(fields=['space', 'field_id']),
            models.Index(fields=['field_id', 'collected_at']),
            models.Index(fields=['data_source']),
        ]
        verbose_name = 'Space Attribute'
        verbose_name_plural = 'Space Attributes'
    
    def __str__(self):
        return f"{self.space.name} - {self.field_name}"
    
    def get_value(self):
        """Get the actual value based on which field is populated."""
        if self.field_value_str is not None:
            return self.field_value_str
        elif self.field_value_int is not None:
            return self.field_value_int
        elif self.field_value_float is not None:
            return self.field_value_float
        return None


class FileMapping(models.Model):
    """
    Maps repository files to human-readable names for Documents mode.
    Supports folder rules with inheritance and file-level overrides.
    """
    # Primary Key
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Space relationship
    space = models.ForeignKey(
        Space,
        on_delete=models.CASCADE,
        related_name='file_mappings'
    )
    
    # File identification
    file_path = models.CharField(
        max_length=1000,
        help_text='Relative path in repository (e.g., docs/README.md)'
    )
    is_folder = models.BooleanField(
        default=False,
        help_text='True if this is a folder, False for files'
    )
    
    # Display configuration
    is_visible = models.BooleanField(
        default=True,
        help_text='Show in Documents mode'
    )
    display_name = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text='Custom display name (if not auto-extracted)'
    )
    display_name_source = models.CharField(
        max_length=50,
        choices=[
            ('custom', 'Custom Name'),
            ('filename', 'Use Filename'),
            ('first_h1', 'First H1 Header'),
            ('first_h2', 'First H2 Header'),
            ('title_frontmatter', 'Title from Frontmatter'),
        ],
        null=True,
        blank=True,
        help_text='Method for determining display name for this item (null = inherit from parent or space default)'
    )
    children_display_name_source = models.CharField(
        max_length=50,
        choices=[
            ('first_h1', 'First H1 Header'),
            ('first_h2', 'First H2 Header'),
            ('title_frontmatter', 'Title from Frontmatter'),
            ('filename', 'Use Filename'),
        ],
        null=True,
        blank=True,
        help_text='Default display name source for children (folders only)'
    )
    
    # Effective (computed) values - pre-calculated inheritance results
    effective_display_name_source = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text='Computed display name source after inheritance resolution'
    )
    effective_is_visible = models.BooleanField(
        default=True,
        help_text='Computed visibility after inheritance resolution'
    )
    
    # Extracted name cache
    extracted_name = models.CharField(
        max_length=500,
        null=True,
        blank=True,
        help_text='Cached extracted name from file content'
    )
    extracted_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the name was last extracted'
    )
    
    # Ordering
    sort_order = models.IntegerField(
        null=True,
        blank=True,
        help_text='Custom sort order (null = auto)'
    )
    
    # Icon (optional)
    icon = models.CharField(
        max_length=10,
        null=True,
        blank=True,
        help_text='Emoji or icon name'
    )
    
    # Rule inheritance (for folders)
    apply_to_children = models.BooleanField(
        default=False,
        help_text='Apply this configuration to all children (folders only)'
    )
    parent_rule = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='child_mappings',
        help_text='Parent folder rule (if inherited)'
    )
    is_override = models.BooleanField(
        default=False,
        help_text='True if this mapping overrides parent folder rules'
    )
    
    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_file_mappings'
    )
    
    class Meta:
        db_table = 'wiki_file_mapping'
        unique_together = [['space', 'file_path']]
        indexes = [
            models.Index(fields=['space', 'is_visible']),
            models.Index(fields=['space', 'file_path']),
            models.Index(fields=['space', 'is_folder']),
        ]
        verbose_name = 'File Mapping'
        verbose_name_plural = 'File Mappings'
    
    def __str__(self):
        return f"{self.space.name}: {self.file_path}"
    
    def get_display_name(self):
        """Get the effective display name for this file."""
        if self.display_name_source == 'custom' and self.display_name:
            return self.display_name
        elif self.display_name_source == 'filename':
            return self.file_path.split('/')[-1]
        elif self.extracted_name:
            return self.extracted_name
        else:
            # Fallback to filename
            return self.file_path.split('/')[-1]
    
    def compute_effective_values(self):
        """
        Compute effective display_name_source and is_visible after inheritance.
        Returns tuple: (effective_source, effective_visible)
        """
        # Determine display name source
        if self.is_folder:
            # Folders always use filename or custom (no inheritance for display name)
            source = self.display_name_source or 'filename'
        elif self.display_name_source:
            # Files with explicit setting use it
            source = self.display_name_source
        else:
            # Files: Walk up parent folders for children_display_name_source
            source = None
            path_parts = self.file_path.split('/')
            
            for i in range(len(path_parts) - 1, 0, -1):
                parent_path = '/'.join(path_parts[:i])
                try:
                    parent = FileMapping.objects.get(
                        space=self.space,
                        file_path=parent_path,
                        is_folder=True
                    )
                    if parent.children_display_name_source:
                        source = parent.children_display_name_source
                        break
                except FileMapping.DoesNotExist:
                    continue
            
            # Files: Fall back to space default if no parent rule found
            if source is None:
                source = self.space.default_display_name_source or 'first_h1'
        
        # Determine visibility - check entire parent chain
        # If ANY parent is hidden, this item should be hidden
        visible = self.is_visible
        if visible:  # Only check parents if this item itself is visible
            path_parts = self.file_path.split('/')
            for i in range(len(path_parts) - 1, 0, -1):
                parent_path = '/'.join(path_parts[:i])
                try:
                    parent = FileMapping.objects.get(
                        space=self.space,
                        file_path=parent_path,
                        is_folder=True
                    )
                    if not parent.is_visible:
                        visible = False
                        break
                except FileMapping.DoesNotExist:
                    continue
        
        return (source, visible)
    
    def save(self, *args, **kwargs):
        """Override save to compute effective values."""
        effective_source, effective_visible = self.compute_effective_values()
        self.effective_display_name_source = effective_source
        self.effective_is_visible = effective_visible
        super().save(*args, **kwargs)
