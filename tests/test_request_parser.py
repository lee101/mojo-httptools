from __future__ import annotations

import ctypes
from array import array

import httptools
import numpy as np
import pytest

import mojo_httptools


class Recorder:
    def __init__(self):
        self.events = []
        self.parser = None
        self.header_keep_alive = None
        self.header_upgrade = None

    def on_message_begin(self):
        self.events.append(("message_begin",))

    def on_url(self, value):
        self.events.append(("url", value))

    def on_header(self, name, value):
        self.events.append(("header", name, value))

    def on_headers_complete(self):
        self.header_keep_alive = self.parser.should_keep_alive()
        self.header_upgrade = self.parser.should_upgrade()
        self.events.append(("headers_complete",))

    def on_body(self, value):
        self.events.append(("body", value))

    def on_message_complete(self):
        self.events.append(("message_complete",))

    def on_chunk_header(self):
        self.events.append(("chunk_header",))

    def on_chunk_complete(self):
        self.events.append(("chunk_complete",))


def parse(parser_class, chunks):
    protocol = Recorder()
    parser = parser_class(protocol)
    protocol.parser = parser
    upgrade = None
    for chunk in chunks:
        try:
            parser.feed_data(chunk)
        except (
            httptools.HttpParserUpgrade,
            mojo_httptools.HttpParserUpgrade,
        ) as exc:
            upgrade = exc.args[0]
    return protocol, parser, upgrade


def normalized(events):
    result = []
    for event in events:
        if event[0] in {"url", "body"} and result and result[-1][0] == event[0]:
            result[-1] = (event[0], result[-1][1] + event[1])
        else:
            result.append(event)
    return result


@pytest.mark.parametrize(
    "request_data",
    [
        b"GET / HTTP/1.1\r\nHost: example.com\r\n\r\n",
        (
            b"OPTIONS * HTTP/1.0\r\n"
            b"Host: example.com\r\nConnection: keep-alive\r\n\r\n"
        ),
        (
            b"POST /submit?q=yes HTTP/1.1\r\n"
            b"Host: example.com\r\nContent-Type: application/octet-stream\r\n"
            b"Content-Length: 7\r\n\r\nabc\x00def"
        ),
    ],
)
def test_complete_requests_match_upstream(request_data):
    reference, reference_parser, reference_upgrade = parse(
        httptools.HttpRequestParser, [request_data]
    )
    actual, actual_parser, actual_upgrade = parse(
        mojo_httptools.HttpRequestParser, [request_data]
    )
    assert actual.events == reference.events
    assert actual_parser.get_method() == reference_parser.get_method()
    assert actual_parser.get_http_version() == reference_parser.get_http_version()
    assert actual_parser.should_keep_alive() == reference_parser.should_keep_alive()
    assert actual.header_keep_alive == reference.header_keep_alive
    assert actual_upgrade == reference_upgrade


def test_fragmented_fixed_length_request_matches_upstream():
    chunks = [
        b"PUT /resource HTTP/1.1\r\nHost: local",
        b"host\r\nContent-Type: text/pl",
        b"ain\r\nContent-Length: 10\r\n\r\n12",
        b"345",
        b"67890",
    ]
    reference, reference_parser, _ = parse(httptools.HttpRequestParser, chunks)
    actual, actual_parser, _ = parse(mojo_httptools.HttpRequestParser, chunks)
    assert normalized(actual.events) == normalized(reference.events)
    assert actual_parser.get_method() == reference_parser.get_method() == b"PUT"


def test_every_byte_fragmentation_has_same_semantics():
    request = (
        b"PATCH /items/42 HTTP/1.1\r\n"
        b"Host: localhost\r\nX-Test: yes\r\nContent-Length: 4\r\n\r\ndata"
    )
    chunks = [request[index : index + 1] for index in range(len(request))]
    reference, _, _ = parse(httptools.HttpRequestParser, chunks)
    actual, _, _ = parse(mojo_httptools.HttpRequestParser, chunks)
    assert normalized(actual.events) == normalized(reference.events)


def test_chunked_body_extensions_and_trailers_match_upstream():
    chunks = [
        (
            b"POST /upload HTTP/1.1\r\nHost: example.com\r\n"
            b"Transfer-Encoding: chunked\r\n\r\n4;foo=bar\r\nWi"
        ),
        b"ki\r\n5\r\npedia\r\n0\r\n",
        b"Digest: sha-256=xyz\r\n\r\n",
    ]
    reference, _, _ = parse(httptools.HttpRequestParser, chunks)
    actual, _, _ = parse(mojo_httptools.HttpRequestParser, chunks)
    assert normalized(actual.events) == normalized(reference.events)
    bodies = [event[1] for event in actual.events if event[0] == "body"]
    assert b"".join(bodies) == b"Wikipedia"


