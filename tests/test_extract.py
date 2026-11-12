"""Offline regressions for extraction and its two HTTP trust boundaries."""
from __future__ import annotations

import io
import json
import socket
import traceback
import unittest
import urllib.error
import urllib.request
import urllib.response
from contextlib import ExitStack
from email.message import Message
from unittest.mock import Mock, patch

from causal_ledger import extract


PUBLIC_IP = "93.184.216.34"
BYTE_LIMIT = 2 * 1024 * 1024


def response(body=b"ok", status=200, headers=None, url="https://example.org/"):
    message = Message()
    for key, value in (headers or {"Content-Type": "text/plain"}).items():
        message[key] = value
    result = urllib.response.addinfourl(io.BytesIO(body), message, url, status)
    result.msg = "stub response"
    return result


class FakeSocket:
    def __init__(self, wire):
        self.wire = wire
        self.sent = bytearray()
        self.connected = None
        self.closed = False

    def settimeout(self, timeout):
        self.timeout = timeout

    def setsockopt(self, *args):
        pass

    def connect(self, address):
        self.connected = address

    def sendall(self, data):
        self.sent.extend(data)

    def makefile(self, *args, **kwargs):
        return io.BytesIO(self.wire)

    def close(self):
        self.closed = True


class FetchNetwork:
    """Exercise real HTTP parsing, with DNS, TCP and TLS entirely stubbed."""
    def __init__(self, replies=None, addresses=None):
        self.replies = list(replies or [(200, {"Content-Type": "text/plain"}, b"ok")])
        self.addresses = addresses or [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443))]
        self.sockets = []
        self.tls = Mock()
        self.tls.wrap_socket.side_effect = lambda sock, **kwargs: sock

    def __enter__(self):
        self.stack = ExitStack()
        self.dns = self.stack.enter_context(patch("socket.getaddrinfo", return_value=self.addresses))
        self.socket_factory = self.stack.enter_context(patch("socket.socket", side_effect=self.new_socket))
        self.stack.enter_context(patch("ssl.create_default_context", return_value=self.tls))
        # Keep the vulnerable implementation offline during the required red run.
        self.stack.enter_context(patch("urllib.request.urlopen", side_effect=self.legacy_open))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def next_reply(self):
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]

    def legacy_open(self, request, **kwargs):
        status, headers, body = self.next_reply()
        return response(body, status, headers, request.full_url)

    def new_socket(self, *args, **kwargs):
        status, headers, body = self.next_reply()
        header_text = "".join(f"{key}: {value}\r\n" for key, value in headers.items())
        wire = f"HTTP/1.1 {status} Stub\r\n{header_text}Connection: close\r\n\r\n".encode() + body
        sock = FakeSocket(wire)
        self.sockets.append(sock)
        return sock


