# Bakes infra/observability's config into the stock Prometheus image — Railway builds from a
# Dockerfile, not arbitrary host volume mounts, so this is how the config actually gets in.
# Build context is the repo root (Railway's default), hence the paths below.
FROM prom/prometheus:latest
COPY infra/observability/prometheus.yml /etc/prometheus/prometheus.yml
COPY infra/observability/alert_rules.yml /etc/prometheus/alert_rules.yml
# No CMD override: the base image's own entrypoint already points at
# /etc/prometheus/prometheus.yml and listens on :9090.
