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


def _resolve_variable(expression, context):
    """Resolve a dot-notation variable expression against a context dict.

    Supports ``variable``, ``variable.key`` (dict), and ``variable.index`` (list/tuple).
    Quoted string literals (single or double quotes) are returned as-is without lookup.
    Returns an empty string if the variable or any part of the path is not found.
    """

    expression = expression.strip()

    # Return quoted string literals as plain strings
    if len(expression) >= 2:
        if (expression[0] == '"' and expression[-1] == '"') or (expression[0] == "'" and expression[-1] == "'"):
            return expression[1:-1]

    parts = expression.split(".")
    value = context.get(parts[0])

    for part in parts[1:]:
        if value is None:
            return ""

        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(part)]
            except ValueError:
                # Part is not a literal integer; try resolving it as a context variable
                if part in context:
                    try:
                        value = value[int(context[part])]
                    except (ValueError, TypeError, IndexError):
                        return ""
                else:
                    return ""
            except IndexError:
                return ""
        else:
            return ""

    return "" if value is None else str(value)


def _resolve_variable_raw(expression, context):
    """Resolve a dot-notation variable expression and return the raw Python object.

    Unlike ``_resolve_variable``, this does not convert the result to a string.
    Returns None if the variable is not found.
    """

    expression = expression.strip()

    if len(expression) >= 2:
        if (expression[0] == '"' and expression[-1] == '"') or (expression[0] == "'" and expression[-1] == "'"):
            return expression[1:-1]

    parts = expression.split(".")
    value = context.get(parts[0])

    for part in parts[1:]:
        if value is None:
            return None

        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, (list, tuple)):
            try:
                value = value[int(part)]
            except ValueError:
                if part in context:
                    try:
                        value = value[int(context[part])]
                    except (ValueError, TypeError, IndexError):
                        return None
                else:
                    return None
            except IndexError:
                return None
        else:
            return None

    return value


def _evaluate_single_condition(expression, context):
    """Evaluate a single conditional expression (no ``and``/``or``) against a context dict.

    Supports:
    - Simple variables: ``variable``, ``variable.key``, ``variable.0``
    - ``not variable`` — logical negation
    - ``variable in list_variable`` — membership test
    - ``variable not in list_variable`` — negated membership test
    - Equality comparisons: ``variable == value`` or ``variable == variable2``
    - Inequality comparisons: ``variable != value`` or ``variable != variable2``

    Returns True if the condition is truthy, False otherwise.
    Falsy values: empty string, '0', 'False', 'None'.
    """

    expression = expression.strip()

    # Handle ``not`` prefix (but not ``not in``)
    if expression.startswith("not ") and " in " not in expression:
        return not _evaluate_single_condition(expression[4:], context)

    # Handle ``not in`` operator
    if " not in " in expression:
        parts = expression.split(" not in ", 1)
        if len(parts) == 2:
            left = _resolve_variable(parts[0].strip(), context)
            right = _resolve_variable_raw(parts[1].strip(), context)
            if isinstance(right, (list, tuple, dict, str)):
                return left not in (right if isinstance(right, (dict, str)) else [str(item) for item in right])

            return True

    # Handle ``in`` operator
    if " in " in expression:
        parts = expression.split(" in ", 1)
        if len(parts) == 2:
            left = _resolve_variable(parts[0].strip(), context)
            right = _resolve_variable_raw(parts[1].strip(), context)
            if isinstance(right, (list, tuple, dict, str)):
                return left in (right if isinstance(right, (dict, str)) else [str(item) for item in right])

            return False

    # Check for != operator (must check before == to avoid false split on !==)
    if "!=" in expression:
        parts = expression.split("!=", 1)
        if len(parts) == 2:
            left = _resolve_variable(parts[0].strip(), context)
            right = _resolve_variable(parts[1].strip(), context)
            return left != right

    # Check for == operator
    if "==" in expression:
        parts = expression.split("==", 1)
        if len(parts) == 2:
            left = _resolve_variable(parts[0].strip(), context)
            right = _resolve_variable(parts[1].strip(), context)
            return left == right

    # Simple variable evaluation — use raw value so collections are truthy/falsy correctly.
    raw = _resolve_variable_raw(expression, context)
    if raw is None:
        return False

    if isinstance(raw, (list, tuple, dict)):
        return len(raw) > 0

    return str(raw) not in ("", "0", "False", "None")


