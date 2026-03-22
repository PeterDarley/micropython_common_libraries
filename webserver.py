"""
Minimal HTTP server for MicroPython (ESP32).

Place this file in `lib/` and import from your `main.py` or REPL.

Usage (blocking):
    from webserver import WebServer
    srv = WebServer(port=80)
    srv.start()

Usage (background thread):
    srv.start_in_thread()

Serving behavior:
- If `www/index.html` exists on the device, it will be served for `GET /`.
- Otherwise a simple HTML page is returned.
"""

import socket
import os
import gc

try:
    import _thread

    _THREAD = True
except Exception:
    _THREAD = False


def _parse_form_data(body_string):
    """Parse a URL-encoded form body into a dict of key->value pairs.

    Handles ``application/x-www-form-urlencoded`` format as sent by HTML
    forms and HTMX.
    """

    result = {}
    if not body_string:
        return result

    for pair in body_string.split("&"):
        if "=" in pair:
            key, value = pair.split("=", 1)
            result[_url_decode(key)] = _url_decode(value)
        elif pair:
            result[_url_decode(pair)] = ""

    return result


def _url_decode(encoded_string):
    """Decode a URL-encoded string (replaces %XX sequences and + with space)."""

    decoded = encoded_string.replace("+", " ")
    result = ""
    index = 0

    while index < len(decoded):
        if decoded[index] == "%" and index + 2 < len(decoded):
            try:
                result += chr(int(decoded[index + 1 : index + 3], 16))
                index += 3
            except ValueError:
                result += decoded[index]
                index += 1
        else:
            result += decoded[index]
            index += 1

    return result


def render_template(template_file, context=None, templates_dir="templates"):
    """Load a template file and substitute ``{{ key }}`` placeholders.

    Also supports ``{% include 'filename' %}`` to include other templates
    with the same context.

    Args:
        template_file: Filename inside *templates_dir* (e.g. ``'index.html'``).
        context: Dict of values to substitute. Keys missing from the template
                 are ignored; placeholders with no matching key become ``''``.
        templates_dir: Directory to load templates from (default ``'templates'``).

    Returns:
        The rendered string, or ``None`` if the file could not be read.
    """

    if context is None:
        context = {}

    tpl_path = "/".join((templates_dir.rstrip("/"), template_file))
    try:
        with open(tpl_path, "rb") as f:
            data = f.read()
    except Exception:
        return None

    text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)

    # Replace known context keys (handle common spacing variants).
    for key, val in context.items():
        sval = "" if val is None else str(val)
        text = text.replace("{{ " + key + " }}", sval)
        text = text.replace("{{" + key + "}}", sval)
        text = text.replace("{{ " + key + "}}", sval)
        text = text.replace("{{" + key + " }}", sval)

    # Handle template includes: {% include 'filename' %}
    while "{%" in text:
        start = text.find("{%")
        end = text.find("%}", start)
        if end == -1:
            break

        tag_content = text[start + 2 : end].strip()
        if tag_content.startswith("include"):
            # Extract the filename from: include 'filename' or include "filename" or include filename
            include_part = tag_content[7:].strip()
            include_filename = None

            if include_part.startswith('"') and '"' in include_part[1:]:
                include_filename = include_part[1 : include_part.index('"', 1)]
            elif include_part.startswith("'") and "'" in include_part[1:]:
                include_filename = include_part[1 : include_part.index("'", 1)]
            else:
                include_filename = include_part.split()[0] if include_part else None

            if include_filename:
                included_content = render_template(include_filename, context, templates_dir)
                if included_content is not None:
                    text = text[:start] + included_content + text[end + 2 :]
                    continue

        # If we get here, skip this tag (malformed include or other tag)
        text = text[:start] + text[end + 2 :]

    # Erase any remaining {{ ... }} placeholders not present in context.
    while "{{" in text:
        start = text.index("{{")
        end = text.find("}}", start)
        if end == -1:
            break
        text = text[:start] + text[end + 2 :]

    return text


