"""
Semantic Versioning Plugin for LiuHao AI OS

Provides SemVer parsing, version comparison, version bumping,
prerelease and build metadata, range checking, and constraint parsing.

Capabilities:
- parse: Parse a semantic version
- compare: Compare two versions
- bump: Bump version (major, minor, patch, prerelease)
- is_valid: Validate a version string
- satisfies: Check if version satisfies a range
- increment_prerelease: Increment prerelease version
"""

from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
import re

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class SemVer:
    """A parsed semantic version."""
    major: int
    minor: int
    patch: int
    prerelease: List[str]
    build: List[str]
    original: str

    def __str__(self) -> str:
        base = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            base += '-' + '.'.join(self.prerelease)
        if self.build:
            base += '+' + '.'.join(self.build)
        return base


class SemverPlugin(BasePlugin):
    """Semantic Versioning (SemVer) plugin.

    Implements SemVer 2.0.0 specification with parsing,
    comparison, bumping, prerelease/build metadata, and
    range/constraint checking.

    Example:
        plugin = SemverPlugin()
        plugin.initialize()
        plugin.activate()

        v = plugin.parse("1.2.3-beta.1+build.123")
        print(v.major, v.minor, v.patch)  # 1 2 3

        result = plugin.compare("1.2.3", "2.0.0")
        print(result)  # -1 (less than)
    """

    # SemVer 2.0.0 regex pattern
    SEMVER_REGEX = re.compile(
        r'^(?P<major>0|[1-9]\d*)'
        r'\.(?P<minor>0|[1-9]\d*)'
        r'\.(?P<patch>0|[1-9]\d*)'
        r'(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?'
        r'(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$'
    )

    def __init__(self) -> None:
        """Initialize the semver plugin."""
        metadata = PluginMetadata(
            name="semver",
            version="1.0.0",
            description="Semantic Versioning parser, comparator, bumper, and range checker",
            author="LiuHao AI OS Team",
            categories=["version", "utility", "development"],
            keywords=["semver", "version", "semantic", "bump", "compare", "release"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the semver plugin."""
        try:
            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the semver plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the semver plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the semver plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "parse",
            "compare",
            "bump",
            "is_valid",
            "satisfies",
            "increment_prerelease",
            "get_release_type",
            "coerce",
        ]

    # ==================== Parsing ====================

    def parse(self, version: str) -> Optional[SemVer]:
        """Parse a semantic version string.

        Args:
            version: Version string to parse.

        Returns:
            SemVer object, or None if invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        match = self.SEMVER_REGEX.match(version.strip())
        if not match:
            return None

        major = int(match.group('major'))
        minor = int(match.group('minor'))
        patch = int(match.group('patch'))

        prerelease = []
        if match.group('prerelease'):
            prerelease = match.group('prerelease').split('.')

        build = []
        if match.group('build'):
            build = match.group('build').split('.')

        return SemVer(
            major=major,
            minor=minor,
            patch=patch,
            prerelease=prerelease,
            build=build,
            original=version.strip(),
        )

    def is_valid(self, version: str) -> Tuple[bool, str]:
        """Check if a version string is valid SemVer.

        Args:
            version: Version string to validate.

        Returns:
            Tuple of (is_valid, reason).
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(version)
        if parsed is None:
            # Try to give specific error
            if not re.match(r'^\d+\.\d+\.\d+', version):
                return False, "Version must have major.minor.patch format"

            if '-' in version:
                pre = version.split('-', 1)[1].split('+', 1)[0]
                if not pre:
                    return False, "Prerelease identifier cannot be empty"

            return False, "Invalid version format"

        return True, "Valid semantic version"

    # ==================== Comparison ====================

    def compare(self, version1: str, version2: str) -> int:
        """Compare two semantic versions.

        Args:
            version1: First version string.
            version2: Second version string.

        Returns:
            -1 if v1 < v2, 0 if v1 == v2, 1 if v1 > v2.

        Raises:
            ValueError: If either version is invalid.
        """
        self._ensure_active()
        self._increment_call_count()

        v1 = self.parse(version1)
        v2 = self.parse(version2)

        if v1 is None:
            raise ValueError(f"Invalid version: {version1}")
        if v2 is None:
            raise ValueError(f"Invalid version: {version2}")

        # Compare major, minor, patch
        if v1.major != v2.major:
            return 1 if v1.major > v2.major else -1

        if v1.minor != v2.minor:
            return 1 if v1.minor > v2.minor else -1

        if v1.patch != v2.patch:
            return 1 if v1.patch > v2.patch else -1

        # Compare prerelease
        # No prerelease > has prerelease
        if not v1.prerelease and v2.prerelease:
            return 1
        if v1.prerelease and not v2.prerelease:
            return -1

        # Both have prerelease: compare each identifier
        if v1.prerelease and v2.prerelease:
            result = self._compare_prerelease(v1.prerelease, v2.prerelease)
            if result != 0:
                return result

        # Build metadata is ignored for precedence
        return 0

    def _compare_prerelease(self, pre1: List[str], pre2: List[str]) -> int:
        """Compare two prerelease identifier lists."""
        min_len = min(len(pre1), len(pre2))

        for i in range(min_len):
            id1 = pre1[i]
            id2 = pre2[i]

            # Both numeric
            if id1.isdigit() and id2.isdigit():
                n1 = int(id1)
                n2 = int(id2)
                if n1 != n2:
                    return 1 if n1 > n2 else -1
            # Both alphanumeric
            elif not id1.isdigit() and not id2.isdigit():
                if id1 != id2:
                    return 1 if id1 > id2 else -1
            # Numeric < alphanumeric
            elif id1.isdigit() and not id2.isdigit():
                return -1
            else:
                return 1

        # Same up to min length: more identifiers = higher precedence
        if len(pre1) != len(pre2):
            return 1 if len(pre1) > len(pre2) else -1

        return 0

    # ==================== Version Bumping ====================

    def bump(
        self,
        version: str,
        release_type: str = "patch",
        prerelease_id: str = "beta",
    ) -> Dict[str, Any]:
        """Bump a version to the next release.

        Args:
            version: Current version string.
            release_type: Type of bump ('major', 'minor', 'patch',
                           'prerelease', 'premajor', 'preminor', 'prepatch').
            prerelease_id: Prerelease identifier ('alpha', 'beta', 'rc', etc.).

        Returns:
            Dict with old and new versions.

        Raises:
            ValueError: If version is invalid or release_type unknown.
        """
        self._ensure_active()
        self._increment_call_count()

        v = self.parse(version)
        if v is None:
            raise ValueError(f"Invalid version: {version}")

        release_type = release_type.lower()

        if release_type == "major":
            new_major = v.major + 1
            new_minor = 0
            new_patch = 0
            new_prerelease = []
        elif release_type == "minor":
            new_major = v.major
            new_minor = v.minor + 1
            new_patch = 0
            new_prerelease = []
        elif release_type == "patch":
            new_major = v.major
            new_minor = v.minor
            new_patch = v.patch + 1
            new_prerelease = []
        elif release_type == "premajor":
            new_major = v.major + 1
            new_minor = 0
            new_patch = 0
            new_prerelease = [prerelease_id, "0"]
        elif release_type == "preminor":
            new_major = v.major
            new_minor = v.minor + 1
            new_patch = 0
            new_prerelease = [prerelease_id, "0"]
        elif release_type == "prepatch":
            new_major = v.major
            new_minor = v.minor
            new_patch = v.patch + 1
            new_prerelease = [prerelease_id, "0"]
        elif release_type == "prerelease":
            if v.prerelease:
                # Increment existing prerelease
                new_major = v.major
                new_minor = v.minor
                new_patch = v.patch
                new_prerelease = self._increment_prerelease_list(v.prerelease)
            else:
                # Start prerelease from patch bump
                new_major = v.major
                new_minor = v.minor
                new_patch = v.patch + 1
                new_prerelease = [prerelease_id, "0"]
        else:
            raise ValueError(f"Unknown release type: {release_type}")

        new_version = SemVer(
            major=new_major,
            minor=new_minor,
            patch=new_patch,
            prerelease=new_prerelease,
            build=[],
            original="",
        )

        return {
            "old_version": version,
            "new_version": str(new_version),
            "release_type": release_type,
            "old": {
                "major": v.major,
                "minor": v.minor,
                "patch": v.patch,
                "prerelease": v.prerelease,
            },
            "new": {
                "major": new_major,
                "minor": new_minor,
                "patch": new_patch,
                "prerelease": new_prerelease,
            },
        }

    def _increment_prerelease_list(self, prerelease: List[str]) -> List[str]:
        """Increment the numeric part of a prerelease list."""
        result = list(prerelease)

        # Find last numeric identifier and increment it
        for i in range(len(result) - 1, -1, -1):
            if result[i].isdigit():
                result[i] = str(int(result[i]) + 1)
                return result

        # No numeric identifier found, add .1
        result.append("1")
        return result

    def increment_prerelease(
        self,
        version: str,
        identifier: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Increment the prerelease version.

        Args:
            version: Current version string.
            identifier: Prerelease identifier (default: use existing or 'beta').

        Returns:
            Dict with incremented version.
        """
        self._ensure_active()
        self._increment_call_count()

        v = self.parse(version)
        if v is None:
            raise ValueError(f"Invalid version: {version}")

        if not v.prerelease:
            # Start a new prerelease
            pre_id = identifier or "beta"
            new_prerelease = [pre_id, "0"]
        else:
            new_prerelease = self._increment_prerelease_list(v.prerelease)

        new_version = SemVer(
            major=v.major,
            minor=v.minor,
            patch=v.patch,
            prerelease=new_prerelease,
            build=[],
            original="",
        )

        return {
            "old_version": version,
            "new_version": str(new_version),
            "prerelease": new_prerelease,
        }

    # ==================== Range Checking ====================

    def satisfies(
        self,
        version: str,
        range_str: str,
    ) -> bool:
        """Check if a version satisfies a version range.

        Supported range syntax:
        - Exact: "1.2.3"
        - Caret: "^1.2.3" (>=1.2.3 <2.0.0)
        - Tilde: "~1.2.3" (>=1.2.3 <1.3.0)
        - Range: "1.2.3 - 2.0.0"
        - Comparison: ">=1.2.3", "<2.0.0", etc.
        - Wildcard: "1.2.x", "1.x", "x"
        - OR: "1.x || 2.x"

        Args:
            version: Version to check.
            range_str: Range expression.

        Returns:
            True if version satisfies the range.
        """
        self._ensure_active()
        self._increment_call_count()

        v = self.parse(version)
        if v is None:
            return False

        # Handle OR conditions
        if '||' in range_str:
            parts = range_str.split('||')
            return any(self.satisfies(version, p.strip()) for p in parts)

        # Handle space-separated AND conditions
        parts = range_str.strip().split()
        if len(parts) > 1:
            # Check for range syntax (A - B)
            if '-' in range_str and len(parts) == 3 and parts[1] == '-':
                return (
                    self.compare(version, parts[0]) >= 0
                    and self.compare(version, parts[2]) <= 0
                )
            # AND all parts
            return all(self._satisfies_single(version, p) for p in parts)

        return self._satisfies_single(version, range_str.strip())

    def _satisfies_single(self, version: str, constraint: str) -> bool:
        """Check if version satisfies a single constraint."""
        v = self.parse(version)
        if v is None:
            return False

        # Wildcards
        constraint = constraint.strip()
        if constraint in ('*', 'x', 'X', 'latest'):
            return True

        # Caret range (^)
        if constraint.startswith('^'):
            base = constraint[1:]
            base_v = self.parse(base)
            if base_v is None:
                return False
            # ^1.2.3 means >=1.2.3 <2.0.0
            # ^0.2.3 means >=0.2.3 <0.3.0
            # ^0.0.3 means >=0.0.3 <0.0.4
            upper_major = base_v.major
            upper_minor = base_v.minor
            upper_patch = base_v.patch

            if base_v.major > 0:
                upper_major += 1
                upper_minor = 0
                upper_patch = 0
            elif base_v.minor > 0:
                upper_minor += 1
                upper_patch = 0
            else:
                upper_patch += 1

            upper = f"{upper_major}.{upper_minor}.{upper_patch}"

            return (
                self.compare(version, base) >= 0
                and self.compare(version, upper) < 0
            )

        # Tilde range (~)
        if constraint.startswith('~'):
            base = constraint[1:]
            base_v = self.parse(base)
            if base_v is None:
                return False
            # ~1.2.3 means >=1.2.3 <1.3.0
            upper = f"{base_v.major}.{base_v.minor + 1}.0"
            return (
                self.compare(version, base) >= 0
                and self.compare(version, upper) < 0
            )

        # Comparison operators
        ops = [">=", "<=", "!=", ">", "<", "="]
        for op in ops:
            if constraint.startswith(op):
                target = constraint[len(op):]
                target_v = self.parse(target)
                if target_v is None:
                    return False

                cmp_result = self.compare(version, target)
                if op == ">=":
                    return cmp_result >= 0
                elif op == "<=":
                    return cmp_result <= 0
                elif op == ">":
                    return cmp_result > 0
                elif op == "<":
                    return cmp_result < 0
                elif op == "!=":
                    return cmp_result != 0
                elif op == "=":
                    return cmp_result == 0

        # Wildcard with x
        if 'x' in constraint.lower():
            # Replace x with appropriate wildcard
            parts = constraint.split('.')
            major = parts[0] if len(parts) > 0 else '0'
            minor = parts[1] if len(parts) > 1 else 'x'
            patch = parts[2] if len(parts) > 2 else 'x'

            if major.lower() in ('x', '*'):
                return True  # Any version

            if minor.lower() in ('x', '*'):
                # Match major only
                major_v = int(major)
                return v.major == major_v

            if patch.lower() in ('x', '*'):
                # Match major.minor
                major_v = int(major)
                minor_v = int(minor)
                return v.major == major_v and v.minor == minor_v

        # Exact match
        return self.compare(version, constraint) == 0

    # ==================== Release Type Detection ====================

    def get_release_type(self, old_version: str, new_version: str) -> Dict[str, Any]:
        """Determine the type of release between two versions.

        Args:
            old_version: Old version string.
            new_version: New version string.

        Returns:
            Dict with release type and magnitude.
        """
        self._ensure_active()
        self._increment_call_count()

        v1 = self.parse(old_version)
        v2 = self.parse(new_version)

        if v1 is None or v2 is None:
            return {"error": "Invalid version string"}

        if self.compare(old_version, new_version) >= 0:
            return {
                "is_forward": False,
                "release_type": "downgrade",
                "magnitude": 0,
            }

        # Determine release type
        if v2.major > v1.major:
            release_type = "major"
            magnitude = 3
        elif v2.minor > v1.minor:
            release_type = "minor"
            magnitude = 2
        elif v2.patch > v1.patch:
            release_type = "patch"
            magnitude = 1
        elif v2.prerelease and not v1.prerelease:
            release_type = "prerelease-start"
            magnitude = 0.5
        elif v2.prerelease and v1.prerelease:
            release_type = "prerelease-bump"
            magnitude = 0.1
        else:
            release_type = "none"
            magnitude = 0

        # Breaking change check
        is_breaking = (
            v2.major > v1.major
            or (v1.major == 0 and v2.minor > v1.minor)
        )

        return {
            "is_forward": True,
            "release_type": release_type,
            "magnitude": magnitude,
            "is_breaking": is_breaking,
            "breaking_change": is_breaking,
            "old_version": old_version,
            "new_version": new_version,
        }

    # ==================== Coerce ====================

    def coerce(self, version: str) -> Optional[str]:
        """Attempt to coerce a string to a valid SemVer.

        Args:
            version: Version string to coerce.

        Returns:
            Coerced version string, or None if not possible.
        """
        self._ensure_active()
        self._increment_call_count()

        # Already valid
        if self.parse(version) is not None:
            return version

        # Try adding missing parts
        v = version.strip().lstrip('vV')

        # Count dots
        dots = v.count('.')

        if dots == 0:
            # Just a number: "1" -> "1.0.0"
            if v.isdigit():
                return f"{v}.0.0"
        elif dots == 1:
            # "1.2" -> "1.2.0"
            parts = v.split('.')
            if parts[0].isdigit() and parts[1].isdigit():
                return f"{parts[0]}.{parts[1]}.0"

        return None

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "spec_version": "2.0.0",
            "supports_prerelease": True,
            "supports_build_metadata": True,
            "range_syntax": [
                "exact",
                "caret (^)",
                "tilde (~)",
                "range (A - B)",
                "comparison (>=, <=, >, <, =, !=)",
                "wildcard (x, *, X)",
                "OR (||)",
            ],
            "bump_types": [
                "major",
                "minor",
                "patch",
                "premajor",
                "preminor",
                "prepatch",
                "prerelease",
            ],
        }