def _evaluate_condition(expression, context):
    """Evaluate a conditional expression against a context dict.

    Supports ``and`` and ``or`` connectives (evaluated left-to-right with
    ``and`` binding tighter than ``or``), as well as ``not`` prefix on
    individual terms.
    """

    # Split on `` or `` first (lower precedence), then `` and `` within each group.
    or_groups = expression.split(" or ")
    for or_group in or_groups:
        and_parts = or_group.split(" and ")
        group_result = True

        for part in and_parts:
            if not _evaluate_single_condition(part, context):
                group_result = False
                break

        if group_result:
            return True

    return False


def _apply_context(text, context):
    """Replace all ``{{ expression }}`` placeholders in text using context.

    Supports simple variable names and dot-notation (e.g. ``variable.key`` or
    ``variable.0``). Unknown variables are replaced with an empty string.
    """

    result = ""
    pos = 0

    while pos < len(text):
        start = text.find("{{", pos)
        if start == -1:
            result += text[pos:]
            break

        end = text.find("}}", start + 2)
        if end == -1:
            result += text[pos:]
            break

        result += text[pos:start]
        expression = text[start + 2 : end].strip()
        result += _resolve_variable(expression, context)
        pos = end + 2

    return result


def _process_if_blocks(text, context):
    """Process ``{% if expression %}...{% endif %}`` blocks in text.

    Supports:
    - ``{% if variable %}`` — true if truthy (non-empty, not '0', 'False', 'None')
    - ``{% if variable.key %}`` — dot-notation access
    - ``{% if variable == value %}`` — equality check
    - ``{% if variable != value %}`` — inequality check
    - ``{% if variable in list %}`` — membership test
    - ``{% if variable not in list %}`` — negated membership test
    - ``{% if condition and condition %}`` — logical AND
    - ``{% if condition or condition %}`` — logical OR
    - ``{% if not variable %}`` — logical negation
    - ``{% else %}`` blocks are also supported

    Nested ``{% if %}`` blocks are handled correctly via depth tracking.
    """

    while "{% if" in text:
        start = text.find("{% if")
        if start == -1:
            break

        if_start = text.find("%}", start)
        if if_start == -1:
            break

        if_tag = text[start + 2 : if_start].strip()
        parts = if_tag.split(None, 1)

        if len(parts) >= 2 and parts[0] == "if":
            expression = parts[1].strip()

            # Find the matching endif and any else by tracking nesting depth
            depth = 1
            search_pos = if_start + 2
            endif_start = -1
            else_start = -1

            while depth > 0 and search_pos < len(text):
                next_if = text.find("{% if", search_pos)
                next_else = text.find("{% else %}", search_pos) if depth == 1 else -1
                next_endif = text.find("{% endif %}", search_pos)

                # Find the earliest tag
                candidates = []
                if next_if != -1:
                    candidates.append((next_if, "if"))
                if next_else != -1:
                    candidates.append((next_else, "else"))
                if next_endif != -1:
                    candidates.append((next_endif, "endif"))

                if not candidates:
                    break

                candidates.sort(key=lambda x: x[0])
                next_pos, next_type = candidates[0]

                if next_type == "if":
                    depth += 1
                    search_pos = next_if + 5
                elif next_type == "else":
                    else_start = next_else
                    search_pos = next_else + 10
                elif next_type == "endif":
                    depth -= 1
                    if depth == 0:
                        endif_start = next_endif
                    search_pos = next_endif + 11

            if endif_start == -1:
                text = text[:start] + text[if_start + 2 :]
                continue

            # Extract if body and else body
            if else_start != -1:
                if_body = text[if_start + 2 : else_start]
                else_body = text[else_start + 10 : endif_start]
            else:
                if_body = text[if_start + 2 : endif_start]
                else_body = ""

            # Evaluate condition
            is_truthy = _evaluate_condition(expression, context)

            if is_truthy:
                text = text[:start] + if_body + text[endif_start + 11 :]
            else:
                text = text[:start] + else_body + text[endif_start + 11 :]
        else:
            # Malformed if tag, skip it
            text = text[:start] + text[if_start + 2 :]

    return text


def _resolve_iterable(list_name, context):
    """Resolve a for-loop iterable expression to a list of items.

    Supports dict.items()/keys()/values(), range(n), and plain variables.
    Returns None if the iterable cannot be resolved.
    """

    for suffix, method in ((".items()", "items"), (".keys()", "keys"), (".values()", "values")):
        if list_name.endswith(suffix):
            dict_name = list_name[: -len(suffix)].strip()
            dict_obj = context.get(dict_name)
            if isinstance(dict_obj, dict):
                return list(getattr(dict_obj, method)())
            return None

    if list_name.startswith("range(") and list_name.endswith(")"):
        range_arg = list_name[6:-1].strip()
        try:
            return list(range(int(range_arg)))
        except ValueError:
            if range_arg in context:
                try:
                    return list(range(int(context[range_arg])))
                except (ValueError, TypeError):
                    pass
            return []

    return context.get(list_name) if "." not in list_name else _resolve_variable_raw(list_name, context)


