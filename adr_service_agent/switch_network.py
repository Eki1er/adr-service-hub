"""Bounded switch evidence: advertised display commands only, no configuration."""
import ipaddress
import re
import time
from datetime import datetime, timezone

# Exact paths, including every parent needed for help traversal. Never accept
# numbers, interface names, free text, pipes, or commands supplied by a model.
PATHS = {
    'show spanning-tree': ('summary', 'detail', 'active', 'root'),
    'show spanning-tree summary': (), 'show spanning-tree detail': (),
    'show spanning-tree active': (), 'show spanning-tree root': (),
    'show loop-protection': ('status',), 'show loop-protection status': (),
    'show loop-detection': ('status',), 'show loop-detection status': (),
    'show ip': ('igmp',), 'show ip igmp': ('snooping',),
    'show ip igmp snooping': ('groups', 'mrouter', 'querier', 'statistics'),
    'show ip igmp snooping groups': (), 'show ip igmp snooping mrouter': (),
    'show ip igmp snooping querier': (), 'show ip igmp snooping statistics': (),
    'show mac-address-table': ('count',), 'show mac-address-table count': (),
    'show lldp': ('neighbors',), 'show lldp neighbors': (),
}
ROOTS = ('spanning-tree', 'loop-protection', 'loop-detection', 'ip', 'mac-address-table', 'lldp')
ERROR = re.compile(r'incomplete command|invalid (?:input|command)|ambiguous command|unrecognized command|unknown command|syntax error|%\s*error', re.I)
MAC = r'(?:[0-9a-f]{2}(?::[0-9a-f]{2}){5}|[0-9a-f]{4}(?:\.[0-9a-f]{4}){2})'


def options(text):
    return set(re.findall(r'(?m)^\s*([A-Za-z][A-Za-z0-9-]*)[ \t]{2,}\S', text))


def category(command):
    if 'spanning-tree' in command: return 'spanning_tree'
    if 'loop-' in command: return 'loop_protection'
    if 'igmp snooping' in command: return 'multicast'
    return 'topology'


def collect(request, secret='', progress=lambda _: None, max_probes=20, max_reads=8, seconds=90):
    """request(command, help_only) uses the existing pinned SSH transport."""
    deadline=time.monotonic()+seconds
    probes=[]; readings=[]; queue=['show']; seen=set(); limited=False
    def clean(text): return text.replace(secret,'[redacted]') if secret else text
    while queue:
        if len(probes)>=max_probes or time.monotonic()>=deadline:
            limited=True; break
        command=queue.pop(0)
        if command in seen: continue
        seen.add(command)
        progress('Inspecting '+command+' syntax')
        probe={'command':command+' ?','truncated':False,'error':''}
        try:
            text,partial=request(command,True)
            probe.update(response=clean(text),truncated=partial)
            probes.append(probe)
            if partial or ERROR.search(text): continue
            advertised=options(text)
            children=ROOTS if command=='show' else PATHS[command]
            for token in children:
                child=command+' '+token
                if token in advertised and child in PATHS and child not in seen:queue.append(child)
            # Root help and show ip/igmp are never executed. Other paths must
            # explicitly advertise the no-argument display form on this device.
            if command in ('show','show ip','show ip igmp') or not re.search(r'(?im)^\s*<cr>(?:\s|$)',text):continue
            if len(readings)>=max_reads or time.monotonic()>=deadline:
                limited=True; continue
            progress('Reading '+command)
            item={'name':command[5:],'command':command,'status':'response received','response':'','truncated':False,'error':''}
            try:
                response,partial=request(command,False)
                item.update(response=clean(response),truncated=partial)
                if not response or ERROR.search(response):item['status']='command needs verification'
            except Exception as exc:
                # Host-key/authentication errors must be handled by the caller,
                # never retried as another command.
                if getattr(exc,'stop_network_collection',False):raise
                item.update(status='read failed',error='Independent read failed; no retry.')
            readings.append(item)
        except Exception as exc:
            if getattr(exc,'stop_network_collection',False):raise
            if probe not in probes:
                probe.update(response='',error='Syntax-help read failed; no retry.');probes.append(probe)
    return {'checked_at':datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'probes':probes,'readings':readings,'limit_reached':limited,'remaining_paths':len(queue),
        'max_probes':max_probes,'max_reads':max_reads}