class FetchURLTests(unittest.TestCase):
    def test_rejects_unsafe_url_syntax_before_network(self):
        urls = ["file:///etc/passwd", "ftp://example.org/a", "data:text/plain,secret",
                "http://user:password@example.org/", "https://user@example.org/",
                "http://example.org:8080/", "https://example.org:80/",
                "http://example.org:443/", "http://example.org:/", "http:///missing",
                "http://example.org\\@evil.org/", "https://example.org/\r\nX-Test: bad",
                " https://example.org/", "http://[fe80::1%25eth0]/"]
        for url in urls:
            with self.subTest(url=url), FetchNetwork() as net:
                with self.assertRaises(ValueError):
                    extract.fetch_url(url)
                net.dns.assert_not_called()
                net.socket_factory.assert_not_called()

    def test_rejects_nonpublic_literal_addresses(self):
        hosts = ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254",
                 "0.0.0.0", "100.64.0.1", "192.0.2.1", "198.18.0.1", "224.0.0.1", "240.0.0.1",
                 "[::]", "[::1]", "[fc00::1]", "[fec0::1]", "[fe80::1]", "[ff02::1]", "[2001:db8::1]",
                 "[::ffff:127.0.0.1]", "[2002:7f00:1::1]", "[2002:a00:1::1]"]
        for host in hosts:
            with self.subTest(host=host), FetchNetwork() as net:
                with self.assertRaises(ValueError):
                    extract.fetch_url(f"https://{host}/")
                net.socket_factory.assert_not_called()

    def test_rejects_any_nonpublic_dns_answer(self):
        for address in ["127.0.0.1", "10.0.0.1", "169.254.169.254", "192.0.2.1"]:
            answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 443)),
                       (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
            with self.subTest(address=address), FetchNetwork(addresses=answers) as net:
                with self.assertRaises(ValueError):
                    extract.fetch_url("https://example.org/")
                net.socket_factory.assert_not_called()

    def test_rejects_private_ipv6_dns_answer(self):
        answers = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))]
        with FetchNetwork(addresses=answers) as net:
            with self.assertRaises(ValueError):
                extract.fetch_url("https://example.org/")
            net.socket_factory.assert_not_called()

    def test_dns_failure_and_empty_answers_have_clear_errors(self):
        with FetchNetwork() as net:
            net.dns.side_effect = socket.gaierror("resolver details")
            with self.assertRaisesRegex(ValueError, "DNS|解析"):
                extract.fetch_url("https://example.org/")
            net.dns.side_effect = None
            net.dns.return_value = []
            with self.assertRaisesRegex(ValueError, "DNS|解析"):
                extract.fetch_url("https://example.org/")

    def test_pins_validated_dns_address_and_preserves_host_and_tls_name(self):
        with FetchNetwork() as net:
            net.dns.side_effect = [net.addresses, [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]]
            self.assertEqual(extract.fetch_url("https://example.org/path?q=one#ignored", timeout=7), "ok")
            self.assertEqual(net.dns.call_count, 1)
            self.assertEqual(net.sockets[0].connected, (PUBLIC_IP, 443))
            self.assertEqual(net.sockets[0].timeout, 7)
            self.assertIn(b"Host: example.org\r\n", net.sockets[0].sent)
            self.assertIn(b"GET /path?q=one HTTP/1.1", net.sockets[0].sent)
            self.assertNotIn(b"ignored", net.sockets[0].sent)
            self.assertEqual(net.tls.wrap_socket.call_args.kwargs["server_hostname"], "example.org")
            self.assertTrue(net.sockets[0].closed)

    def test_public_ip_literals_do_not_require_dns(self):
        for host, target in [("8.8.8.8", ("8.8.8.8", 80)),
                             ("[2606:4700:4700::1111]", ("2606:4700:4700::1111", 80, 0, 0))]:
            with self.subTest(host=host), FetchNetwork() as net:
                self.assertEqual(extract.fetch_url(f"http://{host}/"), "ok")
                net.dns.assert_not_called()
                self.assertEqual(len(net.sockets), 1)
                self.assertEqual(net.sockets[0].connected, target)
                self.assertIn(f"Host: {host}\r\n".encode(), net.sockets[0].sent)

    def test_validates_every_redirect_target(self):
        for target in ["http://127.0.0.1/", "file:///secret", "https://user:secret@example.org/",
                       "https://example.org:444/", "http://[::1]/"]:
            replies = [(302, {"Location": target}, b"")]
            with self.subTest(target=target), FetchNetwork(replies) as net:
                with self.assertRaises(ValueError):
                    extract.fetch_url("https://example.org/")
                self.assertEqual(len(net.sockets), 1)
                self.assertTrue(net.sockets[0].closed)

    def test_redirect_rechecks_dns_even_for_same_hostname(self):
        with FetchNetwork([(302, {"Location": "/next"}, b"")]) as net:
            net.dns.side_effect = [net.addresses, [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]]
            with self.assertRaises(ValueError):
                extract.fetch_url("https://example.org/")
            self.assertEqual(len(net.sockets), 1)

    def test_follows_relative_redirect_and_strips_html(self):
        replies = [(301, {"Location": "/article"}, b""),
                   (200, {"Content-Type": "text/html; charset=utf-8"},
                    b"<p>Hello &amp; world</p><script>bad()</script><style>bad</style><noscript>bad</noscript>")]
        with FetchNetwork(replies) as net:
            self.assertEqual(extract.fetch_url("https://example.org/start"), "Hello & world")
            self.assertEqual(len(net.sockets), 2)
            self.assertIn(b"GET /article HTTP/1.1", net.sockets[1].sent)

    def test_redirects_are_bounded(self):
        with FetchNetwork([(302, {"Location": "/loop"}, b"")]) as net:
            with self.assertRaisesRegex(ValueError, "redirect|重定向"):
                extract.fetch_url("https://example.org/")
            self.assertLessEqual(len(net.sockets), 6)

    def test_rejects_redirect_without_location_and_http_errors(self):
        for status in [302, 404, 500]:
            with self.subTest(status=status), FetchNetwork([(status, {}, b"private server details")]):
                with self.assertRaises(ValueError) as caught:
                    extract.fetch_url("https://example.org/")
                self.assertNotIn("private server details", str(caught.exception))

    def test_rejects_non_document_media_types(self):
        for media in ["application/octet-stream", "application/json", "image/png", "", "text/event-stream"]:
            with self.subTest(media=media), FetchNetwork([(200, {"Content-Type": media}, b"secret")]):
                with self.assertRaisesRegex(ValueError, "media|Content-Type|类型"):
                    extract.fetch_url("https://example.org/")

    def test_bounds_declared_and_actual_response_size(self):
        for headers, body in [({"Content-Type": "text/plain", "Content-Length": str(BYTE_LIMIT + 1)}, b"small"),
                              ({"Content-Type": "text/plain"}, b"x" * (BYTE_LIMIT + 1))]:
            with self.subTest(headers=headers), FetchNetwork([(200, headers, body)]):
                with self.assertRaisesRegex(ValueError, "large|limit|大小|上限"):
                    extract.fetch_url("https://example.org/")

    def test_bounds_chunked_response_without_content_length(self):
        body = b"x" * (BYTE_LIMIT + 1)
        chunked = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        headers = {"Content-Type": "text/plain", "Transfer-Encoding": "chunked"}
        with FetchNetwork([(200, headers, chunked)]):
            with self.assertRaisesRegex(ValueError, "large|limit|大小|上限"):
                extract.fetch_url("https://example.org/")

    def test_accepts_exact_byte_limit(self):
        with FetchNetwork([(200, {"Content-Type": "text/plain"}, b"x" * BYTE_LIMIT)]):
            self.assertEqual(len(extract.fetch_url("https://example.org/")), BYTE_LIMIT)

    def test_rejects_compression_and_unknown_charset(self):
        for headers in [{"Content-Type": "text/plain", "Content-Encoding": "gzip"},
                        {"Content-Type": "text/plain; charset=not-a-charset"}]:
            with self.subTest(headers=headers), FetchNetwork([(200, headers, b"hello")]):
                with self.assertRaisesRegex(ValueError, "encoding|charset|编码"):
                    extract.fetch_url("https://example.org/")


