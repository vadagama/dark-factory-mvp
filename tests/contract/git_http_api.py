"""In-process git smart-HTTP emulator for the contract suite (T068, ADR-031 p.6).

Serves real repositories over the git smart-HTTP protocol from a throwaway project
root: a ``ThreadingHTTPServer`` on a random loopback port enforces the installation
token as HTTP Basic (``base64("x-access-token:<token>")``), then hands the request to
``git http-backend`` (``GIT_PROJECT_ROOT`` = the temp root, ``GIT_HTTP_EXPORT_ALL=1``)
and returns the CGI response verbatim. Real ``git`` does the work — no network beyond
loopback, no credential anywhere but the request header — so ``ProviderClone`` is
exercised against the protocol a provider speaks, not against a mock of it.

The states a test can seed are the vocabulary ``RepositoryProvisioningPort.validate``
distinguishes (ADR-031 p.4): ``unavailable`` (no repository at all), ``empty`` (a bare
repository with an unborn HEAD), ``baseline_absent`` (a commit without
``.factory/product``) and ``baseline_current`` (a commit with it). ``seed`` returns the
head sha, or ``None`` for a state that has no revision.

``require_auth=False`` serves the same repositories without a token. A test that only
checks where a mirror lands needs it: the execution adapter fetches a mirror's
``origin`` without credentials by design (ADR-009), so the remote it can pull from is a
credential-free one. The authenticated path stays covered by the default mode.
"""

import base64
import http.server
import os
import shutil
import subprocess
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from dark_factory.changes.refs import RepositoryRef

GIT_INSTALLATION_TOKEN: Final[str] = "gh-test-installation-token"
"""The token the emulator expects as HTTP Basic, and the one a client should send."""

GIT_USERNAME: Final[str] = "x-access-token"
"""GitHub's conventional username for an installation token over git smart HTTP."""

DEFAULT_BRANCH: Final[str] = "main"
"""Branch every seeded repository points HEAD at (born or unborn)."""

HOST: Final[str] = "127.0.0.1"
"""Loopback interface the emulator binds: a test server must not be reachable off-host."""

_COMMIT_NAME: Final[str] = "Dark Factory"
_COMMIT_EMAIL: Final[str] = "factory@example.com"

_STATE_FILES: Final[Mapping[str, Mapping[str, bytes]]] = {
    "baseline_absent": {"docs/note.md": b"note\n"},
    "baseline_current": {
        "docs/note.md": b"note\n",
        ".factory/product/product.yaml": b"name: pilot\n",
    },
}
"""Tree of each state that has commits; ``.factory/product`` is the ADR-020 baseline."""


def _run_git(*argv: str, cwd: Path | None = None, check: bool = True) -> str:
    """Run one git command with an inline identity, so no global config is required."""
    process = subprocess.run(
        (
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            f"user.name={_COMMIT_NAME}",
            "-c",
            f"user.email={_COMMIT_EMAIL}",
            *argv,
        ),
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )
    return process.stdout


class _GitHttpServer(http.server.ThreadingHTTPServer):
    """The emulator's HTTP server, carrying the project root and the expected token."""

    daemon_threads = True
    project_root: Path
    token: str | None


class _GitHttpHandler(http.server.BaseHTTPRequestHandler):
    """Authenticates the request, then proxies it to ``git http-backend``."""

    protocol_version = "HTTP/1.1"
    server: _GitHttpServer

    def do_GET(self) -> None:
        self._serve()

    def do_POST(self) -> None:
        self._serve()

    def log_message(self, format: str, *args: object) -> None:
        """Silence per-request logging: the emulator must not noise up test output."""

    def _serve(self) -> None:
        if not self._authorized():
            self._send(
                401,
                {"WWW-Authenticate": f'Basic realm="{GIT_USERNAME}"', "Content-Type": "text/plain"},
                b"authentication required\n",
            )
            return
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        status, headers, payload = _http_backend(
            self.server.project_root,
            method=self.command,
            path=self.path,
            content_type=self.headers.get("Content-Type") or "",
            body=body,
            protocol=self.headers.get("Git-Protocol"),
        )
        self._send(status, headers, payload)

    def _authorized(self) -> bool:
        token = self.server.token
        if token is None:
            return True
        # git emits the extraheader value verbatim, so the scheme it sends is
        # whatever the configuration spelled; HTTP schemes are case-insensitive.
        scheme, _, credentials = (self.headers.get("Authorization") or "").partition(" ")
        if scheme.lower() != "basic":
            return False
        expected = base64.b64encode(f"{GIT_USERNAME}:{token}".encode()).decode("ascii")
        return credentials == expected

    def _send(self, status: int, headers: Mapping[str, str], payload: bytes) -> None:
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)


