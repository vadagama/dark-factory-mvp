# Trusted factory OCI image (T033, docs plan T-044; ADR-006/ADR-010/ADR-019).
#
# One image, two roles:
#   - agent pod (deploy/ci/manifests/agent-job-template.yaml): git + `factory`
#     CLI with all runtime dependencies, runs as uid/gid 65532 on a read-only
#     root filesystem with writable /tmp and /workspace only;
#   - factory API + migrations job (charts/dark-factory): `factory api serve`
#     and `alembic upgrade head` (alembic.ini + migrations/ live in /app).
#
# Reproducible-build contract (DoD T033: rebuilding the same SHA yields the
# same digest; deviations are documented in deploy/ci/README.md):
#   - both stages pin the same multi-arch base index by digest;
#   - ARG SOURCE_DATE_EPOCH makes BuildKit normalize the image config
#     `created`/history timestamps (verified: two cold builds match);
#   - every RUN that writes files ends by normalizing mtimes it created —
#     BuildKit does NOT normalize RUN-produced file mtimes by itself;
#   - RUN layers stay byte-identical: apt/dpkg wall-clock logs (/var/log) and
#     the ldconfig aux-cache (embeds library load addresses) are deleted, and
#     uv's timestamped uv_cache.json is dropped from the venv together with
#     its RECORD entry — mtime normalization cannot fix file CONTENTS;
#   - the runtime tree is assembled in the builder and shipped with ONE COPY
#     into a non-existing /app: the root entry then carries the source
#     (SDE-normalized) mtime, while several COPY steps into a pre-existing
#     /app re-stamp the parent directory entry with the wall-clock time of
#     each copy — verified experimentally; other COPY layers are
#     deterministic because the build context mtimes are normalized before
#     the build (workflow step / deploy/ci/scripts/build-image.sh);
#   - dependencies come only from uv.lock (uv sync --frozen) and the build
#     backend is pinned via build-constraints.txt (UV_BUILD_CONSTRAINT);
#   - provenance/SBOM attestations are disabled at build time (they embed
#     invocation parameters and break digest stability); SBOM is produced as a
#     CI artifact by the factory-image workflow instead;
#   - the runtime OS packages are refreshed from a fixed snapshot.debian.org
#     date (`apt-get upgrade`, plus `git` itself) so their versions do not
#     drift. The date is deliberately NOT the base image's own build date: it
#     was moved forward to 20260906T000000Z so the snapshot carries the security
#     updates the fail-closed trivy gate demands — libpcre2-8-0
#     10.42-1+deb12u1 and libssh2-1 1.10.0-3+deb12u1 — which the base image
#     predates. Upgrading from ONE frozen snapshot keeps the layer
#     deterministic; naming packages one by one does not scale (patching only
#     libpcre2-8-0 left libssh2-1 behind). The deviation, and the requests that
#     confirm the snapshot serves the fixed packages, are in deploy/ci/README.md.
# Do NOT touch the pinned digests or the snapshot date casually: each bump is
# a conscious change and produces a new digest for the same SHA.

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254 AS builder

ARG SOURCE_DATE_EPOCH
# Stable per build input (OCI label), never a wall-clock timestamp.
ARG GIT_SHA

# uv pinned by digest (multi-arch index; see deploy/ci/README.md).
COPY --from=ghcr.io/astral-sh/uv:0.11.19@sha256:b46b03ddfcfbf8f547af7e9eaefdf8a39c8cebcba7c98858d3162bd28cf536f6 /uv /usr/local/bin/uv

# Deterministic dependency resolution: lockfile only, no bytecode (a .pyc
# embeds the source mtime and would break reproducibility; the runtime sets
# PYTHONDONTWRITEBYTECODE=1 instead), and build constraints pin the wheel
# builder so the project wheel cannot drift with hatchling releases. uv also
# records its cache state in uv_cache.json inside freshly built dist-info
# directories with a wall-clock timestamp in the file CONTENTS — the file and
# its RECORD entry are dropped in every sync layer below (mtime normalization
# cannot fix file contents).
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_BUILD_CONSTRAINT=/app/build-constraints.txt
WORKDIR /app

