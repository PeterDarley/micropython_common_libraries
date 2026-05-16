"""Generic over-the-air updater for syncing files from a GitHub repository.

This module is intentionally project-agnostic. Host projects provide their
own include/exclude rules and repository defaults.
"""

import hashlib
import json
import os


class OTAUpdater:
    """Check and apply file updates from a configured GitHub repository."""

    _GITHUB_API_BASE: str = "https://api.github.com"
    _GITHUB_RAW_BASE: str = "https://raw.githubusercontent.com"

    _DEFAULT_LOCAL_EXCLUDED_DIRS: tuple = (
        ".git",
        "__pycache__",
    )

    def __init__(
        self,
        *,
        repo_owner: str,
        repo_name: str,
        tracked_root_prefixes: tuple = (),
        tracked_root_files: tuple = (),
        excluded_path_prefixes: tuple = (),
        excluded_paths: tuple = (),
        excluded_local_dirs: tuple = (),
        candidate_branches: tuple = ("main", "master"),
        user_agent: str = "ota-updater",
        track_submodules: bool = False,
    ) -> None:
        """Initialise an updater instance.

        Args:
            repo_owner: GitHub owner/org name.
            repo_name: GitHub repository name.
            tracked_root_prefixes: Path prefixes to include (e.g. "lib/").
            tracked_root_files: Root-level files to include exactly.
            excluded_path_prefixes: Remote/local path prefixes to exclude.
            excluded_paths: Exact remote/local paths to exclude.
            excluded_local_dirs: Directory names to skip during local scans.
            candidate_branches: Branch names to try in order.
            user_agent: HTTP user agent string.
            track_submodules: If True, recursively fetch and track submodule files.
        """

        if not repo_owner or not repo_name:
            raise ValueError("repo_owner and repo_name are required")

        self.repo_owner: str = repo_owner
        self.repo_name: str = repo_name
        self.tracked_root_prefixes: tuple = tuple(tracked_root_prefixes)
        self.tracked_root_files: tuple = tuple(tracked_root_files)
        self.excluded_path_prefixes: tuple = tuple(excluded_path_prefixes)
        self.excluded_paths: tuple = tuple(excluded_paths)
        self.excluded_local_dirs: tuple = self._DEFAULT_LOCAL_EXCLUDED_DIRS + tuple(excluded_local_dirs)
        self.candidate_branches: tuple = tuple(candidate_branches) if candidate_branches else ("main", "master")
        self.user_agent: str = user_agent if user_agent else "ota-updater"
        self.track_submodules: bool = track_submodules

    @property
    def repository_slug(self) -> str:
        """Return repository slug in owner/name format."""

        return self.repo_owner + "/" + self.repo_name

    def check_for_updates(self) -> dict:
        """Compare local tracked files to the remote git tree and return a plan."""

        remote_tree, branch_name = self._fetch_repo_tree()

        remote_file_map: dict = {}
        submodules: dict = {}

        # Separate regular files from submodules
        for entry in remote_tree:
            path = entry.get("path", "")

            if entry.get("type") == "commit":
                # This is a submodule
                if self._should_track_path(path):
                    submodules[path] = entry.get("sha", "")
                continue

            if entry.get("type") != "blob":
                continue

            if not self._should_track_path(path):
                continue

            remote_file_map[path] = entry.get("sha", "")

        # Process submodules if enabled
        if self.track_submodules and submodules:
            gitmodules = self._fetch_gitmodules(branch_name)
            for submodule_path, submodule_sha in submodules.items():
                submodule_info = gitmodules.get(submodule_path, {})
                submodule_url = submodule_info.get("url", "")
                if not submodule_url:
                    continue

                try:
                    submodule_owner, submodule_repo = self._parse_github_url(submodule_url)
                    submodule_tree = self._fetch_submodule_tree(submodule_owner, submodule_repo, submodule_sha)

                    for entry in submodule_tree:
                        if entry.get("type") != "blob":
                            continue

                        file_path = submodule_path + "/" + entry.get("path", "")
                        if not self._should_track_path(file_path):
                            continue

                        remote_file_map[file_path] = entry.get("sha", "")
                except Exception:
                    # Skip submodules that fail to fetch
                    pass

        local_files: set = set(self._list_local_files(""))
        updates: list = []

        for path in sorted(remote_file_map.keys()):
            remote_sha = remote_file_map.get(path, "")

            if path not in local_files:
                updates.append({"path": path, "status": "added"})
                continue

            local_sha = self._local_git_blob_sha(path)
            if local_sha != remote_sha:
                updates.append({"path": path, "status": "modified"})

        for local_path in sorted(local_files):
            if local_path not in remote_file_map:
                updates.append({"path": local_path, "status": "deleted"})

        return {
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "repository": self.repository_slug,
            "branch": branch_name,
            "updates": updates,
            "has_updates": len(updates) > 0,
        }

    def apply_updates(self, branch_name: str, updates: list, remove_deleted: bool = False) -> dict:
        """Apply a previously computed update list and return the result summary."""

        applied_files: list = []
        removed_files: list = []
        failed_files: list = []

        for update_entry in updates:
            path: str = update_entry.get("path", "")
            status: str = update_entry.get("status", "")

            if not path:
                continue

            if status in ("added", "modified"):
                try:
                    file_bytes = self._download_raw_file(path, branch_name)
                    self._write_file(path, file_bytes)
                    applied_files.append(path)
                except Exception as error:
                    failed_files.append({"path": path, "error": str(error)})
            elif status == "deleted" and remove_deleted:
                try:
                    if self._path_exists(path):
                        os.remove(path)
                    removed_files.append(path)
                except Exception as error:
                    failed_files.append({"path": path, "error": str(error)})

        return {
            "repository": self.repository_slug,
            "branch": branch_name,
            "applied_files": applied_files,
            "removed_files": removed_files,
            "failed_files": failed_files,
            "success": len(failed_files) == 0,
        }

    def _fetch_repo_tree(self) -> tuple:
        """Fetch the recursive git tree from GitHub using candidate branches."""

        last_error: Exception | None = None

        for branch_name in self.candidate_branches:
            url = (
                self._GITHUB_API_BASE
                + "/repos/"
                + self.repo_owner
                + "/"
                + self.repo_name
                + "/git/trees/"
                + branch_name
                + "?recursive=1"
            )

            try:
                status_code, response_headers, body = self._http_get(url)
                if status_code != 200:
                    raise OSError("GitHub API returned HTTP {}".format(status_code))

                payload = json.loads(body.decode("utf-8"))
                tree = payload.get("tree", [])
                if not isinstance(tree, list):
                    raise ValueError("Unexpected tree payload")

                return tree, branch_name
            except Exception as error:
                last_error = error

        if last_error is None:
            raise OSError("Unable to fetch repository tree")

        raise last_error

    def _fetch_gitmodules(self, branch_name: str) -> dict:
        """Fetch and parse .gitmodules file to extract submodule mappings.

        Returns a dict mapping submodule paths to {url, ...} dicts.
        """

        url = self._GITHUB_RAW_BASE + "/" + self.repo_owner + "/" + self.repo_name + "/" + branch_name + "/.gitmodules"

        try:
            status_code, response_headers, body = self._http_get(url)
            if status_code != 200:
                return {}

            gitmodules_content = body.decode("utf-8", "replace")
            return self._parse_gitmodules(gitmodules_content)
        except Exception:
            return {}

    @staticmethod
    def _parse_gitmodules(content: str) -> dict:
        """Parse .gitmodules INI format and return dict of submodule->config.

        Example .gitmodules:
            [submodule "lib"]
                path = lib
                url = https://github.com/user/lib.git
        """

        submodules: dict = {}
        current_section: str | None = None

        for line in content.split("\n"):
            line = line.strip()

            if not line or line.startswith(";") or line.startswith("#"):
                continue

            if line.startswith("[submodule"):
                # Extract section name from [submodule "name"]
                if '"' in line:
                    current_section = line.split('"')[1]
                    submodules[current_section] = {}
                continue

            if "=" in line and current_section is not None:
                key, value = line.split("=", 1)
                submodules[current_section][key.strip()] = value.strip()

        return submodules

    @staticmethod
    def _parse_github_url(url: str) -> tuple:
        """Extract (owner, repo) from a GitHub URL.

        Handles formats:
        - https://github.com/owner/repo.git
        - https://github.com/owner/repo
        - git@github.com:owner/repo.git
        """

        url = url.rstrip("/").rstrip(".git")

        if "github.com/" in url:
            # HTTPS format
            remainder = url.split("github.com/", 1)[1]
        elif "github.com:" in url:
            # SSH format
            remainder = url.split("github.com:", 1)[1]
        else:
            raise ValueError("Not a GitHub URL: {}".format(url))

        parts = remainder.split("/")
        if len(parts) < 2:
            raise ValueError("Invalid GitHub URL format: {}".format(url))

        return parts[0], parts[1]

    def _fetch_submodule_tree(self, submodule_owner: str, submodule_repo: str, commit_sha: str) -> list:
        """Fetch the tree for a submodule at a specific commit SHA."""

        url = (
            self._GITHUB_API_BASE
            + "/repos/"
            + submodule_owner
            + "/"
            + submodule_repo
            + "/git/trees/"
            + commit_sha
            + "?recursive=1"
        )

        try:
            status_code, response_headers, body = self._http_get(url)
            if status_code != 200:
                raise OSError("GitHub API returned HTTP {} for submodule {}".format(status_code, submodule_repo))

            payload = json.loads(body.decode("utf-8"))
            tree = payload.get("tree", [])
            if not isinstance(tree, list):
                raise ValueError("Unexpected tree payload for submodule")

            return tree
        except Exception as error:
            raise OSError(
                "Failed to fetch submodule tree for {}@{}: {}".format(submodule_repo, commit_sha, str(error))
            )

    def _download_raw_file(self, path: str, branch_name: str) -> bytes:
        """Download one file from raw.githubusercontent.com and return bytes."""

        url = self._GITHUB_RAW_BASE + "/" + self.repo_owner + "/" + self.repo_name + "/" + branch_name + "/" + path
        status_code, response_headers, body = self._http_get(url)
        if status_code != 200:
            raise OSError("File download failed for {} (HTTP {})".format(path, status_code))

        return body

    def _http_get(self, url: str, redirect_depth: int = 0) -> tuple:
        """Perform a small HTTP GET supporting HTTPS and chunked encoding."""

        if redirect_depth > 3:
            raise OSError("Too many redirects")

        scheme, host, port, path = self._parse_url(url)

        try:
            import socket
        except ImportError:
            import usocket as socket

        use_tls: bool = scheme == "https"
        socket_address = socket.getaddrinfo(host, port)[0][-1]
        sock = socket.socket()

        try:
            sock.connect(socket_address)
            if use_tls:
                try:
                    import ssl
                except ImportError:
                    import ussl as ssl

                try:
                    sock = ssl.wrap_socket(sock, server_hostname=host)
                except TypeError:
                    sock = ssl.wrap_socket(sock)

            request = (
                "GET "
                + path
                + " HTTP/1.1\r\n"
                + "Host: "
                + host
                + "\r\n"
                + "User-Agent: "
                + self.user_agent
                + "\r\n"
                + "Accept: */*\r\n"
                + "Connection: close\r\n\r\n"
            )
            sock.send(request.encode("utf-8"))

            stream = sock.makefile("rb")
            status_line = stream.readline().decode("utf-8", "replace").strip()
            status_code = self._parse_status_code(status_line)

            headers: dict = {}
            while True:
                header_line_raw = stream.readline()
                if not header_line_raw or header_line_raw == b"\r\n":
                    break

                header_line = header_line_raw.decode("utf-8", "replace").strip()
                if ":" in header_line:
                    key, value = header_line.split(":", 1)
                    headers[key.lower().strip()] = value.strip()

            if status_code in (301, 302, 307, 308):
                location = headers.get("location", "")
                if not location:
                    raise OSError("Redirect without location header")

                stream.close()
                return self._http_get(location, redirect_depth + 1)

            body: bytes
            transfer_encoding = headers.get("transfer-encoding", "").lower()
            if "chunked" in transfer_encoding:
                body = self._read_chunked_body(stream)
            else:
                content_length = headers.get("content-length", "")
                if content_length:
                    remaining = int(content_length)
                    chunks: list = []
                    while remaining > 0:
                        chunk = stream.read(min(1024, remaining))
                        if not chunk:
                            break

                        chunks.append(chunk)
                        remaining -= len(chunk)

                    body = b"".join(chunks)
                else:
                    body = stream.read()

            stream.close()
            return status_code, headers, body
        finally:
            try:
                sock.close()
            except Exception:
                pass

    @staticmethod
    def _read_chunked_body(stream) -> bytes:
        """Read a chunked HTTP response body from a file-like stream."""

        parts: list = []
        while True:
            size_line = stream.readline()
            if not size_line:
                break

            size_str = size_line.decode("utf-8", "replace").strip()
            if ";" in size_str:
                size_str = size_str.split(";", 1)[0]

            chunk_size = int(size_str, 16)
            if chunk_size == 0:
                while True:
                    trailer = stream.readline()
                    if not trailer or trailer == b"\r\n":
                        break

                break

            chunk = stream.read(chunk_size)
            parts.append(chunk)
            stream.read(2)

        return b"".join(parts)

    @staticmethod
    def _parse_url(url: str) -> tuple:
        """Parse an absolute URL and return (scheme, host, port, path)."""

        if "://" not in url:
            raise ValueError("URL must include scheme")

        scheme, remainder = url.split("://", 1)
        if "/" in remainder:
            host_part, path = remainder.split("/", 1)
            path = "/" + path
        else:
            host_part = remainder
            path = "/"

        if ":" in host_part:
            host, port_str = host_part.split(":", 1)
            port = int(port_str)
        else:
            host = host_part
            port = 443 if scheme == "https" else 80

        return scheme, host, port, path

    @staticmethod
    def _parse_status_code(status_line: str) -> int:
        """Extract integer status code from an HTTP status line."""

        parts = status_line.split(" ")
        if len(parts) < 2:
            raise OSError("Invalid HTTP response")

        return int(parts[1])

    def _should_track_path(self, path: str) -> bool:
        """Return True when a path should be managed by this updater."""

        if not path or path.startswith("/"):
            return False

        if ".." in path or "\\" in path:
            return False

        if path in self.excluded_paths:
            return False

        for excluded_prefix in self.excluded_path_prefixes:
            if path.startswith(excluded_prefix):
                return False

        # If no explicit tracked rules are provided, include all non-excluded paths.
        if not self.tracked_root_prefixes and not self.tracked_root_files:
            return True

        if path in self.tracked_root_files:
            return True

        for prefix in self.tracked_root_prefixes:
            if path.startswith(prefix):
                return True

        return False

    def _list_local_files(self, relative_dir: str) -> list:
        """Recursively list local tracked files using forward-slash paths."""

        base_path = relative_dir if relative_dir else "."
        try:
            entries = os.listdir(base_path)
        except OSError:
            return []

        results: list = []
        for entry in entries:
            if entry in self.excluded_local_dirs:
                continue

            if relative_dir:
                relative_path = relative_dir + "/" + entry
            else:
                relative_path = entry

            try:
                stat_info = os.stat(relative_path)
                mode = stat_info[0]
            except OSError:
                continue

            is_directory = bool(mode & 0x4000)
            if is_directory:
                results.extend(self._list_local_files(relative_path))
            else:
                normalized = relative_path.replace("\\", "/")
                if self._should_track_path(normalized):
                    results.append(normalized)

        return results

    @staticmethod
    def _path_exists(path: str) -> bool:
        """Return True if path exists on the local filesystem."""

        try:
            os.stat(path)
            return True
        except OSError:
            return False

    @staticmethod
    def _local_git_blob_sha(path: str) -> str:
        """Compute git-compatible blob SHA-1 for a local file."""

        with open(path, "rb") as file_handle:
            data = file_handle.read()

        return OTAUpdater._git_blob_sha(data)

    @staticmethod
    def _git_blob_sha(data: bytes) -> str:
        """Return SHA-1 in the same format git uses for blob objects."""

        header = "blob {}\0".format(len(data)).encode("utf-8")
        digest = hashlib.sha1(header + data).hexdigest()
        return digest

    def _write_file(self, path: str, file_bytes: bytes) -> None:
        """Create parent directories as needed and write bytes to disk."""

        self._ensure_parent_dirs(path)
        with open(path, "wb") as file_handle:
            file_handle.write(file_bytes)

    @staticmethod
    def _ensure_parent_dirs(path: str) -> None:
        """Create parent directories for path if they do not already exist."""

        parts = path.split("/")
        if len(parts) <= 1:
            return

        current = ""
        for part in parts[:-1]:
            current = part if not current else current + "/" + part
            try:
                os.mkdir(current)
            except OSError:
                pass
