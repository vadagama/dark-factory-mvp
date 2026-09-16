# Console image (T036, ADR-021 p.7): nginx serves the built SPA (dist/) and
# proxies /api/ to the API Service inside the cluster.
#
# Base image pinned by digest (nginx-unprivileged 1.29.4, multi-arch index;
# verified via `docker buildx imagetools inspect`). Unlike the factory image
# (ADR-013), this Dockerfile has no reproducible-build requirement, so only
# the base is pinned; dist/ comes from the console-image workflow (Node 22,
# npm ci) and must stay in the build context (no .dockerignore on purpose).
FROM nginxinc/nginx-unprivileged:1.29.4@sha256:c38007e929d1d368ea26df1b696bfc5655e274db11f755fad63c25950a157423

# Upstream of the /api/ proxy. Overridden by the chart with the in-cluster
# Service DNS name; defaults work for a plain `docker run` with a local API.
ENV API_UPSTREAM=host.docker.internal:8000

# OS security updates for the pinned base, for the fail-closed trivy gate of
# console-image.yml (--ignore-unfixed --severity HIGH,CRITICAL). The digest above
# really is the newest upstream build of 1.29.4, but Debian trixie has since
# published fixes for packages that build ships (openssl/libssl3t64,
# libgnutls30t64, util-linux/mount, curl, libexpat1, libsqlite3-0, libssh2-1t64,
# ...), so the patched versions are installed here from the base image's own
# trixie/trixie-security sources. `upgrade` suffices — every fix is a version
# bump of a package already present, so no new dependency has to be pulled in.
# Build-time only: USER 101 below restores the base image's runtime user, so
# nginx still serves on 8080 as UID 101 with the same writable paths. Unlike the
# factory image, this image has no reproducible-build requirement (ADR-021
# p.2/p.7), so the layer is not snapshot-pinned.
USER root
RUN apt-get update \
    && apt-get upgrade -y \
    && rm -rf /var/lib/apt/lists/*

# The base image's non-root runtime user (uid/gid 101).
USER 101

# The stock nginx entrypoint envsubst's every template in /etc/nginx/templates
# into /etc/nginx/conf.d. Only defined env vars are substituted, so nginx
# runtime variables ($uri, $host) survive. No trailing slash on proxy_pass —
# the /api prefix is preserved and the API serves /api/v1/*.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY dist/ /usr/share/nginx/html/

# nginx-unprivileged listens on 8080 as UID 101 by default; writable paths
# are provided by the chart (emptyDir on /tmp and /var/cache/nginx, plus
# /etc/nginx/conf.d for the entrypoint's envsubst render above) because the
# container runs with readOnlyRootFilesystem.
EXPOSE 8080
