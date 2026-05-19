#!/usr/bin/env python3
"""
Server Performance Collector
Collects host metrics every COLLECT_INTERVAL seconds and writes to InfluxDB.

Metrics collected:
  - CPU (total + per-core, iowait, load average)
  - Memory (RAM + swap)
  - Disk (usage per partition + I/O counters)
  - Network (bytes/packets per interface)
  - System (uptime, logged-in users, open file descriptors)
  - Services (auto-detect known daemons by process name)
  - Docker containers (CPU%, RAM%, status, restart count)
  - Databases (port reachability auto-detection)
  - Top processes (by CPU and by memory)
"""

import os
import re
import time
import socket
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import psutil
import docker
from influxdb_client import InfluxDBClient, Point, WritePrecision
from influxdb_client.client.write_api import SYNCHRONOUS

# ─── Configuration ────────────────────────────────────────────────────────────

INFLUXDB_URL    = os.getenv("INFLUXDB_URL",    "http://127.0.0.1:8086")
INFLUXDB_TOKEN  = os.getenv("INFLUXDB_TOKEN",  "")
INFLUXDB_ORG    = os.getenv("INFLUXDB_ORG",    "monitoring")
INFLUXDB_BUCKET = os.getenv("INFLUXDB_BUCKET", "server_metrics")
COLLECT_INTERVAL = int(os.getenv("COLLECT_INTERVAL", "60"))
HOSTNAME = socket.gethostname()

# ─── Service definitions ───────────────────────────────────────────────────────
# Maps a friendly name → list of process names that indicate the service is running.
# Add your own services here.
KNOWN_SERVICES = {
    # Security / monitoring
    "wazuh-agent":      ["wazuh-agentd", "ossec-agentd"],
    "wazuh-manager":    ["wazuh-analysisd", "wazuh-server", "ossec-analysisd"],
    "suricata":         ["suricata"],
    "zeek":             ["zeek"],
    "velociraptor":     ["velociraptor"],
    "zabbix-server":    ["zabbix_server"],
    "zabbix-agent":     ["zabbix_agentd"],
    # Web / mail
    "apache2":          ["apache2", "httpd"],
    "nginx":            ["nginx"],
    "postfix":          ["master"],          # postfix master daemon
    "dovecot":          ["dovecot"],
    # Databases
    "mysql":            ["mysqld", "mariadbd"],
    "postgresql":       ["postgres"],
    "redis":            ["redis-server"],
    "mongodb":          ["mongod"],
    # Infrastructure
    "ssh":              ["sshd"],
    "bind9":            ["named"],
    "docker":           ["dockerd"],
    "tailscaled":       ["tailscaled"],
    "openvpn":          ["openvpn"],
    # Virtualmin / Webmin
    "webmin":           ["miniserv.pl"],
    # FTP
    "proftpd":          ["proftpd"],
    "vsftpd":           ["vsftpd"],
}

