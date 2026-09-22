"""HTTP_HEADERS feeds two places: CORSMiddleware (which answers preflights) and uvicorn's static
response headers. Sending the Access-Control-* ones through both makes every response carry
`access-control-allow-origin: *` twice, and Chrome rejects the preflight
("contains multiple values '*, *'"). Only the non-CORS headers may go to uvicorn."""
from mirobody.server.middleware_stack import static_response_headers


def test_cors_headers_are_stripped_from_static_headers():
    given = [("Server", "mirobody-dev/1.4.4"), ("Access-Control-Allow-Origin", "*"),
             ("access-control-allow-headers", "*"), ("X-Frame-Options", "DENY")]
    assert static_response_headers(given) == [("Server", "mirobody-dev/1.4.4"), ("X-Frame-Options", "DENY")]


def test_static_headers_accepts_dict_and_none():
    assert static_response_headers({"Server": "x", "Access-Control-Max-Age": "600"}) == [("Server", "x")]
    assert static_response_headers(None) == []
