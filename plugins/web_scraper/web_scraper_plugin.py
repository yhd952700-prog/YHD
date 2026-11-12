"""
Web Scraper Plugin for LiuHao AI OS

Provides web content extraction, HTML to text conversion,
metadata extraction, link extraction, and sitemap parsing.

Capabilities:
- fetch: Fetch URL content
- html_to_text: Convert HTML to plain text
- extract_metadata: Extract page metadata
- extract_links: Extract all links from a page
- parse_sitemap: Parse XML sitemap
- scrape: Full scraping with all extractions
"""

import re
import urllib.parse
from typing import Dict, Any, Optional, List, Tuple
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse, urljoin, quote

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class PageMetadata:
    """Metadata extracted from a web page."""
    title: str = ""
    description: str = ""
    keywords: List[str] = field(default_factory=list)
    author: str = ""
    og_title: str = ""
    og_description: str = ""
    og_image: str = ""
    og_url: str = ""
    og_type: str = ""
    twitter_card: str = ""
    twitter_title: str = ""
    twitter_description: str = ""
    canonical_url: str = ""
    robots: str = ""
    language: str = ""
    charset: str = ""


@dataclass
class LinkInfo:
    """Information about a link on a page."""
    url: str
    text: str
    title: str = ""
    is_internal: bool = False
    is_external: bool = False


@dataclass
class ScrapedPage:
    """Result of a web page scraping operation."""
    url: str
    final_url: str
    status_code: int
    success: bool
    html: str
    text: str
    metadata: PageMetadata
    links: List[LinkInfo]
    images: List[str]
    word_count: int
    error: Optional[str] = None


class MLStripper(HTMLParser):
    """HTML to text converter."""

    def __init__(self) -> None:
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text_parts: List[str] = []
        self.skip_tags = {"script", "style", "noscript", "head", "meta", "link"}
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() in self.skip_tags:
            self._skip_depth += 1
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self.skip_tags:
            self._skip_depth -= 1
        if tag in ("p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"):
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self.text_parts.append(data)

    def get_text(self) -> str:
        text = "".join(self.text_parts)
        # Clean up whitespace
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        # Collapse multiple newlines
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


