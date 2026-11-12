"""
Tests for all LiuHao AI OS plugins (plugins/).

For every plugin module this verifies:
- the module imports cleanly,
- the plugin class instantiates,
- the ``name`` attribute is present and non-empty,
- lifecycle works (initialize + activate),
- get_capabilities() returns a non-empty list,
- a simple execution entry point is callable with simple input:
  the plugin's ``execute`` method when it defines one, otherwise a
  representative capability method (see SMOKE map below).

A MockProvider is built for every test and passed to the plugin
constructor whenever the constructor accepts a ``provider`` parameter
(currently no plugin does; they take no constructor arguments).
"""

import importlib
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.plugin_base import BasePlugin, PluginStatus  # noqa: E402
from src.ai.providers import MockProvider  # noqa: E402

# (module_path, class_name, expected plugin.name,
#  smoke method, smoke args, expect FileNotFoundError)
# The smoke method is a representative offline-safe capability of the
# plugin, called with simple input to prove executability. Plugins whose
# capability methods all require real media files are invoked with a
# missing path and must raise FileNotFoundError.
PLUGINS = [
    ("plugins.anagram.anagram_plugin", "AnagramPlugin", "anagram", "find_anagrams", ("test",), False),
    ("plugins.audio.audio_plugin", "AudioPlugin", "audio", "get_info", ("__missing__.mp3",), True),
    ("plugins.barcode.barcode_plugin", "BarcodePlugin", "barcode", "generate_ean13", ("test",), False),
    ("plugins.base64.base64_plugin", "Base64Plugin", "base64", "encode", ("test",), False),
    ("plugins.brainfuck.brainfuck_plugin", "BrainfuckPlugin", "brainfuck", "execute", ("+++.",), False),
    ("plugins.caesar.caesar_cipher_plugin", "CaesarCipherPlugin", "caesar-cipher", "encrypt", ("test",), False),
    ("plugins.calendar.calendar_plugin", "CalendarPlugin", "calendar", "update_event", ("test",), False),
    ("plugins.changelog.changelog_plugin", "ChangelogPlugin", "changelog", "init", ("test",), False),
    ("plugins.chess.chess_plugin", "ChessPlugin", "chess", "parse_pgn", ("test",), False),
    ("plugins.code_formatter.code_formatter_plugin", "CodeFormatterPlugin", "code_formatter", "lint_code", ("test",), False),
    ("plugins.color.color_plugin", "ColorPlugin", "color", "get_named_colors", (), False),
    ("plugins.compression.compression_plugin", "CompressionPlugin", "compression", "compress_rle", ("test",), False),
    ("plugins.cron.cron_plugin", "CronPlugin", "cron", "parse", ("test",), False),
    ("plugins.csv.csv_plugin", "CsvPlugin", "csv", "parse", ("test",), False),
    ("plugins.currency.currency_plugin", "CurrencyPlugin", "currency", "get_all_rates", (), False),
    ("plugins.dice.dice_plugin", "DicePlugin", "dice", "roll_dice", (), False),
    ("plugins.diff.diff_plugin", "DiffPlugin", "diff", "unified_diff", ("hello\n", "world\n"), False),
    ("plugins.emoji.emoji_plugin", "EmojiPlugin", "emoji", "search", ("smile",), False),
    ("plugins.encryption_classic.encryption_classic_plugin", "EncryptionClassicPlugin", "encryption-classic", "atbash", ("test",), False),
    ("plugins.enigma.enigma_plugin", "EnigmaPlugin", "enigma", "encrypt", ("test",), False),
    ("plugins.excel.excel_plugin", "ExcelPlugin", "excel", "evaluate_formula", ("test",), False),
    ("plugins.gitignore.gitignore_plugin", "GitignorePlugin", "gitignore", "generate", (), False),
    ("plugins.hash.hash_plugin", "HashPlugin", "hash", "md5", ("test",), False),
    ("plugins.image.image_plugin", "ImagePlugin", "image", "get_info", ("__missing__.png",), True),
    ("plugins.ip_address.ip_address_plugin", "IPAddressPlugin", "ip-address", "validate_ip", ("test",), False),
    ("plugins.json.json_tool_plugin", "JsonToolPlugin", "json_tool", "validate", ('{"a": 1}',), False),
    ("plugins.leetcode.leetcode_plugin", "LeetcodePlugin", "leetcode", "generate_template", ("test",), False),
    ("plugins.license.license_plugin", "LicensePlugin", "license", "generate", (), False),
    ("plugins.lorem.lorem_ipsum_plugin", "LoremIpsumPlugin", "lorem-ipsum", "generate_words", (), False),
    ("plugins.markdown.markdown_plugin", "MarkdownPlugin", "markdown", "md_to_html", ("test",), False),
    ("plugins.markdown_table.markdown_table_plugin", "MarkdownTablePlugin", "markdown_table", "csv_to_md", ("test",), False),
    ("plugins.morse.morse_plugin", "MorsePlugin", "morse", "encode", ("test",), False),
    ("plugins.notes.notes_plugin", "NotesPlugin", "notes", "create_note", ("test",), False),
    ("plugins.number_base.number_base_plugin", "NumberBasePlugin", "number-base", "to_binary", ("test",), False),
    ("plugins.palindrome.palindrome_plugin", "PalindromePlugin", "palindrome", "is_palindrome", ("test",), False),
    ("plugins.password.password_plugin", "PasswordPlugin", "password", "generate_password", (), False),
    ("plugins.pdf.pdf_plugin", "PDFPlugin", "pdf", "is_available", (), False),
    ("plugins.poker.poker_plugin", "PokerPlugin", "poker", "create_deck", (), False),
    ("plugins.pronounceable.pronounceable_plugin", "PronounceablePlugin", "pronounceable", "generate_word", (), False),
    ("plugins.qrcode.qrcode_plugin", "QRCodePlugin", "qrcode", "generate", ("test",), False),
    ("plugins.regex.regex_plugin", "RegexPlugin", "regex", "validate", ("test",), False),
    ("plugins.roman_numeral.roman_numeral_plugin", "RomanNumeralPlugin", "roman-numeral", "from_roman", ("test",), False),
    ("plugins.rss.rss_plugin", "RSSPlugin", "rss", "is_available", (), False),
    ("plugins.semver.semver_plugin", "SemverPlugin", "semver", "parse", ("test",), False),
    ("plugins.sentiment.sentiment_plugin", "SentimentPlugin", "sentiment", "analyze_sentiment", ("test",), False),
    ("plugins.slug.slug_plugin", "SlugPlugin", "slug", "generate", ("test",), False),
    ("plugins.summarizer.summarizer_plugin", "SummarizerPlugin", "summarizer", "summarize", ("test",), False),
    ("plugins.timezone.timezone_plugin", "TimezonePlugin", "timezone", "get_current_time", (), False),
    ("plugins.todo.todo_plugin", "TodoPlugin", "todo", "create_task", ("test",), False),
    ("plugins.toml.toml_plugin", "TomlPlugin", "toml", "validate", ("test",), False),
    ("plugins.translate.translate_plugin", "TranslatePlugin", "translate", "detect_language", ("test",), False),
    ("plugins.units.units_plugin", "UnitsPlugin", "units", "get_categories", (), False),
    ("plugins.url.url_tool_plugin", "UrlToolPlugin", "url_tool", "parse", ("https://example.com",), False),
    ("plugins.uuid.uuid_plugin", "UuidPlugin", "uuid", "generate", (), False),
    ("plugins.video.video_plugin", "VideoPlugin", "video", "get_supported_formats", (), False),
    ("plugins.vigenere.vigenere_plugin", "VigenerePlugin", "vigenere", "generate_key", (), False),
    ("plugins.weather.weather_plugin", "WeatherPlugin", "weather", "set_units", ("metric",), False),
    ("plugins.web_scraper.web_scraper_plugin", "WebScraperPlugin", "web_scraper", "html_to_text", ("<p>hi</p>",), False),
    ("plugins.xml.xml_plugin", "XmlPlugin", "xml", "parse", ("<root/>",), False),
    ("plugins.yaml.yaml_plugin", "YamlPlugin", "yaml", "parse", ("test",), False),
]


