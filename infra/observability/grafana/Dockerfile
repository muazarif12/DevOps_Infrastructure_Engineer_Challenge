# Bakes the datasource/dashboard provisioning + the Overview dashboard JSON into the stock
# Grafana image. Admin credentials are set as Railway env vars (GF_SECURITY_ADMIN_USER/
# GF_SECURITY_ADMIN_PASSWORD), not baked in here. Build context is the repo root.
FROM grafana/grafana:latest
COPY infra/observability/grafana/provisioning /etc/grafana/provisioning
COPY infra/observability/grafana/dashboards /var/lib/grafana/dashboards
