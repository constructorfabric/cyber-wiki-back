"""
Comment enrichment provider.
"""
from typing import List, Dict, Any
from .base import BaseEnrichmentProvider, EnrichmentCategory
from source_provider.base import SourceAddress
from wiki.models import FileComment


class CommentEnrichmentProvider(BaseEnrichmentProvider):
    """
    Provides file comments as enrichments.
    """
    
    def get_enrichments(self, source_uri: str, user) -> List[Dict[str, Any]]:
        """
        Get comments for a source URI.
        Returns root comments with nested replies structure.
        
        Args:
            source_uri: Universal source address
            user: Django User instance
        
        Returns:
            List of root comment enrichments with nested replies
        """
        address = SourceAddress.parse(source_uri)

        # Match persisted legacy/comment-compatible URIs as the same file.
        root_comments = FileComment.objects.filter(
            source_uri__in=address.equivalent_comment_uris(),
            parent_comment=None
        ).select_related('author').prefetch_related('replies').order_by('line_start', 'created_at')
        
        def serialize_comment(comment):
            """Recursively serialize comment with replies."""
            data = {
                'type': 'comment',
                'id': str(comment.id),
                'source_uri': source_uri,
                'line_start': comment.line_start,
                'line_end': comment.line_end,
                'text': comment.text,
                'author': comment.author.username,
                'thread_id': str(comment.thread_id),
                'parent_id': str(comment.parent_comment.id) if comment.parent_comment else None,
                'is_resolved': comment.is_resolved,
                'anchoring_status': comment.anchoring_status,
                'created_at': comment.created_at.isoformat(),
                'updated_at': comment.updated_at.isoformat(),
            }
            
            # Recursively add replies
            if comment.replies.exists():
                data['replies'] = [serialize_comment(reply) for reply in comment.replies.all()]
            else:
                data['replies'] = []
            
            return data
        
        return [serialize_comment(comment) for comment in root_comments]
    
    def get_enrichment_type(self) -> str:
        return 'comments'
    
    def get_enrichment_category(self) -> str:
        return EnrichmentCategory.REFERENCE
