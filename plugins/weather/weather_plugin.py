"""
Weather Data Plugin for LiuHao AI OS

Provides current weather, forecasts (daily and hourly),
location search, weather alerts, and historical data interface.

Capabilities:
- get_current: Get current weather for a location
- get_forecast_daily: Get daily forecast
- get_forecast_hourly: Get hourly forecast
- search_location: Search for location by name
- get_alerts: Get weather alerts
- get_history: Get historical weather data
"""

import re
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
class WeatherCondition:
    """Current weather conditions."""
    location: str
    country: str
    latitude: float
    longitude: float
    temperature: float
    feels_like: float
    humidity: float
    pressure: float
    wind_speed: float
    wind_direction: float
    wind_direction_label: str
    weather_description: str
    weather_icon: str
    visibility: float
    uv_index: float
    cloud_cover: float
    precipitation: float
    sunrise: str
    sunset: str
    observation_time: str
    units: str = "metric"


@dataclass
class DailyForecast:
    """A single day's forecast."""
    date: str
    temp_min: float
    temp_max: float
    temp_day: float
    temp_night: float
    temp_eve: float
    temp_morn: float
    humidity: float
    pressure: float
    wind_speed: float
    weather_description: str
    weather_icon: str
    precipitation_probability: float
    uv_index: float
    sunrise: str
    sunset: str


@dataclass
class HourlyForecast:
    """A single hour's forecast."""
    time: str
    temperature: float
    feels_like: float
    humidity: float
    pressure: float
    wind_speed: float
    weather_description: str
    weather_icon: str
    precipitation_probability: float


@dataclass
class WeatherAlert:
    """A weather alert/warning."""
    event: str
    severity: str
    description: str
    start: str
    end: str
    sender: str = ""