def test_many_chunks_are_all_consumed_in_one_feed():
    chunk = b"1\r\nx\r\n"
    request_data = (
        b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        + chunk * 500
        + b"0\r\n\r\n"
    )
    protocol, _, _ = parse(mojo_httptools.HttpRequestParser, [request_data])
    assert sum(len(event[1]) for event in protocol.events if event[0] == "body") == 500
    assert protocol.events[-1] == ("message_complete",)


def test_chunk_header_is_not_delayed_until_body_data():
    protocol = Recorder()
    parser = mojo_httptools.HttpRequestParser(protocol)
    protocol.parser = parser
    parser.feed_data(
        b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n4\r\n"
    )
    assert protocol.events[-1] == ("chunk_header",)
    parser.feed_data(b"data\r\n0\r\n\r\n")
    assert protocol.events[-1] == ("message_complete",)


def test_large_pipeline_crosses_reusable_event_batches():
    request = b"GET /batch HTTP/1.1\r\n\r\n"
    repetitions = 5000
    protocol, parser, _ = parse(
        mojo_httptools.HttpRequestParser, [request * repetitions]
    )
    assert sum(event[0] == "message_begin" for event in protocol.events) == repetitions
    assert sum(event[0] == "message_complete" for event in protocol.events) == repetitions
    assert parser.get_method() == b"GET"


@pytest.mark.parametrize("target_length", [17, 18, 19, 49, 50, 51])
def test_simd_line_scan_boundaries_and_scalar_tails(target_length):
    request_data = (
        b"GET /"
        + b"x" * target_length
        + b" HTTP/1.1\r\nX-Long: "
        + b"y" * target_length
        + b"\r\n\r\n"
    )
    reference, _, _ = parse(httptools.HttpRequestParser, [request_data])
    actual, _, _ = parse(mojo_httptools.HttpRequestParser, [request_data])
    assert actual.events == reference.events


@pytest.mark.parametrize("writable", [True, False])
def test_numpy_buffer_crosses_ffi_without_a_copy(writable):
    payload = np.frombuffer(
        bytearray(b"GET /zero-copy HTTP/1.1\r\n\r\n"), dtype=np.uint8
    )
    payload.flags.writeable = writable
    parser = mojo_httptools.HttpRequestParser(None)
    seen_address = None

    def fake_parse(data_address, length, state_address, events_address, capacity):
        nonlocal seen_address
        seen_address = data_address
        state = (ctypes.c_int64 * 16).from_address(state_address)
        state[9] = length
        state[12] = 0
        return 0

    parser._parse_request = fake_parse
    parser.feed_data(payload)
    assert seen_address == payload.ctypes.data


def test_pipelined_requests_match_upstream():
    data = (
        b"GET /one HTTP/1.1\r\nHost: x\r\n\r\n"
        b"POST /two HTTP/1.1\r\nHost: x\r\nContent-Length: 3\r\n\r\ntwo"
        b"GET /three HTTP/1.0\r\n\r\n"
    )
    reference, reference_parser, _ = parse(httptools.HttpRequestParser, [data])
    actual, actual_parser, _ = parse(mojo_httptools.HttpRequestParser, [data])
    assert actual.events == reference.events
    assert actual_parser.get_method() == reference_parser.get_method() == b"GET"
    assert actual_parser.get_http_version() == "1.0"


def test_upgrade_offset_flags_headers_and_reuse_match_upstream():
    request = (
        b"GET /chat HTTP/1.1\r\nHost: example.com\r\n"
        b"Connection: keep-alive, Upgrade\r\nUpgrade: websocket\r\n\r\n"
        b"\x81\x02hi"
    )
    reference, reference_parser, reference_offset = parse(
        httptools.HttpRequestParser, [request]
    )
    actual, actual_parser, actual_offset = parse(
        mojo_httptools.HttpRequestParser, [request]
    )
    assert actual.events == reference.events
    assert actual_offset == reference_offset
    assert request[actual_offset:] == b"\x81\x02hi"
    assert actual.header_upgrade is reference.header_upgrade is True
    assert actual_parser.should_upgrade() is reference_parser.should_upgrade() is True

    followup = b"GET /again HTTP/1.1\r\nHost: example.com\r\n\r\n"
    actual_parser.feed_data(followup)
    assert actual_parser.get_method() == b"GET"