def _http_backend(
    project_root: Path,
    *,
    method: str,
    path: str,
    content_type: str,
    body: bytes,
    protocol: str | None,
) -> tuple[int, dict[str, str], bytes]:
    """Run one CGI request through ``git http-backend`` and parse its response."""
    path_info, _, query = path.partition("?")
    env = dict(os.environ)
    env.update(
        {
            "GIT_PROJECT_ROOT": str(project_root),
            "GIT_HTTP_EXPORT_ALL": "1",
            "PATH_INFO": path_info,
            "QUERY_STRING": query,
            "REQUEST_METHOD": method,
            "CONTENT_TYPE": content_type,
            "CONTENT_LENGTH": str(len(body)),
            "REMOTE_USER": GIT_USERNAME,
        }
    )
    if protocol:
        # The web server maps the Git-Protocol request header onto this variable;
        # http-backend then negotiates protocol v2 instead of falling back to v0.
        env["GIT_PROTOCOL"] = protocol
    process = subprocess.run(("git", "http-backend"), input=body, capture_output=True, env=env)
    return _parse_cgi(process.stdout)


def _parse_cgi(output: bytes) -> tuple[int, dict[str, str], bytes]:
    """Split a CGI response into ``(status, headers, body)`` (``Status:`` is optional)."""
    head, separator, payload = output.partition(b"\r\n\r\n")
    if not separator:
        head, separator, payload = output.partition(b"\n\n")
    status = 200
    headers: dict[str, str] = {}
    for line in head.splitlines():
        name, _, value = line.partition(b":")
        key = name.decode("ascii", errors="replace").strip()
        text = value.decode("utf-8", errors="replace").strip()
        if key.lower() == "status":
            status = int(text.split()[0])
            continue
        headers[key] = text
    return status, headers, payload


class GitHttpEmulator:
    """A loopback git server over a temporary project root of bare repositories."""

    def __init__(self, *, token: str = GIT_INSTALLATION_TOKEN, require_auth: bool = True) -> None:
        self._directory = tempfile.TemporaryDirectory(prefix="git-http-emulator-")
        self._project_root = Path(self._directory.name) / "repositories"
        self._project_root.mkdir(parents=True)
        self._server = _GitHttpServer((HOST, 0), _GitHttpHandler)
        self._server.project_root = self._project_root
        self._server.token = token if require_auth else None
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self.base_url = f"http://{HOST}:{self._server.server_port}"
        self.token = token
        """The credential a well-behaved client sends; ignored when ``require_auth`` is off."""

    # --- test-side seeds and addresses --------------------------------------

    def clone_url(self, repository: RepositoryRef) -> str:
        """The URL ``git clone`` addresses for ``repository`` (``<base>/<slug>.git``)."""
        return f"{self.base_url}/{repository.slug}.git"

    def seed(self, repository: RepositoryRef, state: str) -> str | None:
        """Materialise one repository state; returns its head sha (``None`` if it has none)."""
        bare = self._repository_path(repository)
        shutil.rmtree(bare, ignore_errors=True)
        work = bare.with_name(f"{bare.name}.work")
        shutil.rmtree(work, ignore_errors=True)
        if state == "unavailable":
            return None
        _run_git("init", "--quiet", "--bare", "-b", DEFAULT_BRANCH, str(bare))
        if state == "empty":
            return None
        _run_git("init", "--quiet", "-b", DEFAULT_BRANCH, str(work))
        for relative, content in _STATE_FILES[state].items():
            target = work / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        _run_git("add", "--all", cwd=work)
        _run_git("commit", "--quiet", "--message", "seed", cwd=work)
        revision = _run_git("rev-parse", "HEAD", cwd=work).strip()
        _run_git(
            "push",
            "--quiet",
            str(bare),
            f"refs/heads/{DEFAULT_BRANCH}:refs/heads/{DEFAULT_BRANCH}",
            cwd=work,
        )
        shutil.rmtree(work, ignore_errors=True)
        return revision

    def close(self) -> None:
        """Stop the server and delete the temporary project root."""
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)
        self._directory.cleanup()

    def _repository_path(self, repository: RepositoryRef) -> Path:
        return self._project_root / f"{repository.slug}.git"
