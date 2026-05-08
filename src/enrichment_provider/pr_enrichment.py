"""
Pull request enrichment provider.
"""
import logging
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Any
from .base import BaseEnrichmentProvider, EnrichmentCategory
from source_provider.base import SourceAddress
from git_provider.factory import GitProviderFactory
from service_tokens.models import ServiceToken

_HUNK_HEADER_RE = re.compile(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@')

logger = logging.getLogger(__name__)


class PREnrichmentProvider(BaseEnrichmentProvider):
    """
    Provides PR diffs as enrichments for files that have open PRs.
    """
    
    def get_enrichments(self, source_uri: str, user) -> List[Dict[str, Any]]:
        """
        Get PR enrichments for a source URI.
        
        Args:
            source_uri: Universal source address
            user: Django User instance
        
        Returns:
            List of PR enrichments
        """
        start_time = time.time()
        try:
            # Parse source address
            parse_start = time.time()
            address = SourceAddress.parse(source_uri)
            logger.debug(f"[PR] Parse URI took {time.time() - parse_start:.3f}s")
            
            # Get Git provider
            token_start = time.time()
            service_token = ServiceToken.objects.filter(
                user=user,
                service_type=address.provider
            ).first()
            
            if not service_token:
                logger.debug(f"[PR] No service token for {address.provider}")
                return []
            
            provider = GitProviderFactory.create_from_service_token(service_token)
            logger.debug(f"[PR] Get provider took {time.time() - token_start:.3f}s")
            
            # Get ALL open PRs for this repository
            list_start = time.time()
            prs_response = provider.list_pull_requests(
                repo_id=address.repository,
                state='open',
                page=1,
                per_page=1000  # Fetch all open PRs (most repos have < 1000 open PRs)
            )
            list_duration = time.time() - list_start
            pr_count = len(prs_response.get('pull_requests', []))
            logger.info(f"[PR] List {pr_count} open PRs took {list_duration:.3f}s")
            
            enrichments = []
            
            # Check if this file is modified in any PR — fetch diffs in parallel.
            check_start = time.time()
            prs = prs_response.get('pull_requests', [])
            _PR_DIFF_MAX_WORKERS = 8

            def _fetch_pr_diff(pr):
                """Fetch diff for one PR and return enrichment dict or None."""
                try:
                    pr_file_start = time.time()
                    diff_text = provider.get_pull_request_diff(
                        repo_id=address.repository,
                        pr_number=pr['number']
                    )
                    if not diff_text:
                        logger.debug(f"[PR] No diff available for PR #{pr['number']}")
                        return None

                    # Fast pre-filter: skip full parse when the file path doesn't appear as a
                    # diff marker in this PR at all. Handles both "a/path" and bare "path" formats.
                    if (f'+++ b/{address.path}' not in diff_text and
                            f'+++ {address.path}' not in diff_text):
                        logger.debug(f"[PR] Skipping PR #{pr['number']} (file not in diff)")
                        return None

                    hunks = self._parse_diff_hunks(diff_text, address.path)

                    pr_file_duration = time.time() - pr_file_start
                    logger.debug(f"[PR] Check PR #{pr['number']} took {pr_file_duration:.3f}s (hunks: {len(hunks)})")

                    if hunks:
                        return {
                            'type': 'pr_diff',
                            'pr_number': pr['number'],
                            'pr_title': pr['title'],
                            'pr_author': pr['author'],
                            'pr_state': pr['state'],
                            'pr_url': pr['url'],
                            'from_branch': pr.get('from_branch', ''),
                            'created_at': pr['created_at'],
                            'reviewers': pr.get('reviewers', []),
                            'diff_hunks': hunks,
                        }
                except Exception as e:
                    logger.warning(f"Failed to get files for PR {pr['number']}: {e}")
                    logger.debug(f"Traceback: {traceback.format_exc()}")
                return None

            if prs:
                max_workers = min(_PR_DIFF_MAX_WORKERS, len(prs))
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    futures = {pool.submit(_fetch_pr_diff, pr): pr for pr in prs}
                    for fut in as_completed(futures):
                        result = fut.result()
                        if result:
                            enrichments.append(result)

            check_duration = time.time() - check_start
            logger.info(f"[PR] Check all PRs took {check_duration:.3f}s, found {len(enrichments)} matches")
            
            total_duration = time.time() - start_time
            logger.info(f"[PR] Total PR enrichment time: {total_duration:.3f}s for {address.path}")
            
            return enrichments
        
        except Exception as e:
            logger.error(f"Failed to get PR enrichments: {e}")
            return []
    
    def _parse_diff_hunks(self, diff_text: str, file_path: str) -> List[Dict[str, Any]]:
        """
        Parse unified diff format to extract hunks for a specific file.
        
        Returns list of hunks with line ranges:
        [
            {
                'old_start': 10,
                'old_count': 5,
                'new_start': 10,
                'new_count': 7,
                'lines': [...]
            }
        ]
        """
        logger.debug(f"[PR] Parsing diff for file: {file_path}, diff length: {len(diff_text)} chars")
        hunks = []
        in_file = False
        current_hunk = None
        file_marker_count = 0
        seen_files = []
        
        for line in diff_text.split('\n'):
            # Check if we're entering the section for our file
            # Git diff format: --- a/path/to/file or +++ b/path/to/file or --- path/to/file
            if line.startswith('---') or line.startswith('+++'):
                file_marker_count += 1
                # Extract filename from diff marker (handle a/ and b/ prefixes)
                # Examples: "--- a/README.md", "+++ b/README.md", "--- README.md", "--- tools/README.md"
                marker_file = line[4:].strip()  # Remove "--- " or "+++ "
                if marker_file.startswith('a/') or marker_file.startswith('b/'):
                    marker_file = marker_file[2:]  # Remove a/ or b/ prefix
                
                # Track all unique files we see
                if marker_file and marker_file not in seen_files and marker_file != '/dev/null':
                    seen_files.append(marker_file)
                
                # Match only if exact path match
                # file_path from source_uri is the full path (e.g., "tools/standctl/.trufflehog3.yml" or "README.md")
                is_match = (marker_file == file_path)
                
                if is_match:
                    in_file = True
                    logger.debug(f"[PR] Matched our file: {line} (looking for: {file_path})")
                elif marker_file:  # Only exit if we see a non-empty different file
                    # We're entering a different file's section
                    # Log all file markers we see to help debug
                    if file_marker_count <= 10:  # Only log first 10 to avoid spam
                        logger.debug(f"[PR] Saw different file: {marker_file} (looking for: {file_path})")
                    in_file = False
                continue
            
            # Parse hunk header: @@ -old_start,old_count +new_start,new_count @@
            if in_file and line.startswith('@@'):
                logger.debug(f"[PR] Found hunk header while in_file=True: {line}")
                if current_hunk:
                    hunks.append(current_hunk)
                
                match = _HUNK_HEADER_RE.match(line)
                if match:
                    old_start = int(match.group(1))
                    old_count = int(match.group(2)) if match.group(2) else 1
                    new_start = int(match.group(3))
                    new_count = int(match.group(4)) if match.group(4) else 1
                    
                    current_hunk = {
                        'old_start': old_start,
                        'old_count': old_count,
                        'new_start': new_start,
                        'new_count': new_count,
                        'lines': []
                    }
            
            # Collect hunk lines
            elif in_file and current_hunk is not None:
                if line.startswith('+') or line.startswith('-') or line.startswith(' '):
                    current_hunk['lines'].append(line)
        
        # Don't forget the last hunk
        if current_hunk:
            hunks.append(current_hunk)
        
        logger.debug(f"[PR] Found {len(hunks)} hunks for file {file_path} (file_markers: {file_marker_count}, in_file: {in_file})")
        if len(hunks) == 0 and file_marker_count > 0:
            logger.warning(f"[PR] No hunks found but saw {file_marker_count} file markers. File path might not match. Looking for: {file_path}")
            logger.warning(f"[PR] All files seen in diff: {seen_files}")
        
        return hunks
    
    def get_enrichments_stream(self, source_uri: str, user):
        """
        Generator version of get_enrichments that yields progress events.

        Yields dicts:
          {'type': 'progress', 'message': str}   -- during processing
          {'type': 'result',   'data': list}      -- final enrichments list
          {'type': 'error',    'message': str}    -- on hard failure (also yields result=[])
        """
        start_time = time.time()
        try:
            address = SourceAddress.parse(source_uri)
            service_token = ServiceToken.objects.filter(
                user=user, service_type=address.provider
            ).first()
            if not service_token:
                yield {'type': 'result', 'data': []}
                return

            provider = GitProviderFactory.create_from_service_token(service_token)
            prs_response = provider.list_pull_requests(
                repo_id=address.repository, state='open', page=1, per_page=1000
            )
            prs = prs_response.get('pull_requests', [])
            file_name = address.path.split('/')[-1]

            yield {'type': 'progress', 'message': f'Checking {len(prs)} open PRs for {file_name}…'}

            enrichments = []
            for idx, pr in enumerate(prs, 1):
                title = pr['title']
                short_title = title[:50] + '…' if len(title) > 50 else title
                yield {'type': 'progress', 'message': f"PR #{pr['number']} ({idx}/{len(prs)}): {short_title}"}

                try:
                    diff_text = provider.get_pull_request_diff(
                        repo_id=address.repository, pr_number=pr['number']
                    )
                    if not diff_text:
                        continue
                    if (f'+++ b/{address.path}' not in diff_text and
                            f'+++ {address.path}' not in diff_text):
                        continue
                    hunks = self._parse_diff_hunks(diff_text, address.path)
                    if hunks:
                        enrichments.append({
                            'type': 'pr_diff',
                            'pr_number': pr['number'],
                            'pr_title': pr['title'],
                            'pr_author': pr['author'],
                            'pr_state': pr['state'],
                            'pr_url': pr['url'],
                            'from_branch': pr.get('from_branch', ''),
                            'created_at': pr['created_at'],
                            'reviewers': pr.get('reviewers', []),
                            'diff_hunks': hunks,
                        })
                except Exception as e:
                    logger.warning(f"[PR] Stream: failed on PR #{pr.get('number')}: {e}")

            logger.info(
                f"[PR] Stream: {len(enrichments)} match(es) in {time.time() - start_time:.3f}s "
                f"for {address.path}"
            )
            yield {'type': 'result', 'data': enrichments}

        except Exception as e:
            logger.error(f"[PR] Stream failed: {e}")
            yield {'type': 'error', 'message': str(e)}
            yield {'type': 'result', 'data': []}

    def get_enrichment_type(self) -> str:
        return 'pr_diff'
    
    def get_enrichment_category(self) -> str:
        return EnrichmentCategory.DIFF
