"""Captive portal for initial WiFi configuration.

Starts a temporary WiFi access point and serves a minimal web page so the
user can enter their home WiFi SSID and password.  Credentials are saved
to persistent storage (``system_settings.wifi``) and the device is reset so
it can connect to the real network.

A minimal DNS server runs in a background thread and resolves every hostname
to the AP gateway IP (``192.168.4.1``), which causes mobile devices and
browsers to detect the captive portal and open it automatically.

Usage::

    from captive_portal import CaptivePortal

    portal = CaptivePortal()
    portal.start()  # blocks until credentials are saved, then resets the device
"""

import gc
import socket

import network  # type: ignore

from storage import PersistentDict

_AP_IP: str = "192.168.4.1"

# ---------------------------------------------------------------------------
# HTML helpers
# ---------------------------------------------------------------------------


def _html_escape(text: str) -> str:
    """Escape characters that are special in HTML attribute values and content.

    Args:
        text: Plain string to escape.

    Returns:
        HTML-safe string.
    """

    return text.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


_SAVED_HTML: str = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Saved</title>
<style>
body { font-family: sans-serif; background: #111; color: #eee; display: flex; justify-content: center; padding: 2rem; margin: 0; }
.card { background: #1e1e1e; border-radius: 8px; padding: 2rem; max-width: 420px; width: 100%; }
h1 { margin-top: 0; color: #4caf50; }
p { color: #aaa; }
strong { color: #fff; }
</style>
</head>
<body>
<div class="card">
  <h1>Credentials Saved</h1>
  <p>The device is rebooting and will connect to your WiFi network.</p>
  <p>Once connected, you can access it at
  <strong>http://__HOSTNAME__.local/</strong></p>
</div>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# DNS server helpers
# ---------------------------------------------------------------------------


def _build_dns_reply(query: bytes) -> bytes:
    """Build a DNS A-record reply that resolves every name to the AP IP.

    Args:
        query: Raw bytes of the incoming DNS query packet.

    Returns:
        Raw bytes of the DNS reply packet.
    """

    transaction_id: bytes = query[:2]

    # Flags: QR=1 (response), OPCODE=0, AA=1 (authoritative), TC=0, RD=1, RA=1
    flags: bytes = b"\x81\x80"

    # Counts: 1 question, 1 answer, 0 authority, 0 additional
    counts: bytes = b"\x00\x01\x00\x01\x00\x00\x00\x00"

    # Echo the original question section verbatim (everything after the 12-byte header)
    question_section: bytes = query[12:]

    # Answer RR:
    #   \xc0\x0c  — compressed name pointer to offset 12 (the question name)
    #   \x00\x01  — type A
    #   \x00\x01  — class IN
    #   \x00\x00\x00\x3c — TTL 60 seconds
    #   \x00\x04  — RDLENGTH 4
    #   <IP>      — 4-byte IPv4 address
    ip_bytes: bytes = bytes(int(octet) for octet in _AP_IP.split("."))
    answer: bytes = b"\xc0\x0c\x00\x01\x00\x01\x00\x00\x00\x3c\x00\x04" + ip_bytes

    return transaction_id + flags + counts + question_section + answer


def _run_dns_server(stop_flag: list) -> None:
    """UDP DNS server that answers all queries with the AP gateway IP.

    Runs in a background thread.  Stops when ``stop_flag[0]`` is True.

    Args:
        stop_flag: A single-element list; set ``stop_flag[0] = True`` to stop.
    """

    dns_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        dns_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        dns_socket.bind(("0.0.0.0", 53))
        dns_socket.settimeout(1.0)

        while not stop_flag[0]:
            try:
                data, client_addr = dns_socket.recvfrom(512)
                if data:
                    reply = _build_dns_reply(data)
                    dns_socket.sendto(reply, client_addr)

            except OSError:
                pass

    except Exception as error:
        print("CaptivePortal DNS error:", error)

    finally:
        dns_socket.close()


# ---------------------------------------------------------------------------
# URL decode helper
# ---------------------------------------------------------------------------


def _url_decode(encoded: str) -> str:
    """Decode a URL-encoded string (``%XX`` sequences and ``+`` → space).

    Args:
        encoded: URL-encoded input string.

    Returns:
        Decoded plain string.
    """

    decoded: str = encoded.replace("+", " ")
    result: str = ""
    index: int = 0

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


# ---------------------------------------------------------------------------
# CaptivePortal class
# ---------------------------------------------------------------------------


class CaptivePortal:
    """Serves a WiFi credential form over a local AP hotspot.

    Starts a temporary access point, intercepts all DNS queries to point
    browsers at the portal page, and runs a blocking HTTP server.  When
    the user submits their WiFi SSID and password the credentials are saved
    to persistent storage and the device is reset.

    Example::

        from captive_portal import CaptivePortal

        portal = CaptivePortal()
        portal.start()  # does not return; resets device when done
    """

    def __init__(self, ap_ssid: str = "lightmotron-setup", ap_password: str = "") -> None:
        """Initialise the captive portal.

        Args:
            ap_ssid: SSID for the temporary AP hotspot.
            ap_password: Password for the AP.  Empty string means open (no
                password), which is the default so users can join without
                any prior configuration.
        """

        self._ap_ssid: str = ap_ssid
        self._ap_password: str = ap_password
        self._stop_flag: list = [False]
        self._cached_networks: list = []  # populated by _scan_networks() before AP starts

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _setup_ap(self) -> None:
        """Activate AP mode and configure the network interface.

        Power-save is disabled on the AP interface to prevent the firmware
        from periodically suspending the radio (which would drop connected
        clients and make the portal unreachable).
        """

        ap_interface = network.WLAN(network.AP_IF)
        ap_interface.active(True)

        if self._ap_password:
            ap_interface.config(ssid=self._ap_ssid, password=self._ap_password)
        else:
            # authmode 0 = AUTH_OPEN
            ap_interface.config(ssid=self._ap_ssid, authmode=0)

        # Disable power-save so the AP radio stays on continuously.
        try:
            ap_interface.config(pm=network.WIFI_PS_NONE)
            print("CaptivePortal: AP power-save disabled")
        except Exception as pm_error:
            print("CaptivePortal: could not disable AP power-save:", pm_error)

        print("CaptivePortal: AP active, SSID:", self._ap_ssid)
        print("CaptivePortal: portal at http://", _AP_IP, "/", sep="")

    def _scan_networks(self) -> list:
        """Scan for available WiFi networks and return a sorted, deduplicated list.

        Must be called BEFORE the AP interface is activated.  Once the AP is
        running the ESP32 radio is occupied and ``sta.scan()`` will return an
        empty result set.

        Returns:
            List of ``(ssid, rssi)`` tuples sorted by signal strength,
            strongest first.  ``rssi`` is a negative integer (dBm).
            Returns an empty list if the scan fails.
        """

        sta = network.WLAN(network.STA_IF)
        print("CaptivePortal: STA interface active:", sta.active())
        sta.active(True)
        print("CaptivePortal: STA interface active after activate():", sta.active())

        try:
            print("CaptivePortal: starting scan...")
            results = sta.scan()  # [(ssid, bssid, channel, rssi, authmode, hidden), ...]
            print("CaptivePortal: scan returned", len(results), "raw entries")
        except Exception as error:
            print("CaptivePortal: scan error:", error)
            return []

        seen: dict = {}

        for entry in results:
            try:
                ssid_raw = entry[0]
                rssi: int = entry[3]

                print(
                    "CaptivePortal: raw entry ssid_raw={!r} type={} rssi={}".format(
                        ssid_raw, type(ssid_raw).__name__, rssi
                    )
                )

                if isinstance(ssid_raw, bytes):
                    ssid: str = ssid_raw.decode("utf-8", "replace").strip()
                else:
                    ssid = str(ssid_raw).strip()

                if not ssid:
                    print("CaptivePortal:   -> skipped (empty SSID)")
                    continue

                # Keep the entry with the strongest signal for each SSID
                if ssid not in seen or rssi > seen[ssid]:
                    seen[ssid] = rssi
                    print("CaptivePortal:   -> kept:", repr(ssid), "rssi:", rssi)
                else:
                    print("CaptivePortal:   -> duplicate, weaker signal, skipped:", repr(ssid))

            except Exception as entry_error:
                print("CaptivePortal:   -> entry parse error:", entry_error)
                continue

        networks: list = sorted(seen.items(), key=lambda item: item[1], reverse=True)
        print("CaptivePortal: final network list ({} networks):".format(len(networks)))
        for network_ssid, network_rssi in networks:
            print("  ", repr(network_ssid), "rssi:", network_rssi)
        return networks

    def _build_portal_html(self, networks: list, hostname: str) -> str:
        """Build the WiFi setup page HTML with a list of scanned networks.

        Args:
            networks: List of ``(ssid, rssi)`` tuples from ``_scan_networks``.
            hostname: The configured mDNS hostname for the post-save note.

        Returns:
            Complete HTML page as a string.
        """

        # Build <option> elements for each scanned network
        options: list = []
        for ssid, rssi in networks:
            signal_pct: int = max(0, min(100, 2 * (rssi + 100)))
            safe_ssid: str = _html_escape(ssid)
            options.append(
                '<option value="' + safe_ssid + '">' + safe_ssid + " (" + str(signal_pct) + "%)" + "</option>"
            )

        options_html: str = "\n".join(options)
        safe_hostname: str = _html_escape(hostname)

        return (
            '<!DOCTYPE html><html lang="en"><head>'
            '<meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>Lightmotron Setup</title>"
            "<style>"
            "*,*::before,*::after{box-sizing:border-box;}"
            "body{font-family:sans-serif;background:#111;color:#eee;display:flex;justify-content:center;padding:2rem;margin:0;}"
            ".card{background:#1e1e1e;border-radius:8px;padding:2rem;max-width:420px;width:100%;}"
            "h1{margin-top:0;color:#fff;font-size:1.4rem;}"
            "p{color:#aaa;font-size:.9rem;}"
            "label{display:block;margin-top:1rem;font-size:.85rem;color:#aaa;}"
            "select,input{width:100%;padding:.5rem .6rem;margin-top:.25rem;background:#2c2c2c;color:#fff;border:1px solid #555;border-radius:4px;font-size:1rem;}"
            "button{margin-top:1.5rem;width:100%;padding:.75rem;background:#0d6efd;color:#fff;border:none;border-radius:4px;font-size:1rem;cursor:pointer;}"
            ".note{margin-top:1.5rem;font-size:.8rem;color:#777;border-top:1px solid #333;padding-top:1rem;}"
            '</style></head><body><div class="card">'
            "<h1>Lightmotron &mdash; WiFi Setup</h1>"
            "<p>Select your WiFi network and enter the password.</p>"
            '<form method="POST" action="/save" onsubmit="return onSub()">'
            '<input type="hidden" name="ssid" id="ssid">'
            "<label>Network</label>"
            '<select id="nsel" onchange="onNet(this)">'
            '<option value="">-- select a network --</option>'
            + options_html
            + '<option value="__other__">Other (enter manually)&hellip;</option>'
            "</select>"
            '<div id="other-row" style="display:none">'
            "<label>Network name (SSID)</label>"
            '<input type="text" id="manual" name="manual" maxlength="64" autocomplete="off">'
            "</div>"
            "<label>Password</label>"
            '<input type="password" name="password" maxlength="64" autocomplete="new-password">'
            '<button type="submit">Save &amp; Connect</button>'
            "</form>"
            '<p class="note">After saving, the device will reboot and join your network. '
            "You can then access it at <strong>" + safe_hostname + ".local</strong></p>"
            "</div>"
            "<script>"
            "function onNet(s){"
            'var o=document.getElementById("other-row");'
            'var h=document.getElementById("ssid");'
            'if(s.value==="__other__"){o.style.display="";h.value="";}'
            'else{o.style.display="none";h.value=s.value;}}'
            "function onSub(){"
            'var s=document.getElementById("nsel");'
            'var h=document.getElementById("ssid");'
            'if(s.value==="__other__"){h.value=document.getElementById("manual").value;}'
            "return h.value.trim().length>0;}"
            "(function(){"
            'var s=document.getElementById("nsel");'
            'if(s.options.length>1&&s.options[1].value!=="__other__"){s.selectedIndex=1;onNet(s);}'
            "})();"
            "</script></body></html>"
        )

    def _get_hostname(self) -> str:
        """Return the configured mDNS hostname from persistent storage.

        Returns:
            The stored hostname, or ``lightmotron`` if none is configured.
        """

        stored: str = PersistentDict().get("system_settings", {}).get("hostname", "")
        return stored if stored else "lightmotron"

    def _make_response(
        self, status_line: bytes, body: bytes, content_type: bytes = b"text/html; charset=utf-8"
    ) -> bytes:
        """Build a minimal HTTP response.

        Args:
            status_line: e.g. ``b"200 OK"``
            body: Response body bytes.
            content_type: Value for the Content-Type header.

        Returns:
            Complete HTTP response bytes.
        """

        return (
            b"HTTP/1.1 "
            + status_line
            + b"\r\nContent-Type: "
            + content_type
            + b"\r\nContent-Length: "
            + str(len(body)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + body
        )

    def _handle_request(self, client_socket) -> bool:
        """Handle one HTTP request from a connected client.

        Serves the portal form on GET /, saves credentials on POST /save,
        and redirects all other GET paths to / so that captive-portal
        detection requests (``/generate_204``, ``/hotspot-detect.html``, etc.)
        end up at the form.

        Args:
            client_socket: Connected client socket.

        Returns:
            True if credentials were successfully saved; False otherwise.
        """

        try:
            raw_data: bytes = client_socket.recv(2048)
            if not raw_data:
                return False

            request_line: str = raw_data.split(b"\r\n", 1)[0].decode("utf-8", "replace")
            parts: list = request_line.split(" ")
            method: str = parts[0] if parts else ""
            path: str = parts[1].split("?", 1)[0] if len(parts) > 1 else "/"

            # ----- GET /  (or any captive-portal detection path) -----
            if method == "GET":
                portal_paths = ("/", "/index.html")
                if path not in portal_paths:
                    # Redirect all other paths (OS captive-portal probes) to /
                    response = (
                        b"HTTP/1.1 302 Found\r\nLocation: http://"
                        + _AP_IP.encode()
                        + b"/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
                    )
                    client_socket.send(response)
                    return False

                hostname: str = self._get_hostname()
                html: str = self._build_portal_html(self._cached_networks, hostname)
                body: bytes = html.encode("utf-8")
                client_socket.send(self._make_response(b"200 OK", body))
                return False

            # ----- POST /save -----
            if method == "POST" and path == "/save":
                body_start: int = raw_data.find(b"\r\n\r\n")
                if body_start != -1:
                    form_body: str = raw_data[body_start + 4 :].decode("utf-8", "replace")
                    form: dict = {}

                    for pair in form_body.split("&"):
                        if "=" in pair:
                            key, value = pair.split("=", 1)
                            form[_url_decode(key)] = _url_decode(value)

                    ssid: str = form.get("ssid", "").strip()
                    # Fallback: if JS didn't set the hidden field, use the manual input
                    if not ssid or ssid == "__other__":
                        ssid = form.get("manual", "").strip()
                    password: str = form.get("password", "").strip()

                    if ssid:
                        storage: PersistentDict = PersistentDict()
                        sys_settings: dict = dict(storage.get("system_settings", {}))
                        existing_wifi: dict = dict(sys_settings.get("wifi", {}))
                        existing_wifi["ssid"] = ssid
                        existing_wifi["password"] = password
                        existing_wifi.setdefault("blink_on_connect", True)
                        existing_wifi.setdefault("print_on_connect", True)
                        sys_settings["wifi"] = existing_wifi
                        storage["system_settings"] = sys_settings
                        storage.store()

                        hostname: str = self._get_hostname()
                        html: str = _SAVED_HTML.replace("__HOSTNAME__", hostname)
                        body: bytes = html.encode("utf-8")
                        client_socket.send(self._make_response(b"200 OK", body))
                        return True

            # Bad request fallback
            client_socket.send(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            return False

        except Exception as error:
            print("CaptivePortal: request handler error:", error)
            return False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start the captive portal and block until credentials are saved.

        Scans for nearby networks first (before the AP is activated, while the
        radio is still free), then activates the AP interface, starts the DNS
        server in a background thread, and runs an HTTP server in the calling
        thread.  Returns only after saving credentials then immediately resets
        the device.  This method therefore never returns under normal operation.
        """

        import machine  # type: ignore
        import _thread  # type: ignore
        from time import sleep

        # Scan BEFORE starting the AP — once the AP is up the radio is busy
        # and scan() returns no results.
        self._cached_networks = self._scan_networks()

        self._setup_ap()

        # Indicate captive-portal mode with a purple onboard LED
        try:
            from leds import OnboardLED

            _onboard_led = OnboardLED()
            _onboard_led.set(80, 0, 80)
        except Exception:
            _onboard_led = None

        # Start DNS responder in background
        _thread.start_new_thread(_run_dns_server, (self._stop_flag,))

        # Start HTTP server
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(("0.0.0.0", 80))
        server_socket.listen(3)
        server_socket.settimeout(1.0)

        print("CaptivePortal: HTTP server listening on port 80")

        ap_interface = network.WLAN(network.AP_IF)
        known_stations: set = set()

        credentials_saved: bool = False
        try:
            while not credentials_saved:
                # Log any newly connected or disconnected AP clients
                try:
                    current_stations: set = set(bytes(mac) for mac, *_ in ap_interface.status("stations"))
                    joined: set = current_stations - known_stations
                    left: set = known_stations - current_stations
                    for mac in joined:
                        print("CaptivePortal: client connected:", ":".join("{:02x}".format(b) for b in mac))
                    for mac in left:
                        print("CaptivePortal: client disconnected:", ":".join("{:02x}".format(b) for b in mac))
                    known_stations = current_stations
                except Exception:
                    pass

                try:
                    client_socket, client_addr = server_socket.accept()
                    print("CaptivePortal: HTTP request from", client_addr[0])

                    try:
                        credentials_saved = self._handle_request(client_socket)
                    finally:
                        try:
                            client_socket.close()
                        except Exception:
                            pass

                    gc.collect()

                except OSError:
                    # settimeout causes accept() to raise OSError on timeout — keep looping
                    pass

        finally:
            self._stop_flag[0] = True
            server_socket.close()
            if _onboard_led is not None:
                try:
                    _onboard_led.off()
                except Exception:
                    pass

        print("CaptivePortal: credentials saved, rebooting...")
        sleep(1)
        machine.reset()