class WebServer:
    _instance = None

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, host="0.0.0.0", port=80, www_dir="www", debug=False):
        if getattr(self, "_initialised", False):
            return
        self._initialised = True
        self.host = host
        self.port = port
        self.www_dir = www_dir
        self._sock = None
        self._running = False
        self.debug = debug
        # route table: map URL path -> view function
        # view signature: func(request) -> bytes|str|(bytes, content_type)|(str, content_type)
        self.routes = {}
        # lock for thread-safe route access if _thread is available
        self._routes_lock = _thread.allocate_lock() if _THREAD else None

    def _log(self, *args):
        """Print a debug message if debug mode is enabled."""

        if self.debug:
            try:
                print(*args)
            except Exception:
                pass

    def _get_route(self, path):
        """Thread-safe route lookup by path.

        Matches routes with or without trailing slash (e.g., "/route" matches "/route/").
        """

        if self._routes_lock:
            with self._routes_lock:
                # Try exact match first
                result = self.routes.get(path)
                if result:
                    return result

                # Try with/without trailing slash
                if path != "/":
                    if path.endswith("/"):
                        result = self.routes.get(path.rstrip("/"))
                    else:
                        result = self.routes.get(path + "/")
                    return result
                return None

        # Try exact match first
        result = self.routes.get(path)
        if result:
            return result

        # Try with/without trailing slash
        if path != "/":
            if path.endswith("/"):
                result = self.routes.get(path.rstrip("/"))
            else:
                result = self.routes.get(path + "/")
            return result
        return None

    def add_route(self, url, view_func):
        """Register a view function for a specific URL path."""

        if self._routes_lock:
            with self._routes_lock:
                self.routes[url] = view_func
        else:
            self.routes[url] = view_func

    def add_routes(self, routes):
        """Register multiple routes at once.

        Args:
            routes: A dict mapping URL paths to view functions,
                   or a list of (url, view_func) tuples.
        """

        items = routes.items() if isinstance(routes, dict) else routes
        if self._routes_lock:
            with self._routes_lock:
                for url, view_func in items:
                    self.routes[url] = view_func
        else:
            for url, view_func in items:
                self.routes[url] = view_func

    def route(self, url):
        """Decorator variant for registering a route."""

        def _decorator(fn):
            self.add_route(url, fn)
            return fn

        return _decorator

    def _handle_client(self, cl_sock):
        """Read one HTTP request from *cl_sock* and send a response."""

        try:
            try:
                self._log("websrv: accepted connection from", cl_sock.getpeername())
            except Exception:
                pass
            request = cl_sock.recv(2048)
            if not request:
                return

            # parse request line
            first_line = request.split(b"\r\n", 1)[0]
            self._log("websrv: request:", first_line)
            parts = first_line.split()
            if len(parts) < 2:
                return
            method = parts[0].decode()
            raw_path = parts[1].decode()

            # split query string if present
            if "?" in raw_path:
                path, query = raw_path.split("?", 1)
            else:
                path, query = raw_path, ""

            # parse body and form data from any request
            raw_body = b""
            form_data = {}
            header_end = request.find(b"\r\n\r\n")
            if header_end != -1:
                raw_body = request[header_end + 4 :]
                if raw_body:
                    form_data = _parse_form_data(raw_body.decode("utf-8", "replace"))

            # Routes take precedence
            self._log("websrv: checking route for path:", path, "available routes:", list(self.routes.keys()))
            route_handler = self._get_route(path)

            if route_handler:
                self._log("websrv: routing to", path)
                try:
                    request = Request(
                        method=method,
                        path=path,
                        query=query,
                        raw_path=raw_path,
                        headers={},
                        body=raw_body,
                        form_data=form_data,
                    )
                    if isinstance(route_handler, type) and issubclass(route_handler, View):
                        response = route_handler().dispatch(request)
                    elif isinstance(route_handler, View):
                        response = route_handler.dispatch(request)
                    else:
                        response = route_handler(request)

                    return self._send_result(cl_sock, response)
                except Exception as e:
                    self._log("websrv: route handler exception:", e)
                    return self._send_response(cl_sock, 500, "Internal Server Error", b"", "text/plain")

            # POST to an unmatched route is a 404
            if method == "POST":
                return self._send_response(cl_sock, 404, "Not Found", b"Not Found", "text/plain")

            # No route matched — fall back to static file handling
            if path == "/" or path == "/index.html":
                content, content_type = self._load_index()
                if content is None:
                    self._log("websrv: no index found, returning default page")
                    content = b"<html><body><h1>OK</h1></body></html>"
                    content_type = "text/html"
            else:
                safe_path = path.lstrip("/")
                file_path = "/".join((self.www_dir, safe_path))
                if self._file_exists(file_path):
                    self._log("websrv: serving file", file_path)
                    content = self._readfile(file_path)
                    content_type = self._guess_mime(file_path)
                else:
                    self._log("websrv: file not found", file_path)
                    return self._send_response(cl_sock, 404, "Not Found", b"Not Found", "text/plain")

            return self._send_response(cl_sock, 200, "OK", content, content_type)
        except Exception as e:
            self._log("websrv: handler exception:", e)
            try:
                self._send_response(cl_sock, 500, "Internal Server Error", b"", "text/plain")
            except Exception:
                pass
        finally:
            try:
                cl_sock.close()
            except Exception:
                pass
            gc.collect()

    def _file_exists(self, path):
        """Return True if *path* exists on the filesystem."""

        try:
            os.stat(path)
            return True
        except Exception:
            return False

    def _readfile(self, path):
        """Return contents of *path* as bytes, or None on error."""

        try:
            self._log("websrv: reading file", path)
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            return None

    def _load_index(self):
        """Try to load www/index.html; return (bytes, mime) or (None, None)."""

        idx = self.www_dir + "/index.html"
        if self._file_exists(idx):
            self._log("websrv: found index at", idx)
            return self._readfile(idx), "text/html"
        return None, None

    _MIME = {
        ".html": "text/html",
        ".css": "text/css",
        ".js": "application/javascript",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
    }

    def _guess_mime(self, filename):
        """Return a MIME type for *filename* based on its extension."""

        for ext, mime in self._MIME.items():
            if filename.endswith(ext):
                return mime
        return "application/octet-stream"

    def _send_response(self, cl_sock, status_code, reason, content, content_type="text/html"):
        """Send an HTTP response to the client socket."""

        try:
            if isinstance(content, str):
                body_bytes = content.encode("utf-8")
            else:
                body_bytes = content or b""
            header = "HTTP/1.0 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\n\r\n".format(
                status_code, reason, content_type, len(body_bytes)
            )
            cl_sock.send(header.encode("utf-8"))
            if body_bytes:
                cl_sock.send(body_bytes)
        except Exception:
            pass

    def _send_result(self, cl_sock, res):
        """Normalise a route handler return value and send it as a response."""

        if res is None:
            return self._send_response(cl_sock, 204, "No Content", b"", "text/plain")
        if isinstance(res, Response):
            return self._send_response(cl_sock, res.status, res.reason, res.to_bytes(), res.content_type)
        if isinstance(res, tuple) and len(res) == 2:
            return self._send_response(cl_sock, 200, "OK", res[0], res[1])
        if isinstance(res, bytes):
            return self._send_response(cl_sock, 200, "OK", res, "application/octet-stream")
        if isinstance(res, str):
            return self._send_response(cl_sock, 200, "OK", res, "text/html")
        return self._send_response(cl_sock, 500, "Internal Server Error", b"", "text/plain")

    def start(self):
        """Start the server (blocking)."""

        if self._running:
            self._log("websrv: already running")
            return
        server_address = socket.getaddrinfo(self.host, self.port)[0][-1]
        # Retry socket creation — after a soft reset lingering FDs may briefly
        # exhaust the table before lwIP releases them.
        for creation_attempt in range(10):
            try:
                server_socket = socket.socket()
                break
            except OSError as creation_error:
                if creation_error.args[0] == 23 and creation_attempt < 9:  # ENFILE
                    import time

                    gc.collect()
                    time.sleep_ms(200)
                else:
                    raise
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Retry bind — after a soft reset the old socket may linger briefly.
        for attempt in range(5):
            try:
                server_socket.bind(server_address)
                break
            except OSError:
                if attempt == 4:
                    server_socket.close()
                    raise
                import time

                time.sleep_ms(500)
        server_socket.listen(1)
        self._sock = server_socket
        self._running = True
        self._log("WebServer listening on", server_address)
        try:
            while self._running:
                try:
                    client_socket, remote_address = server_socket.accept()
                except OSError as accept_error:
                    if accept_error.args[0] == 23:  # ENFILE: FD table full, back off
                        import time

                        gc.collect()
                        time.sleep_ms(100)
                        continue
                    raise
                if _THREAD:
                    try:
                        self._log("websrv: spawning thread for client", remote_address)
                        _thread.start_new_thread(self._handle_client, (client_socket,))
                    except Exception as thread_error:
                        self._log("websrv: failed to spawn thread, handling inline", thread_error)
                        self._handle_client(client_socket)
                else:
                    self._log("websrv: handling client inline", remote_address)
                    self._handle_client(client_socket)
        finally:
            try:
                server_socket.close()
            except Exception:
                pass
            self._running = False

    def start_in_thread(self):
        """Start the server in a background thread."""

        if not _THREAD:
            raise RuntimeError("threading not available on this build")
        if self._running:
            self._log("websrv: already running in thread")
            return
        self._log("websrv: starting server in background thread")
        _thread.start_new_thread(self.start, ())

    def stop(self):
        """Stop the server."""

        self._running = False
        try:
            if self._sock:
                self._sock.close()
        except Exception:
            pass
        self._sock = None
        self._log("websrv: stop requested")


