"""Generic over-the-air updater for syncing files from a GitHub repository.

This module is intentionally project-agnostic. Host projects provide their
own include/exclude rules and repository defaults.
"""

import hashlib
import json
import os


class OTAUpdater:
    """Check and apply file updates from a configured GitHub repository."""

    class _SocketStreamFallback:
        """Provide readline/read over sockets that do not expose makefile()."""

        def __init__(self, sock) -> None:
            """Initialise fallback stream wrapper for a socket-like object."""

            self.sock = sock
            self.buffer: bytes = b""

        def _recv_chunk(self, size: int = 1024) -> bytes:
            """Receive one chunk from the underlying socket."""

            try:
                return self.sock.read(size)
            except AttributeError:
                return self.sock.recv(size)

        def readline(self) -> bytes:
            """Read one line ending in '\\n' or return remaining bytes at EOF."""

            while True:
                newline_index = self.buffer.find(b"\n")
                if newline_index >= 0:
                    line = self.buffer[: newline_index + 1]
                    self.buffer = self.buffer[newline_index + 1 :]
                    return line

                chunk = self._recv_chunk(512)
                if not chunk:
                    line = self.buffer
                    self.buffer = b""
                    return line

                self.buffer += chunk

        def read(self, size: int = -1) -> bytes:
            """Read exactly size bytes when size>=0, else read until EOF."""

            if size is None or size < 0:
                parts: list = []
                if self.buffer:
                    parts.append(self.buffer)
                    self.buffer = b""

                while True:
                    chunk = self._recv_chunk(1024)
                    if not chunk:
                        break

                    parts.append(chunk)

                return b"".join(parts)

            while len(self.buffer) < size:
                chunk = self._recv_chunk(max(1, size - len(self.buffer)))
                if not chunk:
                    break

                self.buffer += chunk

            data = self.buffer[:size]
            self.buffer = self.buffer[size:]
            return data

        def close(self) -> None:
            """No-op close; outer socket lifecycle owns the real socket."""

            return

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
        debug_logging: bool = False,
        debug_log_path: str = ".ota_debug.log",
        debug_log_sample_every: int = 50,
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
            debug_logging: If True, write OTA progress diagnostics to debug_log_path.
            debug_log_path: File path used for OTA progress diagnostics.
            debug_log_sample_every: Emit progress logs every N processed entries.
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
        self.debug_logging: bool = bool(debug_logging)
        self.debug_log_path: str = debug_log_path if debug_log_path else ".ota_debug.log"
        self.debug_log_sample_every: int = debug_log_sample_every if debug_log_sample_every > 0 else 50

        # Read .gitignore and add patterns to excluded paths
        self._merge_gitignore_exclusions()

    def _merge_gitignore_exclusions(self) -> None:
        """Read .gitignore from device and merge patterns into excluded lists."""

        try:
            with open(".gitignore", "r") as f:
                lines = f.read().split("\n")
        except OSError:
            # No .gitignore file, continue with existing exclusions
            return

        new_excluded_paths = list(self.excluded_paths)
        new_excluded_prefixes = list(self.excluded_path_prefixes)

        for line in lines:
            line = line.strip()
            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            # Handle trailing slash (directory marker)
            is_dir = line.endswith("/")
            if is_dir:
                line = line.rstrip("/")

            # Skip complex patterns we can't handle simply
            if "**" in line or "?" in line or "[" in line:
                continue

            # Add to excluded paths
            if "*" in line:
                # Pattern like "*.pyc" or "*.pyo"
                new_excluded_prefixes.append(line)
            else:
                # Exact filename or directory
                new_excluded_paths.append(line)
                if is_dir:
                    new_excluded_prefixes.append(line + "/")

        self.excluded_paths = tuple(new_excluded_paths)
        self.excluded_path_prefixes = tuple(new_excluded_prefixes)

    @property
    def repository_slug(self) -> str:
        """Return repository slug in owner/name format."""

        return self.repo_owner + "/" + self.repo_name

    def check_for_updates(self) -> dict:
        """Compare local tracked files to the remote git tree and return a plan.

        Uses incremental chunked processing to minimize memory usage. Temporary
        files are cleaned up after processing completes.
        """

        state_file: str = ".ota_state.json"
        local_files_file: str = ".ota_local_files.json"
        self._debug_log("check_start", "repo={}".format(self.repository_slug))

        try:
            # Fetch remote tree and commit hash (two API calls total).
            branch_name, tree_entries, commit_sha = self._fetch_repo_tree()
            self._debug_log("branch_resolved", branch_name)
            self._debug_log("commit_resolved", commit_sha)
            self._save_tree_snapshot_from_entries(tree_entries, state_file)
            del tree_entries
            self._debug_log("remote_snapshot_saved", state_file)

            # Load the stored last update commit (if any) for local modification detection.
            stored_commit: str = self._load_stored_update_commit()
            self._debug_log("stored_commit", stored_commit if stored_commit else "none")

            # Scan local filesystem and save to temp file
            local_files: list = self._list_local_files("")
            self._debug_log("local_scan_done", "count={}".format(len(local_files)))
            self._save_local_files_snapshot(local_files, local_files_file)
            self._debug_log("local_snapshot_saved", local_files_file)

            # Process incrementally and build updates list
            updates: list = self._process_updates_incremental(state_file, local_files_file, branch_name, stored_commit)
            self._debug_log("check_complete", "updates={}".format(len(updates)))

            return {
                "repo_owner": self.repo_owner,
                "repo_name": self.repo_name,
                "repository": self.repository_slug,
                "branch": branch_name,
                "commit_sha": commit_sha,
                "updates": updates,
                "has_updates": len(updates) > 0,
            }

        finally:
            # Clean up temporary files
            self._cleanup_temp_file(state_file)
            self._cleanup_temp_file(local_files_file)
            self._debug_log("temp_cleanup_done", "{} {}".format(state_file, local_files_file))

    def _save_tree_snapshot_from_entries(self, tree_entries: list, filename: str) -> None:
        """Save pre-fetched tree entries to a newline-delimited snapshot file."""

        written_entries: int = 0
        skipped_entries: int = 0
        blob_entries: int = 0
        submodule_entries: int = 0

        with open(filename, "w") as file_handle:
            for entry in tree_entries:
                entry_type: str = entry.get("type", "")
                entry_path: str = entry.get("path", "")
                entry_sha: str = entry.get("sha", "")

                if not entry_path or not entry_sha:
                    skipped_entries += 1
                    continue

                if entry_type == "blob":
                    blob_entries += 1
                    if self._should_track_path(entry_path):
                        file_handle.write(json.dumps({"type": "blob", "path": entry_path, "sha": entry_sha}) + "\n")
                        written_entries += 1
                    else:
                        self._debug_log("entry_filtered", "path={}".format(entry_path))
                        skipped_entries += 1
                elif entry_type == "commit":
                    submodule_entries += 1
                    if self._should_track_path(entry_path):
                        file_handle.write(
                            json.dumps({"type": "submodule", "path": entry_path, "sha": entry_sha}) + "\n"
                        )
                        written_entries += 1
                    else:
                        self._debug_log("entry_filtered", "path={}".format(entry_path))
                        skipped_entries += 1
                else:
                    skipped_entries += 1

        self._debug_log(
            "snapshot_done",
            "written={} blobs={} submodules={} skipped={}".format(
                written_entries, blob_entries, submodule_entries, skipped_entries
            ),
        )

    def _save_local_files_snapshot(self, local_files: list, filename: str) -> None:
        """Save local file list to a temporary JSON file."""

        with open(filename, "w") as file_handle:
            for local_path in sorted(local_files):
                file_handle.write(local_path + "\n")

    def _process_updates_incremental(
        self, tree_file: str, local_files_file: str, branch_name: str, stored_commit: str = ""
    ) -> list:
        """Process remote tree and local files incrementally in chunks.

        Yields updates without loading entire data structures into memory at once.
        When stored_commit is provided, distinguishes between repo changes and local-only modifications.
        """

        # Load local files set once for quick membership checks.
        try:
            local_files_set: set = set()
            with open(local_files_file, "r") as file_handle:
                while True:
                    line = file_handle.readline()
                    if not line:
                        break

                    local_path = line.strip()
                    if local_path:
                        local_files_set.add(local_path)
        except (OSError, ValueError):
            local_files_set = set()

        # Track submodules for a second pass and track all remote file paths for delete detection.
        submodules: dict = {}
        remote_paths_set: set = set()
        updates: list = []

        # First pass: process top-level repository entries from the snapshot file line-by-line.
        processed_snapshot_entries: int = 0
        try:
            with open(tree_file, "r") as file_handle:
                while True:
                    raw_line = file_handle.readline()
                    if not raw_line:
                        break

                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue

                    try:
                        entry: dict = json.loads(raw_line)
                    except ValueError:
                        continue

                    entry_type: str = entry.get("type", "")
                    path: str = entry.get("path", "")
                    sha: str = entry.get("sha", "")
                    processed_snapshot_entries += 1
                    if processed_snapshot_entries % self.debug_log_sample_every == 0:
                        self._debug_log(
                            "compare_progress",
                            "entries={} updates={} submodules={}".format(
                                processed_snapshot_entries,
                                len(updates),
                                len(submodules),
                            ),
                        )
                    if not path:
                        continue

                    if entry_type == "submodule":
                        submodules[path] = sha
                        continue

                    if entry_type != "blob":
                        continue

                    remote_paths_set.add(path)
                    if path not in local_files_set:
                        updates.append({"path": path, "status": "added"})
                        continue

                    local_sha: str = self._local_git_blob_sha(path)
                    if local_sha != sha:
                        # If we have a stored commit, distinguish between repo changes and local-only mods
                        if stored_commit:
                            updates.append({"path": path, "status": "locally_modified"})
                        else:
                            updates.append({"path": path, "status": "modified"})
        except (OSError, ValueError):
            pass

        # Process submodules if enabled, using iterative tree walking per submodule.
        if self.track_submodules and submodules:
            gitmodules: dict = self._fetch_gitmodules(branch_name)
            self._debug_log("submodules_start", "detected={} mapped={}".format(len(submodules), len(gitmodules)))

            for submodule_path, submodule_sha in submodules.items():
                submodule_info: dict = gitmodules.get(submodule_path, {})
                submodule_url: str = submodule_info.get("url", "")
                if not submodule_url:
                    self._debug_log("submodule_skipped", "path={} reason=no_url".format(submodule_path))
                    continue

                try:
                    self._debug_log("submodule_begin", "path={}".format(submodule_path))
                    submodule_owner, submodule_repo = self._parse_github_url(submodule_url)
                    for submodule_file_path, remote_sha in self._iter_submodule_blob_entries(
                        submodule_owner,
                        submodule_repo,
                        submodule_sha,
                    ):
                        file_path: str = submodule_path + "/" + submodule_file_path
                        if not self._should_track_path(file_path):
                            continue

                        remote_paths_set.add(file_path)

                        if file_path not in local_files_set:
                            updates.append({"path": file_path, "status": "added"})
                            continue

                        local_sha: str = self._local_git_blob_sha(file_path)
                        if local_sha != remote_sha:
                            # If we have a stored commit, distinguish between repo changes and local-only mods
                            if stored_commit:
                                updates.append({"path": file_path, "status": "locally_modified"})
                            else:
                                updates.append({"path": file_path, "status": "modified"})

                    self._debug_log("submodule_done", "path={} updates={}".format(submodule_path, len(updates)))

                except Exception:
                    # Skip submodules that fail to fetch
                    self._debug_log("submodule_failed", "path={}".format(submodule_path))
                    pass

        # Deleted files are those that exist locally but were not found remotely.
        for local_path in sorted(local_files_set):
            if local_path not in remote_paths_set:
                updates.append({"path": local_path, "status": "deleted"})

        self._debug_log(
            "compare_done",
            "entries={} remote_paths={} updates={}".format(
                processed_snapshot_entries,
                len(remote_paths_set),
                len(updates),
            ),
        )

        return updates

    @staticmethod
    def _cleanup_temp_file(filename: str) -> None:
        """Delete a temporary file if it exists."""

        try:
            os.remove(filename)
        except OSError:
            pass

    @staticmethod
    def _load_stored_update_commit() -> str:
        """Load the commit SHA from .ota_deployed_commit.json (written by upload.ps1)."""

        try:
            with open(".ota_deployed_commit.json", "r") as file_handle:
                content = file_handle.read()
                data = json.loads(content)
                return data.get("commit_sha", "")
        except Exception:
            return ""

    @staticmethod
    def save_update_commit(commit_sha: str, branch_name: str) -> None:
        """Save the commit SHA to .ota_deployed_commit.json after a successful OTA update."""

        try:
            data: dict = {"commit_sha": commit_sha, "branch": branch_name}
            with open(".ota_deployed_commit.json", "w") as file_handle:
                file_handle.write(json.dumps(data))
        except Exception:
            pass

    def _debug_log(self, stage: str, message: str = "") -> None:
        """Print a debug line when OTA debug logging is enabled."""

        if not self.debug_logging:
            return

        memory_suffix = self._debug_memory_suffix()
        if message:
            line = "[ota] {}: {}{}".format(stage, message, memory_suffix)
        else:
            line = "[ota] {}{}".format(stage, memory_suffix)

        print(line)

    @staticmethod
    def _debug_memory_suffix() -> str:
        """Return a compact memory suffix for debug log lines."""

        try:
            import gc

            return " mem_free={} mem_alloc={}".format(gc.mem_free(), gc.mem_alloc())
        except Exception:
            return ""

    def apply_updates(
        self, branch_name: str, updates: list, remove_deleted: bool = False, commit_sha: str = ""
    ) -> dict:
        """Apply a previously computed update list and return the result summary.

        If commit_sha is provided, it will be stored after successful apply for tracking.
        """

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
            elif status == "locally_modified":
                # Skip locally-modified files (user chose to keep local version)
                pass
            elif status == "deleted" and remove_deleted:
                try:
                    if self._path_exists(path):
                        os.remove(path)
                    removed_files.append(path)
                except Exception as error:
                    failed_files.append({"path": path, "error": str(error)})

        # After successful apply, save the commit hash for next check
        if commit_sha and len(failed_files) == 0:
            self.save_update_commit(commit_sha, branch_name)
            self._debug_log("commit_saved", commit_sha)

        return {
            "repository": self.repository_slug,
            "branch": branch_name,
            "applied_files": applied_files,
            "removed_files": removed_files,
            "failed_files": failed_files,
            "success": len(failed_files) == 0,
        }

    def _fetch_repo_tree(self) -> tuple:
        """Fetch the full recursive tree and branch HEAD commit, trying candidate branches in order.

        Returns (branch_name, tree_entries, commit_sha) on success.
        """

        last_error = None

        for branch_name in self.candidate_branches:
            try:
                self._debug_log("fetch_branch_attempt", branch_name)
                tree_entries = self._fetch_tree_entries(self.repo_owner, self.repo_name, branch_name, recursive=True)
                self._debug_log("tree_fetched", "branch={} entries={}".format(branch_name, len(tree_entries)))
                commit_sha = self._fetch_branch_head_commit(self.repo_owner, self.repo_name, branch_name)
                self._debug_log("commit_fetched", "branch={} sha={}".format(branch_name, commit_sha))
                return branch_name, tree_entries, commit_sha
            except Exception as error:
                self._debug_log("branch_failed", "branch={} error={}".format(branch_name, str(error)))
                last_error = error

        if last_error is None:
            raise OSError("Unable to resolve repository branch")

        raise last_error

    def _fetch_branch_head_commit(self, repo_owner: str, repo_name: str, branch_name: str) -> str:
        """Fetch the commit SHA of the branch HEAD."""

        # Try the simpler commits endpoint first
        url = self._GITHUB_API_BASE + "/repos/" + repo_owner + "/" + repo_name + "/commits/" + branch_name

        try:
            self._debug_log("commit_api_call", "url={}".format(url))
            status_code, response_headers, body = self._http_get(url)
            self._debug_log("commit_api_response", "status={}".format(status_code))
            if status_code != 200:
                raise OSError("GitHub API returned HTTP {} for branch {}".format(status_code, branch_name))

            payload = json.loads(body.decode("utf-8"))
            commit_sha: str = payload.get("sha", "")
            if not commit_sha:
                raise ValueError("No commit SHA found for branch {}".format(branch_name))

            return commit_sha
        except Exception as error:
            raise OSError("Failed to fetch branch HEAD for {}@{}: {}".format(repo_name, branch_name, str(error)))

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
        current_section = None

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

        return self._fetch_tree_entries(submodule_owner, submodule_repo, commit_sha, recursive=True)

    def _fetch_tree_entries(self, repo_owner: str, repo_name: str, tree_ref: str, recursive: bool = False) -> list:
        """Fetch tree entries for a repository tree ref or branch name."""

        url = self._GITHUB_API_BASE + "/repos/" + repo_owner + "/" + repo_name + "/git/trees/" + tree_ref
        if recursive:
            url = url + "?recursive=1"

        try:
            status_code, response_headers, body = self._http_get(url)
            if status_code != 200:
                raise OSError("GitHub API returned HTTP {} for {}".format(status_code, repo_name))

            payload = json.loads(body.decode("utf-8"))
            tree = payload.get("tree", [])
            if not isinstance(tree, list):
                raise ValueError("Unexpected tree payload")

            return tree
        except Exception as error:
            raise OSError("Failed to fetch tree for {}@{}: {}".format(repo_name, tree_ref, str(error)))

    def _iter_submodule_blob_entries(self, submodule_owner: str, submodule_repo: str, root_tree_sha: str) -> object:
        """Yield (path, sha) blob entries by iteratively walking submodule trees."""

        tree_stack: list = [("", root_tree_sha)]

        while tree_stack:
            base_path, tree_sha = tree_stack.pop()
            tree_entries = self._fetch_tree_entries(submodule_owner, submodule_repo, tree_sha, recursive=False)

            for entry in tree_entries:
                entry_type: str = entry.get("type", "")
                entry_name: str = entry.get("path", "")
                entry_sha: str = entry.get("sha", "")
                if not entry_name or not entry_sha:
                    continue

                if base_path:
                    full_path = base_path + "/" + entry_name
                else:
                    full_path = entry_name

                if entry_type == "blob":
                    yield full_path, entry_sha
                elif entry_type == "tree":
                    tree_stack.append((full_path, entry_sha))

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
            request_bytes = request.encode("utf-8")
            try:
                sock.write(request_bytes)
            except AttributeError:
                sock.send(request_bytes)

            try:
                stream = sock.makefile("rb")
            except AttributeError:
                stream = OTAUpdater._SocketStreamFallback(sock)
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

        # Exclude all .md files (documentation)
        if path.endswith(".md"):
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

        # Check exact prefix matches (e.g., "lib/" in prefixes matches "lib/*")
        for prefix in self.tracked_root_prefixes:
            if path.startswith(prefix):
                return True

        # Also track submodule directory names (e.g., "lib" if "lib/" is in prefixes)
        for prefix in self.tracked_root_prefixes:
            if prefix.endswith("/"):
                dir_name = prefix.rstrip("/")
                if path == dir_name:
                    return True

        return False

    def _should_descend_into_path(self, path: str) -> bool:
        """Return True when a directory path should be traversed."""

        normalized_path = path.rstrip("/")
        if not normalized_path:
            return True

        if normalized_path in self.excluded_paths:
            return False

        for excluded_prefix in self.excluded_path_prefixes:
            normalized_excluded = excluded_prefix.rstrip("/")
            if normalized_path == normalized_excluded or normalized_path.startswith(normalized_excluded + "/"):
                return False

        if not self.tracked_root_prefixes:
            return True

        for tracked_prefix in self.tracked_root_prefixes:
            normalized_tracked = tracked_prefix.rstrip("/")
            if (
                normalized_path == normalized_tracked
                or normalized_path.startswith(normalized_tracked + "/")
                or normalized_tracked.startswith(normalized_path + "/")
            ):
                return True

        return False

    def _list_local_files(self, relative_dir: str) -> list:
        """List local tracked files using an iterative directory walk."""

        initial_dir = relative_dir if relative_dir else "."
        pending_dirs: list = [initial_dir]
        results: list = []

        while pending_dirs:
            current_dir = pending_dirs.pop()
            try:
                entries = os.listdir(current_dir)
            except OSError:
                continue

            for entry in entries:
                if entry in self.excluded_local_dirs:
                    continue

                if current_dir == ".":
                    relative_path = entry
                else:
                    relative_path = current_dir + "/" + entry

                try:
                    stat_info = os.stat(relative_path)
                    mode = stat_info[0]
                except OSError:
                    continue

                is_directory = bool(mode & 0x4000)
                if is_directory:
                    pending_dirs.append(relative_path)
                    continue

                normalized_path = relative_path.replace("\\", "/")
                if self._should_track_path(normalized_path):
                    results.append(normalized_path)

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
        """Compute git-compatible blob SHA-1 for a local file.

        Normalizes line endings (CRLF -> LF) for text files to match repo
        storage format, accounting for git's autocrlf setting on Windows.
        Binary files are not modified.
        """

        with open(path, "rb") as file_handle:
            data = file_handle.read()

        # Only normalize line endings for text files
        text_extensions = (
            ".py",
            ".html",
            ".txt",
            ".json",
            ".css",
            ".js",
            ".md",
            ".sh",
            ".ps1",
            ".xml",
            ".yml",
            ".yaml",
        )
        text_filenames = (
            ".gitignore",
            ".gitattributes",
            ".editorconfig",
        )
        filename = path.rsplit("/", 1)[-1].lower()

        if path.lower().endswith(text_extensions) or filename in text_filenames:
            # Normalize CRLF to LF to match repo storage format
            data = data.replace(b"\r\n", b"\n")

        return OTAUpdater._git_blob_sha(data)

    @staticmethod
    def _git_blob_sha(data: bytes) -> str:
        """Return SHA-1 in the same format git uses for blob objects."""

        header = "blob {}\0".format(len(data)).encode("utf-8")
        raw = hashlib.sha1(header + data).digest()
        return "".join("{:02x}".format(b) for b in raw)

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
