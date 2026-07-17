from app.services.telemetry_parser import TelemetryEntityParser


def test_parse_payload_extracts_and_deduplicates_structured_indicators():
    indicators = TelemetryEntityParser.parse_payload(
        (
            "Visit https://gateway.remote.net/panel and api.remote.net. "
            "Wallet 0x1111111111111111111111111111111111111111 "
            "again https://gateway.remote.net/panel"
        ),
        {
            "entities": [
                {"type": "text_link", "url": "https://docs.remote.io/start"},
                {"type": "url"},
            ]
        },
    )

    assert indicators.count(
        {"type": "canonical_url", "value": "https://gateway.remote.net/panel"}
    ) == 1
    assert {"type": "canonical_url", "value": "https://docs.remote.io/start"} in indicators
    assert {"type": "network_domain", "value": "api.remote.net"} in indicators
    assert {
        "type": "wallet_address",
        "value": "0x1111111111111111111111111111111111111111",
    } in indicators


def test_parse_payload_handles_empty_content():
    assert TelemetryEntityParser.parse_payload("") == []


def test_parse_payload_canonicalizes_url_variants_before_deduplication():
    indicators = TelemetryEntityParser.parse_payload(
        "See https://Example.com/Path/#frag and https://example.com/Path",
        {
            "entities": [
                {"type": "text_link", "url": "HTTPS://EXAMPLE.com/Path/#other"},
            ]
        },
    )

    assert indicators.count(
        {"type": "canonical_url", "value": "https://example.com/Path"}
    ) == 1