class Request:
    """Represents an HTTP request.

    Attributes:
        method: HTTP method as string (e.g., 'GET').
        path: The request path without query string (e.g., '/status').
        query: The raw query string (e.g., 'a=1&b=2').
        raw_path: The original requested path including query if any.
        headers: dict of parsed headers (may be empty).
        body: raw request body bytes or None.
    """

    def __init__(self, method, path, query="", raw_path=None, headers=None, body=None, form_data=None):
        """Create a Request object.

        The server currently provides minimal parsing: method, path, query,
        body, and form_data (for POST requests).
        """
        self.method = method
        self.path = path
        self.query = query
        self.raw_path = raw_path
        self.headers = headers or {}
        self.body = body
        self.form_data = form_data or {}


class Response:
    """Represents an HTTP response.

    Attributes:
        status: HTTP status code (int).
        reason: Short reason phrase (str).
        body: Response body, either `bytes` or `str`.
        content_type: MIME type string.
        headers: Optional dict of extra headers.
    """

    def __init__(self, status=200, reason="OK", body=b"", content_type="text/html", headers=None):
        self.status = status
        self.reason = reason
        self.body = body
        self.content_type = content_type
        self.headers = headers or {}

    def to_bytes(self):
        """Return the body as bytes."""
        if isinstance(self.body, bytes):
            return self.body
        if isinstance(self.body, str):
            return self.body.encode("utf-8")
        return str(self.body).encode("utf-8")

    def __str__(self):
        try:
            if isinstance(self.body, bytes):
                return self.body.decode("utf-8", "replace")
            return str(self.body)
        except Exception:
            return repr(self.body)


class View:
    """Base class for a web server view."""

    def dispatch(self, request):
        """Handle a request for this view.

        Args:
            request (Request): The HTTP request to handle.

        Returns:
            Response: The HTTP response.
        """

        self.request = request

        print(f"Dispatching {request.method} request for {request.path}")

        if request.method == "GET":
            return self.get()
        elif request.method == "POST":
            return self.post()
        elif request.method == "PUT":
            return self.put()
        elif request.method == "DELETE":
            return self.delete()
        else:
            return Response(405, "Method Not Allowed")

    def get(self) -> Response:
        """Handle a GET request.

        Returns:
            Response: The HTTP response.
        """
        return Response(200, "OK", b"GET response")

    def post(self) -> Response:
        """Handle a POST request.

        Form fields are available via ``self.request.form_data``.

        Returns:
            Response: The HTTP response.
        """
        return Response(200, "OK", b"POST response")

    def put(self) -> Response:
        """Handle a PUT request.

        Returns:
            Response: The HTTP response.
        """
        return Response(200, "OK", b"PUT response")

    def delete(self) -> Response:
        """Handle a DELETE request.

        Returns:
            Response: The HTTP response.
        """
        return Response(200, "OK", b"DELETE response")