def analyze(collection):
    """Parse explicit fields only. Unfamiliar firmware output remains unparsed."""
    out={'checked_at':collection.get('checked_at'),'coverage':{},'spanning_tree':{},
        'multicast':{},'observations':[],'findings':[],
        'limitations':['A blocked STP port may be normal redundancy protection, not an active loop.',
            'One switch does not establish the full network topology or multicast delivery.',
            'Multicast memberships are snapshots; discovery and media streaming are different paths.',
            'No loop, querier, VLAN or port configuration was changed.']}
    for family in ('spanning_tree','loop_protection','multicast','topology'):
        out['coverage'][family]={'complete_reads':0,'partial_reads':0,'state':'unverified'}
    for item in collection.get('readings',[]):
        command=item.get('command','')
        if command not in PATHS:continue
        text=item.get('response','')
        cov=out['coverage'][category(command)]
        if item.get('truncated'):
            cov['partial_reads']+=1;continue
        if item.get('status')!='response received' or item.get('error') or ERROR.search(text):continue
        cov['complete_reads']+=1;cov['state']='response received'
        if category(command)=='spanning_tree':
            stp=out['spanning_tree']
            root=re.search(r'(?im)^\s*Root (?:Bridge (?:ID|Address)|ID|Address)\s*[:=]\s*(?:\d+\s+)?('+MAC+r')\s*$',text)
            if root:stp['root_id']=root[1].lower()
            changes=re.search(r'(?im)^\s*(?:Number of topology changes|Topology change count|Topology changes)\s*[:=]\s*(\d+)\s*$',text)
            if changes:stp['topology_changes']=int(changes[1])
            protocol=re.search(r'(?im)^\s*(?:Spanning Tree Protocol|Protocol|STP Mode)\s*[:=]\s*(RSTP|MSTP|STP|802\.1[dsw])\s*$',text)
            if protocol:stp['protocol']=protocol[1].upper()
            ports=[]
            for port,role,state in re.findall(r'(?im)^\s*((?:Gi|Ex|Te|Eth|Po)\S*)\s+(Root|Desg|Designated|Altn|Alternate|Backup|Disabled)\s+(FWD|Forwarding|BLK|Blocking|Discarding|Learning|Disabled)\b',text):
                ports.append({'port':port[:40],'role':role.lower(),'state':state.lower()})
            if ports:stp['ports']=ports[:64]
        elif category(command)=='loop_protection':
            # Report only explicit positive detector output, not occurrence of
            # the word 'loop' in headers, help or negative statements.
            for port in re.findall(r'(?im)^\s*((?:Gi|Ex|Te|Eth|Po)\S*)\s+(?:Loop detected|Loop-detected)\s*$',text):
                out['findings'].append('Switch loop detector reports a loop on '+port[:40]+'. Verify the connected path before any port action.')
        elif category(command)=='multicast':
            mc=out['multicast']
            enabled=re.search(r'(?im)^\s*(?:Global )?IGMP Snooping(?: (?:Status|State))?\s*[:=]\s*(Enabled|Disabled)\s*$',text)
            if enabled:mc['snooping']=enabled[1].lower()
            querier=re.search(r'(?im)^\s*(?:Querier (?:IP )?Address|Querier IP)\s*[:=]\s*(\d+\.\d+\.\d+\.\d+)\s*$',text)
            if querier:
                try:
                    ip=ipaddress.IPv4Address(querier[1])
                    if not ip.is_unspecified and not ip.is_multicast:mc['querier_address']=str(ip)
                except ValueError:pass
            # A row must begin with VLAN + multicast IPv4, not a stray IP in
            # a help/example line. No empty table is interpreted as 'no joins'.
            groups=[]
            if command.endswith(' groups'):
                for vlan,group,tail in re.findall(r'(?m)^\s*(\d{1,4})\s+(\d+\.\d+\.\d+\.\d+)\s+([^\n]+)$',text):
                    try:
                        ip=ipaddress.IPv4Address(group)
                        if not ip.is_multicast or not 1<=int(vlan)<=4094:continue
                    except ValueError:continue
                    ports=re.findall(r'\b(?:Gi|Ex|Te|Eth|Po)[A-Za-z0-9/.-]+\b',tail)
                    if ports:groups.append({'vlan':int(vlan),'group':str(ip),'ports':ports[:32]})
            if groups:mc['groups']=groups[:64]
    stp=out['spanning_tree'];mc=out['multicast']
    if 'root_id' in stp:out['observations'].append('STP root: '+stp['root_id'])
    if 'topology_changes' in stp:out['observations'].append('STP topology changes: '+str(stp['topology_changes'])+' (cumulative)')
    if stp.get('ports'):
        blocked=[p['port'] for p in stp['ports'] if p['state'] in ('blk','blocking','discarding')]
        out['observations'].append('STP port states read: '+str(len(stp['ports']))+'; blocked/discarding: '+(', '.join(blocked) if blocked else 'none in parsed rows'))
    if 'snooping' in mc:out['observations'].append('IGMP snooping: '+mc['snooping'])
    if 'querier_address' in mc:out['observations'].append('Reported IGMP querier: '+mc['querier_address'])
    if mc.get('groups'):out['observations'].append('Multicast membership rows parsed: '+str(len(mc['groups'])))
    for family,cov in out['coverage'].items():
        if not cov['complete_reads']:out['limitations'].append(family.replace('_',' ').capitalize()+': no complete supported display response; unverified.')
    if out['coverage']['spanning_tree']['complete_reads'] and not stp:out['limitations'].append('STP response received but fields were not recognized; no health conclusion.')
    if out['coverage']['multicast']['complete_reads'] and not mc:out['limitations'].append('Multicast response received but fields were not recognized; querier and membership remain unverified.')
    if collection.get('limit_reached'):out['limitations'].append('Network display discovery reached its bound; unvisited paths remain unverified.')
    return out
