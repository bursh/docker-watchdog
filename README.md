# Central Docker Compose Watchdog

Linux, systemd, Python 3.8+, Docker Compose V2, local Docker daemon.

Add labels to exactly one HTTP service per project:

```yaml
services:
  nginx:
    ports:
      - "8085:80"
    labels:
      watchdog.enabled: "true"
      watchdog.path: "/health"
      watchdog.scheme: "http"
      # watchdog.host: "example.com"
```

Use `/` if no health endpoint exists. Apply the labels with your normal `docker compose up -d` deployment (using the original project options).

Install from this directory:

```sh
sudo install -d -m 755 /usr/local/lib/docker-watchdog
sudo install -m 644 watchdog.py /usr/local/lib/docker-watchdog/watchdog.py
sudo install -m 644 docker-watchdog.service /etc/systemd/system/docker-watchdog.service
sudo systemctl daemon-reload
sudo systemctl enable --now docker-watchdog
sudo journalctl -u docker-watchdog -f
```

Checks are scheduled every 60 seconds, with independent project counters and up to 32 workers. Three consecutive failures cause `docker compose restart` using the discovered project name, working directory and compose files. A 120-second cooldown follows each attempt. Maximum three restart attempts per rolling hour per project, in memory. HTTP 2xx/3xx count as success; redirects are not followed. Only published TCP ports of labeled services are considered. IPv4/IPv6 bindings of the same port are supported. HTTPS validates certificates against the IP address; HTTP is recommended behind an ALB terminating TLS.

Limitations: counters reset on daemon restart or disappearance of all labeled containers. Removed containers are not recreated. Custom Compose profiles, environment files and deployment-time variables cannot reliably be reconstructed from labels; ensure the deployment is reproducible under systemd before enabling. Docker errors skip discovery rather than causing mass restarts. Explicitly stopped labeled containers are monitored and may be started by restart. Stop watchdog during planned maintenance or remove the opt-in label by redeploying. Only trusted administrators should control Docker labels and compose files, because the watchdog runs as root. No real Docker integration test was performed; unit tests mock Docker and HTTP.

Run tests: `python3 -m unittest -v`.
