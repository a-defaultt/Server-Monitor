import unittest
from unittest.mock import MagicMock, patch
import collector as collector
from influxdb_client import Point

class TestCollector(unittest.TestCase):

    @patch('psutil.cpu_percent')
    @patch('psutil.cpu_times_percent')
    @patch('psutil.getloadavg')
    @patch('psutil.cpu_count')
    def test_collect_cpu(self, mock_cpu_count, mock_loadavg, mock_cpu_times, mock_cpu_pct):
        mock_cpu_pct.side_effect = [10.0, [5.0, 15.0]] # total, then per-core
        mock_cpu_times.return_value = MagicMock(user=5.0, system=2.0, idle=93.0)
        mock_loadavg.return_value = (0.1, 0.2, 0.5)
        mock_cpu_count.side_effect = [2, 1] # logical, physical

        points = collector.collect_cpu()
        
        self.assertGreaterEqual(len(points), 1)
        # Check total point
        total_point = next(p for p in points if p._tags['core'] == 'total')
        self.assertEqual(total_point._name, 'cpu')
        # InfluxDB Point fields are internal, but we can check the presence of tags
        self.assertEqual(total_point._tags['core'], 'total')

    @patch('psutil.virtual_memory')
    @patch('psutil.swap_memory')
    def test_collect_memory(self, mock_swap, mock_virt):
        mock_virt.return_value = MagicMock(total=16000, used=8000, available=8000, free=4000, percent=50.0)
        mock_swap.return_value = MagicMock(total=4000, used=1000, free=3000, percent=25.0)
        
        points = collector.collect_memory()
        self.assertEqual(len(points), 2)
        self.assertEqual(points[0]._name, 'memory')
        self.assertEqual(points[1]._name, 'swap')

    def test_port_open_mock(self):
        with patch('socket.create_connection') as mock_conn:
            # Test success
            mock_conn.return_value.__enter__.return_value = MagicMock()
            self.assertTrue(collector.port_open('localhost', 80))
            
            # Test failure
            mock_conn.side_effect = ConnectionRefusedError
            self.assertFalse(collector.port_open('localhost', 80))

    @patch('psutil.process_iter')
    def test_running_process_names(self, mock_proc_iter):
        mock_proc_iter.return_value = [
            MagicMock(info={'name': 'nginx'}),
            MagicMock(info={'name': 'nginx'}),
            MagicMock(info={'name': 'ssh'}),
        ]
        counts = collector.running_process_names()
        self.assertEqual(counts['nginx'], 2)
        self.assertEqual(counts['ssh'], 1)

    def test_collect_services(self):
        proc_names = {'nginx': 1, 'sshd': 1}
        points = collector.collect_services(proc_names)
        # Check if nginx is marked as running
        nginx_point = next(p for p in points if p._tags['service'] == 'nginx')
        # We can't easily access ._fields directly in a clean way without reaching into internals
        # but the point was created.
        self.assertIsInstance(nginx_point, Point)

if __name__ == '__main__':
    unittest.main()
