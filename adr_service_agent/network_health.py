"""Common bounded network evidence. No absent field is a healthy reading."""
import math
import re

COUNTERS=('rx_errors','tx_errors','rx_dropped','tx_dropped','rx_broadcast','tx_broadcast','rx_multicast','tx_multicast')


def number(value):
    return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None


def label(value,limit=80):
    return ''.join(c for c in str(value or '') if ord(c)>=32)[:limit]


def base(vendor,checked_at):
    return {'vendor':vendor,'checked_at':checked_at,'devices':[],'networks':[],
        'coverage':{'devices':'unverified','multicast_settings':'unverified','stp_root':'unverified',
            'igmp_memberships':'unverified','igmp_querier':'unverified','loop_events':'unverified'},
        'observations':[],'findings':[],'limitations':[
            'Controller inventory covers its managed devices only, not every switch on the LAN.',
            'A blocked STP port can be normal redundancy protection, not an active loop.',
            'Multicast counters are cumulative; high totals do not establish flooding.',
            'IGMP snooping configuration does not establish active group membership or querier health.',
            'Device discovery and media streaming use different paths; one does not verify the other.',
            'No spanning-tree, VLAN, multicast, mesh, or port settings were changed.']}


def unifi_devices(data,out):
    if not isinstance(data,list) or len(data)>512:raise ValueError('Unexpected device inventory')
    out['coverage']['devices']='response received'
    if len(data)>64:out['limitations'].append('Device inventory limited to 64 rows.')
    for raw in data[:64]:
        if not isinstance(raw,dict):continue
        # Controllers expose switches, APs and gateways in one response. Keep
        # bridge-port evidence separate from AP/radio operation.
        if raw.get('type') not in ('usw','uap','ugw','udm','uxg'):continue
        mac=str(raw.get('mac','')).lower()
        if not re.fullmatch(r'[0-9a-f]{2}(?::[0-9a-f]{2}){5}',mac):continue
        device={'id':mac,'name':label(raw.get('name') or raw.get('model') or mac),
            'type':raw['type'],'state':'online' if type(raw.get('state')) is int and raw['state']==1 else 'offline' if type(raw.get('state')) is int and raw['state']==0 else 'unverified',
            'ports':[]}
        if type(raw.get('uptime')) is int and raw['uptime']>=0:device['uptime_seconds']=raw['uptime']
        for src,dst in (('stp_priority','stp_priority'),('stp_version','stp_protocol')):
            if isinstance(raw.get(src),(str,int)) and not isinstance(raw.get(src),bool):device[dst]=label(raw[src],24)
        table=raw.get('port_table',[])
        if not isinstance(table,list):table=[]
        if len(table)>256:out['limitations'].append(device['name']+': port table limited to 256 rows.')
        for port in table[:256]:
            if not isinstance(port,dict) or type(port.get('port_idx')) is not int or not 0<=port['port_idx']<=4096:continue
            p={'port':port['port_idx'],'name':label(port.get('name') or 'Port '+str(port['port_idx']))}
            if type(port.get('up')) is bool:p['link']='up' if port['up'] else 'down'
            if number(port.get('speed')) is not None:p['speed_mbps']=port['speed']
            state=port.get('stp_state')
            if isinstance(state,str) and state.lower() in ('forwarding','blocking','discarding','learning','listening','disabled'):
                p['stp_state']=state.lower()
            for field in COUNTERS:
                if type(port.get(field)) is int and port[field]>=0:p[field]=port[field]
            device['ports'].append(p)
        out['devices'].append(device)
        if device['state']=='offline':out['findings'].append(device['name']+': controller reports offline; physical cause unverified.')
        blocked=[str(p['port']) for p in device['ports'] if p.get('stp_state') in ('blocking','discarding')]
        if blocked:out['observations'].append(device['name']+': STP blocked/discarding ports '+', '.join(blocked)+'. This alone is not a loop diagnosis.')


def unifi_networks(data,out):
    if not isinstance(data,list) or len(data)>512:raise ValueError('Unexpected network inventory')
    out['coverage']['multicast_settings']='response received'
    if len(data)>64:out['limitations'].append('Multicast network settings limited to 64 rows.')
    for raw in data[:64]:
        if not isinstance(raw,dict) or raw.get('purpose')=='wan':continue
        row={'name':label(raw.get('name') or 'Unnamed network')}
        if type(raw.get('vlan')) is int and 1<=raw['vlan']<=4094:row['vlan']=raw['vlan']
        if type(raw.get('igmp_snooping')) is bool:row['igmp_snooping']=raw['igmp_snooping']
        # Do not copy the network configuration: it may contain secrets. No
        # querier, memberships, or mDNS state is inferred from these settings.
        out['networks'].append(row)


def summary(out):
    devices=out.get('devices',[])
    switches=[d for d in devices if d.get('type')=='usw']
    lines=[]
    if out.get('coverage',{}).get('devices')=='response received':
        lines.append(str(len(switches))+' UniFi switches; '+str(len(devices))+' controller devices read')
        known=sum('stp_state' in p for d in devices for p in d.get('ports',[]))
        lines.append('STP port states: '+str(known)+' reported' if known else 'STP port states: unverified')
    lines+=out.get('observations',[])[:4]
    rows=[n for n in out.get('networks',[]) if 'igmp_snooping' in n]
    if rows:lines.append('IGMP snooping settings read for '+str(len(rows))+' networks')
    return lines


def metrics(out):
    """Target-scoped comparison keys prevent mixing different controllers."""
    target=out.get('target_id')
    if not target:return {}
    values={}
    for device in out.get('devices',[]):
        prefix='network:'+target+':'+device['id'].replace(':','')+':'+device['name'].replace(':','_')+':'
        if device.get('stp_root_id'):values[prefix+'root']=device['stp_root_id']
        if type(device.get('topology_changes')) is int:values[prefix+'topology_changes']=device['topology_changes']
        for port in device.get('ports',[]):
            key=prefix+'port'+str(port['port'])+':'
            for field in COUNTERS:
                if type(port.get(field)) is int:values[key+field]=port[field]
            for field in ('link','stp_state'):
                if field in port:values[key+field]=port[field]
    return values
