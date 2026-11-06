# ADR Service Agent: Network Monitoring Roadmap

Proposed by Esad Becaj (ADRI Media Integrators) for review. This is a planning document only: it changes no code, and nothing here is final until we agree on it.

Baseline: ADR Service Agent 0.29.0 (main, commit 2585c83).

## Goal

Turn ADR Service Agent into the on-site half of a full MSP-grade network monitor for ADRI client sites:

- **On-site (this app):** reads Home Assistant, WattBox, UPS, Control4, Crestron, UniFi, Ruckus, Araknis, Sonos and Bluesound; keeps history; takes approved local actions.
- **Central (ADRI Dell hub + Network Monitoring bot):** checks from outside the site, alerting, the multi-client view, monthly ADRI-branded PDF reports, and triage.

## What 0.29.0 already does well

- An issue-first Overview and a persistent 90-day Issue log with first-seen, last-seen and recovery times.
- Device discovery (manufacturer, hostname, MAC, IP), refreshed every 20 minutes when enabled.
- Internet speed history (UniFi gateway and the Ookla add-on); a 24-hour opt-in ping trial.
- WattBox voltage and current history, outlet control with confirmation, cooldown, post-action verification and an audit log.
- USB/NUT UPS events (30 days); syslog ingestion from approved source IPs (30 days).
- UniFi STP and multicast evidence, read-only.

## Roadmap (priority order)

### P1: Foundations for continuous monitoring

1. **Continuous ping monitoring.** Add an "always on" mode with no 24-hour cutoff. Persist its state so it resumes by itself after a restart or an update.
   - Targets: gateway, first ISP hop, 1.1.1.1 and 8.8.8.8, plus configured key devices.
   - Speed tests should defer around it rather than being blocked.
2. **Longer history.** Keep raw samples for 7 days and hourly rollups (average, min, max, loss %) for at least 90 days, so monthly and quarterly reports have the data they need.
3. **App self-restart.** Enable the Home Assistant add-on `watchdog`, and document the expected behavior after a crash.
4. **Read-only data access for the central hub.** Add an authenticated, read-only JSON endpoint (or a scheduled export) for the Issue log, the history rollups, the inventory and the site record, so the Network Monitoring bot can read them over the VPN or Tailscale instead of using manual downloads.

### P2: ISP and circuit tracking

5. **Site record.** Add configuration fields for:
   - ISP name, plan download/upload speeds, circuit or account ID, public IP (static or dynamic), ISP support phone, backup ISP, and failover type.
6. **ISP fault attribution.** Use layered probes (LAN gateway, ISP first hop, public resolvers) to label each outage as LAN, CPE/modem, ISP or upstream.
7. **WAN events.** Ingest WAN up/down and failover events from UniFi (and Araknis via syslog), so each outage has exact start and end times.

### P3: Network-level visibility

8. **SNMP read-only polling** of switches, routers and APs:
   - Interface up/down, traffic, errors and discards, PoE draw, CPU, memory, temperature and uptime.
   - The central hub may run LibreNMS for this instead; agree which side owns it.
9. **Bandwidth per link:** WAN utilization and switch uplinks, with 95th-percentile values in the monthly report.
10. **Wi-Fi health** via the UniFi and Ruckus controllers: clients per AP, RSSI distribution, channel utilization, interference and AP uptime.
11. **Inventory change tracking:** alert on a new unknown device, a missing known device, and IP/MAC changes, building on discovery.

### P4: Firmware and configuration

12. **Firmware currency:** compare installed firmware with the vendor's latest release (UniFi, Ruckus, Araknis/OvrC, WattBox, Control4) and flag "update available".
    - No automatic upgrades. Upgrades stay in the vendor controllers after ADRI approval.
13. **Config backup hooks:** nightly and pre-upgrade config backups where the vendor supports them (around 30 days kept), with change detection.

### P5: Alerting and recovery

14. **Alert events.** Emit alert and recovery events using the rule "2 consecutive failures before alert", with deduplication and parent/child suppression (for example, a switch down suppresses the devices behind it).
    - Events go to the central hub and optionally to a Home Assistant notify target.
15. **Approved automatic recovery:**
    - Per-device rules, off by default (for example, "outlet 3 powers the modem; power-cycle allowed").
    - Trigger: 2 failed checks. Limit: 1 attempt, then a 30–60 minute cooldown, then escalate to an alert instead of looping.
    - Paused during speed tests, updates and maintenance windows. Every attempt and its result goes in the Issue log.
16. **Maintenance windows** per site, to mute alerts during planned work.

### P6: Reporting (central hub)

17. **Monthly ADRI Media Integrators PDF per client:**
    - Uptime, incidents and MTTR, per-ISP performance against plan, bandwidth trends, inventory changes, power events, firmware status and recommendations.
    - The app provides the data (item 4); the central hub renders the PDFs.

## Out of scope for the app

- Outside-in checks (the Dell hub with Uptime Kuma), multi-client dashboards, PDF rendering and alert delivery to phone or chat. These run centrally.

## Open questions

- SNMP: in the app, or on the central LibreNMS?
- Endpoint auth: HA token, or a separate API key?
- Retention and storage limits on small HA boxes.
- Who owns break-fix response, and what response times apply per client tier?
