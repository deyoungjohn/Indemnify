# DEPLOYMENT.md — Indemnify Protocol Production Deployment Guide

> **Target Audience:** Senior DevOps / Platform Engineers
> **Infrastructure:** Ubuntu 22.04 LTS (bare-metal or cloud VM)
> **Stack:** FastAPI + Uvicorn, Python 3.11+, Nginx, systemd
> **Network:** X Layer Mainnet (Chain ID 196)
> **Security Classification:** CONFIDENTIAL — INTERNAL OPERATIONS

---

## Table of Contents

1. [System Requirements](#1-system-requirements)
2. [Environment Setup](#2-environment-setup)
3. [Key Management & Security](#3-key-management--security)
4. [systemd Service Definitions](#4-systemd-service-definitions)
5. [Nginx Reverse Proxy & TLS Termination](#5-nginx-reverse-proxy--tls-termination)
6. [Firewall Configuration](#6-firewall-configuration)
7. [Health Checks & Monitoring](#7-health-checks--monitoring)
8. [Log Management](#8-log-management)
9. [Update & Rollback Procedure](#9-update--rollback-procedure)
10. [Incident Response Runbook](#10-incident-response-runbook)

---

## 1. System Requirements

| Resource         | Minimum         | Recommended (Production)       |
|------------------|-----------------|--------------------------------|
| CPU              | 4 vCPUs         | 8+ vCPUs                       |
| RAM              | 8 GB            | 32 GB                          |
| Disk             | 50 GB SSD       | 200 GB NVMe SSD                |
| OS               | Ubuntu 22.04 LTS| Ubuntu 22.04 LTS               |
| Python           | 3.11            | 3.11+                          |
| Network          | 100 Mbps        | 1 Gbps dedicated               |
| RPC Provider     | Public endpoint | Private dedicated node (Alchemy / bare-metal) |

> **LATENCY REQUIREMENT:** The Indemnify risk engine carries a strict
> internal SLA of ≤200ms for `/v1/risk/simulate`. A public, rate-limited
> RPC endpoint (e.g., `rpc.xlayer.tech`) will fail this SLA under load.
> **A premium, authenticated RPC provider is non-negotiable in production.**

---

## 2. Environment Setup

### 2.1 Create a Dedicated System User

Never run the Indemnify daemon as `root`. Create an unprivileged system
account with no login shell and no home directory world-access:

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin indemnify
sudo mkdir -p /opt/indemnify
sudo chown -R indemnify:indemnify /opt/indemnify
```

### 2.2 Clone the Repository

```bash
cd /opt/indemnify
sudo -u indemnify git clone https://github.com/your-org/indemnify.git .
```

### 2.3 Install Python Dependencies

```bash
sudo apt-get install -y python3.11 python3.11-venv python3-pip

sudo -u indemnify python3.11 -m venv /opt/indemnify/venv
sudo -u indemnify /opt/indemnify/venv/bin/pip install --upgrade pip
sudo -u indemnify /opt/indemnify/venv/bin/pip install -r daemon/requirements.txt
```

---

## 3. Key Management & Security

> **CRITICAL SECURITY REQUIREMENT**
>
> The `ORACLE_PRIVATE_KEY` is the cryptographic root of trust for the
> entire Indemnify settlement system. A compromised oracle key allows
> an attacker to forge arbitrary policy signatures, draining the
> UnderwriterPool of all capital reserves.
>
> **This key MUST be treated with the same operational security discipline
> as a hardware wallet seed phrase or a CA root certificate.**

### 3.1 Create the Secure Environment File

```bash
# Create the secrets file as root
sudo touch /opt/indemnify/.env
sudo chown indemnify:indemnify /opt/indemnify/.env

# CRITICAL: Restrict file permissions to owner-read only.
# No other system user or process may read this file.
sudo chmod 600 /opt/indemnify/.env
```

Verify the permissions are correct before proceeding:

```bash
ls -la /opt/indemnify/.env
# Expected output:
# -rw------- 1 indemnify indemnify ... /opt/indemnify/.env
```

### 3.2 Populate the Environment File

Edit the file **only** as the `indemnify` system user or as `root`:

```bash
sudo -u indemnify nano /opt/indemnify/.env
```

Populate with the following variables. **Never commit this file to
version control.** Ensure `.env` is listed in `.gitignore`.

```dotenv
# ============================================================
# Indemnify Daemon — Runtime Configuration
# ============================================================
# SECURITY: This file contains a live private key with signing
# authority over the ParametricEscrow.sol settlement contract.
# Permissions MUST remain: chmod 600. Owner: indemnify.
# ============================================================

# --- Oracle Signing Key (CRITICAL — KEEP SECRET) ---
# The private key of the EOA registered as the oracle signer
# in ParametricEscrow.sol. This key signs all policy quotes.
# Rotation requires an on-chain updateOracle() transaction.
ORACLE_PRIVATE_KEY=0xYOUR_ORACLE_PRIVATE_KEY_HERE

# --- Contract Addresses (X Layer Mainnet) ---
ESCROW_CONTRACT_ADDRESS=0xYOUR_PARAMETRIC_ESCROW_ADDRESS
POOL_CONTRACT_ADDRESS=0xYOUR_UNDERWRITER_POOL_ADDRESS

# --- RPC Configuration ---
# Use a premium, authenticated RPC endpoint for production.
# Public endpoints WILL fail the 200ms simulation SLA under load.
RPC_PROVIDER_URL=https://xlayer-mainnet.g.alchemy.com/v2/YOUR_API_KEY

# --- Network ---
CHAIN_ID=196

# --- Pricing ---
FIXED_UNDERWRITER_MARGIN=0.01
```

### 3.3 Key Rotation Procedure

When rotating the oracle key:

1. Generate a new EOA keypair in an air-gapped environment.
2. Call `ParametricEscrow.updateOracle(newOracleAddress)` on-chain from the
   contract owner address.
3. Update `/opt/indemnify/.env` with the new `ORACLE_PRIVATE_KEY`.
4. Restart both systemd services (see Section 9).
5. Verify the new oracle address is active on-chain.
6. Securely destroy the old private key.

---

## 4. systemd Service Definitions

Two independent systemd services manage the Indemnify runtime:

| Service                   | Role                                              | Port   |
|---------------------------|---------------------------------------------------|--------|
| `indemnify-api.service`   | FastAPI/Uvicorn HTTP server (risk engine + quotes)| 8000   |
| `indemnify-oracle.service`| Oracle listener & automated policy settlement     | N/A    |

Both services are configured to restart automatically on failure with an
exponential backoff ceiling, and to start on system boot.

### 4.1 FastAPI Server — `indemnify-api.service`

Create the service file:

```bash
sudo nano /etc/systemd/system/indemnify-api.service
```

```ini
# /etc/systemd/system/indemnify-api.service
# ===========================================
# Indemnify — FastAPI Risk Engine & Quote API
# ===========================================
# Manages the uvicorn ASGI server that exposes:
#   /v1/risk/simulate    — EVM dry-run threat matrix
#   /v1/insurance/quote  — Oracle-signed premium quotes
#   (MCP stdio mode available via --stdio flag)
# ===========================================

[Unit]
Description=Indemnify FastAPI Risk Engine (indemnify-api)
Documentation=https://github.com/your-org/indemnify
After=network-online.target
Wants=network-online.target
# Ensure the oracle listener starts after the API is healthy
Before=indemnify-oracle.service

[Service]
# --- Identity & Isolation ---
Type=exec
User=indemnify
Group=indemnify
WorkingDirectory=/opt/indemnify

# --- Environment ---
EnvironmentFile=/opt/indemnify/.env
Environment="PYTHONPATH=/opt/indemnify"
Environment="PYTHONUNBUFFERED=1"

# --- Execution ---
ExecStart=/opt/indemnify/venv/bin/uvicorn \
    daemon.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 4 \
    --loop uvloop \
    --log-level info \
    --access-log \
    --no-use-colors

# --- Lifecycle ---
ExecReload=/bin/kill -HUP $MAINPID
KillMode=mixed
KillSignal=SIGTERM
TimeoutStartSec=30
TimeoutStopSec=30
Restart=on-failure
RestartSec=5
StartLimitBurst=5
StartLimitIntervalSec=60

# --- Logging ---
StandardOutput=journal
StandardError=journal
SyslogIdentifier=indemnify-api

# --- Security Hardening ---
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/indemnify
CapabilityBoundingSet=
AmbientCapabilities=
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
MemoryDenyWriteExecute=true
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM

[Install]
WantedBy=multi-user.target
```

### 4.2 Oracle Listener — `indemnify-oracle.service`

```bash
sudo nano /etc/systemd/system/indemnify-oracle.service
```

```ini
# /etc/systemd/system/indemnify-oracle.service
# ==============================================
# Indemnify — Oracle Listener & Settlement Engine
# ==============================================
# Manages the event-driven oracle daemon that:
#   - Polls PolicyCreated events from ParametricEscrow.sol
#   - Submits settlement proofs for Tier 0 (success) and
#     Tier 3 (revert) outcomes automatically
#   - Handles partial cashout eligibility for stalled policies
# ==============================================

[Unit]
Description=Indemnify Oracle Listener & Settlement Engine (indemnify-oracle)
Documentation=https://github.com/your-org/indemnify
After=network-online.target indemnify-api.service
Wants=network-online.target

[Service]
# --- Identity & Isolation ---
Type=exec
User=indemnify
Group=indemnify
WorkingDirectory=/opt/indemnify

# --- Environment ---
EnvironmentFile=/opt/indemnify/.env
Environment="PYTHONPATH=/opt/indemnify"
Environment="PYTHONUNBUFFERED=1"

# --- Execution ---
ExecStart=/opt/indemnify/venv/bin/python \
    -m daemon.oracle_listener

# --- Lifecycle ---
KillMode=mixed
KillSignal=SIGTERM
TimeoutStartSec=30
TimeoutStopSec=60
Restart=on-failure
RestartSec=10
StartLimitBurst=3
StartLimitIntervalSec=120

# --- Logging ---
StandardOutput=journal
StandardError=journal
SyslogIdentifier=indemnify-oracle

# --- Security Hardening ---
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/indemnify
CapabilityBoundingSet=
AmbientCapabilities=
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM

[Install]
WantedBy=multi-user.target
```

### 4.3 Enable & Start Services

```bash
# Reload systemd daemon to register new unit files
sudo systemctl daemon-reload

# Enable services to start on system boot
sudo systemctl enable indemnify-api.service
sudo systemctl enable indemnify-oracle.service

# Start services
sudo systemctl start indemnify-api.service
sudo systemctl start indemnify-oracle.service

# Verify service status
sudo systemctl status indemnify-api.service
sudo systemctl status indemnify-oracle.service
```

---

## 5. Nginx Reverse Proxy & TLS Termination

Nginx terminates SSL/TLS at the edge and reverse-proxies HTTPS traffic
from port 443 to the internal FastAPI process on `127.0.0.1:8000`.
The FastAPI process itself is never exposed to the public internet.

### 5.1 Install Nginx & Certbot

```bash
sudo apt-get install -y nginx certbot python3-certbot-nginx
```

### 5.2 Obtain TLS Certificate (Let's Encrypt)

```bash
# Replace risk.indemnify.example.com with your actual domain
sudo certbot --nginx -d risk.indemnify.example.com \
    --non-interactive \
    --agree-tos \
    --email ops@your-org.com \
    --redirect
```

### 5.3 Nginx Server Block Configuration

```bash
sudo nano /etc/nginx/sites-available/indemnify
```

```nginx
# /etc/nginx/sites-available/indemnify
# =====================================
# Indemnify Protocol — Nginx Server Block
# Terminates TLS and proxies to FastAPI on port 8000.
# =====================================

# Redirect all plain HTTP to HTTPS
server {
    listen 80;
    listen [::]:80;
    server_name risk.indemnify.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name risk.indemnify.example.com;

    # --- TLS Configuration ---
    ssl_certificate     /etc/letsencrypt/live/risk.indemnify.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/risk.indemnify.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384:ECDHE-ECDSA-CHACHA20-POLY1305:ECDHE-RSA-CHACHA20-POLY1305:DHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;
    ssl_session_tickets off;

    # --- HSTS (HTTP Strict Transport Security) ---
    # Enforces HTTPS for all future connections for 1 year.
    # IMPORTANT: Only enable after verifying HTTPS is stable.
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

    # --- Security Headers ---
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "DENY" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Content-Security-Policy "default-src 'none'; frame-ancestors 'none';" always;

    # --- Rate Limiting ---
    # Limit by IP: 30r/m for quote endpoint, 120r/m for simulate
    limit_req_zone $binary_remote_addr zone=indemnify_quote:10m rate=30r/m;
    limit_req_zone $binary_remote_addr zone=indemnify_simulate:10m rate=120r/m;

    # --- Request Size Limits ---
    client_max_body_size 1m;

    # --- Upstream Proxy Configuration ---
    location /v1/insurance/quote {
        limit_req zone=indemnify_quote burst=10 nodelay;
        limit_req_status 429;

        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection "";

        # Timeout aligned with daemon SLA: quote must complete in <500ms
        proxy_connect_timeout 5s;
        proxy_send_timeout    10s;
        proxy_read_timeout    15s;
    }

    location /v1/risk/simulate {
        limit_req zone=indemnify_simulate burst=40 nodelay;
        limit_req_status 429;

        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_set_header   Connection "";

        # Strict timeout: simulate MUST complete in <200ms per SLA
        proxy_connect_timeout 5s;
        proxy_send_timeout    5s;
        proxy_read_timeout    5s;
    }

    # --- OpenAPI documentation (development/staging only) ---
    # REMOVE THIS BLOCK IN PRODUCTION or restrict by IP whitelist
    location /docs {
        allow 10.0.0.0/8;    # Internal network only
        allow 127.0.0.1;
        deny  all;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    location /openapi.json {
        allow 10.0.0.0/8;
        allow 127.0.0.1;
        deny  all;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
    }

    # --- Block all other paths ---
    location / {
        return 404;
    }

    # --- Nginx Access & Error Logs ---
    access_log /var/log/nginx/indemnify.access.log combined buffer=16k flush=5s;
    error_log  /var/log/nginx/indemnify.error.log warn;
}
```

### 5.4 Enable & Validate

```bash
# Create symlink to enable the site
sudo ln -s /etc/nginx/sites-available/indemnify /etc/nginx/sites-enabled/

# Test configuration syntax
sudo nginx -t

# Reload Nginx to apply changes
sudo systemctl reload nginx
```

---

## 6. Firewall Configuration

```bash
# Install UFW if not present
sudo apt-get install -y ufw

# Default: deny all inbound, allow all outbound
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (adjust port if non-standard)
sudo ufw allow 22/tcp comment "SSH"

# Allow HTTPS (public-facing Nginx)
sudo ufw allow 443/tcp comment "HTTPS Indemnify API"

# Allow HTTP (Certbot renewal + redirect to HTTPS)
sudo ufw allow 80/tcp comment "HTTP -> HTTPS redirect"

# IMPORTANT: Do NOT expose port 8000 publicly.
# The FastAPI server binds to 127.0.0.1 only and must
# only be accessible via Nginx on localhost.

# Enable firewall
sudo ufw --force enable
sudo ufw status verbose
```

---

## 7. Health Checks & Monitoring

### 7.1 Local Health Check Script

```bash
sudo nano /opt/indemnify/scripts/healthcheck.sh
```

```bash
#!/usr/bin/env bash
# Indemnify daemon health probe.
# Returns exit code 0 if healthy, 1 if degraded.

set -euo pipefail

API_URL="http://127.0.0.1:8000"
TIMEOUT=5

# Check FastAPI root health
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time "${TIMEOUT}" "${API_URL}/docs" 2>/dev/null || echo "000")

if [[ "${HTTP_STATUS}" == "200" ]] || [[ "${HTTP_STATUS}" == "403" ]]; then
    echo "[OK] Indemnify API is healthy (HTTP ${HTTP_STATUS})"
    exit 0
else
    echo "[FAIL] Indemnify API is unreachable (HTTP ${HTTP_STATUS})"
    exit 1
fi
```

```bash
sudo chmod +x /opt/indemnify/scripts/healthcheck.sh
```

### 7.2 systemd Health Check Integration

Add the following to `indemnify-api.service` under `[Service]`:

```ini
ExecStartPost=/bin/bash -c 'sleep 3 && /opt/indemnify/scripts/healthcheck.sh'
```

### 7.3 Recommended Monitoring Stack

| Tool             | Purpose                                      |
|------------------|----------------------------------------------|
| **Prometheus**   | Metrics scraping (uvicorn exposes `/metrics` with `prometheus-fastapi-instrumentator`) |
| **Grafana**      | Dashboards for P_fail distributions, latency percentiles, quote volumes |
| **Alertmanager** | PagerDuty/Slack alerts on P99 latency > 500ms or error rate > 1% |
| **Loki**         | Structured log aggregation from journald     |

---

## 8. Log Management

Both services write structured logs to the systemd journal. Use
`journalctl` for real-time inspection:

```bash
# Stream real-time API logs
sudo journalctl -u indemnify-api.service -f --output=short-precise

# Stream real-time Oracle logs
sudo journalctl -u indemnify-oracle.service -f --output=short-precise

# View last 200 lines of API logs
sudo journalctl -u indemnify-api.service -n 200 --no-pager

# Filter logs for errors only
sudo journalctl -u indemnify-api.service -p err --since "1 hour ago"
```

### Log Retention

Configure journal retention to avoid unbounded disk growth:

```bash
sudo nano /etc/systemd/journald.conf
```

```ini
[Journal]
SystemMaxUse=2G
SystemMaxFileSize=200M
MaxRetentionSec=30day
```

```bash
sudo systemctl restart systemd-journald
```

---

## 9. Update & Rollback Procedure

### 9.1 Zero-Downtime Update

```bash
# Pull latest code
cd /opt/indemnify
sudo -u indemnify git fetch origin main
sudo -u indemnify git pull origin main

# Install any new Python dependencies
sudo -u indemnify /opt/indemnify/venv/bin/pip install -r daemon/requirements.txt --upgrade

# Gracefully reload the API server (sends SIGHUP to uvicorn)
sudo systemctl reload indemnify-api.service

# Restart the oracle listener (requires full restart for new event handlers)
sudo systemctl restart indemnify-oracle.service

# Verify both services are healthy
sudo systemctl status indemnify-api.service indemnify-oracle.service
```

### 9.2 Rollback

```bash
# Identify the previous commit
cd /opt/indemnify
git log --oneline -10

# Roll back to the previous stable commit
sudo -u indemnify git checkout <COMMIT_HASH>
sudo -u indemnify /opt/indemnify/venv/bin/pip install -r daemon/requirements.txt

# Restart services
sudo systemctl restart indemnify-api.service indemnify-oracle.service
```

---

## 10. Incident Response Runbook

### Scenario A: API is Down (HTTP 502 from Nginx)

```bash
# 1. Check if the FastAPI process is running
sudo systemctl status indemnify-api.service

# 2. Check for recent errors
sudo journalctl -u indemnify-api.service -n 100 --no-pager

# 3. Attempt a restart
sudo systemctl restart indemnify-api.service

# 4. If restart fails, check for Python errors in the log
sudo journalctl -u indemnify-api.service -p err -n 50
```

### Scenario B: Oracle Listener Stalled (policies not settling)

```bash
# 1. Check oracle listener status
sudo systemctl status indemnify-oracle.service

# 2. Check RPC connectivity
curl -s -X POST "${RPC_PROVIDER_URL}" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'

# 3. Restart the oracle listener
sudo systemctl restart indemnify-oracle.service

# 4. Verify it picked up from the correct block number in logs
sudo journalctl -u indemnify-oracle.service -n 50 --no-pager
```

### Scenario C: Suspected Key Compromise

> **TREAT THIS AS A CRITICAL SECURITY INCIDENT.**
> Execute the key rotation procedure in Section 3.3 immediately.
> Notify all stakeholders and audit all on-chain transactions
> signed by the compromised oracle address.

```bash
# 1. IMMEDIATELY stop both services to halt new policy creation
sudo systemctl stop indemnify-api.service indemnify-oracle.service

# 2. Follow the key rotation procedure in Section 3.3
# 3. Audit oracle-signed transactions on-chain via block explorer
# 4. Restart services after key rotation is confirmed on-chain
sudo systemctl start indemnify-api.service indemnify-oracle.service
```

---

*Last Updated: 2026-07-07 | Indemnify Protocol — Internal Operations*