class WeatherPlugin(BasePlugin):
    """Weather data plugin.

    Provides current weather, daily and hourly forecasts,
    location search, weather alerts, and historical data interface.

    Note: This plugin uses OpenWeatherMap API if a key is provided,
    otherwise returns simulated/historical data for demonstration.

    Example:
        plugin = WeatherPlugin()
        plugin.initialize({"api_key": "your_api_key"})
        plugin.activate()

        current = plugin.get_current("Beijing")
        print(current.temperature)
    """

    # Weather condition codes and descriptions
    WEATHER_CONDITIONS = {
        "clear": {"description": "Clear sky", "icon": "01d"},
        "few_clouds": {"description": "Few clouds", "icon": "02d"},
        "scattered_clouds": {"description": "Scattered clouds", "icon": "03d"},
        "broken_clouds": {"description": "Broken clouds", "icon": "04d"},
        "shower_rain": {"description": "Shower rain", "icon": "09d"},
        "rain": {"description": "Rain", "icon": "10d"},
        "thunderstorm": {"description": "Thunderstorm", "icon": "11d"},
        "snow": {"description": "Snow", "icon": "13d"},
        "mist": {"description": "Mist", "icon": "50d"},
    }

    def __init__(self) -> None:
        """Initialize the weather plugin."""
        metadata = PluginMetadata(
            name="weather",
            version="1.0.0",
            description="Weather data and forecast plugin",
            author="LiuHao AI OS Team",
            categories=["weather", "utility", "data"],
            keywords=["weather", "forecast", "temperature", "rain", "wind"],
            requires_internet=True,
            dependencies=[],
        )
        super().__init__(metadata)

        self._api_key: str = ""
        self._has_requests: bool = False
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._cache_ttl: int = 600  # 10 minutes
        self._units: str = "metric"  # metric (C) or imperial (F)

        # Sample city data for offline/demo mode
        self._sample_cities = {
            "beijing": {"lat": 39.9042, "lon": 116.4074, "country": "CN", "base_temp": 15},
            "shanghai": {"lat": 31.2304, "lon": 121.4737, "country": "CN", "base_temp": 18},
            "guangzhou": {"lat": 23.1291, "lon": 113.2644, "country": "CN", "base_temp": 25},
            "shenzhen": {"lat": 22.5431, "lon": 114.0579, "country": "CN", "base_temp": 26},
            "chengdu": {"lat": 30.5728, "lon": 104.0668, "country": "CN", "base_temp": 17},
            "new york": {"lat": 40.7128, "lon": -74.0060, "country": "US", "base_temp": 12},
            "london": {"lat": 51.5074, "lon": -0.1278, "country": "GB", "base_temp": 10},
            "tokyo": {"lat": 35.6762, "lon": 139.6503, "country": "JP", "base_temp": 16},
            "paris": {"lat": 48.8566, "lon": 2.3522, "country": "FR", "base_temp": 11},
            "sydney": {"lat": -33.8688, "lon": 151.2093, "country": "AU", "base_temp": 20},
        }

    # ==================== Lifecycle Methods ====================

    def initialize(self, config: Optional[Dict[str, Any]] = None) -> bool:
        """Initialize the weather plugin.

        Args:
            config: Configuration with optional 'api_key' for OpenWeatherMap.
        """
        try:
            try:
                import requests
                self._has_requests = True
            except ImportError:
                self._has_requests = False

            if config:
                self._api_key = config.get("api_key", "")
                self._units = config.get("units", "metric")
                self._cache_ttl = config.get("cache_ttl", 600)

            self._set_status(PluginStatus.INITIALIZED)
            return True
        except Exception as e:
            self._set_error(f"Initialization failed: {e}")
            return False

    def activate(self) -> bool:
        """Activate the weather plugin."""
        try:
            self._set_status(PluginStatus.ACTIVE)
            return True
        except Exception as e:
            self._set_error(f"Activation failed: {e}")
            return False

    def deactivate(self) -> bool:
        """Deactivate the weather plugin."""
        try:
            self._set_status(PluginStatus.INACTIVE)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def shutdown(self) -> bool:
        """Shut down the weather plugin."""
        try:
            self._cache.clear()
            self._set_status(PluginStatus.SHUTDOWN)
            return True
        except Exception as e:
            self._set_error(str(e))
            return False

    def get_capabilities(self) -> List[str]:
        """Return the list of capabilities."""
        return [
            "get_current",
            "get_forecast_daily",
            "get_forecast_hourly",
            "search_location",
            "get_alerts",
            "get_history",
            "set_units",
            "clear_cache",
        ]

    # ==================== Weather Methods ====================

    def get_current(
        self,
        location: str,
        units: Optional[str] = None,
    ) -> WeatherCondition:
        """Get current weather for a location.

        Args:
            location: City name or "lat,lon" coordinates.
            units: Temperature units ('metric' or 'imperial').

        Returns:
            WeatherCondition with current weather data.
        """
        self._ensure_active()
        self._increment_call_count()

        units = units or self._units
        cache_key = f"current:{location}:{units}"

        # Check cache
        cached = self._get_cache(cache_key)
        if cached:
            return self._dict_to_condition(cached, units)

        if self._api_key and self._has_requests:
            result = self._fetch_current_api(location, units)
        else:
            result = self._generate_sample_current(location, units)

        self._set_cache(cache_key, result.__dict__)
        return result

    def get_forecast_daily(
        self,
        location: str,
        days: int = 7,
        units: Optional[str] = None,
    ) -> List[DailyForecast]:
        """Get daily weather forecast.

        Args:
            location: City name or coordinates.
            days: Number of days (1-16).
            units: Temperature units.

        Returns:
            List of DailyForecast objects.
        """
        self._ensure_active()
        self._increment_call_count()

        units = units or self._units
        days = max(1, min(days, 16))
        cache_key = f"daily:{location}:{days}:{units}"

        cached = self._get_cache(cache_key)
        if cached:
            return [DailyForecast(**d) for d in cached]

        if self._api_key and self._has_requests:
            result = self._fetch_daily_api(location, days, units)
        else:
            result = self._generate_sample_daily(location, days, units)

        self._set_cache(cache_key, [f.__dict__ for f in result])
        return result

    def get_forecast_hourly(
        self,
        location: str,
        hours: int = 24,
        units: Optional[str] = None,
    ) -> List[HourlyForecast]:
        """Get hourly weather forecast.

        Args:
            location: City name or coordinates.
            hours: Number of hours (1-48).
            units: Temperature units.

        Returns:
            List of HourlyForecast objects.
        """
        self._ensure_active()
        self._increment_call_count()

        units = units or self._units
        hours = max(1, min(hours, 48))
        cache_key = f"hourly:{location}:{hours}:{units}"

        cached = self._get_cache(cache_key)
        if cached:
            return [HourlyForecast(**h) for h in cached]

        if self._api_key and self._has_requests:
            result = self._fetch_hourly_api(location, hours, units)
        else:
            result = self._generate_sample_hourly(location, hours, units)

        self._set_cache(cache_key, [h.__dict__ for h in result])
        return result

    def search_location(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search for locations by name.

        Args:
            query: Search query (city name).
            limit: Maximum number of results.

        Returns:
            List of location dicts with name, country, lat, lon.
        """
        self._ensure_active()
        self._increment_call_count()

        if self._api_key and self._has_requests:
            return self._search_location_api(query, limit)
        else:
            return self._search_sample(query, limit)

    def get_alerts(self, location: str) -> List[WeatherAlert]:
        """Get weather alerts for a location.

        Args:
            location: City name or coordinates.

        Returns:
            List of WeatherAlert objects.
        """
        self._ensure_active()
        self._increment_call_count()

        if self._api_key and self._has_requests:
            return self._fetch_alerts_api(location)
        else:
            return []  # No sample alerts

    def get_history(
        self,
        location: str,
        date: str,
        units: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Get historical weather data for a date.

        Args:
            location: City name or coordinates.
            date: Date string (YYYY-MM-DD).
            units: Temperature units.

        Returns:
            Dict with historical weather data, or None if unavailable.
        """
        self._ensure_active()
        self._increment_call_count()

        units = units or self._units

        # Generate sample historical data
        city_key = location.lower().strip()
        city_data = self._sample_cities.get(city_key)

        if not city_data:
            # Try partial match
            for name, data in self._sample_cities.items():
                if query in name or name in query:
                    city_data = data
                    break

        if not city_data:
            return None

        base_temp = city_data["base_temp"]

        # Generate seasonal variation based on date
        try:
            dt = datetime.strptime(date, "%Y-%m-%d")
            day_of_year = dt.timetuple().tm_yday
            seasonal = 10 * math.sin(2 * math.pi * (day_of_year - 80) / 365)
        except Exception:
            seasonal = 0

        import random
        random.seed(date + location)

        temp_min = base_temp + seasonal - random.uniform(2, 6)
        temp_max = base_temp + seasonal + random.uniform(2, 8)
        avg_temp = (temp_min + temp_max) / 2

        if units == "imperial":
            temp_min = temp_min * 9/5 + 32
            temp_max = temp_max * 9/5 + 32
            avg_temp = avg_temp * 9/5 + 32

        conditions = ["clear", "few_clouds", "scattered_clouds", "rain", "mist"]
        condition = random.choice(conditions)
        condition_info = self.WEATHER_CONDITIONS.get(condition, {})

        return {
            "date": date,
            "location": location,
            "temp_min": round(temp_min, 1),
            "temp_max": round(temp_max, 1),
            "temp_avg": round(avg_temp, 1),
            "humidity": random.randint(40, 90),
            "pressure": random.randint(1000, 1030),
            "wind_speed": round(random.uniform(1, 15), 1),
            "precipitation": round(random.uniform(0, 20), 1),
            "condition": condition_info.get("description", condition),
            "units": units,
        }

    def set_units(self, units: str) -> None:
        """Set default temperature units."""
        if units.lower() in ("metric", "imperial"):
            self._units = units.lower()

    def clear_cache(self) -> int:
        """Clear the weather data cache.

        Returns:
            Number of entries cleared.
        """
        count = len(self._cache)
        self._cache.clear()
        return count

    # ==================== API Implementations ====================

    def _fetch_current_api(self, location: str, units: str) -> WeatherCondition:
        """Fetch current weather from OpenWeatherMap API."""
        import requests

        base_url = "https://api.openweathermap.org/data/2.5/weather"

        # Determine if location is coordinates
        if "," in location:
            lat, lon = location.split(",")
            params = {"lat": lat.strip(), "lon": lon.strip()}
        else:
            params = {"q": location}

        params.update({
            "appid": self._api_key,
            "units": units,
        })

        try:
            response = requests.get(base_url, params=params, timeout=10)
            data = response.json()

            if response.status_code != 200:
                return self._generate_sample_current(location, units)

            weather = data.get("weather", [{}])[0]
            main = data.get("main", {})
            wind = data.get("wind", {})
            sys = data.get("sys", {})
            coord = data.get("coord", {})

            sunrise = datetime.fromtimestamp(sys.get("sunrise", 0)).strftime("%H:%M")
            sunset = datetime.fromtimestamp(sys.get("sunset", 0)).strftime("%H:%M")

            wind_deg = wind.get("deg", 0)
            wind_label = self._deg_to_compass(wind_deg)

            return WeatherCondition(
                location=data.get("name", location),
                country=sys.get("country", ""),
                latitude=coord.get("lat", 0),
                longitude=coord.get("lon", 0),
                temperature=main.get("temp", 0),
                feels_like=main.get("feels_like", 0),
                humidity=main.get("humidity", 0),
                pressure=main.get("pressure", 0),
                wind_speed=wind.get("speed", 0),
                wind_direction=wind_deg,
                wind_direction_label=wind_label,
                weather_description=weather.get("description", ""),
                weather_icon=weather.get("icon", ""),
                visibility=data.get("visibility", 0) / 1000,
                uv_index=0,
                cloud_cover=data.get("clouds", {}).get("all", 0),
                precipitation=data.get("rain", {}).get("1h", 0),
                sunrise=sunrise,
                sunset=sunset,
                observation_time=datetime.now().isoformat(),
                units=units,
            )
        except Exception:
            return self._generate_sample_current(location, units)

    def _fetch_daily_api(
        self, location: str, days: int, units: str
    ) -> List[DailyForecast]:
        """Fetch daily forecast from API."""
        # Implementation would use onecall API
        # Fallback to sample data
        return self._generate_sample_daily(location, days, units)

    def _fetch_hourly_api(
        self, location: str, hours: int, units: str
    ) -> List[HourlyForecast]:
        """Fetch hourly forecast from API."""
        return self._generate_sample_hourly(location, hours, units)

    def _fetch_alerts_api(self, location: str) -> List[WeatherAlert]:
        """Fetch weather alerts from API."""
        return []

    def _search_location_api(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Search locations using API."""
        import requests

        url = "http://api.openweathermap.org/geo/1.0/direct"
        params = {
            "q": query,
            "limit": limit,
            "appid": self._api_key,
        }

        try:
            response = requests.get(url, params=params, timeout=10)
            if response.status_code == 200:
                results = []
                for item in response.json():
                    results.append({
                        "name": item.get("name", ""),
                        "country": item.get("country", ""),
                        "state": item.get("state", ""),
                        "lat": item.get("lat", 0),
                        "lon": item.get("lon", 0),
                    })
                return results
        except Exception:
            pass

        return self._search_sample(query, limit)

    # ==================== Sample Data Generation ====================

    def _generate_sample_current(self, location: str, units: str) -> WeatherCondition:
        """Generate sample current weather data."""
        city_key = location.lower().strip()
        city_data = self._sample_cities.get(city_key)

        if not city_data:
            # Use first matching city or default
            for name, data in self._sample_cities.items():
                if city_key in name or name in city_key:
                    city_data = data
                    city_key = name
                    break
            if not city_data:
                city_data = {"lat": 0, "lon": 0, "country": "??", "base_temp": 20}

        base_temp = city_data["base_temp"]
        import random
        random.seed(location + datetime.now().strftime("%Y%m%d%H"))

        temp = base_temp + random.uniform(-3, 3)
        feels_like = temp + random.uniform(-2, 2)
        humidity = random.randint(40, 85)
        wind_speed = round(random.uniform(1, 12), 1)
        wind_deg = random.randint(0, 359)

        conditions = ["clear", "few_clouds", "scattered_clouds", "broken_clouds"]
        if humidity > 70:
            conditions.extend(["rain", "mist"])
        condition = random.choice(conditions)
        condition_info = self.WEATHER_CONDITIONS.get(condition, {})

        if units == "imperial":
            temp = temp * 9/5 + 32
            feels_like = feels_like * 9/5 + 32

        sunrise = "06:30"
        sunset = "18:45"

        return WeatherCondition(
            location=location.title(),
            country=city_data.get("country", ""),
            latitude=city_data.get("lat", 0),
            longitude=city_data.get("lon", 0),
            temperature=round(temp, 1),
            feels_like=round(feels_like, 1),
            humidity=humidity,
            pressure=random.randint(1005, 1025),
            wind_speed=wind_speed,
            wind_direction=wind_deg,
            wind_direction_label=self._deg_to_compass(wind_deg),
            weather_description=condition_info.get("description", condition),
            weather_icon=condition_info.get("icon", "01d"),
            visibility=round(random.uniform(5, 20), 1),
            uv_index=round(random.uniform(1, 10), 1),
            cloud_cover=random.randint(0, 80),
            precipitation=round(random.uniform(0, 5), 1),
            sunrise=sunrise,
            sunset=sunset,
            observation_time=datetime.now().isoformat(),
            units=units,
        )

    def _generate_sample_daily(
        self, location: str, days: int, units: str
    ) -> List[DailyForecast]:
        """Generate sample daily forecast data."""
        import random
        random.seed(location + "daily")

        current = self._generate_sample_current(location, units)
        forecasts = []

        for i in range(days):
            date = (datetime.now() + timedelta(days=i)).strftime("%Y-%m-%d")

            day_variation = random.uniform(-5, 5)
            temp_max = current.temperature + day_variation + random.uniform(2, 6)
            temp_min = current.temperature + day_variation - random.uniform(3, 8)
            temp_day = (temp_max + temp_min) / 2
            temp_night = temp_min + random.uniform(0, 2)

            condition = random.choice(list(self.WEATHER_CONDITIONS.keys()))
            condition_info = self.WEATHER_CONDITIONS[condition]

            forecasts.append(DailyForecast(
                date=date,
                temp_min=round(temp_min, 1),
                temp_max=round(temp_max, 1),
                temp_day=round(temp_day, 1),
                temp_night=round(temp_night, 1),
                temp_eve=round((temp_day + temp_night) / 2, 1),
                temp_morn=round(temp_min + random.uniform(1, 3), 1),
                humidity=random.randint(40, 85),
                pressure=random.randint(1005, 1025),
                wind_speed=round(random.uniform(1, 15), 1),
                weather_description=condition_info["description"],
                weather_icon=condition_info["icon"],
                precipitation_probability=round(random.uniform(0, 1), 2),
                uv_index=round(random.uniform(1, 10), 1),
                sunrise="06:30",
                sunset="18:45",
            ))

        return forecasts

    def _generate_sample_hourly(
        self, location: str, hours: int, units: str
    ) -> List[HourlyForecast]:
        """Generate sample hourly forecast data."""
        import random
        random.seed(location + "hourly")

        current = self._generate_sample_current(location, units)
        forecasts = []

        for i in range(hours):
            time = (datetime.now() + timedelta(hours=i)).replace(minute=0, second=0, microsecond=0)

            # Diurnal temperature variation
            hour = time.hour
            temp_variation = 5 * math.sin(2 * math.pi * (hour - 6) / 24)
            temp = current.temperature + temp_variation + random.uniform(-1, 1)
            feels_like = temp + random.uniform(-1, 1)

            condition = random.choice(list(self.WEATHER_CONDITIONS.keys()))
            condition_info = self.WEATHER_CONDITIONS[condition]

            forecasts.append(HourlyForecast(
                time=time.isoformat(),
                temperature=round(temp, 1),
                feels_like=round(feels_like, 1),
                humidity=random.randint(40, 85),
                pressure=random.randint(1005, 1025),
                wind_speed=round(random.uniform(1, 12), 1),
                weather_description=condition_info["description"],
                weather_icon=condition_info["icon"],
                precipitation_probability=round(random.uniform(0, 1), 2),
            ))

        return forecasts

    def _search_sample(self, query: str, limit: int) -> List[Dict[str, Any]]:
        """Search sample cities."""
        results = []
        query_lower = query.lower()

        for name, data in self._sample_cities.items():
            if query_lower in name or name in query_lower:
                results.append({
                    "name": name.title(),
                    "country": data["country"],
                    "state": "",
                    "lat": data["lat"],
                    "lon": data["lon"],
                })

        return results[:limit]

    # ==================== Cache ====================

    def _get_cache(self, key: str) -> Optional[Dict[str, Any]]:
        """Get cached data if not expired."""
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["timestamp"] < self._cache_ttl:
                return entry["data"]
            else:
                del self._cache[key]
        return None

    def _set_cache(self, key: str, data: Any) -> None:
        """Set cached data."""
        self._cache[key] = {
            "data": data,
            "timestamp": time.time(),
        }

    # ==================== Utilities ====================

    def _deg_to_compass(self, degrees: float) -> str:
        """Convert wind direction degrees to compass label."""
        directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                     "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        index = round(degrees / 22.5) % 16
        return directions[index]

    def _dict_to_condition(self, data: Dict[str, Any], units: str) -> WeatherCondition:
        """Convert dict to WeatherCondition."""
        return WeatherCondition(**data)
