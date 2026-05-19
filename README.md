# Server Monitor

A self-contained Docker stack that monitors a server from inside, stores metrics
in InfluxDB (7-day retention), and displays a pre-built Grafana dashboard. It can also monitor remote servers via SSH.

## Features

| Category | Metrics |
|---|---|
| **CPU** | Total%, per-core%, user/system/iowait/steal, load avg 1/5/15m |
| **Memory** | Used, available, cached, buffers, swap |
| **Disk** | Usage% per partition, I/O read/write bytes+ops per device |
| **Network** | TX/RX bytes+packets, errors, drops per interface |
| **System** | Uptime, logged-in users, open file descriptors |
| **Services** | Running/down status for 20+ known daemons (Wazuh, Zabbix, Apache, Postfix, …) |
| **Databases** | Port reachability for MySQL, PostgreSQL, Redis, MongoDB, Elasticsearch |
| **Docker** | CPU%, memory%, status, restart count for every container |
| **Top Processes** | Top 15 by CPU and top 15 by memory (accurate CPU collection) |
| **Remote Monitoring** | Agentless monitoring of remote Linux servers via SSH |

## Stack

```
influxdb:2.7    ← time-series DB, 7-day retention, port 8086 (loopback only)
collector       ← Python agent (psutil + docker SDK + paramiko), pid:host + network:host
grafana:10.4    ← dashboard, port 3000, zero manual config needed
```

## Requirements

- Docker Engine (with Compose plugin or docker-compose standalone)
- User must be in the `docker` group (for container stats)
- Ports: **3000** (Grafana) must be reachable; 8086 is loopback-only
- **SSH Access**: For remote monitoring, the collector needs access to the remote server's SSH port and a valid SSH key.

## Quick start

```bash
chmod +x setup.sh
./setup.sh
```

That's it. The script:
1. Generates a `.env` with random strong passwords
2. Builds the collector image
3. Starts all three containers
4. Prints the Grafana URL and credentials

**First metrics appear ~60 seconds after start.**

## Monitoring Remote Servers

The collector can monitor remote Linux servers without installing any agents on them. It uses SSH to poll metrics.

1.  **Configure Hosts**: Create a `remote_servers.json` in the project root based on `remote_servers.json.example`.
    ```json
    [
      {
        "hostname": "192.168.1.100",
        "username": "monitor",
        "key_path": "/root/.ssh/id_rsa"
      }
    ]
    ```
2.  **SSH Keys**: Place your SSH keys in `~/.ssh/` on the host machine. They are automatically mounted into the collector container at `/root/.ssh/`.
3.  **Restart**: Run `sudo docker compose up -d --build` to apply the configuration.

## Adding custom services to monitor

Edit `collector/collector.py` → `KNOWN_SERVICES` dict:

```python
KNOWN_SERVICES = {
    ...
    "my-app": ["my-app-process-name"],   # ← add here
}
```

Then rebuild: `docker compose build collector && docker compose up -d collector`

## Useful commands

```bash
# Live collector logs
docker compose logs -f collector

# All container status
docker compose ps

# Stop everything (data is preserved in volumes)
docker compose down

# Stop + delete all data (fresh start)
docker compose down -v

# Rebuild after code changes
docker compose up -d --build
```

## Dashboard sections

| Section | Panels |
|---|---|
| System Overview | CPU gauge, RAM gauge, uptime, load avg, swap, open FDs |
| CPU & Memory | Time-series graphs for CPU breakdown and memory |
| Disk | Partition bar-gauge, table with sizes, I/O rate graph |
| Network | TX/RX bytes/s per interface, error/drop counters |
| Services & Databases | Color-coded up/down status tables |
| Docker Containers | Full table + CPU%/Mem% time-series per container |
| Top Processes | Tables sorted by CPU% and by Memory% (PID, Name, CPU, Mem) |

## Security notes

- InfluxDB port 8086 is bound to `127.0.0.1` only — not reachable from outside.
- Grafana port 3000 is open on all interfaces. Put it behind your firewall or add
  Tailscale/nginx reverse proxy with auth for internet-facing servers.
- The collector uses `pid:host` (read-only process visibility) and mounts the
  Docker socket read-only.
- Remote monitoring keys are mounted read-only into the collector container.

## Changing the collection interval

In `.env` or `docker-compose.yml`, set `COLLECT_INTERVAL` (seconds).
Lower values give finer granularity but use more disk space.

## Adjusting 7-day retention

InfluxDB retention is set at first run via `DOCKER_INFLUXDB_INIT_RETENTION=168h`.
To change it after deployment, use the InfluxDB UI at `http://localhost:8086`
or the `influx` CLI inside the container:

```bash
docker exec -it monitor_influxdb influx bucket update \
  --name server_metrics --retention 336h   # e.g. 14 days ||  730h is a month || 8760h is a year
```
