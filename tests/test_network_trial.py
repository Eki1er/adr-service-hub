import io
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'adr_service_agent'))
from network_trial import NetworkTrial, DURATION, BASELINE, RETENTION, MAX_SAMPLES, gateway_address
from network_trial_ui import panel, metrics, chart
from monitoring import MonitoringSchedule
from agent import Pilot, Handler, page, ProtocolError


class TrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now = 1800000000
        self.elapsed = 100
        self.probe = Mock(return_value={'sent': 5, 'received': 5, 'latency_ms': 8.5})
        self.trial = NetworkTrial(self.root/'trial.json', {'unifi_gateway_host':'192.168.16.1'},
                                  clock=lambda:self.now, probe_fn=self.probe, monotonic=lambda:self.elapsed)

    def sample(self):
        self.trial._collect(dict(self.trial.current))

    def test_opt_in_only_and_duplicate_start_does_not_extend_deadline(self):
        self.trial.tick()
        panel(self.trial, 'test')
        self.probe.assert_not_called()
        self.trial.start()
        deadline = self.trial.current['deadline']
        self.now += 100
        self.assertFalse(self.trial.start())
        self.assertEqual(self.trial.current['deadline'], deadline)
        self.probe.assert_not_called()
        self.assertEqual(deadline, 1800000000+DURATION)

    def test_only_fixed_targets_and_numeric_gateway_no_shell_inputs(self):
        for host in ('', '-c 500', '8.8.8.8', '127.0.0.1', '0.0.0.0', '224.0.0.1', 'http://192.168.16.1', 'router.local'):
            with self.subTest(host=host), self.assertRaises(ValueError):
                gateway_address({'unifi_gateway_host':host})
        self.trial.start()
        self.sample()
        self.assertCountEqual(self.probe.call_args_list,
            [unittest.mock.call('Gateway','192.168.16.1'), unittest.mock.call('Cloudflare','1.1.1.1'), unittest.mock.call('Google','8.8.8.8')])

    def test_baseline_and_weighted_loss_real_failures_remain_unknown(self):
        self.trial.start()
        self.sample()
        self.assertTrue(self.trial.samples[-1]['baseline'])
        self.now += BASELINE
        self.probe.return_value = {'sent':5, 'received':0, 'latency_ms':0}
        self.sample()
        self.assertFalse(self.trial.samples[-1]['baseline'])
        self.assertIsNone(self.trial.samples[-1]['rows'][0]['latency_ms'])
        self.assertEqual(self.trial.samples[-1]['rows'][0]['loss_percent'], 100)
        self.probe.side_effect = RuntimeError('private-secret')
        self.now += 60
        self.sample()
        self.assertIsNone(self.trial.samples[-1]['rows'][0]['loss_percent'])
        self.assertEqual(metrics(self.trial.samples,0), ('8.5 ms','50.0%',2))
        self.assertNotIn('private-secret', panel(self.trial,'test'))

    def test_interval_never_overlaps_or_replays_missed_ticks(self):
        self.trial.start()
        with patch('network_trial.threading.Thread') as worker:
            self.trial.tick()
            self.now += 120
            self.trial.tick()
            worker.assert_called_once()
            self.assertTrue(worker.call_args.kwargs['daemon'])
        self.trial.busy = False
        with patch('network_trial.threading.Thread') as worker:
            self.trial.tick()
            self.trial.busy = False
            self.trial.tick()
            worker.assert_called_once()

    def test_auto_stop_exact_deadline_without_another_ping(self):
        self.trial.start()
        self.sample()
        self.now += DURATION
        self.trial.tick()
        self.assertEqual(self.trial.current['state'], 'completed')
        self.assertEqual(len(self.trial.samples),1)
        self.assertEqual(self.probe.call_count,3)
        self.trial.tick()
        self.assertEqual(self.probe.call_count,3)

    def test_monotonic_deadline_stops_even_if_wall_clock_is_adjusted(self):
        self.trial.start()
        self.elapsed += DURATION
        self.trial.tick()
        self.assertEqual(self.trial.current['state'], 'completed')
        self.probe.assert_not_called()

    def test_manual_stop_drops_inflight_sample_and_preserves_prior_history(self):
        self.trial.start()
        self.sample()
        entered, release = threading.Event(), threading.Event()
        def blocked(*_):
            entered.set()
            release.wait(3)
            return {'sent':5,'received':5,'latency_ms':10}
        self.trial.probe = blocked
        worker = threading.Thread(target=self.sample)
        worker.start()
        self.assertTrue(entered.wait(2))
        self.trial.stop()
        release.set()
        worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(self.trial.samples),1)
        self.assertEqual(self.trial.current['state'],'stopped')

    def test_restart_keeps_history_but_does_not_resume_or_extend_trial(self):
        self.trial.start()
        self.sample()
        restarted = NetworkTrial(self.trial.path,self.trial.options,clock=lambda:self.now,probe_fn=self.probe)
        self.assertEqual(restarted.current['state'],'interrupted')
        self.assertEqual(len(restarted.samples),1)
        restarted.tick()
        self.assertEqual(self.probe.call_count,3)
        self.assertEqual(self.trial.path.stat().st_mode & 0o777, 0o600)

    def test_corrupt_history_is_not_overwritten_and_blocks_new_trial(self):
        self.trial.path.write_text('{broken')
        restarted = NetworkTrial(self.trial.path,self.trial.options)
        with self.assertRaises(ValueError):
            restarted.start()
        self.assertEqual(self.trial.path.read_text(),'{broken')

    def test_storage_failure_stops_probes_and_retains_previous_file(self):
        self.trial.start()
        original = self.trial.path.read_text()
        with patch.object(self.trial,'_save',side_effect=OSError('private-secret')):
            self.sample()
        self.assertFalse(self.trial.running)
        self.assertEqual(self.trial.current['state'],'storage error')
        self.trial.tick()
        self.assertEqual(self.probe.call_count,3)
        self.assertEqual(self.trial.path.read_text(),original)
        self.assertNotIn('private-secret',self.trial.error)

    def test_retention_sample_limit_and_missing_history_not_zero(self):
        self.trial.start()
        self.sample()
        sample = self.trial.samples[0]
        self.trial.samples = [dict(sample,time=self.now-RETENTION-1)] + [sample]*(MAX_SAMPLES+1)
        self.trial._prune()
        self.assertEqual(len(self.trial.samples), MAX_SAMPLES)
        self.now += RETENTION+1
        self.assertEqual(self.trial.snapshot()['samples'],[])

    def test_chart_breaks_at_missing_measurements_and_unsampled_intervals(self):
        self.trial.start()
        for delta in (0,60,180):
            self.now = 1800000000+delta
            self.sample()
        markup = chart(self.trial.current,self.trial.samples,'latency_ms','Latency','ms')
        self.assertEqual(markup.count('class="trend-line"'),6)  # two segments per target
        self.assertNotIn('<circle',markup)
        self.trial.current['targets'][0][0]='<script>bad</script>'
        self.assertNotIn('<script>bad',panel(self.trial,'test'))


class TrialWiringTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root/'options.json').write_text(json.dumps({'unifi_gateway_host':'192.168.16.1','automatic_monitoring':False}))
        self.pilot = Pilot(self.root,self.root/'options.json')

    def handler(self, action, csrf=None, peer='172.30.32.2'):
        h=Handler.__new__(Handler)
        h.server=SimpleNamespace(pilot=self.pilot)
        h.client_address=(peer,1234)
        h.path='/'+action
        body=urlencode({'csrf':csrf or self.pilot.csrf}).encode()
        h.rfile=io.BytesIO(body)
        h.wfile=io.BytesIO()
        h.headers={'Content-Length':str(len(body)), 'Content-Type':'application/x-www-form-urlencoded'}
        h.send_response=Mock()
        h.send_header=Mock()
        h.end_headers=Mock()
        h.send_error=Mock()
        h.send_page=Mock()
        return h

    def test_ingress_and_csrf_required_before_start_stop_or_download(self):
        for action in ('network-trial-start','network-trial-stop','network-trial-report'):
            h=self.handler(action,csrf='bad')
            h.do_POST()
            h.send_error.assert_called_with(403)
            h=self.handler(action,peer='127.0.0.1')
            h.do_POST()
            h.send_error.assert_called_once()
        self.assertFalse(self.pilot.network_trial.running)

    def test_start_launches_background_monitor_even_when_regular_schedule_is_off(self):
        with patch('agent.threading.Thread') as worker:
            h=self.handler('network-trial-start')
            h.do_POST()
            self.assertTrue(self.pilot.network_trial.running)
            worker.assert_called_once()
            loop=worker.call_args.kwargs['target']
        with patch.object(self.pilot.monitor_stop,'wait',side_effect=[False,True]), patch.object(self.pilot.network_trial,'tick') as tick:
            loop()
            tick.assert_called_once()

    def test_stop_and_download_work_while_other_checks_are_busy(self):
        self.pilot.network_trial.start()
        self.pilot.service_running=True
        h=self.handler('network-trial-stop')
        h.do_POST()
        self.assertFalse(self.pilot.network_trial.running)
        h=self.handler('network-trial-report')
        h.do_POST()
        h.send_response.assert_called_with(200)
        result=json.loads(h.wfile.getvalue())
        self.assertEqual(result['sessions'][0]['state'],'stopped')
        self.assertNotIn('options',result)

    def test_only_speedtests_deferred_regular_system_checks_keep_running(self):
        self.pilot.network_trial.start()
        for source in ('green','gateway'):
            with self.assertRaises(ProtocolError):
                self.pilot.start_network_job('internet_test',source=source)
        scheduler=MonitoringSchedule({},self.root/'schedule.json',clock=lambda:10000)
        scheduler.next_due={'service':0,'internet_test':0}
        dispatch=Mock()
        self.assertEqual(scheduler.tick(dispatch,defer_speedtest=True),'service')
        dispatch.assert_called_once_with('service')
        self.assertEqual(scheduler.next_due['internet_test'],0)
        self.assertEqual(scheduler.tick(dispatch),'internet_test')

    def test_page_has_network_item_and_get_never_starts_trial(self):
        with patch.object(self.pilot.network_trial,'start') as start:
            markup=page(self.pilot)
            self.assertEqual(markup.count('id="network-monitoring"'),1)
            self.assertIn('Network Monitoring',markup)
            start.assert_not_called()
        self.assertFalse(self.pilot.network_trial.running)


if __name__ == '__main__':
    unittest.main()
