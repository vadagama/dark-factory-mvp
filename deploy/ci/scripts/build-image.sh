#!/bin/sh
# Local deterministic build of the factory OCI image (T033, docs T-044).
#
# Complements .github/workflows/factory-image.yml (the authoritative trusted
# build): lets a human rebuild the same SHA locally and run the
# reproducibility check before pushing.
#
# Usage:
#   deploy/ci/scripts/build-image.sh [--push] [--platform <p>] [--rebuild-check]
#
# Defaults: single-platform build for the host architecture, loaded into the
# local docker daemon as ghcr.io/vadagama/dark-factory:sha-<git-sha>.
#
# Reproducibility contract (see deploy/ci/image/Dockerfile):
#   - the working tree must be clean; mtimes are normalized to the HEAD
#     commit timestamp (BUILD context mtimes are part of the digest);
#   - SOURCE_DATE_EPOCH = HEAD commit timestamp normalizes image config
#     timestamps;
#   - --rebuild-check builds twice (--no-cache on the second run) and
#     compares image IDs. Note: the local check is single-arch and compares
#     the docker image config ID; the full multi-arch manifest comparison
#     runs in CI (workflow_dispatch with rebuild-check=true).
set -eu

IMAGE=ghcr.io/vadagama/dark-factory
SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH='' cd -- "$SCRIPT_DIR/../../.." && pwd)

PUSH=0
PLATFORM=""
REBUILD_CHECK=0

while [ $# -gt 0 ]; do
    case "$1" in
        --push) PUSH=1 ;;
        --platform) [ $# -ge 2 ] || { echo "error: --platform needs a value" >&2; exit 2; }; PLATFORM=$2; shift ;;
        --rebuild-check) REBUILD_CHECK=1 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

command -v docker >/dev/null 2>&1 || { echo "error: docker is not available" >&2; exit 1; }
command -v git >/dev/null 2>&1 || { echo "error: git is not available" >&2; exit 1; }

cd "$REPO_ROOT"

# A dirty tree inside the build context changes the build inputs silently —
# refuse to build. Changes outside the context (dockerignore whitelist) do
# not affect the digest and are not our business here.
#
# The paths are passed as a POSIX positional list, not as a bash array: the
# script declares #!/bin/sh, and on Debian/Ubuntu /bin/sh is dash, where
# `name=(...)` is a syntax error. The option loop above shifts on every
# iteration, so it has consumed all arguments and $@ is free to reuse here.
set -- \
    alembic.ini \
    migrations \
    src \
    pyproject.toml \
    uv.lock \
    README.md \
    deploy/ci/image
dirty=$(git status --porcelain -- "$@")
if [ -n "$dirty" ]; then
    echo "error: files inside the build context have uncommitted changes:" >&2
    echo "$dirty" >&2
    exit 1
fi

SHA=$(git rev-parse HEAD)
EPOCH=$(git log -1 --format=%ct HEAD)
TAG=sha-$SHA

# COPY layers preserve context mtimes: normalize the whole tree to the commit
# timestamp (the CI workflow does the same before its build). python3 does it
# portably: BSD touch (macOS) does not understand GNU's `-d @epoch`.
python3 - "$EPOCH" <<'PY'
import os
import sys

epoch = int(sys.argv[1])

for root, dirs, files in os.walk("."):
    dirs[:] = [d for d in dirs if d != ".git"]
    for name in dirs + files:
        path = os.path.join(root, name)
        try:
            os.utime(path, (epoch, epoch), follow_symlinks=False)
        except OSError as error:
            raise SystemExit(f"cannot normalize mtime of {path}: {error}")
PY

if [ -z "$PLATFORM" ]; then
    PLATFORM=$(docker version --format '{{.Server.Arch}}' | sed 's/^arm64$/linux\/arm64/; s/^x86_64$/linux\/amd64/')
fi

build_args="--build-arg SOURCE_DATE_EPOCH=$EPOCH --build-arg GIT_SHA=$SHA"

echo "building $IMAGE:$TAG ($PLATFORM) with SOURCE_DATE_EPOCH=$EPOCH"

# shellcheck disable=SC2086
docker buildx build \
    --file deploy/ci/image/Dockerfile \
    --platform "$PLATFORM" \
    --provenance=false --sbom=false \
    --tag "$IMAGE:$TAG" \
    $build_args \
    .

image_id=$(docker image inspect --format '{{.Id}}' "$IMAGE:$TAG")
echo "built $IMAGE:$TAG id=$image_id"

if [ "$REBUILD_CHECK" -eq 1 ]; then
    echo "rebuild check: second cold build (--no-cache)"
    # shellcheck disable=SC2086
    docker buildx build \
        --file deploy/ci/image/Dockerfile \
        --platform "$PLATFORM" \
        --provenance=false --sbom=false \
        --no-cache \
        --tag "$IMAGE:$TAG" \
        $build_args \
        .

    image_id_2=$(docker image inspect --format '{{.Id}}' "$IMAGE:$TAG")
    echo "rebuild id=$image_id_2"
    if [ "$image_id" != "$image_id_2" ]; then
        echo "error: reproducibility deviation: same SHA produced different image IDs: $image_id != $image_id_2" >&2
        exit 1
    fi
    echo "rebuild check passed: image IDs match"
fi

if [ "$PUSH" -eq 1 ]; then
    echo "pushing $IMAGE:$TAG"
    docker push "$IMAGE:$TAG"
fi
