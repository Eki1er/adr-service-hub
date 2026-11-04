import sys
import unittest
from html.parser import HTMLParser
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'adr_service_agent'))
from dashboard import grouped_cards

class Targets(HTMLParser):
    def __init__(self,text):
        super().__init__();self.rows=[];self.feed(text)
    def handle_starttag(self,tag,attrs):
        row=dict(attrs)
        if 'data-check-status' in row:self.rows.append(row)

class SummaryTargets(unittest.TestCase):
    def test_scheduled_network_diagnostic_has_target(self):
        report={'checks':[{'system':'Network diagnostics','status':'unverified',
            'service_findings':['Probe <failed>'],'data_limitations':[]}]}
        markup=grouped_cards(report,diagnostics_markup='<section id="diagnostics"></section>')
        matches=[r for r in Targets(markup).rows if r['data-check-status']=='unverified']
        self.assertEqual([r['id'] for r in matches],['network-diagnostic-check'])
        self.assertIn('Probe &lt;failed&gt;',markup)
    def test_pending_cards_do_not_count_as_unverified(self):
        targets=Targets(grouped_cards(None)).rows
        self.assertTrue(targets);self.assertTrue(all(r['data-check-status']=='pending' for r in targets))