COPY deploy/ci/image/build-constraints.txt /app/build-constraints.txt
COPY pyproject.toml uv.lock README.md ./
# Cache mount keeps downloads out of the image layers; only /app is committed
# to a layer (with normalized mtimes at the end of each RUN).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
    && find /app -name uv_cache.json -delete \
    && find /app -name RECORD -exec sed -i '/uv_cache[.]json/d' {} + \
    && find /app -exec touch -h -d @$SOURCE_DATE_EPOCH {} +

COPY src/ /app/src/
# --no-editable: the project is installed as a built wheel (hatchling pinned
# through UV_BUILD_CONSTRAINT), so the runtime stage needs no source tree.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
    && find /app -name uv_cache.json -delete \
    && find /app -name RECORD -exec sed -i '/uv_cache[.]json/d' {} + \
    && find /app -exec touch -h -d @$SOURCE_DATE_EPOCH {} +

# Alembic contract of the chart migrations job: alembic.ini (script_location =
# migrations) plus the migrations tree in WORKDIR, `alembic upgrade head` runs
# from /app.
COPY alembic.ini /app/alembic.ini
COPY migrations/ /app/migrations/
# Assemble the runtime tree (/out) out of the build tree: the runtime stage
# ships it with ONE COPY (see the contract block above), excluding the build
# files (pyproject.toml, uv.lock, README.md, build-constraints.txt).
RUN mkdir /out \
    && cp -a /app/.venv /out/.venv \
    && cp /app/alembic.ini /out/alembic.ini \
    && cp -a /app/migrations /out/migrations \
    && find /out -exec touch -h -d @$SOURCE_DATE_EPOCH {} +

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG SOURCE_DATE_EPOCH
ARG GIT_SHA

# git for the agent role (anonymous HTTPS clone, public pilot repository) and
# runtime libs it needs; versions come from the fixed snapshot date, not from
# a moving mirror. ca-certificates ships with the slim base image. The upgrade
# below lifts the base image's OS packages to that same snapshot — see the
# header and deploy/ci/README.md.
RUN set -eux; \
    sed -i \
      -e "s|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/20260906T000000Z|g" \
      -e "s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/20260906T000000Z|g" \
      /etc/apt/sources.list.d/debian.sources; \
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99snapshot; \
    apt-get update; \
    # The snapshot is frozen, so upgrading the whole OS layer is deterministic
    # and patches every package the base image ships stale — which is what the
    # trivy gate measures. A per-package list cannot keep up with new advisories.
    apt-get upgrade -y; \
    apt-get install --no-install-recommends -y git; \
    rm -rf /var/lib/apt/lists/*; \
    # apt/dpkg embed wall-clock timestamps INSIDE log file contents (dpkg.log,
    # apt history/term) — mtime normalization does not help; the logs are
    # runtime noise, so delete them for a deterministic layer.
    find /var/log -type f -delete; \
    # ldconfig's aux-cache embeds library load addresses; delete it (it is
    # regenerated on demand; /etc/ld.so.cache is deterministic).
    rm -f /var/cache/ldconfig/aux-cache; \
    find / -path /sys -prune -o -path /proc -prune -o -path /dev -prune \
      -o -path /etc/hosts -prune -o -path /etc/resolv.conf -prune \
      -o -path /etc/hostname -prune \
      -o -exec touch -h -d @$SOURCE_DATE_EPOCH {} +

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

# Single COPY of the assembled runtime tree: the /app root entry carries the
# builder's SDE-normalized mtime. A pre-existing /app (e.g. via an earlier
# WORKDIR) would be re-stamped by the copy with wall-clock time.
COPY --from=builder /out /app
WORKDIR /app

LABEL org.opencontainers.image.title="dark-factory" \
      org.opencontainers.image.description="Software Dark Factory MVP: factory CLI, API and migrations image (T033)" \
      org.opencontainers.image.source="https://github.com/vadagama/dark-factory-mvp" \
      org.opencontainers.image.revision="${GIT_SHA}"

# Same uid/gid as the agent-Job securityContext (T030) and the chart API pod
# (T031): non-root, read-only root filesystem, writable /tmp (HOME) only.
USER 65532:65532

# Real entrypoints are set by the callers: `factory api serve` (chart), the
# agent-Job command (deploy/ci), `alembic upgrade head` (migrations job).
CMD ["factory", "--help"]
