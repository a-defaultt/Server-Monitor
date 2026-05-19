import logging
import paramiko
from influxdb_client import Point

log = logging.getLogger(__name__)

class RemoteCollector:
    def __init__(self, hostname, username, key_path=None, password=None, port=22):
        self.hostname = hostname
        self.username = username
        self.key_path = key_path
        self.password = password
        self.port = port
        self.client = None

    def connect(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            if self.key_path:
                self.client.connect(self.hostname, port=self.port, username=self.username, key_filename=self.key_path, timeout=10)
            else:
                self.client.connect(self.hostname, port=self.port, username=self.username, password=self.password, timeout=10)
            return True
        except Exception as e:
            log.error(f"Failed to connect to {self.hostname}: {e}")
            return False

    def exec_command(self, command):
        if not self.client:
            return None
        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            return stdout.read().decode('utf-8').strip()
        except Exception as e:
            log.error(f"Error executing command on {self.hostname}: {e}")
            return None

    def collect_metrics(self):
        if not self.client and not self.connect():
            return []

        points = []
        try:
            points += self._collect_cpu()
            points += self._collect_memory()
            points += self._collect_disk()
            points += self._collect_load()
        except Exception as e:
            log.error(f"Error collecting remote metrics from {self.hostname}: {e}")
            self.client = None # Reset connection for next time
        return points

    def _collect_load(self):
        output = self.exec_command("cat /proc/loadavg")
        if not output: return []
        parts = output.split()
        return [
            Point("cpu")
            .tag("host", self.hostname)
            .tag("monitoring_type", "remote")
            .tag("core", "total")
            .field("load_1m", float(parts[0]))
            .field("load_5m", float(parts[1]))
            .field("load_15m", float(parts[2]))
        ]

    def _collect_cpu(self):
        # Fallback to a simple calculation if more detailed stats are hard to parse
        # This gets the idle percentage from 'top'
        output = self.exec_command("top -bn1 | grep 'Cpu(s)'")
        if not output: return []
        try:
            # Output format: "%Cpu(s):  5.9 us,  1.5 sy,  0.0 ni, 92.5 id,  0.0 wa,  0.0 hi,  0.0 si,  0.0 st"
            idle = float(output.split('id')[0].split(',')[-1].strip())
            usage = 100.0 - idle
            return [
                Point("cpu")
                .tag("host", self.hostname)
                .tag("monitoring_type", "remote")
                .tag("core", "total")
                .field("usage_pct", usage)
            ]
        except:
            return []

    def _collect_memory(self):
        output = self.exec_command("free -b")
        if not output: return []
        lines = output.splitlines()
        # Mem:       16433233920  2681532416  9307779072     123891712  4443922432 13531062272
        for line in lines:
            if line.startswith("Mem:"):
                parts = line.split()
                total = int(parts[1])
                used = int(parts[2])
                available = int(parts[6])
                return [
                    Point("memory")
                    .tag("host", self.hostname)
                    .tag("monitoring_type", "remote")
                    .field("total_bytes", total)
                    .field("used_bytes", used)
                    .field("available_bytes", available)
                    .field("usage_pct", (used/total)*100 if total > 0 else 0)
                ]
        return []

    def _collect_disk(self):
        # -B1 ensures output is in bytes. -P ensures POSIX output format (one line per entry)
        output = self.exec_command("df -B1 -P")
        if not output: return []
        lines = output.splitlines()
        points = []
        for line in lines[1:]: # Skip header
            parts = line.split()
            if len(parts) < 6: continue
            device = parts[0]
            # Skip virtual/temp filesystems
            if device in ("tmpfs", "devtmpfs", "udev"): continue
            try:
                total = int(parts[1])
                used = int(parts[2])
                free = int(parts[3])
                mountpoint = parts[5]
                points.append(
                    Point("disk_usage")
                    .tag("host", self.hostname)
                    .tag("monitoring_type", "remote")
                    .tag("device", device)
                    .tag("mountpoint", mountpoint)
                    .field("total_bytes", total)
                    .field("used_bytes", used)
                    .field("free_bytes", free)
                    .field("usage_pct", (used/total)*100 if total > 0 else 0)
                )
            except (ValueError, IndexError):
                continue
        return points

    def close(self):
        if self.client:
            self.client.close()
            self.client = None
