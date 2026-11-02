import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]/'adr_service_agent';sys.path.insert(0,str(ROOT))
from agent import Pilot,page
from diagnostic_hub import evidence


def snapshot(stamp,cpu=12):
    return {'checked_at':stamp,'readings':[{'name':'Environment','command':'show env all','status':'response received',
        'response':'Current CPU Usage : '+str(cpu)+'%\nPRIVATE-CONSOLE','truncated':False}], 'discovery':{}}

class AraknisEvidence(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'options.json').write_text('{}');self.pilot=Pilot(self.root,self.root/'options.json')

    def test_individual_check_is_available_without_combined_report(self):
        self.pilot.araknis_diagnostics=snapshot('2026-11-02T20:00:00+00:00')
        out=self.pilot.diagnostics.report_provider();row=out['summaries'][0]
        self.assertEqual(row['system'],'Araknis');self.assertEqual(row['responses_received'],1)
        self.assertEqual(row['evidence_source'],'individual_diagnostic');self.assertIn('CPU 12%',row['readings'])
        self.assertNotIn('PRIVATE-CONSOLE',json.dumps(out));self.assertIsNone(self.pilot.service_report)

    def test_newer_individual_replaces_only_older_araknis_in_analysis(self):
        report={'format':'adr-service-report-v1','completed_at':'2026-11-02T19:00:00+00:00',
            'checks':[{'system':'Araknis','completed_at':'2026-11-02T19:00:00+00:00','status':'unverified','snapshot':None},
                {'system':'WattBox','completed_at':'2026-11-02T19:00:00+00:00','snapshot':{'outlets':[]}}]}
        original=copy.deepcopy(report)
        out=evidence(report,{},individual={'Araknis':snapshot('2026-11-02T20:00:00+00:00')})
        self.assertEqual([r['system'] for r in out['summaries']],['WattBox','Araknis'])
        self.assertEqual(report,original);self.assertEqual(out['summaries'][-1]['status'],'verified')

    def test_newer_combined_result_and_invalid_individual_timestamps_are_preserved(self):
        report={'format':'adr-service-report-v1','checks':[{'system':'Araknis','completed_at':'2026-11-02T21:00:00+00:00',
            'status':'verified','snapshot':snapshot('2026-11-02T21:00:00+00:00',22)}]}
        for stamp in ('2026-11-02T20:00:00+00:00','invalid','2026-11-02T20:00:00'):
            row=evidence(report,{},individual={'Araknis':snapshot(stamp)})['summaries'][0]
            self.assertIn('CPU 22%',row['readings']);self.assertEqual(row['evidence_source'],'combined_report')

    def test_login_help_does_not_count_as_diagnostics_and_power_section_removed(self):
        self.pilot.araknis={'welcome':'(switch)#','help':'show ?','checked_at':'2026-11-02T20:00:00+00:00','truncated':False}
        self.assertEqual(self.pilot.diagnostics.report_provider(),{})
        text=page(self.pilot)
        for key in ('adr-power-status','adr-power-events','adr-power-event-list'):self.assertNotIn('id="'+key+'"',text)
        self.assertIn('id="adr-native-history"',text)