@pytest.fixture()
def provider():
    """Mock LLM provider used as the ``provider`` argument where accepted."""
    return MockProvider(name="test", model="test")


def _build_plugin(module_path: str, class_name: str, provider) -> BasePlugin:
    """Import the plugin class and instantiate it.

    Passes the MockProvider as ``provider`` when the constructor accepts
    it; otherwise instantiates with no arguments.
    """
    module = importlib.import_module(module_path)
    plugin_class = getattr(module, class_name)
    assert issubclass(plugin_class, BasePlugin)

    kwargs = {}
    init_params = inspect.signature(plugin_class.__init__).parameters
    if "provider" in init_params:
        kwargs["provider"] = provider
    return plugin_class(**kwargs)


@pytest.mark.parametrize(
    "module_path,class_name,expected_name,smoke_method,smoke_args,expect_fnf",
    PLUGINS,
    ids=[entry[1] for entry in PLUGINS],
)
def test_plugin(
    module_path,
    class_name,
    expected_name,
    smoke_method,
    smoke_args,
    expect_fnf,
    provider,
):
    """Import, instantiate and exercise one plugin with simple input."""
    plugin = _build_plugin(module_path, class_name, provider)

    # name attribute present and non-empty
    assert isinstance(plugin.name, str)
    assert plugin.name
    assert plugin.name == expected_name

    # Lifecycle
    assert plugin.initialize() is True
    assert plugin.activate() is True
    assert plugin.status == PluginStatus.ACTIVE
    assert plugin.is_active()

    # Capabilities declared
    capabilities = plugin.get_capabilities()
    assert isinstance(capabilities, list)
    assert len(capabilities) > 0

    # Execution entry point callable with simple input
    if hasattr(plugin, "execute") and callable(getattr(plugin, "execute")):
        plugin.execute("+++.")
    else:
        method = getattr(plugin, smoke_method)
        assert callable(method)
        if expect_fnf:
            with pytest.raises(FileNotFoundError):
                method(*smoke_args)
        else:
            result = method(*smoke_args)
            success = getattr(result, "success", None)
            assert success is not False

    plugin.deactivate()
    plugin.shutdown()
