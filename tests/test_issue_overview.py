import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
from issue_overview import IssueLog, build, iso, log_markup, RETENTION
from agent import Pilot, page


class IssueOverviewTests(unittest.TestCase):
    def pilot(self,report=None,**extra):
        values=dict(current_service_report=lambda:report,green_snapshot=None,green_history={},network_history={},
            usb_ups=SimpleNamespace(status=lambda:{'enabled':False}),power_status=lambda:{'enabled':False},
            network_trial=SimpleNamespace(snapshot=lambda:{'samples':[],'current':None}))
        values.update(extra);return SimpleNamespace(**values)

    def test_audio_unavailable_is_not_inferred_from_idle_or_unknown(self):
        report={'checks':[{'system':'Sonos','status':'review','completed_at':iso(1800000000),'snapshot':{'players':[
            {'entity_id':'media_player.a','state':'unavailable','name':'Office'},
            {'entity_id':'media_player.b','state':'idle'},
            {'entity_id':'media_player.c','state':'unknown'}]}}]}
        rows,issues,evaluated=build(self.pilot(report),1800000000)
        self.assertEqual(next(r['title'] for r in rows if r['system']=='Sonos'),'1 zone unavailable')
        self.assertEqual(len(issues),1)
        self.assertNotIn('Sonos',evaluated)
        self.assertNotIn('offline',issues[0]['title'])

    def test_speed_uses_previous_tests_and_requires_three(self):
        history={'green':{'down':{'name':'Green speed test Download','unit':'Mbps','points':[
            {'time':iso(1800000000+i*60),'value':v} for i,v in enumerate((100,100,100,50))]}}}
        pilot=self.pilot(green_history=history)
        _,issues,_=build(pilot,1800000200)
        self.assertTrue(any('50% below average' in r['title'] for r in issues))
        history['green']['down']['points'].pop(0)
        self.assertFalse(build(pilot,1800000200)[1])

    def test_missing_voltage_is_not_phase_loss_and_measured_pair_is_possible(self):
        readings=[{'name':'L1 Voltage','entity_id':'sensor.l1_voltage','state':'unavailable','attributes':{'unit_of_measurement':'V'}},
            {'name':'L2 Voltage','entity_id':'sensor.l2_voltage','state':'120','attributes':{'unit_of_measurement':'V'}}]
        pilot=self.pilot({'checks':[{'system':'Sense','status':'review','completed_at':iso(1800000000),'snapshot':{'readings':readings}}]})
        self.assertFalse(build(pilot,1800000000)[1])
        readings[0]['state']='0'
        self.assertTrue(any(r['title']=='Possible missing phase · L1' for r in build(pilot,1800000000)[1]))
        readings[0]['state']='140'
        self.assertTrue(any('High voltage' in r['title'] for r in build(pilot,1800000000)[1]))

    def test_empty_checks_never_claim_all_systems_healthy(self):
        rows,issues,evaluated=build(self.pilot(),1800000000)
        self.assertEqual(len(rows),10)
        self.assertTrue(all(r['state']=='unknown' for r in rows))
        self.assertEqual(issues,[])
        self.assertEqual(evaluated,{})

    def test_journal_deduplicates_survives_restart_and_resolves_only_new_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'issues.json';now=1800000000
            log=IssueLog(path,clock=lambda:now)
            issue=dict(key='sonos:a',scope='Sonos',system='Sonos',title='<Office> unavailable',at=iso(now),active=True)
            log.update([issue],{'Sonos':iso(now)})
            log.update([issue],{'Sonos':iso(now)})
            self.assertEqual(len(log.items),1)
            restarted=IssueLog(path,clock=lambda:now+60)
            restarted.update([],{})
            self.assertEqual(restarted.items[0]['state'],'Open')
            restarted.update([],{'Sonos':iso(now)})
            self.assertEqual(restarted.items[0]['state'],'Open')
            restarted.update([],{'Sonos':iso(now+60)})
            self.assertEqual(restarted.items[0]['state'],'Recovered')
            self.assertEqual(restarted.items[0]['first_seen'],iso(now))
            self.assertIn('&lt;Office&gt;',log_markup(restarted))
            self.assertIn('Recovered:',log_markup(restarted))
            expired=IssueLog(path,clock=lambda:now+RETENTION+120)
            expired.update([],{})
            self.assertEqual(expired.items,[])

    def test_power_events_are_retained_with_their_source_times(self):
        now=1800000000
        power={'enabled':True,'status':'verified','checked_at':iso(now),'current':{'connected':True,'power_lost':False},
            'events':[{'kind':'input_power_lost','observed_at':iso(now-120)},{'kind':'input_power_restored','observed_at':iso(now)}]}
        rows,issues,_=build(self.pilot(power_status=lambda:power),now)
        self.assertTrue(any('losses today: 1' in r['title'] for r in rows))
        with tempfile.TemporaryDirectory() as directory:
            log=IssueLog(Path(directory)/'issues.json',clock=lambda:now)
            log.update(issues,{})
            log.update(issues,{})
            self.assertEqual(len(log.items),2)
            self.assertTrue(all(r['state']=='Recorded' for r in log.items))
            self.assertEqual(log.items[0]['first_seen'],iso(now-120))

    def test_corrupt_journal_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'issues.json';path.write_text('{bad')
            log=IssueLog(path);log.update([],{})
            self.assertTrue(log.error)
            self.assertEqual(path.read_text(),'{bad')

    def test_overview_and_settings_log_render_in_real_page(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);options=root/'options.json';options.write_text('{}')
            pilot=Pilot(root,options)
            output=page(pilot)
            self.assertIn('aria-label="System status"',output)
            self.assertIn('data-overview-view="av"',output)
            self.assertGreater(output.index('id="issue-log"'),output.index('id="connections"'))
            self.assertIn('Last 90 days',output)


if __name__=='__main__':unittest.main()
