# Security policy

The current development line is 1.2.x; there is no guaranteed support SLA. Report suspected vulnerabilities privately using GitHub's private vulnerability reporting if enabled. If unavailable, open an issue requesting a private contact without exploit details or secrets. Do not disclose sensitive details publicly before coordination.

Docker Watchdog executes Docker Compose with root-equivalent access. Restrict control of the Docker daemon, Compose files, labels, configuration and hooks to trusted administrators. Protect state and configuration files; keep metrics on loopback and use trusted HTTPS webhook destinations. Do not use this tool with untrusted multi-tenant workloads. Pause before maintenance. Validate upgrades and recovery modes on isolated hosts. The release history and tests do not constitute a security audit.
