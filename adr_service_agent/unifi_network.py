"""Read-only UniFi Network controller adapter using existing pinned local access.

These are controller routes used by aiounifi, not a claim of stability in the
public Integration API. Exact key/firmware compatibility needs a live check.
"""
from datetime import datetime,timezone
from network_health import base,unifi_devices,unifi_networks
from unifi_gateway import settings,request,target,GatewayError,GatewayTrustError,GatewayAuthenticationError


def collect(options,pin,request_fn=request):
    stamp=datetime.now(timezone.utc).isoformat(timespec='seconds')
    out=base('UniFi',stamp)
    try:
        config=settings(options)
        if not isinstance(pin,dict):raise GatewayTrustError('Approve the UniFi gateway certificate in Connections first.')
        out['target_id']=target(config,pin)
    except GatewayError as exc:
        out['limitations'].append(str(exc));return out
    for operation,normalize in (('devices',unifi_devices),('networks',unifi_networks)):
        try:
            body=request_fn(config,pin,'GET',operation)
            if body.get('meta',{}).get('rc')!='ok':raise ValueError()
            normalize(body.get('data'),out)
        except GatewayAuthenticationError as exc:
            out['limitations'].append(str(exc))
            break
        except GatewayTrustError:
            out['limitations'].append('UniFi network reads stopped: approve the current gateway certificate in Connections. API key was not sent.')
            break
        except (GatewayError,ValueError,TypeError,AttributeError):
            # Preserve the independent successful read. Never include a raw
            # response, credentials, or provider exception text in evidence.
            out['limitations'].append('UniFi '+operation+' read unverified. Check local key access and firmware compatibility; no retry.')
    out['limitations'].append('STP root, IGMP querier/memberships, and loop-event delivery need additional verified sources; unavailable fields are not healthy readings.')
    return out