# ─── Database port checks ──────────────────────────────────────────────────────
# Format: friendly_name → (host, port)
DB_PORTS = {
    "mysql_mariadb": ("127.0.0.1", 3306),
    "postgresql":    ("127.0.0.1", 5432),
    "redis":         ("127.0.0.1", 6379),
    "mongodb":       ("127.0.0.1", 27017),
    "elasticsearch": ("127.0.0.1", 9200),
    "memcached":     ("127.0.0.1", 11211),
}

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    """Return True if TCP port accepts a connection."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def running_process_names() -> dict[str, int]:
    """Return {process_name: count} for all running processes."""
    counts: dict[str, int] = {}
    for proc in psutil.process_iter(["name"]):
        try:
            name = proc.info["name"] or ""
            if name:
                counts[name] = counts.get(name, 0) + 1
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return counts


# ─── Metric collectors ────────────────────────────────────────────────────────

def collect_cpu() -> list[Point]:
    points: list[Point] = []

    # Prime the cpu_percent counters on first call (returns 0.0)
    # A second call after interval=1 gives a real reading.
    pct_total = psutil.cpu_percent(interval=1)
    pct_per_core = psutil.cpu_percent(interval=None, percpu=True)
    times_pct = psutil.cpu_times_percent(interval=None)
    load1, load5, load15 = psutil.getloadavg()
    cpu_count_logical = psutil.cpu_count(logical=True)
    cpu_count_physical = psutil.cpu_count(logical=False) or 0

    points.append(
        Point("cpu")
        .tag("host", HOSTNAME)
        .tag("core", "total")
        .field("usage_pct",    float(pct_total))
        .field("user_pct",     float(times_pct.user))
        .field("system_pct",   float(times_pct.system))
        .field("idle_pct",     float(times_pct.idle))
        .field("iowait_pct",   float(getattr(times_pct, "iowait", 0.0)))
        .field("steal_pct",    float(getattr(times_pct, "steal",  0.0)))
        .field("load_1m",      float(load1))
        .field("load_5m",      float(load5))
        .field("load_15m",     float(load15))
        .field("logical_cores",  int(cpu_count_logical))
        .field("physical_cores", int(cpu_count_physical))
    )

    for i, pct in enumerate(pct_per_core):
        points.append(
            Point("cpu")
            .tag("host", HOSTNAME)
            .tag("core", f"core{i}")
            .field("usage_pct", float(pct))
        )

    return points


def collect_memory() -> list[Point]:
    mem  = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return [
        Point("memory")
        .tag("host", HOSTNAME)
        .field("total_bytes",     int(mem.total))
        .field("used_bytes",      int(mem.used))
        .field("available_bytes", int(mem.available))
        .field("free_bytes",      int(mem.free))
        .field("cached_bytes",    int(getattr(mem, "cached",  0)))
        .field("buffers_bytes",   int(getattr(mem, "buffers", 0)))
        .field("usage_pct",       float(mem.percent)),

        Point("swap")
        .tag("host", HOSTNAME)
        .field("total_bytes", int(swap.total))
        .field("used_bytes",  int(swap.used))
        .field("free_bytes",  int(swap.free))
        .field("usage_pct",   float(swap.percent)),
    ]


def collect_disk() -> list[Point]:
    points: list[Point] = []

    # Usage per partition
    for part in psutil.disk_partitions(all=False):
        if not part.mountpoint:
            continue
        # Skip docker overlay / tmpfs noise
        if part.fstype in ("overlay", "tmpfs", "devtmpfs", "squashfs"):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
            points.append(
                Point("disk_usage")
                .tag("host",       HOSTNAME)
                .tag("device",     part.device)
                .tag("mountpoint", part.mountpoint)
                .tag("fstype",     part.fstype)
                .field("total_bytes",   int(usage.total))
                .field("used_bytes",    int(usage.used))
                .field("free_bytes",    int(usage.free))
                .field("usage_pct",     float(usage.percent))
            )
        except (PermissionError, FileNotFoundError):
            pass

    # I/O counters per physical disk
    try:
        io_all = psutil.disk_io_counters(perdisk=True)
        for dev, io in io_all.items():
            # Skip loop/ram/dm devices
            if re.match(r"^(loop|ram|dm-|sr)", dev):
                continue
            points.append(
                Point("disk_io")
                .tag("host",   HOSTNAME)
                .tag("device", dev)
                .field("read_bytes",    int(io.read_bytes))
                .field("write_bytes",   int(io.write_bytes))
                .field("read_count",    int(io.read_count))
                .field("write_count",   int(io.write_count))
                .field("read_time_ms",  int(io.read_time))
                .field("write_time_ms", int(io.write_time))
                .field("busy_time_ms",  int(getattr(io, "busy_time", 0)))
            )
    except Exception as exc:
        log.warning("disk_io collection failed: %s", exc)

    return points


def collect_network() -> list[Point]:
    points: list[Point] = []
    net_io = psutil.net_io_counters(pernic=True)
    for iface, io in net_io.items():
        if iface == "lo":
            continue
        points.append(
            Point("network")
            .tag("host",      HOSTNAME)
            .tag("interface", iface)
            .field("bytes_sent",    int(io.bytes_sent))
            .field("bytes_recv",    int(io.bytes_recv))
            .field("packets_sent",  int(io.packets_sent))
            .field("packets_recv",  int(io.packets_recv))
            .field("errin",         int(io.errin))
            .field("errout",        int(io.errout))
            .field("dropin",        int(io.dropin))
            .field("dropout",       int(io.dropout))
        )
    return points


def collect_system() -> list[Point]:
    boot_time     = psutil.boot_time()
    uptime_secs   = time.time() - boot_time
    users_count   = len(psutil.users())

    open_fds = max_fds = 0
    try:
        with open("/proc/sys/fs/file-nr") as fh:
            parts  = fh.read().split()
            open_fds = int(parts[0])
            max_fds  = int(parts[2])
    except Exception:
        pass

    return [
        Point("system")
        .tag("host", HOSTNAME)
        .field("uptime_seconds",         float(uptime_secs))
        .field("users_logged_in",        int(users_count))
        .field("open_file_descriptors",  int(open_fds))
        .field("max_file_descriptors",   int(max_fds))
        .field("boot_timestamp",         float(boot_time))
    ]


def collect_services(proc_names: dict[str, int]) -> list[Point]:
    points: list[Point] = []
    for svc_name, candidate_procs in KNOWN_SERVICES.items():
        count = sum(proc_names.get(p, 0) for p in candidate_procs)
        points.append(
            Point("service_status")
            .tag("host",    HOSTNAME)
            .tag("service", svc_name)
            .field("running",        int(count > 0))
            .field("process_count",  int(count))
        )
    return points


def collect_top_processes() -> list[Point]:
    """Top 15 processes by CPU% and top 15 by memory%."""
    # First pass: Initialize CPU counters for all processes
    procs = []
    for proc in psutil.process_iter(["pid", "name", "username", "memory_percent", "status"]):
        try:
            proc.cpu_percent(None)
            procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    # Wait a short interval for CPU usage to be measurable
    time.sleep(0.1)

    snapshot: list[dict] = []
    # Second pass: Get actual CPU usage and other info
    for proc in procs:
        try:
            info = proc.info
            snapshot.append({
                "pid":    info["pid"],
                "name":   info["name"] or "?",
                "user":   info["username"] or "?",
                "cpu":    proc.cpu_percent(None),
                "mem":    info["memory_percent"] or 0.0,
                "status": info["status"] or "?",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    points: list[Point] = []
    # Sort by CPU
    for rank, proc in enumerate(sorted(snapshot, key=lambda x: x["cpu"], reverse=True)[:15], 1):
        points.append(
            Point("top_proc_cpu")
            .tag("host", HOSTNAME)
            .tag("name", proc["name"])
            .tag("user", proc["user"])
            .tag("pid",  str(proc["pid"]))
            .field("pid",        int(proc["pid"])) # Also as field for pivot
            .field("rank",       int(rank))
            .field("cpu_pct",    float(proc["cpu"]))
            .field("mem_pct",    float(proc["mem"]))
        )

    # Sort by Memory
    for rank, proc in enumerate(sorted(snapshot, key=lambda x: x["mem"], reverse=True)[:15], 1):
        points.append(
            Point("top_proc_mem")
            .tag("host", HOSTNAME)
            .tag("name", proc["name"])
            .tag("user", proc["user"])
            .tag("pid",  str(proc["pid"]))
            .field("pid",     int(proc["pid"])) # Also as field for pivot
            .field("rank",    int(rank))
            .field("cpu_pct", float(proc["cpu"]))
            .field("mem_pct", float(proc["mem"]))
        )

    return points


def collect_docker() -> list[Point]:
    points: list[Point] = []
    try:
        client = docker.from_env()
        for container in client.containers.list(all=True):
            status = container.status            # running / exited / paused …
            cpu_pct = mem_pct = mem_usage = mem_limit = 0.0
            net_rx = net_tx = blk_read = blk_write = 0

            if status == "running":
                try:
                    s = container.stats(stream=False)

                    # CPU %
                    cpu_delta = (s["cpu_stats"]["cpu_usage"]["total_usage"]
                                 - s["precpu_stats"]["cpu_usage"]["total_usage"])
                    sys_delta  = (s["cpu_stats"]["system_cpu_usage"]
                                  - s["precpu_stats"]["system_cpu_usage"])
                    n_cpus = len(s["cpu_stats"]["cpu_usage"].get("percpu_usage") or [1])
                    if sys_delta > 0:
                        cpu_pct = (cpu_delta / sys_delta) * n_cpus * 100.0

                    # Memory %
                    mem_usage = s["memory_stats"].get("usage", 0)
                    mem_limit = s["memory_stats"].get("limit", 1)
                    # Subtract cache from usage (Docker reports RSS differently)
                    cache = s["memory_stats"].get("stats", {}).get("cache", 0)
                    mem_usage_real = max(0, mem_usage - cache)
                    mem_pct = (mem_usage_real / mem_limit * 100) if mem_limit else 0.0
                    mem_usage = mem_usage_real

                    # Network I/O (summed across all interfaces)
                    for iface_data in s.get("networks", {}).values():
                        net_rx += iface_data.get("rx_bytes", 0)
                        net_tx += iface_data.get("tx_bytes", 0)

                    # Block I/O
                    for entry in s.get("blkio_stats", {}).get("io_service_bytes_recursive") or []:
                        if entry.get("op") == "Read":
                            blk_read += entry.get("value", 0)
                        elif entry.get("op") == "Write":
                            blk_write += entry.get("value", 0)

                except Exception as exc:
                    log.debug("Container stats error (%s): %s", container.name, exc)

            container.reload()
            restart_count = container.attrs.get("RestartCount", 0)
            image_tag = (container.image.tags or ["unknown"])[0]

            points.append(
                Point("docker_container")
                .tag("host",           HOSTNAME)
                .tag("container_name", container.name)
                .tag("image",          image_tag)
                .tag("status",         status)
                .field("running",         int(status == "running"))
                .field("cpu_pct",         round(float(cpu_pct), 3))
                .field("mem_pct",         round(float(mem_pct), 3))
                .field("mem_usage_bytes", int(mem_usage))
                .field("mem_limit_bytes", int(mem_limit))
                .field("net_rx_bytes",    int(net_rx))
                .field("net_tx_bytes",    int(net_tx))
                .field("blk_read_bytes",  int(blk_read))
                .field("blk_write_bytes", int(blk_write))
                .field("restart_count",   int(restart_count))
            )

    except Exception as exc:
        log.warning("Docker collection failed: %s", exc)

    return points


def collect_databases() -> list[Point]:
    points: list[Point] = []
    for db_name, (host, port) in DB_PORTS.items():
        reachable = port_open(host, port)
        points.append(
            Point("database_status")
            .tag("host",     HOSTNAME)
            .tag("database", db_name)
            .field("reachable", int(reachable))
            .field("port",      int(port))
        )
    return points


# ─── Main loop ────────────────────────────────────────────────────────────────

def wait_for_influxdb(retries: int = 40, delay: int = 5) -> None:
    """Block until InfluxDB port accepts connections."""
    parsed = urlparse(INFLUXDB_URL)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8086

    log.info("Waiting for InfluxDB at %s:%d …", host, port)
    for attempt in range(1, retries + 1):
        if port_open(host, port):
            log.info("InfluxDB is up.")
            return
        log.info("  attempt %d/%d — retrying in %ds", attempt, retries, delay)
        time.sleep(delay)
    raise RuntimeError(f"InfluxDB at {host}:{port} did not become ready in time.")


def main() -> None:
    log.info("=== Server Monitor Collector starting ===")
    log.info("Hostname      : %s", HOSTNAME)
    log.info("InfluxDB URL  : %s", INFLUXDB_URL)
    log.info("Org / Bucket  : %s / %s", INFLUXDB_ORG, INFLUXDB_BUCKET)
    log.info("Interval      : %ds", COLLECT_INTERVAL)

    wait_for_influxdb()

    # Extra wait so InfluxDB finishes its init (bucket/token creation)
    time.sleep(5)

    client = InfluxDBClient(url=INFLUXDB_URL, token=INFLUXDB_TOKEN, org=INFLUXDB_ORG)
    write_api = client.write_api(write_options=SYNCHRONOUS)

    # Prime psutil CPU counters (first call always returns 0.0)
    psutil.cpu_percent(interval=None)
    psutil.cpu_percent(interval=None, percpu=True)
    log.info("CPU counters primed. Starting collection loop …")

    while True:
        cycle_start = time.monotonic()
        try:
            proc_names = running_process_names()
            all_points: list[Point] = []

            all_points += collect_cpu()
            all_points += collect_memory()
            all_points += collect_disk()
            all_points += collect_network()
            all_points += collect_system()
            all_points += collect_services(proc_names)
            all_points += collect_top_processes()
            all_points += collect_docker()
            all_points += collect_databases()

            write_api.write(
                bucket=INFLUXDB_BUCKET,
                org=INFLUXDB_ORG,
                record=all_points,
                write_precision=WritePrecision.S,
            )

            elapsed = time.monotonic() - cycle_start
            log.info("✓ wrote %d points in %.2fs", len(all_points), elapsed)

        except KeyboardInterrupt:
            log.info("Interrupted — shutting down.")
            break
        except Exception as exc:
            log.error("Collection cycle failed: %s", exc, exc_info=True)

        sleep_time = max(0.0, COLLECT_INTERVAL - (time.monotonic() - cycle_start))
        time.sleep(sleep_time)

    client.close()
    log.info("Collector stopped.")


if __name__ == "__main__":
    main()
