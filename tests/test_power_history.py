import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
from power_history import OutletHistory
from dashboard import grouped_cards,configure_site_timezone,tile_value
from dashboard_visuals import history_chart


class PowerHistoryTests(unittest.TestCase):
    def test_real_outlet_samples_persist_changes_and_isolate_new_target(self):
        with tempfile.TemporaryDirectory() as directory:
            now=[1793760000.0];path=Path(directory)/'history.json'
            history=OutletHistory(path,lambda:now[0])
            inventory={'outlets':[{'number':1,'label':'Router','on':True},{'number':2,'label':'Disk','on':False}]}
            first=history.observe('a'*64,inventory)
            self.assertEqual(first['series'][0]['points'][0]['value'],1)
            now[0]+=1200;inventory['outlets'][1]['on']=True
            second=history.observe('a'*64,inventory)
            self.assertEqual(second['series'][0]['points'][-1]['value'],2)
            self.assertEqual([p['value'] for p in second['series'][1]['points']],[0,1])
            restarted=OutletHistory(path,lambda:now[0]);self.assertFalse(restarted.error)
            self.assertEqual(len(restarted.data['samples']),2)
            self.assertEqual(len(restarted.observe('b'*64,inventory)['series'][0]['points']),1)

    def test_corrupt_history_is_preserved_and_old_history_pruned(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'history.json';path.write_text('{bad')
            history=OutletHistory(path)
            self.assertTrue(history.observe('a',{'outlets':[]})['error'])
            self.assertEqual(path.read_text(),'{bad')
            path.unlink();now=[1793760000.0];history=OutletHistory(path,lambda:now[0])
            inventory={'outlets':[{'number':1,'on':True}]}
            history.observe('a',inventory);now[0]+=8*86400
            self.assertEqual(len(history.observe('a',inventory)['series'][0]['points']),1)

    def test_event_markers_do_not_imply_continuous_state_and_use_site_zone(self):
        configure_site_timezone({'site_timezone':'America/Los_Angeles'})
        output=history_chart({'name':'Power','unit':'state','points_only':True,'points':[
            {'time':'2026-11-04T03:00:00+00:00','value':1},
            {'time':'2026-11-04T03:10:00+00:00','value':0}]})
        self.assertEqual(output.count('class="chart-point"'),2)
        self.assertNotIn('class="chart-line"',output)
        self.assertIn('19:00',output);self.assertIn('PST',output)
        configure_site_timezone({'site_timezone':'America/New_York'})

    def test_internet_tile_is_featured_once_with_graphs_and_actual_speeds(self):
        snapshot={'readings':[{'name':'Green Ookla speed test Download','state':781.07,'unit':'Mbit/s'},
                              {'name':'Green Ookla speed test Upload','state':861.42,'unit':'Mbit/s'}],
                  'speed_test':{'provider':'ookla_addon'},'network_history':[]}
        report={'checks':[{'system':'Internet','status':'verified','snapshot':snapshot}]}
        output=grouped_cards(report)
        self.assertEqual(output.count('id="system-internet"'),1)
        self.assertIn('internet-feature',output)
        self.assertIn('class="system-tile" open',output)
        self.assertIn('781.07 Mbit/s',output)
        self.assertIn('861.42 Mbit/s',output)
        self.assertIn('network-history-grid',output)
        self.assertLess(output.index('id="system-internet"'),output.index('ap-heading'))


if __name__=='__main__':unittest.main()
