from __future__ import annotations

import math
import os
import platform
import sys
import time


sys.path.insert(
    0,
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"
    ),
)

import httptools  # noqa: E402
import mojo_httptools  # noqa: E402


def timeit(function, repeat=5):
    function()
    best = math.inf
    for _ in range(repeat):
        start = time.perf_counter()
        function()
        best = min(best, time.perf_counter() - start)
    return best


class Sink:
    def __init__(self):
        self.total = 0

    def on_message_begin(self):
        self.total += 1

    def on_url(self, value):
        self.total += len(value)

    def on_header(self, name, value):
        self.total += len(name) + len(value)

    def on_headers_complete(self):
        self.total += 1

    def on_body(self, value):
        self.total += len(value)

    def on_message_complete(self):
        self.total += 1

    def on_chunk_header(self):
        self.total += 1

    def on_chunk_complete(self):
        self.total += 1


def parse_once(parser_class, data):
    parser_class(Sink()).feed_data(data)


def fragmented(parser_class, request, repetitions):
    parser = parser_class(Sink())
    for _ in range(repetitions):
        parser.feed_data(request)


def cpu_name():
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as file:
            for line in file:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown CPU"


def main():
    get = (
        b"GET /plaintext HTTP/1.1\r\nHost: localhost\r\n"
        b"User-Agent: mojo-httptools-bench\r\n\r\n"
    )
    post = (
        b"POST /ingest HTTP/1.1\r\nHost: localhost\r\n"
        b"Content-Type: application/octet-stream\r\nContent-Length: 128\r\n\r\n"
        + b"x" * 128
    )
    large_body = b"x" * (16 * 1024 * 1024)
    large_post = (
        b"POST /blob HTTP/1.1\r\nHost: localhost\r\nContent-Length: "
        + str(len(large_body)).encode()
        + b"\r\n\r\n"
        + large_body
    )
    chunk = b"1000\r\n" + b"x" * 4096 + b"\r\n"
    chunked = (
        b"POST /chunks HTTP/1.1\r\nHost: localhost\r\n"
        b"Transfer-Encoding: chunked\r\n\r\n"
        + chunk * 2048
        + b"0\r\n\r\n"
    )

    cases = [
        ("100k pipelined GET requests", lambda cls: parse_once(cls, get * 100_000)),
        ("50k pipelined POST requests", lambda cls: parse_once(cls, post * 50_000)),
        ("single POST, 16 MiB body", lambda cls: parse_once(cls, large_post)),
        ("chunked POST, 8 MiB / 2048 chunks", lambda cls: parse_once(cls, chunked)),
        (
            "20k separate feed_data calls",
            lambda cls: fragmented(cls, get, 20_000),
        ),
    ]

    print(f"Machine: {cpu_name()} ({platform.system()} {platform.machine()})")
    print()
    print("| workload | mojo-httptools | httptools 0.8 | speedup |")
    print("|---|---:|---:|---:|")
    for name, workload in cases:
        mojo_seconds = timeit(
            lambda workload=workload: workload(mojo_httptools.HttpRequestParser),
            repeat=3,
        )
        upstream_seconds = timeit(
            lambda workload=workload: workload(httptools.HttpRequestParser),
            repeat=3,
        )
        print(
            f"| {name} | {mojo_seconds * 1e3:.2f} ms | "
            f"{upstream_seconds * 1e3:.2f} ms | "
            f"{upstream_seconds / mojo_seconds:.2f}x |"
        )


if __name__ == "__main__":
    main()
