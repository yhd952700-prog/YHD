"""
.gitignore Management Plugin for LiuHao AI OS

Provides .gitignore generation for different languages/frameworks,
parsing, file check, merging, template library, and custom rule management.

Capabilities:
- generate: Generate .gitignore for language/framework
- parse: Parse .gitignore into rules
- is_ignored: Check if a file path is ignored
- merge: Merge multiple .gitignore files
- get_templates: List available templates
- add_rule: Add a rule to .gitignore
- remove_rule: Remove a rule from .gitignore
- get_categories: Get template categories
"""

import re
import fnmatch
from typing import Dict, Any, Optional, List, Set, Tuple
from dataclasses import dataclass, field

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class GitignoreRule:
    """A single .gitignore rule."""
    pattern: str
    is_negation: bool
    is_comment: bool
    is_blank: bool
    raw: str
    scope: str = ""  # directory scope if pattern is dir-specific


@dataclass
class ParseResult:
    """Result of parsing a .gitignore file."""
    rules: List[GitignoreRule]
    ignore_patterns: List[str]
    negation_patterns: List[str]
    comment_count: int
    blank_count: int
    total_rules: int


class GitignorePlugin(BasePlugin):
    """.gitignore management plugin.

    Provides .gitignore generation, parsing, file checking,
    and template library for many languages and frameworks.

    Example:
        plugin = GitignorePlugin()
        plugin.initialize()
        plugin.activate()

        gitignore = plugin.generate(["Python", "Node"])
        print(gitignore)
    """

    # Template library: language/framework -> list of patterns
    TEMPLATES: Dict[str, Dict[str, Any]] = {
        "Python": {
            "category": "language",
            "patterns": [
                "# Byte-compiled / optimized / DLL files",
                "__pycache__/",
                "*.py[cod]",
                "*$py.class",
                "",
                "# C extensions",
                "*.so",
                "",
                "# Distribution / packaging",
                ".Python",
                "build/",
                "develop-eggs/",
                "dist/",
                "downloads/",
                "eggs/",
                ".eggs/",
                "lib/",
                "lib64/",
                "parts/",
                "sdist/",
                "var/",
                "wheels/",
                "share/python-wheels/",
                "*.egg-info/",
                ".installed.cfg",
                "*.egg",
                "MANIFEST",
                "",
                "# Unit test / coverage reports",
                "htmlcov/",
                ".tox/",
                ".nox/",
                ".coverage",
                ".coverage.*",
                ".cache",
                "nosetests.xml",
                "coverage.xml",
                "*.cover",
                "*.py,cover",
                ".hypothesis/",
                ".pytest_cache/",
                "cover/",
                "",
                "# Environments",
                ".env",
                ".venv",
                "env/",
                "venv/",
                "ENV/",
                "env.bak/",
                "venv.bak/",
                "",
                "# IDE",
                ".idea/",
                ".vscode/",
                "*.swp",
                "*.swo",
            ],
        },
        "Node": {
            "category": "language",
            "patterns": [
                "# Dependencies",
                "node_modules/",
                "",
                "# Testing",
                "coverage/",
                "*.lcov",
                ".nyc_output/",
                "",
                "# Build outputs",
                "dist/",
                "build/",
                ".next/",
                ".nuxt/",
                ".out/",
                "",
                "# Environment variables",
                ".env",
                ".env.local",
                ".env.development.local",
                ".env.test.local",
                ".env.production.local",
                "",
                "# Logs",
                "logs/",
                "*.log",
                "npm-debug.log*",
                "yarn-debug.log*",
                "yarn-error.log*",
                "pnpm-debug.log*",
                "",
                "# Editor directories and files",
                ".vscode/",
                ".idea/",
                "*.suo",
                "*.ntvs*",
                "*.njsproj",
                "*.sln",
                "*.sw?",
            ],
        },
        "Java": {
            "category": "language",
            "patterns": [
                "# Compiled class file",
                "*.class",
                "",
                "# Log file",
                "*.log",
                "",
                "# Package Files",
                "*.jar",
                "*.war",
                "*.nar",
                "*.ear",
                "*.zip",
                "*.tar.gz",
                "*.rar",
                "",
                "# Virtual machine crash logs",
                "hs_err_pid*",
                "replay_pid*",
                "",
                "# Build tools",
                "target/",
                "build/",
                ".gradle/",
                ".idea/",
                ".settings/",
                ".classpath",
                ".project",
                ".factorypath",
                "",
                "# Maven",
                "pom.xml.tag",
                "pom.xml.releaseBackup",
                "pom.xml.versionsBackup",
                "pom.xml.next",
                "release.properties",
                "dependency-reduced-pom.xml",
                "buildNumber.properties",
                ".mvn/timing.properties",
                ".mvn/wrapper/maven-wrapper.jar",
                "",
                "# IDE",
                "*.iml",
                "*.ipr",
                "*.iws",
                ".idea/",
                ".vscode/",
            ],
        },
        "JavaScript": {
            "category": "language",
            "patterns": [
                "# Logs",
                "logs",
                "*.log",
                "npm-debug.log*",
                "yarn-debug.log*",
                "yarn-error.log*",
                "",
                "# Runtime data",
                "pids",
                "*.pid",
                "*.seed",
                "*.pid.lock",
                "",
                "# Dependencies",
                "node_modules/",
                "jspm_packages/",
                "",
                "# Build output",
                "dist/",
                "build/",
                "",
                "# TypeScript cache",
                "*.tsbuildinfo",
                "",
                "# Optional npm cache directory",
                ".npm",
                "",
                "# Optional eslint cache",
                ".eslintcache",
                "",
                "# Environment variables",
                ".env",
                ".env.local",
                "",
                "# IDE",
                ".idea/",
                ".vscode/",
                "*.swp",
            ],
        },
        "TypeScript": {
            "category": "language",
            "patterns": [
                "# TypeScript",
                "*.tsbuildinfo",
                "",
                "# Dependencies",
                "node_modules/",
                "",
                "# Build output",
                "dist/",
                "build/",
                "*.js",
                "*.d.ts",
                "",
                "# Logs",
                "*.log",
                "",
                "# Environment",
                ".env",
                "",
                "# IDE",
                ".vscode/",
                ".idea/",
            ],
        },
        "Go": {
            "category": "language",
            "patterns": [
                "# Binaries for programs and plugins",
                "*.exe",
                "*.exe~",
                "*.dll",
                "*.so",
                "*.dylib",
                "",
                "# Test binary, built with `go test -c`",
                "*.test",
                "",
                "# Output of the go coverage tool",
                "*.out",
                "",
                "# Dependency directories",
                "vendor/",
                "",
                "# Go workspace file",
                "go.work",
                "",
                "# IDE",
                ".idea/",
                ".vscode/",
            ],
        },
        "Rust": {
            "category": "language",
            "patterns": [
                "/target",
                "",
                "# These are backup files generated by rustfmt",
                "**/*.rs.bk",
                "",
                "# MSVC Windows builds",
                "*.pdb",
                "",
                "# IDE",
                ".idea/",
                ".vscode/",
            ],
        },
        "Ruby": {
            "category": "language",
            "patterns": [
                "*.gem",
                "*.rbc",
                "/.config",
                "/coverage/",
                "/InstalledFiles",
                "/pkg/",
                "/spec/reports/",
                "/spec/examples.txt",
                "/test/tmp/",
                "/test/version_tmp/",
                "/tmp/",
                "",
                "node_modules/",
                "",
                "Gemfile.lock",
                "",
                ".byebug_history",
                "",
                ".env",
                "",
                ".idea/",
                ".vscode/",
            ],
        },
        "PHP": {
            "category": "language",
            "patterns": [
                "vendor/",
                "",
                "*.log",
                "",
                ".env",
                ".env.*",
                "!.env.example",
                "",
                "bootstrap/cache/*.php",
                "!bootstrap/cache/.gitkeep",
                "",
                "storage/*.key",
                "storage/framework/cache/**",
                "storage/framework/sessions/**",
                "storage/framework/views/**",
                "storage/logs/*.log",
                "storage/logs/*.log.*",
                "",
                "public/hot",
                "public/storage",
                "",
                "nova_modules/",
                "",
                ".idea/",
                ".vscode/",
            ],
        },
        "C++": {
            "category": "language",
            "patterns": [
                "# Prerequisites",
                "*.d",
                "",
                "# Compiled Object files",
                "*.o",
                "*.obj",
                "*.ko",
                "*.elf",
                "",
                "# Linker output",
                "*.ilk",
                "*.map",
                "*.exp",
                "",
                "# Precompiled Headers",
                "*.gch",
                "*.pch",
                "",
                "# Compiled Dynamic libraries",
                "*.so",
                "*.dylib",
                "*.dll",
                "",
                "# Executable files",
                "*.exe",
                "*.out",
                "*.app",
                "",
                "# Debug files",
                "*.dSYM/",
                "*.su",
                "*.idb",
                "*.pdb",
                "",
                "# IDE",
                ".vs/",
                ".idea/",
                ".vscode/",
                "*.swp",
            ],
        },
        "React": {
            "category": "framework",
            "patterns": [
                "# dependencies",
                "/node_modules",
                "",
                "# testing",
                "/coverage",
                "",
                "# production",
                "/build",
                "",
                "# misc",
                ".env",
                ".env.local",
                "*.log",
                "",
                "npm-debug.log*",
                "yarn-debug.log*",
                "yarn-error.log*",
                "",
                "# IDE",
                ".vscode/",
                ".idea/",
                ".DS_Store",
            ],
        },
        "Vue": {
            "category": "framework",
            "patterns": [
                "node_modules/",
                "dist/",
                ".DS_Store",
                "",
                ".local",
                ".env.local",
                ".env.*.local",
                "",
                "npm-debug.log*",
                "yarn-debug.log*",
                "yarn-error.log*",
                "pnpm-debug.log*",
                "",
                "*.log",
                "",
                ".idea/",
                ".vscode/",
                "*.suo",
                "*.ntvs*",
                "*.njsproj",
                "*.sln",
                "*.sw?",
            ],
        },
        "Django": {
            "category": "framework",
            "patterns": [
                "*.log",
                "*.pot",
                "*.pyc",
                "__pycache__/",
                "local_settings.py",
                "db.sqlite3",
                "db.sqlite3-journal",
                "media/",
                "",
                ".env",
                "venv/",
                "env/",
                ".venv/",
                "",
                "staticfiles/",
                "",
                ".idea/",
                ".vscode/",
            ],
        },
        "Flask": {
            "category": "framework",
            "patterns": [
                "*.pyc",
                "__pycache__/",
                "instance/",
                ".webassets-cache",
                "",
                "*.log",
                "",
                ".env",
                "venv/",
                "env/",
                "",
                "instance_venv/",
                "",
                ".idea/",
                ".vscode/",
            ],
        },
        "Spring": {
            "category": "framework",
            "patterns": [
                "target/",
                "!.mvn/wrapper/maven-wrapper.jar",
                "",
                "### STS ###",
                ".apt_generated",
                ".classpath",
                ".factorypath",
                ".project",
                ".settings",
                ".springBeans",
                ".sts4-cache",
                "",
                "### IntelliJ IDEA ###",
                ".idea",
                "*.iws",
                "*.iml",
                "*.ipr",
                "",
                "### NetBeans ###",
                "/nbproject/private/",
                "/nbbuild/",
                "/dist/",
                "/nbdist/",
                "/.nb-gradle/",
                "",
                "### VS Code ###",
                ".vscode/",
            ],
        },
        "Laravel": {
            "category": "framework",
            "patterns": [
                "vendor/",
                "node_modules/",
                "",
                "public/hot",
                "public/storage",
                "",
                "storage/*.key",
                "storage/framework/cache/**",
                "storage/framework/sessions/**",
                "storage/framework/views/**",
                "storage/logs/*.log",
                "",
                ".env",
                ".env.backup",
                ".env.*",
                "!.env.example",
                "",
                "npm-debug.log",
                "yarn-debug.log",
                "yarn-error.log",
                "",
                ".idea/",
                ".vscode/",
                "Homestead.json",
                "Homestead.yaml",
            ],
        },
        "macOS": {
            "category": "os",
            "patterns": [
                ".DS_Store",
                ".AppleDouble",
                ".LSOverride",
                "",
                "# Icon must end with two \\r",
                "Icon\r",
                "",
                "# Thumbnails",
                "._*",
                "",
                "# Files that might appear in the root of a volume",
                ".DocumentRevisions-V100",
                ".fseventsd",
                ".Spotlight-V100",
                ".TemporaryItems",
                ".Trashes",
                ".VolumeIcon.icns",
                ".com.apple.timemachine.donotpresent",
                "",
                "# Directories potentially created on remote AFP share",
                ".AppleDB",
                ".AppleDesktop",
                "Network Trash Folder",
                "Temporary Items",
                ".apdisk",
            ],
        },
        "Windows": {
            "category": "os",
            "patterns": [
                "# Windows thumbnail cache files",
                "Thumbs.db",
                "Thumbs.db:encryptable",
                "ehthumbs.db",
                "ehthumbs_vista.db",
                "",
                "# Dump file",
                "*.stackdump",
                "",
                "# Folder config file",
                "[Dd]esktop.ini",
                "",
                "# Recycle Bin used on file shares",
                "$RECYCLE.BIN/",
                "",
                "# Windows Installer files",
                "*.cab",
                "*.msi",
                "*.msix",
                "*.msm",
                "*.msp",
                "",
                "# Windows shortcuts",
                "*.lnk",
            ],
        },
        "Linux": {
            "category": "os",
            "patterns": [
                "*~",
                "",
                "# temporary files which can be created if a process still has a handle open of a deleted file",
                ".fuse_hidden*",
                "",
                "# KDE directory preferences",
                ".directory",
                "",
                "# Linux trash folder which might appear on any partition or disk",
                ".Trash-*",
                "",
                "# .nfs files are created when an open file is removed but is still being accessed",
                ".nfs*",
            ],
        },
        "IDE": {
            "category": "editor",
            "patterns": [
                "# JetBrains IDEs",
                ".idea/",
                "*.iml",
                "*.iws",
                "*.ipr",
                "",
                "# VS Code",
                ".vscode/",
                "!.vscode/settings.json",
                "!.vscode/tasks.json",
                "!.vscode/launch.json",
                "!.vscode/extensions.json",
                "",
                "# Vim",
                "*.swp",
                "*.swo",
                "*.swn",
                "*~",
                "",
                "# Emacs",
                "*~",
                "\\#*\\#",
                ".#*",
                "",
                "# Sublime Text",
                "*.sublime-workspace",
                "*.sublime-project",
                "",
                "# Eclipse",
                ".project",
                ".classpath",
                ".settings/",
            ],
        },
        "Database": {
            "category": "data",
            "patterns": [
                "# Database files",
                "*.db",
                "*.sqlite",
                "*.sqlite3",
                "",
                "# Database dumps",
                "*.sql",
                "*.dump",
                "",
                "# Database locks",
                "*.lock",
            ],
        },
        "Docker": {
            "category": "devops",
            "patterns": [
                "# Docker",
                "docker-compose.override.yml",
                "docker-compose.*.override.yml",
                "",
                "# Docker env files",
                ".env",
                ".env.*",
                "!.env.example",
                "",
                "# Docker volumes",
                "volumes/",
                "data/",
            ],
        },
    }

    def __init__(self) -> None:
        """Initialize the gitignore plugin."""
        metadata = PluginMetadata(
            name="gitignore",
            version="1.0.0",
            description=".gitignore generation, parsing, and template library",
            author="LiuHao AI OS Team",
            categories=["development", "utility", "git"],
            keywords=["gitignore", "git", "template", "ignore", "config"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._default_templates: List[str] = ["Python"]

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the gitignore plugin."""
        try:
            if config:
                self._default_templates = config.get("default_templates", ["Python"])

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the gitignore plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the gitignore plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the gitignore plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "generate",
            "parse",
            "is_ignored",
            "merge",
            "get_templates",
            "add_rule",
            "remove_rule",
            "get_categories",
            "list_rules",
        ]

    # ==================== Generation ====================

    def generate(
        self,
        templates: Optional[List[str]] = None,
        *,
        custom_rules: Optional[List[str]] = None,
        include_header: bool = True,
    ) -> str:
        """Generate a .gitignore file from templates.

        Args:
            templates: List of template names.
            custom_rules: Additional custom rules to add.
            include_header: Whether to include a header comment.

        Returns:
            .gitignore content string.
        """
        self._ensure_active()
        self._increment_call_count()

        template_list = templates or self._default_templates

        lines: List[str] = []

        if include_header:
            lines.append("# .gitignore")
            lines.append(f"# Generated from templates: {', '.join(template_list)}")
            lines.append("")

        for template_name in template_list:
            template = self._get_template(template_name)
            if not template:
                continue

            lines.append(f"### {template_name} ###")
            lines.append("")
            lines.extend(template)
            lines.append("")

        if custom_rules:
            lines.append("### Custom Rules ###")
            lines.append("")
            lines.extend(custom_rules)
            lines.append("")

        return "\n".join(lines).rstrip() + "\n"

    def _get_template(self, name: str) -> Optional[List[str]]:
        """Get a template by name (case-insensitive)."""
        for template_name, data in self.TEMPLATES.items():
            if template_name.lower() == name.lower():
                return data["patterns"]
        return None

    # ==================== Parsing ====================

    def parse(self, gitignore_text: str) -> ParseResult:
        """Parse a .gitignore file into rules.

        Args:
            gitignore_text: The .gitignore content.

        Returns:
            ParseResult with parsed rules.
        """
        self._ensure_active()
        self._increment_call_count()

        rules: List[GitignoreRule] = []
        ignore_patterns: List[str] = []
        negation_patterns: List[str] = []
        comment_count = 0
        blank_count = 0

        for line in gitignore_text.split("\n"):
            stripped = line.strip()

            if stripped == "":
                blank_count += 1
                rules.append(GitignoreRule(
                    pattern="",
                    is_negation=False,
                    is_comment=False,
                    is_blank=True,
                    raw=line,
                ))
                continue

            if stripped.startswith("#"):
                comment_count += 1
                rules.append(GitignoreRule(
                    pattern=stripped[1:].strip(),
                    is_negation=False,
                    is_comment=True,
                    is_blank=False,
                    raw=line,
                ))
                continue

            # Trailing spaces are ignored unless escaped
            if not stripped.endswith("\\ "):
                stripped = stripped.rstrip()

            # Negation pattern
            is_negation = stripped.startswith("!")
            pattern = stripped[1:] if is_negation else stripped

            # Determine scope (directory-specific)
            scope = ""
            if pattern.startswith("/"):
                pattern = pattern[1:]
            elif "/" in pattern.rstrip("/"):
                scope = "dir"

            rule = GitignoreRule(
                pattern=pattern,
                is_negation=is_negation,
                is_comment=False,
                is_blank=False,
                raw=line,
                scope=scope,
            )
            rules.append(rule)

            if is_negation:
                negation_patterns.append(pattern)
            else:
                ignore_patterns.append(pattern)

        return ParseResult(
            rules=rules,
            ignore_patterns=ignore_patterns,
            negation_patterns=negation_patterns,
            comment_count=comment_count,
            blank_count=blank_count,
            total_rules=len([r for r in rules if not r.is_blank and not r.is_comment]),
        )

    # ==================== Checking ====================

    def is_ignored(
        self,
        filepath: str,
        gitignore_text: str,
        *,
        is_dir: bool = False,
    ) -> bool:
        """Check if a file path would be ignored by .gitignore.

        Args:
            filepath: Path to check.
            gitignore_text: The .gitignore content.
            is_dir: Whether the path is a directory.

        Returns:
            True if the path is ignored.
        """
        self._ensure_active()
        self._increment_call_count()

        parsed = self.parse(gitignore_text)
        ignored = False

        # Get all non-comment, non-blank rules in order
        rules = [r for r in parsed.rules if not r.is_blank and not r.is_comment]

        for rule in rules:
            pattern = rule.pattern

            # Skip patterns ending with / if checking a file
            if pattern.endswith("/") and not is_dir:
                continue

            # Remove trailing / for matching
            match_pattern = pattern.rstrip("/")

            # Check if the pattern matches
            if self._match_pattern(filepath, match_pattern, is_dir):
                if rule.is_negation:
                    ignored = False
                else:
                    ignored = True

        return ignored

    def _match_pattern(self, filepath: str, pattern: str, is_dir: bool) -> bool:
        """Check if a filepath matches a gitignore pattern."""
        # Normalize paths
        filepath = filepath.lstrip("./")
        parts = filepath.split("/")
        basename = parts[-1]

        # If pattern contains no /, match against basename
        if "/" not in pattern:
            return fnmatch.fnmatch(basename, pattern)

        # Pattern with / - match against full path
        # Handle ** (matches zero or more directories)
        if "**" in pattern:
            return self._match_double_star(filepath, pattern)

        return fnmatch.fnmatch(filepath, pattern)

    def _match_double_star(self, filepath: str, pattern: str) -> bool:
        """Match a pattern with ** against a filepath."""
        # Convert ** to regex-like pattern
        regex_parts = []
        pattern_parts = pattern.split("/")

        for pp in pattern_parts:
            if pp == "**":
                regex_parts.append(".*")
            else:
                # Convert fnmatch pattern to regex
                escaped = re.escape(pp)
                escaped = escaped.replace(r"\*", "[^/]*")
                escaped = escaped.replace(r"\?", "[^/]")
                regex_parts.append(escaped)

        regex = "^" + "/".join(regex_parts) + "$"
        return bool(re.match(regex, filepath))

    # ==================== Merging ====================

    def merge(
        self,
        sources: List[str],
        *,
        remove_duplicates: bool = True,
    ) -> str:
        """Merge multiple .gitignore files.

        Args:
            sources: List of .gitignore contents.
            remove_duplicates: Whether to remove duplicate rules.

        Returns:
            Merged .gitignore content.
        """
        self._ensure_active()
        self._increment_call_count()

        all_rules: List[str] = []
        seen: Set[str] = set()

        for source in sources:
            for line in source.split("\n"):
                stripped = line.strip()

                if not stripped or stripped.startswith("#"):
                    if not remove_duplicates or line not in seen:
                        all_rules.append(line)
                        seen.add(line)
                    continue

                if remove_duplicates:
                    if stripped not in seen:
                        all_rules.append(line)
                        seen.add(stripped)
                else:
                    all_rules.append(line)

        return "\n".join(all_rules).rstrip() + "\n"

    # ==================== Rule Management ====================

    def add_rule(
        self,
        gitignore_text: str,
        rule: str,
        *,
        section: Optional[str] = None,
    ) -> str:
        """Add a rule to a .gitignore file.

        Args:
            gitignore_text: Current .gitignore content.
            rule: Rule to add.
            section: Section/comment to add under (optional).

        Returns:
            Updated .gitignore content.
        """
        self._ensure_active()
        self._increment_call_count()

        lines = gitignore_text.rstrip().split("\n")

        if section:
            # Find the section
            section_header = f"### {section} ###"
            found_section = False
            insert_idx = -1

            for i, line in enumerate(lines):
                if line.strip() == section_header:
                    found_section = True
                    # Find the end of the section (next blank line or section)
                    for j in range(i + 1, len(lines)):
                        if lines[j].strip().startswith("### ") or \
                           (lines[j].strip() == "" and j < len(lines) - 1 and
                            lines[j + 1].strip().startswith("### ")):
                            insert_idx = j
                            break
                    if insert_idx == -1:
                        insert_idx = len(lines)
                    break

            if found_section and insert_idx > 0:
                lines.insert(insert_idx, rule)
                return "\n".join(lines) + "\n"

        # Add at the end
        lines.append("")
        lines.append(rule)
        return "\n".join(lines).rstrip() + "\n"

    def remove_rule(
        self,
        gitignore_text: str,
        rule: str,
    ) -> Tuple[str, bool]:
        """Remove a rule from a .gitignore file.

        Args:
            gitignore_text: Current .gitignore content.
            rule: Rule to remove.

        Returns:
            Tuple of (updated_content, was_removed).
        """
        self._ensure_active()
        self._increment_call_count()

        lines = gitignore_text.split("\n")
        new_lines = []
        removed = False

        for line in lines:
            if line.strip() == rule.strip():
                removed = True
                continue
            new_lines.append(line)

        return "\n".join(new_lines), removed

    # ==================== Templates ====================

    def get_templates(self, *, category: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get available .gitignore templates.

        Args:
            category: Optional category filter.

        Returns:
            List of template info dicts.
        """
        self._ensure_active()
        self._increment_call_count()

        templates = []
        for name, data in self.TEMPLATES.items():
            if category and data["category"] != category:
                continue
            templates.append({
                "name": name,
                "category": data["category"],
                "rule_count": len([
                    p for p in data["patterns"]
                    if p.strip() and not p.startswith("#")
                ]),
            })

        return templates

    def get_categories(self) -> List[str]:
        """Get template categories.

        Returns:
            List of category names.
        """
        self._ensure_active()
        self._increment_call_count()

        categories = set()
        for data in self.TEMPLATES.values():
            categories.add(data["category"])

        return sorted(categories)

    def list_rules(self, template_name: str) -> List[str]:
        """List rules for a specific template.

        Args:
            template_name: Name of the template.

        Returns:
            List of pattern strings (non-comment rules).
        """
        self._ensure_active()
        self._increment_call_count()

        template = self._get_template(template_name)
        if not template:
            raise ValueError(f"Unknown template: {template_name}")

        return [
            p for p in template
            if p.strip() and not p.startswith("#") and not p.strip() == ""
        ]

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        categories: Dict[str, int] = {}
        total_rules = 0

        for name, data in self.TEMPLATES.items():
            cat = data["category"]
            rule_count = len([
                p for p in data["patterns"]
                if p.strip() and not p.startswith("#")
            ])
            categories[cat] = categories.get(cat, 0) + 1
            total_rules += rule_count

        return {
            "templates_count": len(self.TEMPLATES),
            "categories": categories,
            "total_rules": total_rules,
            "default_templates": self._default_templates,
        }
