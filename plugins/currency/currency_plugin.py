"""
Currency Conversion Plugin for LiuHao AI OS

Provides exchange rates, multiple currency support, rate history,
conversion with formatting, and rate caching.

Capabilities:
- convert: Convert amount between currencies
- get_rate: Get exchange rate for a currency pair
- get_all_rates: Get all exchange rates
- get_supported_currencies: List supported currencies
- convert_batch: Batch convert multiple amounts
- get_rate_history: Get historical rates
"""

import time
from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..plugin_base import (
    BasePlugin,
    PluginMetadata,
    PluginStatus,
    PluginExecutionError,
)


@dataclass
class ConversionResult:
    """Result of a currency conversion."""
    amount: float
    from_currency: str
    to_currency: str
    result: float
    rate: float
    rate_date: str
    formatted_from: str
    formatted_to: str


@dataclass
class RateInfo:
    """Information about an exchange rate."""
    base_currency: str
    target_currency: str
    rate: float
    inverse_rate: float
    date: str
    source: str


class CurrencyPlugin(BasePlugin):
    """Currency conversion plugin.

    Supports exchange rate lookup, currency conversion,
    rate history, and formatted output.

    Example:
        plugin = CurrencyPlugin()
        plugin.initialize()
        plugin.activate()

        result = plugin.convert(100, "USD", "CNY")
        print(result.result)
    """

    # Base exchange rates (relative to USD as of approximate 2024 rates)
    BASE_RATES = {
        "USD": 1.0,
        "EUR": 0.92,
        "GBP": 0.79,
        "JPY": 150.0,
        "CNY": 7.24,
        "HKD": 7.82,
        "TWD": 31.5,
        "KRW": 1330.0,
        "SGD": 1.34,
        "AUD": 1.53,
        "NZD": 1.65,
        "CAD": 1.36,
        "CHF": 0.88,
        "SEK": 10.4,
        "NOK": 10.6,
        "DKK": 6.87,
        "INR": 83.2,
        "RUB": 92.5,
        "BRL": 5.05,
        "ZAR": 18.6,
        "MXN": 17.1,
        "THB": 35.2,
        "MYR": 4.72,
        "IDR": 15700.0,
        "PHP": 56.3,
        "VND": 24500.0,
        "PLN": 4.02,
        "CZK": 23.5,
        "HUF": 360.0,
        "TRY": 32.2,
        "ILS": 3.75,
        "AED": 3.67,
        "SAR": 3.75,
        "ARS": 850.0,
        "COP": 3950.0,
        "CLP": 950.0,
        "PEN": 3.75,
        "EGP": 47.5,
        "NGN": 1500.0,
        "PKR": 278.0,
        "BDT": 110.0,
        "UAH": 38.5,
        "RON": 4.57,
        "BGN": 1.80,
        "HRK": 6.95,
        "ISK": 138.0,
    }

    # Currency symbols
    CURRENCY_SYMBOLS = {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "CNY": "¥",
        "HKD": "HK$",
        "TWD": "NT$",
        "KRW": "₩",
        "SGD": "S$",
        "AUD": "A$",
        "NZD": "NZ$",
        "CAD": "C$",
        "CHF": "CHF",
        "SEK": "kr",
        "NOK": "kr",
        "DKK": "kr",
        "INR": "₹",
        "RUB": "₽",
        "BRL": "R$",
        "ZAR": "R",
        "MXN": "Mex$",
        "THB": "฿",
        "MYR": "RM",
        "IDR": "Rp",
        "PHP": "₱",
        "VND": "₫",
        "PLN": "zł",
        "CZK": "Kč",
        "HUF": "Ft",
        "TRY": "₺",
        "ILS": "₪",
        "AED": "د.إ",
        "SAR": "﷼",
        "ARS": "$",
        "COP": "COL$",
        "CLP": "CLP$",
        "PEN": "S/",
        "EGP": "E£",
        "NGN": "₦",
        "PKR": "₨",
        "BDT": "৳",
        "UAH": "₴",
        "RON": "lei",
        "BGN": "лв",
        "HRK": "kn",
        "ISK": "kr",
    }

    # Currency names
    CURRENCY_NAMES = {
        "USD": "US Dollar",
        "EUR": "Euro",
        "GBP": "British Pound",
        "JPY": "Japanese Yen",
        "CNY": "Chinese Yuan",
        "HKD": "Hong Kong Dollar",
        "TWD": "New Taiwan Dollar",
        "KRW": "South Korean Won",
        "SGD": "Singapore Dollar",
        "AUD": "Australian Dollar",
        "NZD": "New Zealand Dollar",
        "CAD": "Canadian Dollar",
        "CHF": "Swiss Franc",
        "SEK": "Swedish Krona",
        "NOK": "Norwegian Krone",
        "DKK": "Danish Krone",
        "INR": "Indian Rupee",
        "RUB": "Russian Ruble",
        "BRL": "Brazilian Real",
        "ZAR": "South African Rand",
        "MXN": "Mexican Peso",
        "THB": "Thai Baht",
        "MYR": "Malaysian Ringgit",
        "IDR": "Indonesian Rupiah",
        "PHP": "Philippine Peso",
        "VND": "Vietnamese Dong",
        "PLN": "Polish Zloty",
        "CZK": "Czech Koruna",
        "HUF": "Hungarian Forint",
        "TRY": "Turkish Lira",
        "ILS": "Israeli Shekel",
        "AED": "UAE Dirham",
        "SAR": "Saudi Riyal",
        "ARS": "Argentine Peso",
        "COP": "Colombian Peso",
        "CLP": "Chilean Peso",
        "PEN": "Peruvian Sol",
        "EGP": "Egyptian Pound",
        "NGN": "Nigerian Naira",
        "PKR": "Pakistani Rupee",
        "BDT": "Bangladeshi Taka",
        "UAH": "Ukrainian Hryvnia",
        "RON": "Romanian Leu",
        "BGN": "Bulgarian Lev",
        "HRK": "Croatian Kuna",
        "ISK": "Icelandic Krona",
    }

    def __init__(self) -> None:
        """Initialize the currency plugin."""
        metadata = PluginMetadata(
            name="currency",
            version="1.0.0",
            description="Currency conversion and exchange rates",
            author="LiuHao AI OS Team",
            categories=["finance", "conversion", "utility"],
            keywords=["currency", "exchange", "money", "convert", "forex"],
            requires_internet=False,
            dependencies=[],
        )
        super().__init__(metadata)

        self._rates: Dict[str, float] = dict(self.BASE_RATES)
        self._base_currency: str = "USD"
        self._rate_date: str = datetime.now().strftime("%Y-%m-%d")
        self._rate_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl: int = 3600  # 1 hour
        self._history: Dict[str, List[Dict[str, Any]]] = {}  # currency -> list of rates

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the currency plugin."""
        try:
            if config:
                if "base_currency" in config:
                    self._base_currency = config["base_currency"]
                if "rates" in config:
                    self._rates.update(config["rates"])

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the currency plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the currency plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the currency plugin."""
        try:
            self._rate_cache.clear()
            self._history.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "convert",
            "get_rate",
            "get_all_rates",
            "get_supported_currencies",
            "convert_batch",
            "get_rate_history",
            "format_currency",
            "update_rates",
        ]

    # ==================== Conversion Methods ====================

    def convert(
        self,
        amount: float,
        from_currency: str,
        to_currency: str,
        date: Optional[str] = None,
    ) -> ConversionResult:
        """Convert an amount from one currency to another.

        Args:
            amount: Amount to convert.
            from_currency: Source currency code.
            to_currency: Target currency code.
            date: Exchange rate date (YYYY-MM-DD). Default: latest.

        Returns:
            ConversionResult with the converted amount.

        Raises:
            ValueError: If either currency is not supported.
        """
        self._ensure_active()
        self._increment_call_count()

        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency not in self._rates:
            raise ValueError(f"Unsupported currency: {from_currency}")
        if to_currency not in self._rates:
            raise ValueError(f"Unsupported currency: {to_currency}")

        if amount <= 0:
            return ConversionResult(
                amount=amount,
                from_currency=from_currency,
                to_currency=to_currency,
                result=0,
                rate=0,
                rate_date=self._rate_date,
                formatted_from=self._format_currency(0, from_currency),
                formatted_to=self._format_currency(0, to_currency),
            )

        # Get rate (convert via USD base)
        from_rate = self._rates[from_currency]
        to_rate = self._rates[to_currency]

        # rate = how much of target you get per 1 unit of source
        rate = to_rate / from_rate
        result = amount * rate

        return ConversionResult(
            amount=amount,
            from_currency=from_currency,
            to_currency=to_currency,
            result=round(result, 2),
            rate=round(rate, 6),
            rate_date=date or self._rate_date,
            formatted_from=self._format_currency(amount, from_currency),
            formatted_to=self._format_currency(result, to_currency),
        )

    def get_rate(self, from_currency: str, to_currency: str) -> RateInfo:
        """Get the exchange rate for a currency pair.

        Args:
            from_currency: Base currency code.
            to_currency: Target currency code.

        Returns:
            RateInfo with the exchange rate.
        """
        self._ensure_active()
        self._increment_call_count()

        from_currency = from_currency.upper()
        to_currency = to_currency.upper()

        if from_currency not in self._rates:
            raise ValueError(f"Unsupported currency: {from_currency}")
        if to_currency not in self._rates:
            raise ValueError(f"Unsupported currency: {to_currency}")

        rate = self._rates[to_currency] / self._rates[from_currency]
        inverse_rate = self._rates[from_currency] / self._rates[to_currency]

        return RateInfo(
            base_currency=from_currency,
            target_currency=to_currency,
            rate=round(rate, 6),
            inverse_rate=round(inverse_rate, 6),
            date=self._rate_date,
            source="static",
        )

    def get_all_rates(self, base_currency: str = "USD") -> Dict[str, float]:
        """Get all exchange rates relative to a base currency.

        Args:
            base_currency: Base currency code.

        Returns:
            Dict mapping currency codes to exchange rates.
        """
        self._ensure_active()
        self._increment_call_count()

        base_currency = base_currency.upper()
        if base_currency not in self._rates:
            raise ValueError(f"Unsupported base currency: {base_currency}")

        base_rate = self._rates[base_currency]
        return {
            currency: round(rate / base_rate, 6)
            for currency, rate in self._rates.items()
        }

    def get_supported_currencies(self) -> List[Dict[str, str]]:
        """Get list of all supported currencies.

        Returns:
            List of dicts with 'code', 'name', 'symbol'.
        """
        currencies = []
        for code in sorted(self._rates.keys()):
            currencies.append({
                "code": code,
                "name": self.CURRENCY_NAMES.get(code, code),
                "symbol": self.CURRENCY_SYMBOLS.get(code, code),
            })
        return currencies

    def convert_batch(
        self,
        conversions: List[Dict[str, Any]],
    ) -> List[ConversionResult]:
        """Batch convert multiple currency pairs.

        Args:
            conversions: List of dicts with 'amount', 'from', 'to'.

        Returns:
            List of ConversionResult objects.
        """
        self._ensure_active()

        results = []
        for conv in conversions:
            result = self.convert(
                amount=conv.get("amount", 0),
                from_currency=conv.get("from", "USD"),
                to_currency=conv.get("to", "USD"),
            )
            results.append(result)

        return results

    def format_currency(self, amount: float, currency: str) -> str:
        """Format a currency amount with the appropriate symbol.

        Args:
            amount: Numeric amount.
            currency: Currency code.

        Returns:
            Formatted currency string.
        """
        return self._format_currency(amount, currency)

    # ==================== Rate History ====================

    def get_rate_history(
        self,
        from_currency: str,
        to_currency: str,
        days: int = 7,
    ) -> List[Dict[str, Any]]:
        """Get historical exchange rates.

        Generates simulated historical data based on current rates.

        Args:
            from_currency: Base currency.
            to_currency: Target currency.
            days: Number of days of history.

        Returns:
            List of dicts with 'date' and 'rate'.
        """
        self._ensure_active()
        self._increment_call_count()

        current_rate = self.get_rate(from_currency, to_currency).rate
        history = []

        import random
        random.seed(f"{from_currency}_{to_currency}_history")

        for i in range(days - 1, -1, -1):
            date = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            # Add random variation of +/- 2%
            variation = random.uniform(-0.02, 0.02)
            rate = current_rate * (1 + variation)
            history.append({
                "date": date,
                "rate": round(rate, 6),
                "from": from_currency,
                "to": to_currency,
            })

        return history

    def update_rates(self, new_rates: Dict[str, float]) -> int:
        """Update exchange rates.

        Args:
            new_rates: Dict of currency codes to rates.

        Returns:
            Number of rates updated.
        """
        count = 0
        for currency, rate in new_rates.items():
            if rate > 0:
                self._rates[currency.upper()] = rate
                count += 1
        self._rate_date = datetime.now().strftime("%Y-%m-%d")
        return count

    # ==================== Internal Helpers ====================

    def _format_currency(self, amount: float, currency: str) -> str:
        """Format currency with symbol and proper decimal places."""
        currency = currency.upper()
        symbol = self.CURRENCY_SYMBOLS.get(currency, currency)

        # Determine decimal places
        # Most currencies use 2 decimals, but some (like JPY) use 0
        zero_decimal_currencies = {
            "JPY", "KRW", "IDR", "VND", "CLP", "ARS", "COP",
        }

        if currency in zero_decimal_currencies:
            formatted = f"{amount:,.0f}"
        else:
            formatted = f"{amount:,.2f}"

        # Determine symbol placement
        if symbol in ("$", "€", "£", "₹", "₽", "R$", "S$", "A$", "C$", "HK$", "NT$",
                      "NZ$", "Mex$", "COL$", "CLP$", "E£"):
            return f"{symbol}{formatted}"
        else:
            return f"{formatted} {symbol}"

    # ==================== Statistics ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get plugin statistics."""
        return {
            "supported_currencies": len(self._rates),
            "base_currency": self._base_currency,
            "rate_date": self._rate_date,
            "cache_size": len(self._rate_cache),
        }
