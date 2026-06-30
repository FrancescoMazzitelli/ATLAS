import requests
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class NominatimClient:
    def __init__(self, host: str = "localhost", port: int = 8080, timeout: int = 10):
        self.base_url = f"http://{host}:{port}"
        self.timeout = timeout
        logger.info(f"Nominatim geocoding: {self.base_url}")

    def reverse(self, lat: float, lon: float) -> Optional[dict]:
        try:
            resp = requests.get(
                f"{self.base_url}/reverse",
                params={"lat": lat, "lon": lon, "format": "json", "addressdetails": 1},
                timeout=self.timeout,
                headers={"User-Agent": "AURORA/1.0"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Reverse geocode failed for ({lat}, {lon}): {e}")
            return None

    def reverse_name(self, lat: float, lon: float) -> str:
        result = self.reverse(lat, lon)
        if result:
            addr = result.get("address", {})
            return ", ".join(
                filter(None, [
                    addr.get("road"),
                    addr.get("suburb"),
                    addr.get("neighbourhood"),
                    addr.get("city", addr.get("town", addr.get("village", ""))),
                ])
            ) or result.get("display_name", f"({lat:.4f}, {lon:.4f})")
        return f"({lat:.4f}, {lon:.4f})"

    def search(self, query: str, limit: int = 5) -> Optional[list]:
        try:
            resp = requests.get(
                f"{self.base_url}/search",
                params={"q": query, "format": "json", "limit": limit},
                timeout=self.timeout,
                headers={"User-Agent": "AURORA/1.0"},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"Search failed for {query!r}: {e}")
            return None

    def search_first(self, query: str) -> Optional[Tuple[float, float, str]]:
        results = self.search(query, limit=1)
        if results:
            r = results[0]
            return (float(r["lat"]), float(r["lon"]), r.get("display_name", query))
        return None
