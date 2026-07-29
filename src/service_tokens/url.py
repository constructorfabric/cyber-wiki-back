"""Canonical-form helpers for service base URLs.

`Space.git_base_url` and `ServiceToken.base_url` are matched by exact
equality at lookup time. To make that match reliable, both columns go
through `canonical_base_url()` on save and at migration time, so values
that differ only by trailing slash or scheme/host casing are stored
identically.
"""
from urllib.parse import urlsplit, urlunsplit


PUBLIC_GITHUB_BASE_URL = 'https://api.github.com'
PUBLIC_GITHUB_WEB_BASE_URL = 'https://github.com'


def canonical_base_url(value):
    """Return a canonical form of a service base URL.

    - Empty / None values are returned unchanged (the column is nullable
      and an empty string is a valid "no base_url" marker for
      custom-header tokens).
    - Scheme and host are lower-cased (URL spec: case-insensitive).
    - Trailing slash on the path is stripped.
    - Path, query, and fragment are otherwise preserved (case-sensitive).
    """
    if not value:
        return value
    parts = urlsplit(value)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip('/')
    return urlunsplit((scheme, netloc, path, parts.query, parts.fragment))


def normalize_service_base_url(service_type, value):
    """Return the canonical API base URL for a provider."""
    canonical = canonical_base_url(value)
    if service_type != 'github':
        return canonical
    if not canonical:
        return PUBLIC_GITHUB_BASE_URL

    parts = urlsplit(canonical)
    scheme = parts.scheme or 'https'
    netloc = parts.netloc.lower()
    path = parts.path.rstrip('/')

    if netloc in {'github.com', 'api.github.com'}:
        return PUBLIC_GITHUB_BASE_URL

    if not path:
        path = '/api/v3'
    elif not path.endswith('/api/v3'):
        path = f'{path}/api/v3'

    return urlunsplit((scheme, netloc, path, '', ''))


def service_base_url_lookup_candidates(service_type, value):
    """Return exact-match base_url candidates for token lookup."""
    canonical = canonical_base_url(value)
    if service_type != 'github':
        return [canonical] if canonical else []

    candidates = [normalize_service_base_url(service_type, canonical)]
    if not canonical:
        candidates.append(PUBLIC_GITHUB_WEB_BASE_URL)
    else:
        parts = urlsplit(canonical)
        scheme = parts.scheme or 'https'
        root_url = urlunsplit((scheme, parts.netloc.lower(), '', '', ''))
        candidates.append(root_url)
        if parts.netloc.lower() in {'github.com', 'api.github.com'}:
            candidates.append(PUBLIC_GITHUB_WEB_BASE_URL)

    deduped = []
    for candidate in candidates:
        if candidate and candidate not in deduped:
            deduped.append(candidate)
    return deduped