@pytest.mark.parametrize(
    ("request_data", "expected_at_headers"),
    [
        (b"GET / HTTP/1.1\r\nHost: x\r\n\r\n", True),
        (b"GET / HTTP/1.1\r\nConnection: close\r\n\r\n", False),
        (b"GET / HTTP/1.0\r\n\r\n", False),
        (
            b"GET / HTTP/1.0\r\nConnection: keep-alive\r\n\r\n",
            True,
        ),
    ],
)
def test_keep_alive_decision_matches_upstream(request_data, expected_at_headers):
    reference, reference_parser, _ = parse(
        httptools.HttpRequestParser, [request_data]
    )
    actual, actual_parser, _ = parse(
        mojo_httptools.HttpRequestParser, [request_data]
    )
    assert actual.header_keep_alive == reference.header_keep_alive == expected_at_headers
    assert actual_parser.should_keep_alive() == reference_parser.should_keep_alive()


@pytest.mark.parametrize(
    "value",
    [
        bytearray(b"GET / HTTP/1.1\r\n\r\n"),
        memoryview(b"GET / HTTP/1.1\r\n\r\n"),
        array("B", b"GET / HTTP/1.1\r\n\r\n"),
        np.frombuffer(bytearray(b"GET / HTTP/1.1\r\n\r\n"), dtype=np.uint8),
    ],
)
def test_bytes_like_inputs(value):
    protocol, parser, _ = parse(mojo_httptools.HttpRequestParser, [value])
    assert parser.get_method() == b"GET"
    assert protocol.events[-1] == ("message_complete",)


def test_non_bytes_input_matches_upstream_type_error():
    with pytest.raises(TypeError):
        mojo_httptools.HttpRequestParser(None).feed_data("GET / HTTP/1.1\r\n\r\n")


@pytest.mark.parametrize(
    "value",
    [
        bytearray(),
        array("B"),
        np.array([], dtype=np.uint8),
    ],
)
def test_empty_writable_buffers_are_safe_noops(value):
    parser = mojo_httptools.HttpRequestParser(None)
    parser.feed_data(value)
    assert parser.get_method() == b"DELETE"


@pytest.mark.parametrize(
    "result, consumed, more",
    [
        (8193, 0, 0),
        (0, -1, 0),
        (0, 1000, 0),
        (0, 0, 1),
    ],
)
def test_invalid_native_results_are_not_silently_accepted(
    result, consumed, more
):
    parser = mojo_httptools.HttpRequestParser(None)

    def fake_parse(data_address, length, state_address, events_address, capacity):
        state = (ctypes.c_int64 * 16).from_address(state_address)
        state[9] = consumed
        state[12] = more
        return result

    parser._parse_request = fake_parse
    with pytest.raises(mojo_httptools.HttpParserCallbackError) as caught:
        parser.feed_data(b"GET / HTTP/1.1\r\n\r\n")
    assert isinstance(caught.value.__context__, RuntimeError)