def _process_for_loops(text, context, templates_dir):
    """Process ``{% for var in list %}...{% endfor %}`` blocks recursively.

    Handles nested for loops by recursively processing the loop body before
    applying variable substitution, preventing inner loop variables from being
    erased by the outer loop's context application.
    """

    while "{% for" in text:
        start = text.find("{% for")
        if start == -1:
            break

        # Find the matching endfor
        for_start = text.find("%}", start)
        if for_start == -1:
            break

        # Extract for tag content
        for_tag = text[start + 2 : for_start].strip()

        # Parse: for var in list_name or for var1, var2 in list_name
        in_pos = for_tag.find(" in ")
        if in_pos != -1 and for_tag.startswith("for "):
            var_names_str = for_tag[4:in_pos].strip()
            list_name = for_tag[in_pos + 4 :].strip()

            var_names = [v.strip() for v in var_names_str.split(",")]
            is_tuple_unpack = len(var_names) > 1

            # Find matching endfor by tracking nesting depth
            depth = 1
            search_pos = for_start + 2
            endfor_start = -1

            while depth > 0 and search_pos < len(text):
                next_for = text.find("{% for", search_pos)
                next_endfor = text.find("{% endfor %}", search_pos)

                if next_for != -1 and (next_endfor == -1 or next_for < next_endfor):
                    depth += 1
                    search_pos = next_for + 6
                elif next_endfor != -1:
                    depth -= 1
                    if depth == 0:
                        endfor_start = next_endfor
                    search_pos = next_endfor + 12
                else:
                    break

            if endfor_start == -1:
                text = text[:start] + text[for_start + 2 :]
                continue

            loop_body = text[for_start + 2 : endfor_start]

            items = _resolve_iterable(list_name, context)

            if items is not None:
                rendered_chunks = []

                try:
                    if not isinstance(items, (list, tuple)):
                        items = [items]

                    for item in items:
                        loop_context = dict(context)

                        if is_tuple_unpack:
                            try:
                                if isinstance(item, (tuple, list)):
                                    for i, var_name in enumerate(var_names):
                                        loop_context[var_name] = item[i] if i < len(item) else ""
                                else:
                                    loop_context[var_names[0]] = item
                                    for var_name in var_names[1:]:
                                        loop_context[var_name] = ""
                            except (TypeError, IndexError):
                                loop_context[var_names[0]] = item
                                for var_name in var_names[1:]:
                                    loop_context[var_name] = ""
                        else:
                            loop_context[var_names[0]] = item

                        # Recursively process nested for loops before applying context
                        loop_text = _process_for_loops(loop_body, loop_context, templates_dir)
                        loop_text = _process_if_blocks(loop_text, loop_context)
                        loop_text = _process_includes(loop_text, loop_context, templates_dir)
                        rendered_chunks.append(_apply_context(loop_text, loop_context))
                        del loop_context, loop_text
                        gc.collect()

                    rendered_loop = "".join(rendered_chunks)
                    text = text[:start] + rendered_loop + text[endfor_start + 12 :]

                except Exception:
                    text = text[:start] + text[endfor_start + 12 :]
            else:
                text = text[:start] + text[endfor_start + 12 :]
        else:
            text = text[:start] + text[for_start + 2 :]

    return text