class FetchFeedTests(unittest.TestCase):
    def test_feed_keeps_xml_and_redirect_base_url(self):
        body = b'<?xml version="1.0"?><rss version="2.0"><channel><title>Demo</title></channel></rss>'
        replies = [(302, {"Location": "/feeds/latest.xml"}, b""),
                   (200, {"Content-Type": "application/rss+xml"}, body)]
        with FetchNetwork(replies):
            raw, final_url = extract.fetch_feed("https://example.org/start")
        self.assertEqual(raw, body)
        self.assertEqual(final_url, "https://example.org/feeds/latest.xml")

    def test_feed_rejects_html_and_private_redirects(self):
        for replies in [[(200, {"Content-Type": "text/html"}, b"<html>blocked</html>")],
                        [(302, {"Location": "http://127.0.0.1/"}, b"")]]:
            with FetchNetwork(replies), self.assertRaises(ValueError):
                extract.fetch_feed("https://example.org/feed")

    def test_feed_accepts_generic_media_for_xml_parser_but_document_fetch_does_not(self):
        raw = b'<rss version="2.0"><channel><title>Demo</title></channel></rss>'
        replies = [(200, {"Content-Type": "application/octet-stream"}, raw)]
        with FetchNetwork(replies):
            body, _ = extract.fetch_feed("https://example.org/feed")
        self.assertEqual(raw, body)
        with FetchNetwork(replies), self.assertRaises(ValueError):
            extract.fetch_url("https://example.org/feed")

    def test_feed_preserves_non_utf8_xml_bytes(self):
        raw = '<?xml version="1.0" encoding="utf-16"?><feed xmlns="http://www.w3.org/2005/Atom"><title>虚构</title></feed>'.encode("utf-16")
        with FetchNetwork([(200, {"Content-Type": "application/atom+xml"}, raw)]):
            body, _ = extract.fetch_feed("https://example.org/feed")
        self.assertEqual(raw, body)


