"""
Unit tests for source provider base module.

Tested Scenarios:
- SourceAddress parsing from URI (various formats)
- SourceAddress with line numbers (single and range)
- SourceAddress without line numbers
- SourceAddress to_uri() conversion
- SourceAddress string representation
- Invalid URI format handling
- Edge cases (empty paths, special characters)

Untested Scenarios / Gaps:
- BaseSourceProvider implementations (abstract class)
- URI encoding/decoding for special characters
- Very long paths or repository names
- Unicode in paths
- Case sensitivity in provider names

Test Strategy:
- Pure unit tests (no database)
- Test parsing and serialization
- Test error handling
- Test round-trip conversion (parse → to_uri)
"""
import pytest
from source_provider.base import SourceAddress, BaseSourceProvider


class TestSourceAddress:
    """Tests for SourceAddress dataclass."""
    
    def test_parse_simple_uri(self):
        """Test parsing a simple URI without line numbers."""
        uri = "git://github/facebook_react/main/README.md"
        addr = SourceAddress.parse(uri)
        
        assert addr.provider == "github"
        assert addr.repository == "facebook_react"
        assert addr.branch == "main"
        assert addr.path == "README.md"
        assert addr.line_start is None
        assert addr.line_end is None
    
    def test_parse_uri_with_single_line(self):
        """Test parsing URI with single line number."""
        uri = "git://github/facebook_react/main/src/index.js#42"
        addr = SourceAddress.parse(uri)
        
        assert addr.provider == "github"
        assert addr.repository == "facebook_react"
        assert addr.branch == "main"
        assert addr.path == "src/index.js"
        assert addr.line_start == 42
        assert addr.line_end is None
    
    def test_parse_uri_with_line_range(self):
        """Test parsing URI with line range."""
        uri = "git://github/facebook_react/main/src/index.js#10-20"
        addr = SourceAddress.parse(uri)
        
        assert addr.provider == "github"
        assert addr.repository == "facebook_react"
        assert addr.branch == "main"
        assert addr.path == "src/index.js"
        assert addr.line_start == 10
        assert addr.line_end == 20
    
    def test_parse_uri_with_nested_path(self):
        """Test parsing URI with deeply nested path."""
        uri = "git://bitbucket_server/PROJECT_repo/develop/src/components/Button/index.tsx#5-15"
        addr = SourceAddress.parse(uri)
        
        assert addr.provider == "bitbucket_server"
        assert addr.repository == "PROJECT_repo"
        assert addr.branch == "develop"
        assert addr.path == "src/components/Button/index.tsx"
        assert addr.line_start == 5
        assert addr.line_end == 15
    
    def test_parse_invalid_uri_format(self):
        """Test that invalid URI format raises ValueError."""
        invalid_uris = [
            "not-a-git-uri",
            "git://",
            "git://github",
            "git://github/repo",
            "git://github/repo/branch",  # Missing path
            "http://github.com/repo",
            "git:/github/repo/branch/path",  # Missing //
        ]
        
        for uri in invalid_uris:
            with pytest.raises(ValueError, match="Invalid source URI format"):
                SourceAddress.parse(uri)
    
    def test_to_uri_without_lines(self):
        """Test converting SourceAddress to URI without line numbers."""
        addr = SourceAddress(
            provider="github",
            repository="facebook_react",
            branch="main",
            path="README.md"
        )
        
        assert addr.to_uri() == "git://github/facebook_react/main/README.md"
    
    def test_to_uri_with_single_line(self):
        """Test converting SourceAddress to URI with single line."""
        addr = SourceAddress(
            provider="github",
            repository="facebook_react",
            branch="main",
            path="src/index.js",
            line_start=42
        )
        
        assert addr.to_uri() == "git://github/facebook_react/main/src/index.js#42"
    
    def test_to_uri_with_line_range(self):
        """Test converting SourceAddress to URI with line range."""
        addr = SourceAddress(
            provider="github",
            repository="facebook_react",
            branch="main",
            path="src/index.js",
            line_start=10,
            line_end=20
        )
        
        assert addr.to_uri() == "git://github/facebook_react/main/src/index.js#10-20"
    
    def test_to_uri_with_same_start_end_line(self):
        """Test that same start and end line shows as single line."""
        addr = SourceAddress(
            provider="github",
            repository="facebook_react",
            branch="main",
            path="src/index.js",
            line_start=42,
            line_end=42
        )
        
        # Should show as single line, not range
        assert addr.to_uri() == "git://github/facebook_react/main/src/index.js#42"
    
    def test_string_representation(self):
        """Test __str__ returns URI."""
        addr = SourceAddress(
            provider="github",
            repository="facebook_react",
            branch="main",
            path="README.md",
            line_start=10,
            line_end=20
        )
        
        assert str(addr) == "git://github/facebook_react/main/README.md#10-20"
    
    def test_round_trip_conversion(self):
        """Test that parse → to_uri is idempotent."""
        original_uri = "git://bitbucket_server/PROJECT_repo/feature-branch/docs/api.md#100-200"
        
        addr = SourceAddress.parse(original_uri)
        converted_uri = addr.to_uri()
        
        assert converted_uri == original_uri
    
    def test_parse_path_with_dots(self):
        """Test parsing path with multiple dots."""
        uri = "git://github/repo/main/config.test.js"
        addr = SourceAddress.parse(uri)
        
        assert addr.path == "config.test.js"
    
    def test_parse_pre_query_canonical_github_uri_with_common_branch_name(self):
        uri = "git://github/octo/repo/main/docs/readme.md"
        addr = SourceAddress.parse(uri)

        assert addr.repository == "octo/repo"
        assert addr.branch == "main"
        assert addr.path == "docs/readme.md"

    def test_parse_plain_legacy_github_repo_branch_path_without_regression(self):
        uri = "git://github/repo/main/docs/readme.md"
        addr = SourceAddress.parse(uri)

        assert addr.repository == "repo"
        assert addr.branch == "main"
        assert addr.path == "docs/readme.md"

    def test_parse_canonical_github_owner_repo_uri_with_ref_query(self):
        uri = "git://github/octo/repo/docs/readme.md?ref=main"
        addr = SourceAddress.parse(uri)

        assert addr.provider == "github"
        assert addr.repository == "octo/repo"
        assert addr.branch == "main"
        assert addr.path == "docs/readme.md"

    def test_parse_github_uri_with_query_ref_supports_slash_branch(self):
        uri = "git://github/octo/repo/docs/readme.md?ref=feature%2Fnew-ui"
        addr = SourceAddress.parse(uri)

        assert addr.repository == "octo/repo"
        assert addr.branch == "feature/new-ui"
        assert addr.path == "docs/readme.md"

    def test_parse_canonical_github_repo_root_uri(self):
        uri = "git://github/octo/repo?ref=main"
        addr = SourceAddress.parse(uri)

        assert addr.repository == "octo/repo"
        assert addr.branch == "main"
        assert addr.path == ""

    @pytest.mark.parametrize("branch", ["release-2026", "hotfix", "feature/new-ui"])
    def test_canonical_github_repo_root_round_trips_without_branch_guessing(self, branch):
        addr = SourceAddress(
            provider="github",
            repository="octo/repo",
            branch=branch,
            path="",
        )

        assert SourceAddress.parse(addr.to_uri()) == addr

    def test_to_uri_uses_query_ref_for_github_slash_branches(self):
        addr = SourceAddress(
            provider="github",
            repository="octo/repo",
            branch="feature/new-ui",
            path="docs/readme.md",
        )

        assert addr.to_uri() == "git://github/octo/repo/docs/readme.md?ref=feature%2Fnew-ui"

    def test_to_uri_uses_query_ref_for_github_simple_branches_too(self):
        addr = SourceAddress(
            provider="github",
            repository="octo/repo",
            branch="main",
            path="docs/readme.md",
        )

        assert addr.to_uri() == "git://github/octo/repo/docs/readme.md?ref=main"

    def test_to_uri_uses_query_ref_for_github_repo_roots(self):
        addr = SourceAddress(
            provider="github",
            repository="octo/repo",
            branch="release-2026",
            path="",
        )

        assert addr.to_uri() == "git://github/octo/repo?ref=release-2026"

    def test_parse_github_uri_with_ref_and_base_url_query_context(self):
        uri = "git://github/octo/repo/docs/readme.md?ref=feature%2Fnew-ui&base_url=https%3A%2F%2Fghe.example.com"
        addr = SourceAddress.parse(uri)

        assert addr.repository == "octo/repo"
        assert addr.branch == "feature/new-ui"
        assert addr.path == "docs/readme.md"
        assert addr.base_url == "https://ghe.example.com"

    def test_parse_for_github_context_supports_persisted_old_canonical_anybranch_uri(self):
        uri = "git://github/octo/repo/release2026/docs/readme.md"

        addr = SourceAddress.parse_for_github_context(
            uri,
            repository="octo/repo",
            branch="release2026",
        )

        assert addr.repository == "octo/repo"
        assert addr.branch == "release2026"
        assert addr.path == "docs/readme.md"

    def test_parse_for_github_context_does_not_break_plain_legacy_repo_branch_path(self):
        uri = "git://github/repo/release2026/docs/readme.md"

        addr = SourceAddress.parse_for_github_context(
            uri,
            repository="octo/repo",
            branch="release2026",
        )

        assert addr.repository == "repo"
        assert addr.branch == "release2026"
        assert addr.path == "docs/readme.md"

    def test_to_uri_preserves_github_base_url_context(self):
        addr = SourceAddress(
            provider="github",
            repository="octo/repo",
            branch="feature/new-ui",
            path="docs/readme.md",
            base_url="https://ghe.example.com",
        )

        assert addr.to_uri() == (
            "git://github/octo/repo/docs/readme.md"
            "?ref=feature%2Fnew-ui&base_url=https%3A%2F%2Fghe.example.com"
        )


class TestBaseSourceProvider:
    """Tests for BaseSourceProvider abstract class."""
    
    def test_get_content_not_implemented(self):
        """Test that get_content raises NotImplementedError."""
        provider = BaseSourceProvider()
        addr = SourceAddress(
            provider="github",
            repository="repo",
            branch="main",
            path="file.txt"
        )
        
        with pytest.raises(NotImplementedError):
            provider.get_content(addr)
    
    def test_get_tree_not_implemented(self):
        """Test that get_tree raises NotImplementedError."""
        provider = BaseSourceProvider()
        addr = SourceAddress(
            provider="github",
            repository="repo",
            branch="main",
            path="src"
        )
        
        with pytest.raises(NotImplementedError):
            provider.get_tree(addr)