@pytest.mark.parametrize(
    ("request_data", "ours_error", "upstream_error"),
    [
        (
            b"SPAM / HTTP/1.1",
            mojo_httptools.HttpParserInvalidMethodError,
            httptools.HttpParserInvalidMethodError,
        ),
        (
            b"POST HTTP/1.1",
            mojo_httptools.HttpParserInvalidURLError,
            httptools.HttpParserInvalidURLError,
        ),
        (
            b"GET / HTTP/1.1\n\n",
            mojo_httptools.HttpParserError,
            httptools.HttpParserError,
        ),
        (
            b"POST / HTTP/1.1\r\nContent-Length: x\r\n\r\n",
            mojo_httptools.HttpParserError,
            httptools.HttpParserError,
        ),
        (
            (
                b"POST / HTTP/1.1\r\nContent-Length: 1\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            ),
            mojo_httptools.HttpParserError,
            httptools.HttpParserError,
        ),
        (
            (
                b"POST / HTTP/1.1\r\nContent-Length: 1\r\n"
                b"Content-Length: 1\r\n\r\nx"
            ),
            mojo_httptools.HttpParserError,
            httptools.HttpParserError,
        ),
        (
            (
                b"POST / HTTP/1.1\r\n"
                b"Transfer-Encoding: chunked, gzip\r\n\r\n"
            ),
            mojo_httptools.HttpParserError,
            httptools.HttpParserError,
        ),
        (
            (
                b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
                b"1 x\r\na\r\n0\r\n\r\n"
            ),
            mojo_httptools.HttpParserError,
            httptools.HttpParserError,
        ),
    ],
)
def test_error_categories_match_upstream(request_data, ours_error, upstream_error):
    with pytest.raises(upstream_error):
        httptools.HttpRequestParser(None).feed_data(request_data)
    with pytest.raises(ours_error):
        mojo_httptools.HttpRequestParser(None).feed_data(request_data)


@pytest.mark.parametrize(
    "callback",
    [
        "on_message_begin",
        "on_url",
        "on_header",
        "on_headers_complete",
        "on_body",
        "on_message_complete",
    ],
)
def test_callback_errors_preserve_context(callback):
    class CallbackFailure(Exception):
        pass

    class Protocol(Recorder):
        pass

    protocol = Protocol()

    def fail(*args):
        raise CallbackFailure

    setattr(protocol, callback, fail)
    parser = mojo_httptools.HttpRequestParser(protocol)
    protocol.parser = parser
    request = (
        b"POST / HTTP/1.1\r\nHost: x\r\nContent-Length: 1\r\n\r\nx"
    )
    with pytest.raises(mojo_httptools.HttpParserCallbackError) as caught:
        parser.feed_data(request)
    assert isinstance(caught.value.__context__, CallbackFailure)


def test_chunk_callback_errors_preserve_context():
    class CallbackFailure(Exception):
        pass

    class Protocol(Recorder):
        def on_chunk_header(self):
            raise CallbackFailure

    protocol = Protocol()
    parser = mojo_httptools.HttpRequestParser(protocol)
    protocol.parser = parser
    request = (
        b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
        b"1\r\nx\r\n0\r\n\r\n"
    )
    with pytest.raises(mojo_httptools.HttpParserCallbackError) as caught:
        parser.feed_data(request)
    assert isinstance(caught.value.__context__, CallbackFailure)


def test_lowercase_framing_headers_are_recognized():
    protocol, _, _ = parse(
        mojo_httptools.HttpRequestParser,
        [
            (
                b"POST / HTTP/1.1\r\ncontent-length: 4\r\n"
                b"connection: close\r\n\r\ndata"
            )
        ],
    )
    assert ("body", b"data") in protocol.events
    assert protocol.header_keep_alive is False


def test_delete_in_header_value_is_rejected_like_upstream():
    request = b"GET / HTTP/1.1\r\nX-Test: bad\x7fvalue\r\n\r\n"
    with pytest.raises(httptools.HttpParserError):
        httptools.HttpRequestParser(None).feed_data(request)
    with pytest.raises(mojo_httptools.HttpParserError):
        mojo_httptools.HttpRequestParser(None).feed_data(request)


def test_initial_metadata_matches_upstream():
    reference = httptools.HttpRequestParser(None)
    actual = mojo_httptools.HttpRequestParser(None)
    assert actual.get_method() == reference.get_method()
    assert actual.get_http_version() == reference.get_http_version()
    assert actual.should_keep_alive() == reference.should_keep_alive()
    assert actual.should_upgrade() == reference.should_upgrade()


def test_llhttp_http_method_set_matches_upstream():
    methods = (
        "DELETE GET HEAD POST PUT CONNECT OPTIONS TRACE COPY LOCK MKCOL MOVE "
        "PROPFIND PROPPATCH SEARCH UNLOCK BIND REBIND UNBIND ACL REPORT "
        "MKACTIVITY CHECKOUT MERGE M-SEARCH NOTIFY SUBSCRIBE UNSUBSCRIBE PATCH "
        "PURGE MKCALENDAR LINK UNLINK SOURCE QUERY"
    ).split()
    for method in methods:
        request_data = (
            f"{method} / HTTP/1.1\r\nHost: example.com\r\n\r\n".encode()
        )
        reference, reference_parser, _ = parse(
            httptools.HttpRequestParser, [request_data]
        )
        actual, actual_parser, _ = parse(
            mojo_httptools.HttpRequestParser, [request_data]
        )
        assert actual.events == reference.events
        assert actual_parser.get_method() == reference_parser.get_method()


def test_rtsp_and_http2_preface_methods_are_rejected_for_http1():
    methods = (
        "PRI DESCRIBE ANNOUNCE SETUP PLAY PAUSE TEARDOWN GET_PARAMETER "
        "SET_PARAMETER REDIRECT RECORD FLUSH"
    ).split()
    for method in methods:
        request_data = f"{method} / HTTP/1.1\r\nHost: example.com\r\n\r\n".encode()
        with pytest.raises(httptools.HttpParserError):
            httptools.HttpRequestParser(None).feed_data(request_data)
        with pytest.raises(mojo_httptools.HttpParserError):
            mojo_httptools.HttpRequestParser(None).feed_data(request_data)


def test_dangerous_leniency_signature_accepts_all_upstream_keywords():
    parser = mojo_httptools.HttpRequestParser(None)
    parser.set_dangerous_leniencies(
        lenient_headers=False,
        lenient_chunked_length=False,
        lenient_keep_alive=False,
        lenient_transfer_encoding=False,
        lenient_version=False,
        lenient_data_after_close=False,
        lenient_optional_lf_after_cr=False,
        lenient_optional_cr_before_lf=False,
        lenient_optional_crlf_after_chunk=False,
        lenient_spaces_after_chunk_size=False,
    )