class JSONAndExtractionTests(unittest.TestCase):
    def test_parses_whole_json_object_and_fences(self):
        value = {"claims": [{"text": "braces { and }", "nested": {"ok": True}}]}
        raw = json.dumps(value)
        for text in [raw, " \n" + raw + "\n", "```json\n" + raw + "\n```", "```\n" + raw + "\n```",
                     "```JSON\n" + raw + "\n```"]:
            with self.subTest(text=text):
                self.assertEqual(extract.parse_json_block(text), value)

    def test_never_accepts_nested_object_from_top_level_array_or_scalar(self):
        for raw in ['[{"claims": []}]', '"{\\"claims\\": []}"', "null", "42", "[]"]:
            for text in [raw, "```json\n" + raw + "\n```"]:
                with self.subTest(text=text), self.assertRaisesRegex(ValueError, "JSON.*object|JSON.*对象"):
                    extract.parse_json_block(text)

    def test_malformed_json_errors_do_not_echo_model_content(self):
        for text in ["SECRET_DOCUMENT", '{"private": SECRET_DOCUMENT}',
                     'preface {"private": "SECRET_DOCUMENT"}', '{"private": "SECRET_DOCUMENT"} trailing',
                     '{"n": NaN}', '{"n": Infinity}', None, {"claims": []}]:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "JSON") as caught:
                extract.parse_json_block(text)
            self.assertNotIn("SECRET_DOCUMENT", str(caught.exception))

    def test_rejects_empty_input_without_llm_call(self):
        for content in ["", " \n\t"]:
            with self.subTest(content=content), patch.object(extract, "chat_json") as chat:
                with self.assertRaisesRegex(ValueError, "empty|为空"):
                    extract.extract_claims(content)
                chat.assert_not_called()

    def test_rejects_over_limit_instead_of_truncating(self):
        for content in ["x" * 12001, "x" * 12000 + " "]:
            with self.subTest(length=len(content)), patch.object(extract, "chat_json") as chat:
                with self.assertRaisesRegex(ValueError, "12000"):
                    extract.extract_claims(content)
                chat.assert_not_called()

    def test_accepts_exact_input_limit_and_preserves_reporter_override(self):
        reporter = {"name": "Known source", "type": "official"}
        with patch.object(extract, "chat_json", return_value={"claims": [], "reporter": {"name": "model"}}) as chat:
            result = extract.extract_claims("x" * 12000, today="2026-01-02", reporter=reporter)
        self.assertIn("x" * 12000, chat.call_args.args[1])
        self.assertIn("2026-01-02", chat.call_args.args[1])
        self.assertEqual(result["reporter"], reporter)

    def test_null_reporter_can_be_overridden_without_type(self):
        with patch.object(extract, "chat_json", return_value={"claims": [], "reporter": None}):
            result = extract.extract_claims("synthetic", reporter={"name": "Known"})
        self.assertEqual(result["reporter"], {"name": "Known", "type": "unknown"})

    def test_prompt_marks_document_as_untrusted_and_ignores_its_instructions(self):
        self.assertIn("不可信", extract.SYSTEM_PROMPT)
        self.assertIn("指令", extract.SYSTEM_PROMPT)
        self.assertRegex(extract.SYSTEM_PROMPT, "不得|不要|不遵循|不执行")

    def test_prompt_marks_signals_pending_not_evidence(self):
        prompt = extract.USER_TEMPLATE
        self.assertIn("PENDING", prompt)
        self.assertIn("不是已验证证据", prompt)
        self.assertNotIn("不依赖任何人说法", prompt)

    def test_prompt_only_extracts_quoted_explicit_future_commitments(self):
        prompt = extract.USER_TEMPLATE
        self.assertNotIn("如果这条主张为真", prompt)
        self.assertNotIn("相对今天合理", prompt)
        for required in ["明确", "未来", "承诺", "逐字引用", "截止", "不得编造", "空数组"]:
            self.assertIn(required, prompt)
        self.assertIn("主体|谓词|客体|时间范围", prompt)
        self.assertNotRegex(prompt, r'"p"\s*:')


