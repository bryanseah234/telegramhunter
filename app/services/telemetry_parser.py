import re
from typing import Any, Dict, List
from urllib.parse import urlsplit, urlunsplit


class TelemetryEntityParser:
    URL_PATTERN = re.compile(r"https?://[^\s<>'\"`)\]}]+", re.IGNORECASE)
    DOMAIN_PATTERN = re.compile(
        r"\b(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
        r"(?:com|net|org|io|xyz|top|site|online|store|tech|ru|cn|pw|cc|dpdns\.org|duckdns\.org)\b",
        re.IGNORECASE,
    )
    CRYPTO_PATTERN = re.compile(
        r"\b(?:0x[a-fA-F0-9]{40}|[13][a-km-zA-HJ-NP-Z1-9]{25,34}|T[A-Za-z1-9]{33}|bc1[q-z0-9]{39,59})\b"
    )

    @staticmethod
    def canonicalize_url(value: str) -> str:
        clean = (value or "").strip().strip("'\"]}>.,")
        if not clean:
            return ""

        try:
            parsed = urlsplit(clean)
        except ValueError:
            return clean.split("#", 1)[0].rstrip("/")

        if not parsed.scheme or not parsed.netloc:
            return clean.split("#", 1)[0].rstrip("/")

        userinfo = ""
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            userinfo += "@"

        host = (parsed.hostname or "").lower()
        port = ""
        try:
            if parsed.port is not None:
                port = f":{parsed.port}"
        except ValueError:
            host = parsed.netloc.lower()

        path = parsed.path.rstrip("/")
        return urlunsplit((parsed.scheme.lower(), f"{userinfo}{host}{port}", path, parsed.query, ""))

    @classmethod
    def parse_payload(cls, content: str, raw_payload: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        indicators: List[Dict[str, Any]] = []
        if not content:
            return indicators

        for url in cls.URL_PATTERN.findall(content):
            canonical_url = cls.canonicalize_url(url)
            if canonical_url:
                indicators.append({"type": "canonical_url", "value": canonical_url})

        for domain in cls.DOMAIN_PATTERN.findall(content):
            indicators.append({"type": "network_domain", "value": domain.strip().lower()})

        for wallet in cls.CRYPTO_PATTERN.findall(content):
            indicators.append({"type": "wallet_address", "value": wallet.strip()})

        if raw_payload and isinstance(raw_payload, dict):
            entities = raw_payload.get("entities", []) or []
            for ent in entities:
                if ent.get("type") == "text_link" and ent.get("url"):
                    canonical_url = cls.canonicalize_url(ent.get("url"))
                    if canonical_url:
                        indicators.append({"type": "canonical_url", "value": canonical_url})

        seen = set()
        deduped = []
        for ind in indicators:
            key = (ind["type"], ind["value"])
            if key not in seen and len(ind["value"]) > 3:
                seen.add(key)
                deduped.append(ind)
        return deduped
