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
#   - COPY layers are deterministic because the build context mtimes are
#     normalized before the build (workflow step / deploy/ci/scripts/build-image.sh);
#   - dependencies come only from uv.lock (uv sync --frozen) and the build
#     backend is pinned via build-constraints.txt (UV_BUILD_CONSTRAINT);
#   - provenance/SBOM attestations are disabled at build time (they embed
#     invocation parameters and break digest stability); SBOM is produced as a
#     CI artifact by the factory-image workflow instead;
#   - git is installed from a fixed snapshot.debian.org date (same date the
#     base image was built against) so package versions do not drift.
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
# directories with a wall-clock timestamp in the file CONTENTS — deleted in
# every sync layer below (mtime normalization cannot fix file contents).
ENV UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=0 \
    UV_LINK_MODE=copy \
    UV_BUILD_CONSTRAINT=/app/build-constraints.txt
WORKDIR /app

COPY deploy/ci/image/build-constraints.txt /app/build-constraints.txt
COPY pyproject.toml uv.lock README.md ./
# Cache mount keeps downloads out of the image layers; only /app/.venv is
# committed to a layer (with normalized mtimes at the end of each RUN).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
    && find /app -name uv_cache.json -delete \
    && find /app -exec touch -h -d @$SOURCE_DATE_EPOCH {} +

COPY src/ /app/src/
# --no-editable: the project is installed as a built wheel (hatchling pinned
# through UV_BUILD_CONSTRAINT), so the runtime stage needs no source tree.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable \
    && find /app -name uv_cache.json -delete \
    && find /app -exec touch -h -d @$SOURCE_DATE_EPOCH {} +

FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ARG SOURCE_DATE_EPOCH
ARG GIT_SHA

# git for the agent role (anonymous HTTPS clone, public pilot repository) and
# runtime libs it needs; versions come from the fixed snapshot date, not from
# a moving mirror. ca-certificates ships with the slim base image.
RUN set -eux; \
    sed -i \
      -e "s|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/20260824T000000Z|g" \
      -e "s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/20260824T000000Z|g" \
      /etc/apt/sources.list.d/debian.sources; \
    echo 'Acquire::Check-Valid-Until "false";' > /etc/apt/apt.conf.d/99snapshot; \
    apt-get update; \
    apt-get install --no-install-recommends -y git; \
    rm -rf /var/lib/apt/lists/*; \
    # apt/dpkg embed wall-clock timestamps INSIDE log file contents (dpkg.log,
    # apt history/term) — mtime normalization does not help; the logs are
    # runtime noise, so delete them for a deterministic layer.
    find /var/log -type f -delete; \
    find / -path /sys -prune -o -path /proc -prune -o -path /dev -prune \
      -o -path /etc/hosts -prune -o -path /etc/resolv.conf -prune \
      -o -path /etc/hostname -prune \
      -o -exec touch -h -d @$SOURCE_DATE_EPOCH {} +

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
# Alembic contract of the chart migrations job: alembic.ini (script_location =
# migrations) plus the migrations tree in WORKDIR, `alembic upgrade head` runs
# from /app.
COPY alembic.ini /app/alembic.ini
COPY migrations/ /app/migrations/

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
