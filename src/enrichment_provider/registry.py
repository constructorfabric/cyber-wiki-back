"""
Enrichment provider registry and aggregator.
"""
from typing import List, Dict, Any
from .base import BaseEnrichmentProvider
from .comment_enrichment import CommentEnrichmentProvider
from .local_changes_enrichment import LocalChangesEnrichmentProvider
from .pr_enrichment import PREnrichmentProvider
from .diff_enrichment import DiffEnrichmentProvider
from .edit_session_enrichment import EditEnrichmentProvider, CommitEnrichmentProvider


class EnrichmentRegistry:
    """
    Registry for enrichment providers.
    Manages all available enrichment providers and aggregates their results.
    """
    
    def __init__(self):
        self._providers: List[BaseEnrichmentProvider] = []
        self._register_default_providers()
    
    def _register_default_providers(self):
        """Register default enrichment providers."""
        self.register(CommentEnrichmentProvider())
        self.register(LocalChangesEnrichmentProvider())
        self.register(PREnrichmentProvider())
        self.register(DiffEnrichmentProvider())
        self.register(EditEnrichmentProvider())
        self.register(CommitEnrichmentProvider())
    
    def register(self, provider: BaseEnrichmentProvider):
        """
        Register an enrichment provider.
        
        Args:
            provider: Enrichment provider instance
        """
        self._providers.append(provider)
    
    def get_providers(self) -> List[BaseEnrichmentProvider]:
        """Get all registered providers."""
        return self._providers
    
    def get_provider_by_type(self, enrichment_type: str) -> BaseEnrichmentProvider:
        """
        Get provider by enrichment type.
        
        Args:
            enrichment_type: Type of enrichment
            
        Returns:
            Provider instance or None
        """
        for provider in self._providers:
            if provider.get_enrichment_type() == enrichment_type:
                return provider
        return None
    
    def get_enrichment_types(self) -> List[str]:
        """
        Get list of all available enrichment types.
        
        Returns:
            List of enrichment type strings
        """
        return [provider.get_enrichment_type() for provider in self._providers]
    
    def get_all_enrichments(self, source_uri: str, user) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get enrichments from all providers for a source URI.
        Providers are called concurrently so fast ones (comments, edits) don't
        wait behind slow ones (PR diffs that hit external APIs).
        
        Args:
            source_uri: Universal source address
            user: Django User instance
            
        Returns:
            Dictionary mapping enrichment types to lists of enrichments
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import logging
        logger = logging.getLogger(__name__)

        result = {}

        def _call_provider(provider):
            enrichment_type = provider.get_enrichment_type()
            try:
                enrichments = provider.get_enrichments(source_uri, user)
                return enrichment_type, enrichments or []
            except Exception as e:
                logger.error(f"Error getting enrichments from {enrichment_type} provider: {e}", exc_info=True)
                return enrichment_type, []

        with ThreadPoolExecutor(max_workers=len(self._providers)) as pool:
            futures = [pool.submit(_call_provider, p) for p in self._providers]
            for fut in as_completed(futures):
                enrichment_type, enrichments = fut.result()
                if enrichments:
                    result[enrichment_type] = enrichments
        
        return result
    
    def get_enrichments_by_type(self, source_uri: str, user, enrichment_type: str) -> List[Dict[str, Any]]:
        """
        Get enrichments of a specific type for a source URI.
        
        Args:
            source_uri: Universal source address
            user: Django User instance
            enrichment_type: Type of enrichment to retrieve
            
        Returns:
            List of enrichments
        """
        provider = self.get_provider_by_type(enrichment_type)
        if provider:
            try:
                return provider.get_enrichments(source_uri, user)
            except Exception as e:
                import logging
                logger = logging.getLogger(__name__)
                logger.error(f"Error getting {enrichment_type} enrichments: {e}", exc_info=True)
        return []
    
    def get_enrichment_metadata(self) -> Dict[str, Dict[str, str]]:
        """
        Get metadata for all enrichment types.
        
        Returns:
            Dictionary mapping enrichment types to their metadata (category, etc.)
        """
        metadata = {}
        for provider in self._providers:
            enrichment_type = provider.get_enrichment_type()
            metadata[enrichment_type] = {
                'type': enrichment_type,
                'category': provider.get_enrichment_category(),
            }
        return metadata


# Global registry instance
_registry = None


def get_registry() -> EnrichmentRegistry:
    """Get the global enrichment registry instance."""
    global _registry
    if _registry is None:
        _registry = EnrichmentRegistry()
    return _registry
