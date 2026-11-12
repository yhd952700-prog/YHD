"""
Changelog Management Plugin for LiuHao AI OS

Provides Keep a Changelog format support, version bumping (semver),
adding entries, releasing new versions, and changelog validation.

Capabilities:
- init: Initialize a new changelog
- add_entry: Add an entry to unreleased section
- release: Release a new version
- version_bump: Bump version number (semver)
- validate: Validate changelog format
- get_latest_version: Get latest version info
- get_unreleased: Get unreleased changes
- parse: Parse a changelog file
- compare_versions: Compare two versions
"""

import re
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class ChangelogEntry:
    """A single changelog entry."""
    category: str
    description: str
    scope: Optional[str] = None


@dataclass
class ChangelogVersion:
    """A version section in changelog."""
    version: str
    date: Optional[str]
    entries: Dict[str, List[str]]
    is_unreleased: bool = False
    yanked: bool = False


@dataclass
class VersionInfo:
    """Semantic version info."""
    major: int
    minor: int
    patch: int
    prerelease: Optional[str] = None
    build: Optional[str] = None


class ChangelogPlugin(BasePlugin):
    """Changelog management plugin.

    Provides Keep a Changelog format support, semantic versioning,
    and changelog validation.

    Example:
        plugin = ChangelogPlugin()
        plugin.initialize()
        plugin.activate()

        changelog = plugin.init("My Project")
        print(changelog)
    """

    # Standard Keep a Changelog categories
    CATEGORIES = [
        "Added",
        "Changed",
        "Deprecated",
        "Removed",
        "Fixed",
        "Security",
    ]

    # Semver regex
    SEMVER_PATTERN = re.compile(
        r"^(?P<major>0|[1-9]\d*)"
        r"\.(?P<minor>0|[1-9]\d*)"
        r"\.(?P<patch>0|[1-9]\d*)"
        r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
        r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
    )

    # Changelog version header pattern
    VERSION_HEADER_PATTERN = re.compile(
        r"^##\s+"
        r"(?:\[(?P<version>[^\]]+)\])"
        r"(?:\s*-\s*(?P<date>\d{4}-\d{2}-\d{2}))?"
        r"(\s*\[YANKED\])?"
        r"\s*$",
        re.IGNORECASE,
    )

    def __init__(self) -> None:
        """Initialize the changelog plugin."""
        metadata = PluginMetadata(
            name="changelog",
            version="1.0.0",
            description="Changelog management with Keep a Changelog format and semver",
            author="LiuHao AI OS Team",
            categories=["development", "utility", "versioning"],
            keywords=["changelog", "semver", "version", "release", "keepachangelog"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_format: str = "keepachangelog"
        self._initial_version: str = "0.1.0"

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the changelog plugin."""
        try:
            if config:
                self._default_format = config.get("default_format", "keepachangelog")
                self._initial_version = config.get("initial_version", "0.1.0")

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the changelog plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the changelog plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the changelog plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "init",
            "add_entry",
            "release",
            "version_bump",
            "validate",
            "get_latest_version",
            "get_unreleased",
            "parse",
            "compare_versions",
            "get_categories",
        ]

    # ==================== Init ====================

    def init(
        self,
        project_name: str,
        *,
        initial_version: Optional[str] = None,
        description: str = "",
    ) -> str:
        """Initialize a new changelog in Keep a Changelog format.

        Args:
            project_name: Name of the project.
            initial_version: Initial version number.
            description: Project description.

        Returns:
            Initial changelog markdown string.
        """
        self._ensure_active()
        self._increment_call_count()

        version = initial_version or self._initial_version
        today = datetime.now().strftime("%Y-%m-%d")

        changelog = f"""# Changelog

All notable changes to **{project_name}** will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

{description if description else ""}

## [Unreleased]

### Added
- Initial project setup

### Changed
- Nothing yet

### Deprecated
- Nothing yet

### Removed
- Nothing yet

### Fixed
- Nothing yet

### Security
- Nothing yet

## [{version}] - {today}

### Added
- Initial release
- Core functionality

[unreleased]: https://example.com/compare/{version}...HEAD
[{version}]: https://example.com/releases/tag/{version}
"""
        return changelog

    # ==================== Add Entry ====================

    def add_entry(
        self,
        changelog: str,
        description: str,
        category: str = "Added",
        *,
        scope: Optional[str] = None,
    ) -> str:
        """Add an entry to the Unreleased section of a changelog.

        Args:
            changelog: The changelog text.
            description: Entry description.
            category: Entry category (Added, Changed, Fixed, etc.).
            scope: Optional scope prefix.

        Returns:
            Updated changelog text.
        """
        self._ensure_active()
        self._increment_call_count()

        # Normalize category
        cat = category.capitalize()
        if cat not in self.CATEGORIES:
            raise ValueError(
                f"Invalid category: {category}. "
                f"Use one of: {self.CATEGORIES}"
            )

        # Format the entry
        if scope:
            entry = f"- **{scope}:** {description}"
        else:
            entry = f"- {description}"

        # Find the Unreleased section and the category
        lines = changelog.split("\n")
        in_unreleased = False
        in_category = False
        category_header = f"### {cat}"

        new_lines = []
        inserted = False

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Detect Unreleased section
            if re.match(r"^##\s+\[Unreleased\]", stripped, re.IGNORECASE):
                in_unreleased = True
                new_lines.append(line)
                continue

            # Detect end of Unreleased section (next version)
            if in_unreleased and re.match(r"^##\s+\[", stripped) and "Unreleased" not in stripped:
                # If we haven't inserted yet, add the category and entry
                if not inserted:
                    new_lines.append(category_header)
                    new_lines.append("")
                    new_lines.append(entry)
                    new_lines.append("")
                in_unreleased = False
                in_category = False
                new_lines.append(line)
                continue

            if in_unreleased:
                # Check for category header
                if stripped == category_header:
                    in_category = True
                    new_lines.append(line)
                    continue

                # Check for other category header
                if stripped.startswith("### ") and stripped != category_header:
                    if not inserted and cat < stripped[4:]:
                        # Insert our category before this one alphabetically
                        new_lines.append(category_header)
                        new_lines.append("")
                        new_lines.append(entry)
                        new_lines.append("")
                        inserted = True
                    in_category = False
                    new_lines.append(line)
                    continue

                # If we're in our category, add the entry at the end of the list
                if in_category:
                    if stripped.startswith("- ") or stripped.startswith("* "):
                        new_lines.append(line)
                        continue
                    else:
                        # End of category list
                        new_lines.append(entry)
                        inserted = True
                        in_category = False
                        new_lines.append(line)
                        continue

            new_lines.append(line)

        # Handle case where Unreleased is at the end
        if in_unreleased and not inserted:
            new_lines.append(category_header)
            new_lines.append("")
            new_lines.append(entry)
            new_lines.append("")

        return "\n".join(new_lines)

    # ==================== Release ====================

    def release(
        self,
        changelog: str,
        version: str,
        *,
        date: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Release a new version from the Unreleased section.

        Args:
            changelog: The changelog text.
            version: New version number.
            date: Release date (default: today).

        Returns:
            Tuple of (updated_changelog, old_unreleased_content).
        """
        self._ensure_active()
        self._increment_call_count()

        release_date = date or datetime.now().strftime("%Y-%m-%d")

        # Validate version format
        if not self.SEMVER_PATTERN.match(version):
            raise ValueError(f"Invalid semantic version: {version}")

        lines = changelog.split("\n")
        new_lines = []
        unreleased_content: List[str] = []
        in_unreleased = False
        unreleased_start = -1
        unreleased_end = -1

        # Find the Unreleased section
        for i, line in enumerate(lines):
            stripped = line.strip()
            if re.match(r"^##\s+\[Unreleased\]", stripped, re.IGNORECASE):
                in_unreleased = True
                unreleased_start = i
                continue
            if in_unreleased and re.match(r"^##\s+\[", stripped) and "Unreleased" not in stripped:
                unreleased_end = i
                break

        if unreleased_start == -1:
            raise ValueError("No [Unreleased] section found in changelog")

        if unreleased_end == -1:
            unreleased_end = len(lines)

        # Extract unreleased content (everything after the header)
        unreleased_lines = lines[unreleased_start + 1:unreleased_end]
        unreleased_content = "\n".join(unreleased_lines).strip()

        # Build new changelog
        # Replace [Unreleased] section with new version + empty unreleased
        new_header = f"## [{version}] - {release_date}"

        # Create new Unreleased section
        new_unreleased = [
            "## [Unreleased]",
            "",
            "### Added",
            "",
            "### Changed",
            "",
            "### Deprecated",
            "",
            "### Removed",
            "",
            "### Fixed",
            "",
            "### Security",
            "",
        ]

        # Construct new changelog
        result_lines = (
            lines[:unreleased_start]
            + new_unreleased
            + [""]
            + [new_header]
            + [""]
            + unreleased_lines
            + lines[unreleased_end:]
        )

        return "\n".join(result_lines), unreleased_content

    # ==================== Version Bump ====================

    def version_bump(
        self,
        current_version: str,
        bump_type: str = "patch",
        *,
        prerelease: Optional[str] = None,
    ) -> str:
        """Bump a semantic version number.

        Args:
            current_version: Current version string.
            bump_type: Type of bump: major, minor, patch, premajor, preminor, prepatch, prerelease.
            prerelease: Prerelease identifier (e.g., 'alpha', 'beta', 'rc').

        Returns:
            New version string.
        """
        self._ensure_active()
        self._increment_call_count()

        info = self._parse_version(current_version)
        major, minor, patch = info.major, info.minor, info.patch
        pre = info.prerelease

        bump = bump_type.lower()

        if bump == "major":
            major += 1
            minor = 0
            patch = 0
            pre = None
        elif bump == "minor":
            minor += 1
            patch = 0
            pre = None
        elif bump == "patch":
            patch += 1
            pre = None
        elif bump == "premajor":
            major += 1
            minor = 0
            patch = 0
            pre = prerelease or "alpha.1"
        elif bump == "preminor":
            minor += 1
            patch = 0
            pre = prerelease or "alpha.1"
        elif bump == "prepatch":
            patch += 1
            pre = prerelease or "alpha.1"
        elif bump == "prerelease":
            if pre:
                # Try to increment numeric prerelease
                pre_match = re.match(r"^(.+?)\.(\d+)$", pre)
                if pre_match:
                    pre_name = pre_match.group(1)
                    pre_num = int(pre_match.group(2)) + 1
                    pre = f"{pre_name}.{pre_num}"
                else:
                    pre = f"{pre}.1"
            else:
                patch += 1
                pre = prerelease or "alpha.1"
        else:
            raise ValueError(
                f"Invalid bump type: {bump_type}. "
                f"Use: major, minor, patch, premajor, preminor, prepatch, prerelease"
            )

        version = f"{major}.{minor}.{patch}"
        if pre:
            version += f"-{pre}"
        if info.build:
            version += f"+{info.build}"

        return version

    def _parse_version(self, version: str) -> VersionInfo:
        """Parse a semantic version string."""
        match = self.SEMVER_PATTERN.match(version.strip())
        if not match:
            raise ValueError(f"Invalid semantic version: {version}")

        return VersionInfo(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=match.group("prerelease"),
            build=match.group("build"),
        )

    def compare_versions(self, version1: str, version2: str) -> int:
        """Compare two semantic versions.

        Args:
            version1: First version string.
            version2: Second version string.

        Returns:
            -1 if v1 < v2, 0 if equal, 1 if v1 > v2.
        """
        self._ensure_active()
        self._increment_call_count()

        v1 = self._parse_version(version1)
        v2 = self._parse_version(version2)

        # Compare major.minor.patch
        if v1.major != v2.major:
            return -1 if v1.major < v2.major else 1
        if v1.minor != v2.minor:
            return -1 if v1.minor < v2.minor else 1
        if v1.patch != v2.patch:
            return -1 if v1.patch < v2.patch else 1

        # Compare prerelease (no prerelease > prerelease)
        if v1.prerelease is None and v2.prerelease is None:
            return 0
        if v1.prerelease is None:
            return 1
        if v2.prerelease is None:
            return -1

        # Compare prerelease identifiers
        v1_parts = v1.prerelease.split(".")
        v2_parts = v2.prerelease.split(".")

        for i in range(max(len(v1_parts), len(v2_parts))):
            if i >= len(v1_parts):
                return -1
            if i >= len(v2_parts):
                return 1

            p1 = v1_parts[i]
            p2 = v2_parts[i]

            # Try numeric comparison
            p1_is_num = p1.isdigit()
            p2_is_num = p2.isdigit()

            if p1_is_num and p2_is_num:
                n1 = int(p1)
                n2 = int(p2)
                if n1 != n2:
                    return -1 if n1 < n2 else 1
            elif p1_is_num:
                return -1  # numeric < alphanumeric
            elif p2_is_num:
                return 1
            else:
                if p1 != p2:
                    return -1 if p1 < p2 else 1

        return 0

    # ==================== Validation ====================

    def validate(self, changelog: str) -> Tuple[bool, List[str], List[str]]:
        """Validate a changelog format.

        Args:
            changelog: The changelog text.

        Returns:
            Tuple of (is_valid, errors, warnings).
        """
        self._ensure_active()
        self._increment_call_count()

        errors: List[str] = []
        warnings: List[str] = []
        lines = changelog.split("\n")

        # Check for title
        if not lines or not lines[0].startswith("# "):
            errors.append("Changelog must start with a level-1 heading")

        # Check for Keep a Changelog reference
        if "Keep a Changelog" not in changelog:
            warnings.append("Changelog does not reference Keep a Changelog format")

        # Check for Semantic Versioning reference
        if "Semantic Versioning" not in changelog:
            warnings.append("Changelog does not reference Semantic Versioning")

        # Check for Unreleased section
        has_unreleased = False
        version_count = 0
        versions: List[str] = []

        for line in lines:
            stripped = line.strip()

            if re.match(r"^##\s+\[Unreleased\]", stripped, re.IGNORECASE):
                has_unreleased = True

            version_match = re.match(r"^##\s+\[([^\]]+)\]", stripped)
            if version_match and "Unreleased" not in version_match.group(1):
                version_count += 1
                versions.append(version_match.group(1))

        if not has_unreleased:
            errors.append("Missing [Unreleased] section")

        if version_count == 0:
            warnings.append("No released versions found")

        # Check version format
        for v in versions:
            if not self.SEMVER_PATTERN.match(v):
                warnings.append(f"Version '{v}' may not follow semver format")

        # Check categories in Unreleased
        in_unreleased = False
        found_categories: set = set()

        for line in lines:
            stripped = line.strip()
            if re.match(r"^##\s+\[Unreleased\]", stripped, re.IGNORECASE):
                in_unreleased = True
                continue
            if in_unreleased and re.match(r"^##\s+\[", stripped) and "Unreleased" not in stripped:
                break
            if in_unreleased and stripped.startswith("### "):
                cat = stripped[4:].strip()
                found_categories.add(cat)

        if has_unreleased:
            for cat in self.CATEGORIES:
                if cat not in found_categories:
                    warnings.append(f"Unreleased section missing category: {cat}")

        return len(errors) == 0, errors, warnings

    # ==================== Parse ====================

    def parse(self, changelog: str) -> Dict[str, Any]:
        """Parse a changelog into structured data.

        Args:
            changelog: The changelog text.

        Returns:
            Dict with parsed changelog data.
        """
        self._ensure_active()
        self._increment_call_count()

        lines = changelog.split("\n")
        versions: List[ChangelogVersion] = []
        current_version: Optional[ChangelogVersion] = None
        current_category: Optional[str] = None
        title = ""

        for line in lines:
            stripped = line.strip()

            # Title
            if stripped.startswith("# ") and not title:
                title = stripped[2:].strip()
                continue

            # Version header
            version_match = re.match(r"^##\s+\[([^\]]+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?", stripped)
            if version_match:
                version_name = version_match.group(1)
                date = version_match.group(2)
                is_unreleased = version_name.lower() == "unreleased"
                yanked = "[YANKED]" in stripped.upper()

                current_version = ChangelogVersion(
                    version=version_name,
                    date=date,
                    entries={},
                    is_unreleased=is_unreleased,
                    yanked=yanked,
                )
                versions.append(current_version)
                current_category = None
                continue

            # Category header
            if current_version and stripped.startswith("### "):
                current_category = stripped[4:].strip()
                if current_category not in current_version.entries:
                    current_version.entries[current_category] = []
                continue

            # Entry item
            if current_version and current_category and (stripped.startswith("- ") or stripped.startswith("* ")):
                entry = stripped[2:].strip()
                current_version.entries[current_category].append(entry)

        return {
            "title": title,
            "versions": [
                {
                    "version": v.version,
                    "date": v.date,
                    "entries": v.entries,
                    "is_unreleased": v.is_unreleased,
                    "yanked": v.yanked,
                }
                for v in versions
            ],
            "version_count": len(versions),
        }

    # ==================== Query ====================

    def get_latest_version(self, changelog: str) -> Optional[Dict[str, Any]]:
        """Get the latest released version from a changelog.

        Args:
            changelog: The changelog text.

        Returns:
            Dict with latest version info, or None.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(changelog)
        versions = parsed["versions"]

        for v in versions:
            if not v["is_unreleased"] and not v["yanked"]:
                return v

        return None

    def get_unreleased(self, changelog: str) -> Optional[Dict[str, Any]]:
        """Get the Unreleased section from a changelog.

        Args:
            changelog: The changelog text.

        Returns:
            Dict with unreleased entries, or None.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(changelog)

        for v in parsed["versions"]:
            if v["is_unreleased"]:
                return v

        return None

    def get_categories(self) -> List[str]:
        """Get standard changelog categories.

        Returns:
            List of category names.
        """
        return list(self.CATEGORIES)

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "default_format": self._default_format,
            "initial_version": self._initial_version,
            "categories": list(self.CATEGORIES),
            "bump_types": [
                "major", "minor", "patch",
                "premajor", "preminor", "prepatch", "prerelease",
            ],
        }