class APINetwork:
    """Run urllib's actual redirect handlers over an in-memory HTTP transport."""
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []

    def __enter__(self):
        self.stack = ExitStack()
        network = self

        class HTTPSStub(urllib.request.HTTPSHandler):
            def https_open(self, request):
                network.requests.append(request)
                status, headers, body = network.replies.pop(0)
                return response(body, status, headers, request.full_url)

        class HTTPStub(urllib.request.HTTPHandler):
            http_open = HTTPSStub.https_open

        class FileStub(urllib.request.FileHandler):
            file_open = HTTPSStub.https_open

        real_build = urllib.request.build_opener
        self.stack.enter_context(patch("urllib.request.build_opener", side_effect=lambda *handlers: real_build(*handlers, HTTPSStub(), HTTPStub(), FileStub())))
        legacy_opener = real_build(HTTPSStub(), HTTPStub(), FileStub())
        self.stack.enter_context(patch("urllib.request.urlopen", side_effect=legacy_opener.open))
        self.stack.enter_context(patch("socket.getaddrinfo", side_effect=AssertionError("Live DNS forbidden")))
        self.stack.enter_context(patch("socket.socket", side_effect=AssertionError("Live sockets forbidden")))
        self.stack.enter_context(patch.dict("os.environ", {"CL_LLM_API_KEY": "SECRET_KEY", "CL_LLM_BASE_URL": "https://api.example.org/v1"}, clear=True))
        return self

    def __exit__(self, *args):
        return self.stack.__exit__(*args)


def completion(content='{"claims": []}'):
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode()


