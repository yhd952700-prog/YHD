"""
RSS/Atom Feed Plugin for LiuHao AI OS

Provides feed parsing, feed discovery, new items detection,
feed aggregation, and OPML support.

Capabilities:
- parse_feed: Parse an RSS/Atom feed
- discover_feed: Discover feed URL from a website
- check_new_items: Detect new items since last check
- aggregate_feeds: Aggregate multiple feeds
- parse_opml: Parse OPML file
- export_opml: Export feeds to OPML
"""

import re
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class FeedItem:
    """A single item in a feed."""
    title: str
    link: str
    description: str
    pub_date: str = ""
    author: str = ""
    guid: str = ""
    categories: List[str] = field(default_factory=list)
    content: str = ""
    image: str = ""


@dataclass
class FeedInfo:
    """Information about a feed."""
    title: str
    link: str
    description: str
    language: str = ""
    last_build_date: str = ""
    generator: str = ""
    item_count: int = 0
    feed_type: str = "rss"  # rss or atom
    items: List[FeedItem] = field(default_factory=list)


class RSSPlugin(BasePlugin):
    """RSS/Atom feed parsing and management plugin.

    Supports RSS 2.0, Atom 1.0, feed discovery from websites,
    new item detection, feed aggregation, and OPML import/export.

    Example:
        plugin = RSSPlugin()
        plugin.initialize()
        plugin.activate()

        feed = plugin.parse_feed("https://example.com/feed.xml")
        for item in feed.items:
            print(item.title)
    """

    def __init__(self) -> None:
        """Initialize the RSS plugin."""
        metadata = PluginMetadata(
            name="rss",
            version="1.0.0",
            description="RSS/Atom feed parsing and management plugin",
            author="LiuHao AI OS Team",
            categories=["web", "rss", "feed", "utility"],
            keywords=["rss", "atom", "feed", "syndication", "opml"],
            requires_internet=True,
            dependencies=[],
        )
        super().__init__(metadata)

        self._has_requests: bool = False
        self._has_feedparser: bool = False
        self._seen_items: Dict[str, set] = {}  # feed_url -> set of guids

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the RSS plugin."""
        try:
            try:
                import requests
                self._has_requests = True
            except ImportError:
                self._has_requests = False

            try:
                import feedparser
                self._has_feedparser = True
            except ImportError:
                self._has_feedparser = False

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the RSS plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the RSS plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the RSS plugin."""
        try:
            self._seen_items.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "parse_feed",
            "discover_feed",
            "check_new_items",
            "aggregate_feeds",
            "parse_opml",
            "export_opml",
            "is_available",
        ]

    # ==================== Feed Methods ====================

    def is_available(self) -> bool:
        """Check if feed parsing is available."""
        return self._has_requests or self._has_feedparser

    def parse_feed(self, feed_url: str, max_items: Optional[int] = None) -> FeedInfo:
        """Parse an RSS or Atom feed.

        Args:
            feed_url: URL of the feed.
            max_items: Maximum number of items to return.

        Returns:
            FeedInfo with feed metadata and items.
        """
        self._ensure_active()
        self._increment_call_count()

        if self._has_feedparser:
            return self._parse_with_feedparser(feed_url, max_items)
        elif self._has_requests:
            return self._parse_basic(feed_url, max_items)
        else:
            raise PluginExecutionError(
                "feedparser or requests library is required"
            )

    def discover_feed(self, url: str) -> List[str]:
        """Discover RSS/Atom feed URLs from a website.

        Looks for feed links in the HTML head section.

        Args:
            url: Website URL to discover feeds from.

        Returns:
            List of discovered feed URLs.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_requests:
            raise PluginExecutionError("requests library is required")

        import requests

        try:
            response = requests.get(url, timeout=15)
            if response.status_code != 200:
                return []

            html = response.text
            feeds: List[str] = []

            # Look for RSS feed links
            rss_pattern = re.compile(
                r'<link[^>]+type=["\']application/rss\+xml["\'][^>]+href=["\']([^"\']+)["\']',
                re.I,
            )
            for match in rss_pattern.finditer(html):
                feed_url = self._resolve_url(match.group(1), url)
                if feed_url not in feeds:
                    feeds.append(feed_url)

            # Look for Atom feed links
            atom_pattern = re.compile(
                r'<link[^>]+type=["\']application/atom\+xml["\'][^>]+href=["\']([^"\']+)["\']',
                re.I,
            )
            for match in atom_pattern.finditer(html):
                feed_url = self._resolve_url(match.group(1), url)
                if feed_url not in feeds:
                    feeds.append(feed_url)

            # Check common feed paths
            common_paths = [
                "/feed", "/feed/", "/rss", "/rss/", "/atom.xml",
                "/rss.xml", "/feed.xml", "/index.xml",
            ]
            parsed = urlparse(url)
            base = f"{parsed.scheme}://{parsed.netloc}"
            for path in common_paths:
                candidate = base + path
                if candidate not in feeds:
                    try:
                        head = requests.head(candidate, timeout=5, allow_redirects=True)
                        content_type = head.headers.get("content-type", "")
                        if "xml" in content_type or "rss" in content_type or "atom" in content_type:
                            feeds.append(candidate)
                    except Exception:
                        pass

            return feeds
        except Exception:
            return []

    def check_new_items(self, feed_url: str) -> List[FeedItem]:
        """Check for new items since the last check.

        Args:
            feed_url: URL of the feed.

        Returns:
            List of new FeedItem objects.
        """
        self._ensure_active()
        self._increment_call_count()

        feed = self.parse_feed(feed_url)

        if feed_url not in self._seen_items:
            # First check: record all items as seen, return empty
            self._seen_items[feed_url] = {
                item.guid or item.link for item in feed.items
            }
            return []

        seen = self._seen_items[feed_url]
        new_items: List[FeedItem] = []

        for item in feed.items:
            identifier = item.guid or item.link
            if identifier and identifier not in seen:
                new_items.append(item)
                seen.add(identifier)

        return new_items

    def aggregate_feeds(
        self,
        feed_urls: List[str],
        max_items: int = 50,
        sort_by_date: bool = True,
    ) -> List[FeedItem]:
        """Aggregate items from multiple feeds.

        Args:
            feed_urls: List of feed URLs.
            max_items: Maximum total items to return.
            sort_by_date: Sort by publication date (newest first).

        Returns:
            List of aggregated feed items.
        """
        self._ensure_active()

        all_items: List[FeedItem] = []

        for feed_url in feed_urls:
            try:
                feed = self.parse_feed(feed_url, max_items=20)
                all_items.extend(feed.items)
            except Exception:
                continue

        if sort_by_date:
            all_items.sort(key=lambda x: x.pub_date, reverse=True)

        return all_items[:max_items]

    def parse_opml(self, opml_content: str) -> List[Dict[str, str]]:
        """Parse an OPML file and extract feed outlines.

        Args:
            opml_content: OPML XML content string.

        Returns:
            List of dicts with 'title', 'xml_url', 'html_url', 'category'.
        """
        self._ensure_active()
        self._increment_call_count()

        feeds: List[Dict[str, str]] = []

        try:
            root = ET.fromstring(opml_content)

            # Find all outline elements with type="rss"
            for outline in root.iter("outline"):
                outline_type = outline.get("type", "")
                if outline_type.lower() in ("rss", "atom", "link"):
                    feed_info = {
                        "title": outline.get("title", outline.get("text", "")),
                        "xml_url": outline.get("xmlUrl", ""),
                        "html_url": outline.get("htmlUrl", ""),
                        "category": outline.get("category", ""),
                    }
                    if feed_info["xml_url"]:
                        feeds.append(feed_info)

        except ET.ParseError:
            pass

        return feeds

    def export_opml(self, feeds: List[Dict[str, str]], title: str = "Feeds") -> str:
        """Export a list of feeds to OPML format.

        Args:
            feeds: List of dicts with 'title', 'xml_url', 'html_url'.
            title: OPML document title.

        Returns:
            OPML XML string.
        """
        self._ensure_active()
        self._increment_call_count()

        opml = ET.Element("opml", version="2.0")

        head = ET.SubElement(opml, "head")
        title_elem = ET.SubElement(head, "title")
        title_elem.text = title

        body = ET.SubElement(opml, "body")

        for feed in feeds:
            outline = ET.SubElement(body, "outline")
            outline.set("type", "rss")
            outline.set("text", feed.get("title", ""))
            outline.set("title", feed.get("title", ""))
            outline.set("xmlUrl", feed.get("xml_url", ""))
            if feed.get("html_url"):
                outline.set("htmlUrl", feed["html_url"])

        return ET.tostring(opml, encoding="unicode", xml_declaration=True)

    # ==================== Parser Implementations ====================

    def _parse_with_feedparser(self, feed_url: str, max_items: Optional[int]) -> FeedInfo:
        """Parse feed using feedparser library."""
        import feedparser

        d = feedparser.parse(feed_url)

        feed_type = "rss"
        if d.bozo and not d.entries:
            # Has error and no entries
            pass

        if hasattr(d, "version") and "atom" in d.version.lower():
            feed_type = "atom"

        items: List[FeedItem] = []
        for entry in d.entries[:max_items] if max_items else d.entries:
            item = FeedItem(
                title=getattr(entry, "title", ""),
                link=getattr(entry, "link", ""),
                description=getattr(entry, "summary", ""),
                pub_date=getattr(entry, "published", getattr(entry, "updated", "")),
                author=getattr(entry, "author", ""),
                guid=getattr(entry, "id", getattr(entry, "guid", "")),
                categories=[c.get("term", "") for c in getattr(entry, "tags", [])],
                content=getattr(entry, "content", [{}])[0].get("value", "")
                if hasattr(entry, "content") and entry.content else "",
            )

            # Extract image
            if hasattr(entry, "media_content") and entry.media_content:
                item.image = entry.media_content[0].get("url", "")

            items.append(item)

        info = FeedInfo(
            title=d.feed.get("title", ""),
            link=d.feed.get("link", ""),
            description=d.feed.get("subtitle", d.feed.get("description", "")),
            language=d.feed.get("language", ""),
            last_build_date=d.feed.get("updated", d.feed.get("published", "")),
            generator=d.feed.get("generator", ""),
            item_count=len(items),
            feed_type=feed_type,
            items=items,
        )

        return info

    def _parse_basic(self, feed_url: str, max_items: Optional[int]) -> FeedInfo:
        """Parse feed using requests + xml.etree (basic implementation)."""
        import requests

        response = requests.get(feed_url, timeout=30)
        content = response.text

        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            return FeedInfo(
                title="", link="", description="", items=[]
            )

        # Determine feed type
        feed_type = "rss"
        channel = None

        if root.tag == "rss" or root.tag.endswith("}rss"):
            feed_type = "rss"
            channel = root.find("channel")
            if channel is None:
                # Try with namespace
                for child in root:
                    if child.tag.endswith("channel"):
                        channel = child
                        break
        elif "feed" in root.tag.lower():
            feed_type = "atom"
            channel = root  # Atom uses <feed> as root

        if channel is None:
            return FeedInfo(
                title="", link="", description="", items=[], feed_type=feed_type,
            )

        # Parse feed info
        title = self._get_child_text(channel, "title")
        link = self._get_child_text(channel, "link")
        description = self._get_child_text(channel, "description", "subtitle")
        language = self._get_child_text(channel, "language")
        last_build = self._get_child_text(channel, "lastBuildDate", "updated")
        generator = self._get_child_text(channel, "generator")

        # Parse items
        items: List[FeedItem] = []
        item_tag = "item" if feed_type == "rss" else "entry"

        for item_elem in channel.findall(item_tag):
            if max_items and len(items) >= max_items:
                break

            item_title = self._get_child_text(item_elem, "title")
            item_link = self._get_child_text(item_elem, "link")
            item_desc = self._get_child_text(item_elem, "description", "summary")
            item_date = self._get_child_text(item_elem, "pubDate", "published", "updated")
            item_author = self._get_child_text(item_elem, "author")
            item_guid = self._get_child_text(item_elem, "guid", "id")

            # For Atom, link might be in href attribute
            if not item_link and feed_type == "atom":
                link_elem = item_elem.find("link")
                if link_elem is not None:
                    item_link = link_elem.get("href", "")

            categories = []
            for cat_elem in item_elem.findall("category"):
                cat_text = cat_elem.get("term", cat_elem.text or "")
                if cat_text:
                    categories.append(cat_text)

            items.append(FeedItem(
                title=item_title,
                link=item_link,
                description=item_desc,
                pub_date=item_date,
                author=item_author,
                guid=item_guid,
                categories=categories,
            ))

        return FeedInfo(
            title=title,
            link=link,
            description=description,
            language=language,
            last_build_date=last_build,
            generator=generator,
            item_count=len(items),
            feed_type=feed_type,
            items=items,
        )

    # ==================== Internal Helpers ====================

    def _get_child_text(self, elem: ET.Element, *tag_names: str) -> str:
        """Get text from a child element, trying multiple tag names."""
        for tag in tag_names:
            child = elem.find(tag)
            if child is not None and child.text:
                return child.text.strip()

            # Try with namespace
            for child_elem in elem:
                if child_elem.tag.endswith(f"}}{tag}") or child_elem.tag.endswith(tag):
                    if child_elem.text:
                        return child_elem.text.strip()

        return ""

    def _resolve_url(self, url: str, base_url: str) -> str:
        """Resolve a relative URL."""
        from urllib.parse import urljoin
        if url.startswith(("http://", "https://")):
            return url
        return urljoin(base_url, url)
