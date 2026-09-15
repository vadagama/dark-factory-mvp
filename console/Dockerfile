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