class ChatJSONTests(unittest.TestCase):
    def test_success_uses_environment_credentials_and_compatible_request(self):
        with APINetwork([(200, {"Content-Type": "application/json"}, completion())]) as net:
            self.assertEqual(extract.chat_json("system", "document", timeout=9), {"claims": []})
            request = net.requests[0]
            self.assertEqual(request.full_url, "https://api.example.org/v1/chat/completions")
            self.assertEqual(request.get_header("Authorization"), "Bearer SECRET_KEY")
            body = json.loads(request.data)
            self.assertEqual(body["model"], "gpt-4o-mini")
            self.assertEqual(body["messages"], [{"role": "system", "content": "system"}, {"role": "user", "content": "document"}])
            self.assertNotIn("SECRET_KEY", request.data.decode())

    def test_requires_https_and_rejects_url_credentials(self):
        for base in ["http://api.example.org/v1", "file:///secret", "https://user:password@api.example.org/v1"]:
            with self.subTest(base=base), APINetwork([(200, {}, completion())]) as net:
                with patch.dict("os.environ", {"CL_LLM_BASE_URL": base}):
                    with self.assertRaisesRegex(ValueError, "HTTPS|credentials|凭据"):
                        extract.chat_json("system", "document")
                self.assertEqual(net.requests, [])

    def test_refuses_api_redirects_without_resending_bearer_key(self):
        for target in ["https://attacker.example/steal", "http://api.example.org/insecure", "/same-origin"]:
            for code in [301, 302, 303, 307, 308]:
                replies = [(code, {"Location": target}, b"SECRET_KEY"), (200, {}, completion())]
                with self.subTest(target=target, code=code), APINetwork(replies) as net:
                    with self.assertRaisesRegex(RuntimeError, f"HTTP {code}"):
                        extract.chat_json("system", "document")
                    self.assertEqual(len(net.requests), 1)

    def test_http_error_does_not_expose_response_body_or_key_in_traceback(self):
        with APINetwork([(401, {"Content-Type": "text/plain"}, b"SECRET_KEY SECRET_DOCUMENT")]):
            try:
                extract.chat_json("system", "SECRET_DOCUMENT")
            except RuntimeError as error:
                rendered = "".join(traceback.format_exception(error))
                self.assertIn("HTTP 401", rendered)
                self.assertNotIn("SECRET_KEY", str(error))
                self.assertNotIn("SECRET_DOCUMENT", str(error))
                self.assertNotIn("LLM HTTP 401: SECRET", rendered)
            else:
                self.fail("HTTP error must be reported")

    def test_free_tier_quota_error_has_safe_actionable_message(self):
        body = json.dumps({"error": {"code": "AllocationQuota.FreeTierOnly",
                                     "message": "SECRET_KEY SECRET_DOCUMENT"}}).encode()
        with APINetwork([(403, {"Content-Type": "application/json"}, body)]):
            with self.assertRaises(RuntimeError) as caught:
                extract.chat_json("system", "document")
        message = str(caught.exception)
        self.assertIn("AllocationQuota.FreeTierOnly", message)
        self.assertIn("免费额度", message)
        self.assertIn("不会自动", message)
        self.assertNotIn("SECRET_KEY", message)
        self.assertNotIn("SECRET_DOCUMENT", message)

    def test_unknown_provider_error_does_not_echo_untrusted_code(self):
        for body in (b'[]', b'{"error": null}', b'{"error":{"code":"SECRET_KEY","message":"SECRET_DOCUMENT"}}'):
            with APINetwork([(403, {"Content-Type": "application/json"}, body)]):
                with self.assertRaisesRegex(RuntimeError, "LLM HTTP 403") as caught:
                    extract.chat_json("system", "document")
                self.assertNotIn("SECRET", str(caught.exception))

    def test_malformed_api_envelopes_raise_sanitized_meaningful_errors(self):
        bodies = [b"SECRET_DOCUMENT", b"\xff", b"[]", b"null", b"{}", b'{"choices": []}',
                  b'{"choices": {}}', b'{"choices": [null]}', b'{"choices": [{"message": null}]}',
                  b'{"choices": [{"message": {"content": []}}]}', completion(""),
                  completion("SECRET_DOCUMENT"), completion('[{"claims": []}]')]
        for body in bodies:
            with self.subTest(body=body), APINetwork([(200, {"Content-Type": "application/json"}, body)]):
                with self.assertRaisesRegex(RuntimeError, "LLM.*(response|响应|JSON)") as caught:
                    extract.chat_json("system", "document")
                self.assertNotIn("SECRET_DOCUMENT", str(caught.exception))
                self.assertNotIn("SECRET_KEY", str(caught.exception))

    def test_api_response_bytes_are_bounded(self):
        body = completion('{"text": "' + "x" * BYTE_LIMIT + '"}')
        with APINetwork([(200, {"Content-Type": "application/json"}, body)]):
            with self.assertRaisesRegex(RuntimeError, "large|limit|大小|上限"):
                extract.chat_json("system", "document")

    def test_invalid_api_key_header_is_rejected_without_echoing_key(self):
        with APINetwork([(200, {}, completion())]) as net:
            with patch.dict("os.environ", {"CL_LLM_API_KEY": "SECRET_KEY\nInjected: value"}):
                with self.assertRaises(ValueError) as caught:
                    extract.chat_json("system", "document")
            self.assertNotIn("SECRET_KEY", str(caught.exception))
            self.assertEqual(net.requests, [])

    def test_network_errors_do_not_echo_sensitive_details(self):
        with APINetwork([]):
            opener = Mock()
            opener.open.side_effect = urllib.error.URLError("SECRET_KEY SECRET_DOCUMENT")
            with patch("urllib.request.build_opener", return_value=opener), patch("urllib.request.urlopen", side_effect=opener.open.side_effect):
                with self.assertRaisesRegex(RuntimeError, "LLM") as caught:
                    extract.chat_json("system", "document")
            self.assertNotIn("SECRET_KEY", str(caught.exception))
            self.assertNotIn("SECRET_DOCUMENT", str(caught.exception))

    def test_llm_config_environment_priority_is_preserved(self):
        cases = [({"CL_LLM_API_KEY": "first", "DASHSCOPE_API_KEY": "second", "OPENAI_API_KEY": "third"},
                  (extract.OPENAI_BASE, "first", "gpt-4o-mini")),
                 ({"DASHSCOPE_API_KEY": "second"}, (extract.DASHSCOPE_BASE, "second", "qwen-plus")),
                 ({"OPENAI_API_KEY": "third", "OPENAI_BASE_URL": "https://custom.example/v1", "CL_LLM_MODEL": "custom"},
                  ("https://custom.example/v1", "third", "custom"))]
        for env, expected in cases:
            with self.subTest(env=env), patch.dict("os.environ", env, clear=True):
                cfg = extract.llm_config()
                self.assertEqual((cfg["base_url"], cfg["api_key"], cfg["model"]), expected)
        with patch.dict("os.environ", {}, clear=True), self.assertRaises(RuntimeError):
            extract.llm_config()


if __name__ == "__main__":
    unittest.main()