class WebScraperPlugin(BasePlugin):
    """Web scraping plugin for extracting content from URLs.

    Supports HTML to text conversion, metadata extraction (including
    Open Graph and Twitter tags), link extraction, and sitemap parsing.

    Example:
        plugin = WebScraperPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.scrape("https://example.com")
        print(result.text)
    """

    def __init__(self) -> None:
        """Initialize the web scraper plugin."""
        metadata = PluginMetadata(
            name="web_scraper",
            version="1.0.0",
            description="Web scraping and content extraction plugin",
            author="LiuHao AI OS Team",
            categories=["web", "scraping", "utility"],
            keywords=["scrape", "web", "html", "url", "metadata", "links"],
            requires_internet=True,
            dependencies=[],
        )
        super().__init__(metadata)

        self._has_requests: bool = False
        self._has_bs4: bool = False
        self._user_agent = (
            "Mozilla/5.0 (compatible; LiuHao-AI-OS/1.0; +https://liuhao.ai)"
        )
        self._request_timeout: int = 30

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the web scraper plugin."""
        try:
            try:
                import requests
                self._has_requests = True
            except ImportError:
                self._has_requests = False

            try:
                from bs4 import BeautifulSoup
                self._has_bs4 = True
            except ImportError:
                self._has_bs4 = False

            if config:
                self._user_agent = config.get("user_agent", self._user_agent)
                self._request_timeout = config.get("timeout", self._request_timeout)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the web scraper plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the web scraper plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the web scraper plugin."""
        try:
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "fetch",
            "html_to_text",
            "extract_metadata",
            "extract_links",
            "parse_sitemap",
            "scrape",
            "is_available",
        ]

    # ==================== Web Scraping Methods ====================

    def is_available(self) -> bool:
        """Check if web scraping is available (has requests library)."""
        return self._has_requests

    def fetch(
        self,
        url: str,
        method: str = "GET",
        headers: Optional[Dict[str, str]] = None,
        params: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> Tuple[int, str, str]:
        """Fetch a URL and return status, content, and final URL.

        Args:
            url: URL to fetch.
            method: HTTP method (GET, POST).
            headers: Custom HTTP headers.
            params: Query parameters.
            timeout: Request timeout in seconds.

        Returns:
            Tuple of (status_code, html_content, final_url).
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_requests:
            raise PluginExecutionError(
                "requests library is required for web scraping"
            )

        import requests

        req_headers = {"User-Agent": self._user_agent}
        if headers:
            req_headers.update(headers)

        timeout = timeout or self._request_timeout

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=req_headers,
                params=params,
                timeout=timeout,
                allow_redirects=True,
            )
            return response.status_code, response.text, response.url
        except requests.RequestException as e:
            return 0, f"Error: {e}", url

    def html_to_text(self, html: str) -> str:
        """Convert HTML content to plain text.

        Args:
            html: HTML content string.

        Returns:
            Plain text extracted from HTML.
        """
        self._ensure_active()
        self._increment_call_count()

        if not html:
            return ""

        if self._has_bs4:
            return self._html_to_text_bs4(html)
        else:
            return self._html_to_text_basic(html)

    def extract_metadata(self, html: str, base_url: str = "") -> PageMetadata:
        """Extract metadata from HTML content.

        Args:
            html: HTML content string.
            base_url: Base URL for resolving relative URLs.

        Returns:
            PageMetadata with extracted metadata.
        """
        self._ensure_active()
        self._increment_call_count()

        if not html:
            return PageMetadata()

        if self._has_bs4:
            return self._extract_metadata_bs4(html, base_url)
        else:
            return self._extract_metadata_regex(html, base_url)

    def extract_links(
        self,
        html: str,
        base_url: str,
        include_external: bool = True,
        include_internal: bool = True,
    ) -> List[LinkInfo]:
        """Extract all links from HTML content.

        Args:
            html: HTML content string.
            base_url: Base URL for resolving relative links.
            include_external: Include external links.
            include_internal: Include internal links.

        Returns:
            List of LinkInfo objects.
        """
        self._ensure_active()
        self._increment_call_count()

        if not html or not base_url:
            return []

        if self._has_bs4:
            links = self._extract_links_bs4(html, base_url)
        else:
            links = self._extract_links_regex(html, base_url)

        # Filter
        filtered = []
        for link in links:
            if link.is_internal and not include_internal:
                continue
            if link.is_external and not include_external:
                continue
            filtered.append(link)

        return filtered

    def parse_sitemap(self, sitemap_url: str) -> List[Dict[str, Any]]:
        """Parse an XML sitemap.

        Args:
            sitemap_url: URL of the sitemap.xml file.

        Returns:
            List of URL entries with loc, lastmod, changefreq, priority.
        """
        self._ensure_active()
        self._increment_call_count()

        if not self._has_requests:
            raise PluginExecutionError("requests library is required")

        import requests

        try:
            response = requests.get(
                sitemap_url,
                headers={"User-Agent": self._user_agent},
                timeout=self._request_timeout,
            )
            if response.status_code != 200:
                return []

            return self._parse_sitemap_xml(response.text, sitemap_url)
        except requests.RequestException:
            return []

    def scrape(
        self,
        url: str,
        extract_links: bool = True,
        extract_images: bool = True,
    ) -> ScrapedPage:
        """Full web page scraping.

        Fetches the page, extracts text, metadata, links, and images.

        Args:
            url: URL to scrape.
            extract_links: Whether to extract links.
            extract_images: Whether to extract image URLs.

        Returns:
            ScrapedPage with all extracted data.
        """
        self._ensure_active()
        self._increment_call_count()

        status, html, final_url = self.fetch(url)

        if status == 0 or not html:
            return ScrapedPage(
                url=url,
                final_url=final_url,
                status_code=status,
                success=False,
                html="",
                text="",
                metadata=PageMetadata(),
                links=[],
                images=[],
                word_count=0,
                error=html if html.startswith("Error:") else "Unknown error",
            )

        text = self.html_to_text(html)
        metadata = self.extract_metadata(html, final_url)

        links = []
        if extract_links:
            links = self.extract_links(html, final_url)

        images = []
        if extract_images:
            images = self._extract_images(html, final_url)

        word_count = len(text.split())

        return ScrapedPage(
            url=url,
            final_url=final_url,
            status_code=status,
            success=200 <= status < 300,
            html=html,
            text=text,
            metadata=metadata,
            links=links,
            images=images,
            word_count=word_count,
        )

    # ==================== BeautifulSoup Implementations ====================

    def _html_to_text_bs4(self, html: str) -> str:
        """Convert HTML to text using BeautifulSoup."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")

        # Remove script, style, etc.
        for element in soup(["script", "style", "noscript", "head", "meta", "link"]):
            element.decompose()

        # Get text
        text = soup.get_text(separator="\n")

        # Clean up
        lines = [line.strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    def _extract_metadata_bs4(self, html: str, base_url: str) -> PageMetadata:
        """Extract metadata using BeautifulSoup."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        meta = PageMetadata()

        # Title
        if soup.title and soup.title.string:
            meta.title = soup.title.string.strip()

        # Language
        if soup.html and soup.html.get("lang"):
            meta.language = soup.html["lang"]

        # Charset
        meta_tag = soup.find("meta", charset=True)
        if meta_tag:
            meta.charset = meta_tag["charset"]

        # Meta tags
        for tag in soup.find_all("meta"):
            name = tag.get("name", "").lower()
            prop = tag.get("property", "").lower()
            content = tag.get("content", "")

            if name == "description":
                meta.description = content
            elif name == "keywords":
                meta.keywords = [k.strip() for k in content.split(",") if k.strip()]
            elif name == "author":
                meta.author = content
            elif name == "robots":
                meta.robots = content
            elif prop == "og:title":
                meta.og_title = content
            elif prop == "og:description":
                meta.og_description = content
            elif prop == "og:image":
                meta.og_image = self._resolve_url(content, base_url)
            elif prop == "og:url":
                meta.og_url = content
            elif prop == "og:type":
                meta.og_type = content
            elif name == "twitter:card":
                meta.twitter_card = content
            elif name == "twitter:title":
                meta.twitter_title = content
            elif name == "twitter:description":
                meta.twitter_description = content

        # Canonical URL
        canonical = soup.find("link", rel="canonical")
        if canonical and canonical.get("href"):
            meta.canonical_url = self._resolve_url(canonical["href"], base_url)

        return meta

    def _extract_links_bs4(self, html: str, base_url: str) -> List[LinkInfo]:
        """Extract links using BeautifulSoup."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(html, "html.parser")
        base_domain = urlparse(base_url).netloc
        links: List[LinkInfo] = []
        seen = set()

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            absolute_url = self._resolve_url(href, base_url)
            if absolute_url in seen:
                continue
            seen.add(absolute_url)

            link_domain = urlparse(absolute_url).netloc
            is_internal = link_domain == base_domain or link_domain == ""

            text = a_tag.get_text(strip=True)[:200]
            title = a_tag.get("title", "")

            links.append(LinkInfo(
                url=absolute_url,
                text=text,
                title=title,
                is_internal=is_internal,
                is_external=not is_internal,
            ))

        return links

    def _extract_images(self, html: str, base_url: str) -> List[str]:
        """Extract image URLs from HTML."""
        if self._has_bs4:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            images = []
            seen = set()
            for img in soup.find_all("img", src=True):
                src = img["src"].strip()
                if src:
                    absolute = self._resolve_url(src, base_url)
                    if absolute not in seen:
                        seen.add(absolute)
                        images.append(absolute)
            return images
        else:
            # Regex fallback
            img_pattern = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
            images = []
            seen = set()
            for match in img_pattern.finditer(html):
                src = match.group(1)
                absolute = self._resolve_url(src, base_url)
                if absolute not in seen:
                    seen.add(absolute)
                    images.append(absolute)
            return images

    # ==================== Regex-based Implementations ====================

    def _html_to_text_basic(self, html: str) -> str:
        """Convert HTML to text using regex (fallback)."""
        stripper = MLStripper()
        try:
            stripper.feed(html)
            return stripper.get_text()
        except Exception:
            # Simple regex fallback
            text = re.sub(r"<script.*?</script>", "", html, flags=re.DOTALL | re.I)
            text = re.sub(r"<style.*?</style>", "", text, flags=re.DOTALL | re.I)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()

    def _extract_metadata_regex(self, html: str, base_url: str) -> PageMetadata:
        """Extract metadata using regex."""
        meta = PageMetadata()

        # Title
        title_match = re.search(r"<title>(.*?)</title>", html, re.I | re.DOTALL)
        if title_match:
            meta.title = re.sub(r"<[^>]+>", "", title_match.group(1)).strip()

        # Meta description
        desc_match = re.search(
            r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I,
        )
        if desc_match:
            meta.description = desc_match.group(1)

        # Meta keywords
        kw_match = re.search(
            r'<meta[^>]+name=["\']keywords["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I,
        )
        if kw_match:
            meta.keywords = [k.strip() for k in kw_match.group(1).split(",") if k.strip()]

        # OG tags
        og_title = re.search(
            r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I,
        )
        if og_title:
            meta.og_title = og_title.group(1)

        og_desc = re.search(
            r'<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I,
        )
        if og_desc:
            meta.og_description = og_desc.group(1)

        og_image = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html, re.I,
        )
        if og_image:
            meta.og_image = self._resolve_url(og_image.group(1), base_url)

        return meta

    def _extract_links_regex(self, html: str, base_url: str) -> List[LinkInfo]:
        """Extract links using regex."""
        base_domain = urlparse(base_url).netloc
        links: List[LinkInfo] = []
        seen = set()

        # Find all <a> tags
        a_pattern = re.compile(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.DOTALL)

        for match in a_pattern.finditer(html):
            href = match.group(1).strip()
            text_raw = match.group(2)

            if not href or href.startswith("#") or href.startswith("javascript:"):
                continue

            # Strip tags from link text
            text = re.sub(r"<[^>]+>", "", text_raw).strip()[:200]

            absolute_url = self._resolve_url(href, base_url)
            if absolute_url in seen:
                continue
            seen.add(absolute_url)

            link_domain = urlparse(absolute_url).netloc
            is_internal = link_domain == base_domain or link_domain == ""

            links.append(LinkInfo(
                url=absolute_url,
                text=text,
                title="",
                is_internal=is_internal,
                is_external=not is_internal,
            ))

        return links

    def _parse_sitemap_xml(self, xml_content: str, base_url: str) -> List[Dict[str, Any]]:
        """Parse sitemap XML content."""
        urls: List[Dict[str, Any]] = []

        # Check if it's a sitemap index
        if "<sitemapindex" in xml_content.lower():
            # Parse sitemap index recursively
            sitemap_pattern = re.compile(
                r"<sitemap>.*?<loc>(.*?)</loc>.*?</sitemap>",
                re.I | re.DOTALL,
            )
            for match in sitemap_pattern.finditer(xml_content):
                sitemap_loc = match.group(1).strip()
                try:
                    sub_urls = self.parse_sitemap(sitemap_loc)
                    urls.extend(sub_urls)
                except Exception:
                    pass
            return urls

        # Parse regular sitemap
        url_pattern = re.compile(
            r"<url>.*?</url>",
            re.I | re.DOTALL,
        )

        for url_match in url_pattern.finditer(xml_content):
            url_block = url_match.group(0)

            entry: Dict[str, Any] = {}

            loc_match = re.search(r"<loc>(.*?)</loc>", url_block, re.I | re.DOTALL)
            if loc_match:
                entry["loc"] = loc_match.group(1).strip()

            lastmod_match = re.search(r"<lastmod>(.*?)</lastmod>", url_block, re.I)
            if lastmod_match:
                entry["lastmod"] = lastmod_match.group(1).strip()

            changefreq_match = re.search(r"<changefreq>(.*?)</changefreq>", url_block, re.I)
            if changefreq_match:
                entry["changefreq"] = changefreq_match.group(1).strip()

            priority_match = re.search(r"<priority>(.*?)</priority>", url_block, re.I)
            if priority_match:
                try:
                    entry["priority"] = float(priority_match.group(1).strip())
                except ValueError:
                    pass

            if "loc" in entry:
                urls.append(entry)

        return urls

    # ==================== Utility Methods ====================

    def _resolve_url(self, url: str, base_url: str) -> str:
        """Resolve a relative URL against a base URL."""
        if not url:
            return ""
        if url.startswith(("http://", "https://", "//")):
            if url.startswith("//"):
                return "https:" + url
            return url
        return urljoin(base_url, url)
