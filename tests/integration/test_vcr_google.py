from __future__ import annotations

import vcr

from g2nc.utils.http import RetryConfig, create_client, request_with_retries

CASSETTE = "tests/cassettes/google_discovery.yaml"


@vcr.use_cassette(CASSETTE, filter_headers=["user-agent"])
def test_google_discovery_with_vcr() -> None:
    # Simple GET using our http helper against a Google public endpoint,
    # replayed via VCR cassette to avoid real network calls.
    client = create_client(timeout=10.0, verify=True)
    resp = request_with_retries(
        client,
        "GET",
        "https://www.googleapis.com/discovery/v1/apis",
        params={"name": "people"},
        retry=RetryConfig(max_retries=2),
        expected=(200,),
    )
    assert resp.status_code == 200
    # Minimal invariant from discovery directory response
    assert "kind" in resp.text