def render_template(template_file, context=None, templates_dir="templates"):
    """Load a template file and substitute ``{{ key }}`` placeholders.

    Also supports ``{% include 'filename' %}`` to include other templates
    with the same context, ``{% for var in list %}...{% endfor %}`` loops
    (including ``{% for key, value in dict.items() %}``, ``{% for key in dict.keys() %}``,
    ``{% for value in dict.values() %}``, and ``{% for i in range(n) %}`` where *n* is
    an integer literal or a context variable),
    ``{% if expression %}...{% endif %}`` conditionals (including ``==``, ``!=``,
    ``in``, ``not in``, ``and``, ``or``, and ``not``),
    and dot-notation for dicts/lists (e.g. ``{{ item.key }}``, ``{{ item.0 }}``).

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

    # Handle for loops (including nested): {% for var in list %}...{% endfor %}
    text = _process_for_loops(text, context, templates_dir)

    # Process {% if %}...{% endif %} conditionals.
    text = _process_if_blocks(text, context)

    # Replace all {{ ... }} placeholders (supports dot-notation).
    text = _apply_context(text, context)

    # Handle template includes: {% include 'filename' %}
    text = _process_includes(text, context, templates_dir)

    return text


def _process_includes(text, context, templates_dir):
    """Process ``{% include 'filename' %}`` tags in text.

    Loads and renders each included template with the current context,
    so loop variables and other context values are available in includes.
    """

    while "{%" in text:
        start = text.find("{%")
        end = text.find("%}", start)
        if end == -1:
            break

        tag_content = text[start + 2 : end].strip()
        if tag_content.startswith("include"):
            include_part = tag_content[7:].strip().strip("\"'")
            include_filename = include_part.split()[0] if include_part else None

            if include_filename:
                included_content = render_template(include_filename, context, templates_dir)
                if included_content is not None:
                    text = text[:start] + included_content + text[end + 2 :]
                    continue

        # If we get here, skip this tag (malformed include or other tag)
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
                return self._lookup_route(path)

        return self._lookup_route(path)

    def _lookup_route(self, path):
        """Look up a route by path, trying with/without trailing slash."""

        result = self.routes.get(path)
        if result or path == "/":
            return result

        alternate = path.rstrip("/") if path.endswith("/") else path + "/"
        return self.routes.get(alternate)

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
        ".svg": "image/svg+xml",
    }

    def _guess_mime(self, filename):
        """Return a MIME type for *filename* based on its extension."""

        for ext, mime in self._MIME.items():
            if filename.endswith(ext):
                return mime
        return "application/octet-stream"

    def _send_response(self, cl_sock, status_code, reason, content, content_type="text/html", extra_headers=None):
        """Send an HTTP response to the client socket."""

        try:
            if isinstance(content, str):
                body_bytes = content.encode("utf-8")
            else:
                body_bytes = content or b""
            header = "HTTP/1.0 {} {}\r\nContent-Type: {}\r\nContent-Length: {}".format(
                status_code, reason, content_type, len(body_bytes)
            )
            if extra_headers:
                for key, value in extra_headers.items():
                    header += "\r\n{}: {}".format(key, value)
            header += "\r\n\r\n"
            cl_sock.send(header.encode("utf-8"))
            if body_bytes:
                cl_sock.send(body_bytes)
        except Exception:
            pass

    def _send_file(self, cl_sock, file_path: str, status_code: int, reason: str, content_type: str):
        """Send an HTTP response streaming from a file on disk.

        Streams the file in 1KB chunks to avoid loading it all into memory.
        Cleans up the file after sending.
        """

        try:
            # Get file size (os.stat returns (size, ...) tuple in MicroPython)
            file_size = os.stat(file_path)[6]

            # Send HTTP headers
            header = "HTTP/1.0 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\n\r\n".format(
                status_code, reason, content_type, file_size
            )
            cl_sock.send(header.encode("utf-8"))

            # Stream file in 1KB chunks
            chunk_size = 1024
            try:
                with open(file_path, "rb") as f:
                    while True:
                        chunk = f.read(chunk_size)
                        if not chunk:
                            break
                        cl_sock.send(chunk)
            finally:
                # Clean up the temp file
                try:
                    os.remove(file_path)
                except Exception:
                    pass
        except Exception:
            pass

    def _send_result(self, cl_sock, res):
        """Normalise a route handler return value and send it as a response."""

        if res is None:
            return self._send_response(cl_sock, 204, "No Content", b"", "text/plain")
        if isinstance(res, FileResponse):
            return self._send_file(cl_sock, res.file_path, res.status, res.reason, res.content_type)
        if isinstance(res, Response):
            return self._send_response(
                cl_sock, res.status, res.reason, res.to_bytes(), res.content_type, res.headers or None
            )
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

    @property
    def query_params(self) -> dict:
        """Return the decoded query string parameters as a dict."""

        return _parse_form_data(self.query)


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


class FileResponse:
    """Represents an HTTP response that streams from a file on disk.

    This avoids loading the entire file into memory at once.

    Attributes:
        file_path: Path to the file to stream (str).
        status: HTTP status code (int).
        reason: Short reason phrase (str).
        content_type: MIME type string.
    """

    def __init__(self, file_path: str, status: int = 200, reason: str = "OK", content_type: str = "text/html"):
        self.file_path = file_path
        self.status = status
        self.reason = reason
        self.content_type = content_type


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
