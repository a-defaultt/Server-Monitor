# Project Specification: Server Monitor

## 1. Project Hierarchy
```text
server-monitor/
├── collector/
│   ├── collector.py          # Python agent for metric collection
│   ├── Dockerfile            # Docker configuration for collector
│   └── requirements.txt      # Python dependencies
├── grafana/
│   └── provisioning/
│       ├── dashboards/       # Grafana dashboard definitions
│       └── datasources/      # InfluxDB data source configuration
├── docker-compose.yml        # Main orchestration file
├── README.md                 # Project documentation
└── setup.sh                  # Deployment script
```



```text
server-monitor/
├── setup.sh                          ← one command deploy
├── docker-compose.yml
├── .env.example
├── README.md
├── collector/
│   ├── Dockerfile
│   ├── requirements.txt
│   └── collector.py                  ← the brain
└── grafana/
    └── provisioning/
        ├── datasources/influxdb.yml  ← auto-wired, no UI setup
        └── dashboards/
            ├── dashboard.yml
            └── server_overview.json  ← full pre-built dashboard

```





## 2. Stack
- **Database:** InfluxDB v2.7 (time-series)
- **Collector Agent:** Custom Python agent (utilizing `psutil` and Docker SDK)
- **Visualization:** Grafana v10.4

## 3. Interaction & Services
- **Services:**
  - `influxdb`: Stores all collected metrics.
  - `collector`: Periodically (default 60s) polls host system and Docker stats, pushes them to `influxdb`.
  - `grafana`: Reads data from `influxdb` to render dashboards.
- **Collector Interactions:**
  - Shares host network (`network_mode: host`) to communicate with InfluxDB on `127.0.0.1:8086`.
  - Shares host PID (`pid: host`) to monitor host processes.
  - Mounts Docker socket (`/var/run/docker.sock`) to monitor containers.

## 4. Scope
A self-contained Docker-based server monitoring solution designed for local or individual server monitoring. It collects system, network, service, database, and Docker metrics and provides a pre-built Grafana dashboard.

## 5. APIs & Data
- **InfluxDB:** InfluxDB API is used internally by the collector and Grafana.
- **Metrics Scope:** CPU, Memory, Disk I/O, Network, System Uptime, Service status, Database port connectivity, Docker container stats, and Top Processes.
- **Retention:** Default retention is 7 days (168h).

## 6. Rate Limiting
- **Collection Interval:** Default collection interval is 60 seconds. This can be configured via `COLLECT_INTERVAL` in the `.env` or `docker-compose.yml`.

## 7. AI Components
- **Presence:** None. No AI or ML components are used in this project.

## 8. Data Management
- **Database:** InfluxDB 2.x.
- **Volumes:** `influxdb_data` and `grafana_data` are used to persist data.

## 9. Operation Workflow
1. Run `./setup.sh` to initialize passwords and start the stack.
2. The `collector` service runs continuously, polling metrics based on the set interval and pushing them to the local `influxdb`.
3. Grafana connects to `influxdb` (via pre-provisioned data source configuration) to visualize the data.
