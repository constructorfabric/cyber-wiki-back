"""Canonical-form helper for service base URLs.

`Space.git_base_url` and `ServiceToken.base_url` are matched by exact
equality at lookup time. To make that match reliable, both columns go
through `canonical_base_url()` on save and at migration time, so values
that differ only by trailing slash or scheme/host casing are stored
identically.
"""
from urllib.parse import urlsplit, urlunsplit


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
