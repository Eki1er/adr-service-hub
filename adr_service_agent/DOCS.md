## Device discovery identity and web links (0.26.0)

Open Network → Diagnostics → Discover devices. Discovery remains scoped to one
configured private IPv4 subnet of /24 or smaller and sends one ICMP echo per
address with four workers. Only responding addresses are enriched. The table shows
manufacturer, hostname, MAC address, IP address, reported device type and identity
source. Unknown means the available evidence did not identify that field.

Identity uses two bounded GET reads through the existing pinned UniFi gateway
connection: active clients (`stat/sta`) and managed devices (`stat/device`). These
legacy controller routes may reject the existing API key on some firmware; no
login fallback, new credentials, or certificate bypass is attempted. Only fixed
identity fields are retained. Managed-device type codes supply known roles; numeric
fingerprint IDs are not guessed. Vendor OUI text is ignored for locally assigned
MACs. Identity is controller-reported metadata, not a fresh device login.

If needed, an exact IP match in the app container's local ARP table supplies a MAC;
a routed next hop is never used as the remote device's MAC. Reverse DNS runs in a
bounded child process with a two-second timeout and at most four workers. Conflicting
MAC records suppress identity rather than choosing one. Subnets outside the
container's local segment may have no ARP records. Manufacturer/hostname coverage
therefore needs live testing against the installed gateway and DNS setup.

After a manual discovery, the last selected allowed subnet refreshes every 20
minutes while both automatic monitoring and diagnostics are enabled. Existing
saved discovery history can supply the prior subnet on upgrade. No other subnet is
inferred or scanned. Attempts are persisted before launching, scans defer while
other checks/power actions are active, there is no overlapping scan or catch-up
queue, and startup waits at least 60 seconds. Disable automatic_monitoring to stop
recurring jobs. Manual discovery remains available. Failed attempts wait for the
next interval. Connectivity/system checks do not erase the last discovery table.

On responders only, bounded TCP connections check ports 80 and 443 (one-second
socket timeout each, four workers). The configured WattBox remains HTTP-only unless
its existing HTTPS opt-in is set. Reachable ports get Open HTTP/Open HTTPS links;
no web page is fetched, certificate is approved, credentials are sent or login is
attempted. The browser opens only on click in a new tab. Links use the literal
private IP and require LAN/VPN access from the user's device; HTTPS can show the
device's certificate warning. An open TCP port does not prove a usable web UI.

Traffic is bounded to one ping per scoped host and two TCP connections per
responder, plus the two controller reads and any reverse-DNS lookups. This is not a
bandwidth test or general port scan. Enriched results use the existing seven-day,
96-sample history with an additional size bound below 2 MiB; older samples can be
pruned earlier when identity records are large. IP reuse clears prior identity
fields, and identity timestamps remain separate from later ping observations.

Install only after the current 24-hour trial completes: app installation restarts
the agent and interrupts the trial. This revision has not run on the live Green.
Validate manufacturer/hostname/MAC against the gateway, verify links locally, and
confirm a second scan appears after 20 minutes without starting a speed test.

## Network Monitoring trial (0.25.0)

Update ADR Service Agent to 0.25.0 and reopen its Web UI. Open **Network → Network
Monitoring → Start 24-hour trial**. Uses the existing `unifi_gateway_host` setting;
it must contain a private, non-loopback unicast IP address (the pilot gateway is
192.168.16.1). No new options, API key, certificate approval or separate app is
required for ICMP. This setting identifies the target; the trial makes no router
configuration changes.

The Green sends five ICMP echo probes to each of the gateway, 1.1.1.1 and 8.8.8.8
once per minute. Three targets run concurrently with a nine-second subprocess
timeout each, no shell and no automatic retry. At most one sample batch is active.
The first five minutes are marked as baseline. Baseline latency is weighted by
received packets; loss uses confirmed sent/received counts. Failed/malformed
commands remain unknown, never zero loss or confirmed outages. Chart paths break
on missing values or more than 90 seconds between samples. This is endpoint
sampling, without per-hop route tracing or precise outage durations.

Collection runs with the page closed and stops at the original 24-hour deadline
(also bounded by a monotonic timer). Duplicate Start clicks do not extend it.
**Stop trial** stops new batches and retains history. In-flight bounded probes
can finish after Stop but do not append a sample. Stop and download remain
available while other checks run. An app restart or shutdown interrupts the trial;
start explicitly again afterward. No missed samples are fabricated or replayed.

Private app storage retains seven days of history, bounded to 10,080 batches and
32 trials; the next write prunes expired data. Charts show the latest trial and
**Download monitoring history** exports all retained trials with target addresses
and measurement timestamps. The file contains no API keys or app options. Protect
it as network diagnostic data. A corrupt existing file is left unchanged and blocks
new trials. Storage failures stop collection with an explicit warning.

ADR's manual and scheduled speed tests are deferred while the trial is running;
existing read-only system checks and UPS monitoring continue. Scheduled speed tests
resume on their original due times after Stop/completion, without a catch-up queue.
Other apps' own test schedules are not changed. The trial never calls speed-test,
device-control or configuration APIs. It works even if regular automatic monitoring
is disabled. Ingress authorization and CSRF apply to Start, Stop and download.

Live acceptance on Green: verify that the target shown is 192.168.16.1, start once,
confirm the deadline and increasing sample count, check replies against a wired
test, and inspect both charts. Stop once and verify the count stops increasing
while the history download remains available. Start a fresh trial and leave the
app running for the 24-hour check. ICMP filtering/deprioritization can resemble loss;
missing gateway or public replies do not establish a device or ISP fault. Automated
tests use simulated responses; Green build, ICMP permissions and the live 24-hour
run have not been observed from the development environment.

## WattBox electrical history and controls (0.24.6)

The WattBox tile shows total measured voltage (V) and current (A), with separate
history graphs. System checks read `?PowerStatus` over the existing approved SSH
connection. ASCII protocol v2.4 returns amps, watts, volts, and a safe-voltage flag;
readings are parsed in those units without scaling or estimating from outlet count.
This release uses whole-unit metering, not per-outlet metering or energy in kWh.

Metered history starts on collection, is retained for seven days with at most 512
samples, and is isolated by configured WattBox identity. Existing outlet-state
history is preserved on disk but is no longer collected or displayed. Unsupported
metering (including WB-150/250), rejected queries, and failed reads show unavailable
values and create explicit graph gaps. A failed meter read does not discard valid
outlet inventory. No electrical data has been verified on the live pilot yet.

Each outlet has a separate text and color status badge: On, Off, Unknown, or Awaiting
read. On / Off / Reset buttons use neutral styling rather than indicating state.
Existing permission settings, named confirmations, single-outlet commands and
post-action verification still apply. The redundant mobile header shortcuts are
removed; History and Connections remain in their existing sections.

Protocol reference:
https://www.snapav.com/wcsstore/ExtendedSitesCatalogAssetStore/attachments/documents/PowerManagement/ProtocolsAndDrivers/SnapAV_Wattbox_API_V2.4.pdf

## History chart display (0.24.5)

Network history uses paired download/upload lines and a shared legend and grid.
Use Show to display all readings or one series, and Period for 24 hours, a week,
or all retained observations. Periods end at the latest recorded reading, not at
an assumed current result. Controls only change the displayed chart; they do not
run checks. Selections persist during automatic dashboard refresh.

Lines connect recorded tests to make trends readable. Daily throughput and speed-test
ping observations can connect across up to 36 hours; longer gaps and explicitly
unavailable readings break the line. Connectivity latency uses a one-hour gap limit.
Hover a sample for its value and site-local measurement time. Green and UniFi
remain separate measurement sources; collection, test scheduling and retention
are unchanged.

UPS and WattBox history uses vertical observation bars instead of dots. Dashed
rails show unobserved time, not continuous confirmed power. UPS events retain
receipt-time chart positions and separate device-reported times in the event list.
This display does not invent outage durations or recover unrecorded history.

## UPS results and Ask ADR navigation (0.24.4)

Network opens with an expanded Internet tile showing Green download/upload,
Cloudflare/Google ICMP latency, sampled ICMP loss, DNS status and history graphs.
The existing seven-day speed and 24-hour latency history is retained. New ICMP-loss
and DNS-lookup timing series start on collection. Ookla results retain their own
measurement timestamps and remain separate from the Green connectivity probes.
UniFi gateway speed results/history remain under the UniFi tile, without mixing
those measurements into Green results.

Power shows USB UPS history as discrete markers of recorded line/battery states,
using receipt times on the chart axis. The event list separately shows source
reported times when available. Markers do not imply continuous monitoring between
events or precise outage durations. Missing states are not plotted as utility power.

WattBox history records actual outlet inventories collected by system checks,
with seven-day retention and a maximum of 512 samples. The graph shows total outlets
reported on and up to four outlets with observed changes. These are discrete
observations, not watts/volts or proof of connected-device operation. This history
starts with the first successful system check after the update; it cannot backfill
past outlet states. The saved target identity isolates replacement/different devices.
Unreadable existing history is preserved and a warning is shown. No extra device
queries or faster polling are added. Charts use the site's timezone.

Open Power to see the USB UPS status and the latest 12 recorded events. This uses
the existing NUT status/notification listener; no new device queries or settings
are introduced. The card covers reported line/battery status and history, not
additional charge, load, voltage or runtime sensors. Those remain available in
the underlying UPS integration and any separately collected WattBox readings.

Disconnected or errored monitoring shows current power status as unverified.
A connected listener alone is not a UPS heartbeat. Baseline records are labeled
as baselines, not outages/restorations. Reported and receipt times remain separate;
missing source timestamps are not invented. All dates use site_timezone. The
existing listener may miss brief events or events before monitoring started.

Ask ADR has an Overview tile, a compact tile on each category view, a sidebar
shortcut and a fifth bottom-bar item on mobile. All jump to the same Ask ADR form,
which remains available during system checks. Changing views does not create a
new conversation or reset the question/answer. Shortcuts only navigate; they do
not start microphone capture, submit questions, run checks or control equipment.
The existing background refresh preserves the shared Ask form.

Automated checks cover markup uniqueness, initial running-check navigation,
UPS event timestamps/baselines/disconnections, bounded escaped history and Ask
navigation without network actions. Mobile layout has not yet been tested on
the live Green ingress page.

## Ookla SpeedTest add-on provider (0.24.3)

Install https://github.com/MrSuicideParrot/hassio-speedtest-addon separately and
accept its license/privacy options yourself if you agree. Test it once and verify
that sensor.speedtest_download, sensor.speedtest_upload and sensor.speedtest_ping
match the log. The add-on posts server_id/server_name and bytes_received/bytes_sent
attributes; ADR requires that signature, matching server IDs, timestamps within
15 seconds of each other and no older than 24 hours. This is source-shape validation,
not cryptographic attestation. The current add-on version writes these exact sensor
IDs; renamed entities are not auto-selected.

On the original Speedtest.net integration entry, open the three-dot menu > System
options, turn off Enable polling for changes, and save. Keep that integration and
its three sensors. Disable any separate speed-test automations during validation.
In the SpeedTest add-on leave start on boot and watchdog off: it exits after each
test, and ADR controls the schedule. ADR does not change those settings for you.

In ADR Configuration choose green_speedtest_provider: ookla_addon, save and restart
ADR. Run All Checks reads the existing Ookla sensor results without starting a test.
Run Ookla Speed Test and scheduled speed tests send one fixed Core service request:
POST /api/services/hassio/addon_start, addon 6b87c29e_speedtest_addon. ADR retains
hassio_api: false: it calls only this Core service, not the Supervisor management
API. Actual Core authorization and the installed app identity require live testing.
The add-on must be the repository above; no arbitrary app slug is accepted.

ADR then polls the three states for up to 150 seconds. All must have new timestamps
at or after the request, be newer than the prior result, and pass source-shape checks.
A start timeout is ambiguous and is never retried; inspect the add-on log. A sensor
read never invokes update_entity or starts the built-in Python test. No fallback to
Cloudflare or gateway measurements happens when Ookla values are unverified.

Green probe/DNS checks and UniFi gateway tests remain separate. Green history is
isolated by speed-test provider and server, avoiding comparisons across server
changes. Cloudflare remains selectable explicitly. No official Ookla binary is
bundled or redistributed by ADR; acceptance and commercial usage rights remain
subject to Ookla's terms. Client rollout licensing remains unverified.

Source inspected: speedtest/rootfs/etc/services.d/speedtest/run in the add-on
repository, and homeassistant/components/hassio/services.yaml in Home Assistant
Core. Automated fixture tests cover source checks, failed/mixed/stale values,
start semantics, deadline/freshness and provider routing. The ADR-to-add-on start
has not been tested on the live Green yet.

## Green Internet checks (0.24.2)

The Internet check runs on the Green/service host with any router brand. No UniFi
API key or gateway certificate approval is required for this check. Run All Checks
collects five ICMP probes each to 1.1.1.1 and 8.8.8.8, plus a bounded example.com
lookup through the host resolver. ICMP loss is a short sample, not proof of an ISP
fault; blocked probes stay unverified. DNS answers may be cached.

Under Network tools, Run Green Speed Test makes bounded HTTPS download/upload
measurements against speed.cloudflare.com. It uses ordinary certificate validation,
a fixed destination, no redirects, no automatic retries and no device commands.
The implementation measures complete application payload transfers after warmup;
it is not the browser Cloudflare measurement engine or Ookla Speedtest. Results
can be limited by Green CPU, Ethernet, other traffic and the endpoint. They do not
establish subscribed ISP speed, maximum line capacity or client Wi-Fi performance.
The endpoint paths are documented in Cloudflare's measurement engine source:
https://github.com/cloudflare/speedtest/tree/main/src/config

A run transfers at most 181 MB down and 86 MB up (267 MB payload total). It stops
increasing a direction's payload once a measured transfer takes two seconds.
There is a shared 90-second time budget with a socket operation timeout up to ten
seconds; routine connectivity probes can take a further 23 seconds. Failed or
incomplete directions remain unverified; old successful values are not reused to
fill a failed direction. A saved throughput result keeps its original timestamps
and is removed from current readings after 24 hours. History remains separate
from gateway measurements, with seven days of speeds and 24 hours of latency.

Existing automatic_speedtests and speedtest_interval_minutes control scheduled
Green tests (default six hours). Maximum payload at the default schedule is about
1.07 GB per day. Automatic monitoring remains controlled by automatic_monitoring.
Routine system checks do not trigger bandwidth tests. UI refreshes start neither.

For sites configured with UniFi, Run UniFi Gateway Speed Test remains available.
Its gateway API and saved certificate requirements are unchanged. Green results
and UniFi results are labeled separately; a failed Green result does not silently
fall back to a gateway result. Ask ADR and downloaded reports include Green probes,
DNS, throughput quality and the original measurement times. All displayed times
use the client's site_timezone.

This update has automated fixture tests but has not been built or
measured on a live Green. Verify the container starts, both test buttons work,
router-independent checks succeed without UniFi configuration, and the measurement
limitations are clear before treating it as a field-tested diagnostic tool.

# Network diagnostics — 0.24.0 development

UniFi is the primary network adapter. It reuses the configured local gateway API
key and approved TLS certificate. Two fixed GET-only controller routes read
`/proxy/network/api/s/{site}/stat/device` and
`/proxy/network/api/s/{site}/rest/networkconf`. These routes are used by the
aiounifi client; they are not a guarantee of public Integration API stability.
No alternate host, cookie login, redirects, API writes or automatic retries are
introduced. Certificate validation failure stops further network reads before
sending the API key. Compatibility and key permissions require a live check.

Only normalized allowlisted device/port fields and network name/VLAN/IGMP
snooping settings are retained. Raw network configuration may contain secrets;
it is never saved, displayed or sent to cloud analysis. One response per route
is bounded to 1 MB; inventories are limited to 64 devices/networks and 256 ports
per device. Omitted rows are identified. Controller-managed inventory is not a
LAN-wide or third-party switch inventory.

Ask ADR receives shared network evidence, bounded device/port excerpts, original
collection timestamps, coverage and report comparisons. Root bridge, active
querier and group memberships stay unverified unless explicitly read from a
supported source. IGMP enabled is a configuration fact, not proof of media
stream delivery. STP blocking is an observation, not proof of an active loop.
Error/multicast/broadcast counters are cumulative; comparisons show differences
without assigning rates, causes, or a storm verdict. Counter decreases may be
wraps/resets. Comparisons use this process session's successful report baselines.

Native event selection includes up to 16 retained reports mentioning STP, BPDU,
loops, topology, IGMP or multicast, even when ordinary newer messages have moved
them beyond the generic latest-event list. This is a keyword selection of
untrusted source reports, not fault classification. Original source and
receipt/import timestamps are preserved; no event receipt does not prove health.
Forwarding must be configured and verified per controller. Existing UDP source
allowlisting and retention limits remain in force.

Araknis is a secondary adapter. A separate bounded help walk starts at `show ?`;
every command path must be both statically allowlisted and advertised by its
parent, and execution requires complete help explicitly advertising `<CR>`.
The additional pass is limited to 20 help probes, 8 reads, a 90-second admission
deadline and existing per-request/page/response limits. No numeric/interface
arguments, configuration dumps, arbitrary commands or writes are allowed.
Unexpected display formats remain unparsed; original bounded console evidence
is included only in the local downloadable report. Auth/identity failures stop
this additional pass and preserve the independent ordinary health readings.
It is not a claim of full root/querier/member coverage for every firmware.

## Acceptance on Green

1. Update only after the development build is reviewed and published.
2. Run All Checks with the existing UniFi local key/certificate configured.
3. Confirm actual switch names, port states and counter fields against UniFi.
4. Inspect network coverage: missing root/querier/memberships must stay unverified.
5. Forward native UniFi events and verify a real known event before relying on delivery.
6. Ask about loops/multicast and verify the answer cites observations and limits.

Tests exercise bounded reads, partial failures, trust failures, secret exclusion,
STP blocking versus loop diagnosis, cumulative counters, target isolation,
strict CLI help gating, and retained native network-event provenance. No live
hardware or Docker build was tested in this workspace.

## Previous unpublished cleanup — 0.23.1

# Latest Araknis evidence and streamlined Ask ADR — 0.23.1 development

Ask ADR now merges a newer individual Araknis diagnostic snapshot into its
analysis evidence without changing the combined report. Original reading times
and the source (individual diagnostic versus combined report) are retained. It
uses the newer timestamp, excludes raw console output and does not treat SSH
login/help as a health diagnostic. This fixes the case where successful
individual diagnostics appeared in the UI while analysis still had no readings.

The dedicated UPS status and Recent power events section are removed from Ask
ADR. Power evidence remains available when relevant, and native device events
stay in the shared event history. Responses prioritize the actual question and
do not add a routine Power heading. Native forwarding and historical source
retrieval are unchanged: the earlier real UPS switchover still requires its
retained WattBox/OvrC source record; no synthetic outage entry is added.

290 Python tests passed, including newer individual snapshots, timestamp ordering,
original-report preservation, safe excerpts and removal of the power UI section.
Main remains 0.23.0 pending publication. Existing Node UI checks also pass.

---

# Native device event ingestion — 0.23.0 development

This build includes the unpublished 0.22.1 conversation fixes (configurable
100/day analysis and voice defaults, no repeated page checkboxes, default readout,
and Araknis reads allowed during a separate UPS poll). Main remains 0.22.0 until
publication is approved.

The new collector accepts **device-originated syslog** and explicitly imported
plain-text exports. It does not configure equipment, scrape OvrC, or download
historical logs automatically. Native reports and sampled UPS observations stay
separate in Ask ADR. The collector lives inside the existing Service Agent app.

## Configure on Green

The receiver is off by default and its host UDP port is also disabled by default.
Enable both intentionally for a client LAN:

```yaml
native_events_enabled: true
native_event_sources:
  - host: 192.168.50.1
    vendor: unifi
    name: Gateway
  - host: 192.168.50.20
    vendor: ruckus
    name: Stairs AP
```

These addresses are examples, not configured client addresses. Add each actual
sender separately; a Ruckus AP may send from its own IP rather than the master.
Each entry requires a private IPv4 host, vendor and name. At most 16 sources;
vendors: unifi, ruckus, wattbox, crestron, control4, sonos, bluos. A vendor entry
labels a source; it does not make that equipment support syslog automatically.

In the app's Network settings, map container `5514/udp` to an available LAN host
port, normally 5514. Configure the supported device/controller log destination as
the Green's LAN IP and that host port, using UDP. Do not point devices at the
internal ingress URL. Keep this port on the client LAN, not the public Internet.
No host networking, Supervisor access, second app or additional privilege is used.

UniFi officially supports CEF export in Integration > System Logging / SIEM.
Start with infrastructure categories such as Internet, Power and System. Whether
the installed Network version exports over UDP to this listener must be verified
on the actual controller; no device settings have been changed by development.
Ruckus Unleashed documents syslog events, but the installed firmware, forwarding
settings and exact sender addresses need verification before relying on a feed.

The status line shows whether the listener runs and when each configured source
last delivered a message in this session. **A running listener or quiet source
is not proof of complete coverage or healthy equipment.** Restarting retains
history but returns source delivery status to unverified until a new event arrives.
Container port forwarding and actual source-IP preservation need Green testing.

## Historical exports

In Ask ADR, expand Native device events, choose a configured source and import a
UTF-8 `.txt` or `.log` excerpt. Limits: 128 KB, 256 nonempty lines, 8192 bytes per
line. Archives, support bundles, arbitrary binary files and remote URLs are not
accepted. The import is authenticated by ingress and CSRF and never executes log
content. Imported source identity is supplied by the user, not authenticated by
the device. Importing a file does not establish an active feed.

This can accept a plain-text WattBox or other device log once retrieved through
its supported export path. It cannot obtain the earlier outage by itself. OvrC
Power Insights history still requires access to OvrC and may smooth brief changes.
No undocumented WattBox log API or arbitrary SSH filesystem command is introduced.
Crestron, Control4, Sonos and BluOS live event/log adapters are not implemented in
this build; their supported export/event mechanisms need separate verification.

## What is retained and sent to Ask ADR

- Configured source identity, vendor label, transport, event name/code/severity
  where available, redacted message excerpt, device time and receipt time.
- RFC5424/ISO timestamps with offsets and CEF `rt` values are normalized to UTC.
  Legacy syslog times without year/timezone stay explicitly unverified. Import
  time is never presented as the original event time. Future/invalid device
  timestamps remain unverified; device time is still a device report, not trusted
  clock synchronization.
- Generic syslog/text is retained without pretending to parse a manufacturer's
  entire event catalog. CEF headers and `msg`/`rt` fields receive structured parsing.
- Configured credentials and common password/token/API-key patterns are redacted
  before storage. Messages are truncated at 800 characters with a truncation flag.
  Logs can still contain device names, addresses and other operational data.
- Local retention: at most 1000 recent messages, 30 days by receipt time and 2 MB
  serialized history, whichever limit is reached first. Writes are batched at
  five-second intervals and on shutdown/import; a hard crash can lose that batch.
  Files are private. Invalid saved history is retained for inspection and blocked.
- Identical source/time/code/message records with an unambiguous device timestamp
  are deduplicated; this is a heuristic, not a certified event count. Untimed
  repeated messages are preserved. No exact outage or complete alert counts are
  inferred.
- UDP allows only configured actual source IPs and 20 messages per source per
  second, with bounded packet sizes and a paced listener. Rejection/drop counts
  are visible. UDP may lose messages and source IPs can be spoofed; records are
  untrusted reports and never trigger actions.
- Ask ADR receives the latest 12 native messages and source coverage through its
  existing explicit Ask button and cloud configuration gate. It currently does
  not search all retained history automatically. The UI displays the latest 20.
  Native message instructions are ignored; model output has no tools.

Current sampled status remains useful for “what is happening now?” Native logs
are primary evidence of reported events; neither replaces functional testing.
Correlation is a possible relationship, not proof of causation. No automatic
resets, support submissions, cloud log forwarding or Proxmox changes are made.

## Validation and acceptance

286 Python tests and the dashboard/voice Node checks pass offline. They cover
configuration, disabled/no-bind behavior, source allowlisting, CEF/time parsing,
legacy timestamps, imports/CSRF, redaction, retention/rate bounds, restart/target
isolation and native evidence in Ask ADR. The actual Green image build, LAN UDP
forwarding, UniFi/Ruckus message delivery, historical WattBox export and phone
playback still need live acceptance. The development tests do not configure or
query client equipment and have not retrieved the earlier outage.

References:
https://help.ui.com/hc/en-us/articles/33349041044119-UniFi-System-Logs-SIEM-Integration
https://support.ruckuswireless.com/documents/5921-ruckus-unleashed-200-19-syslog-event-message-reference-guide
https://help.snapone.com/ovrc-ug/Content/WB-OVRC/Power%20Insights.htm

---

# Conversation usability and switch reads — 0.22.1 development

## Changed

- Daily analysis and transcription defaults increase from 10 to 100 each. Set
  `daily_analysis_limit` and `daily_voice_limit` independently from 0–10000;
  **0 disables that local daily cap**. Existing counters are retained, so a count
  of 10 no longer blocks after upgrading. Failed attempts still count. API
  billing/provider limits remain separate and no cloud call runs automatically.
- Ask ADR and Speak require no extra page checkbox. Existing
  `cloud_analysis_enabled` and `voice_enabled` configuration gates remain.
  Clicking Ask ADR explicitly sends that question and collected readings;
  Speak explicitly sends that recording after stopping. Microphone
  permission remains browser-controlled. The page retains a short disclosure.
- The read-replies checkbox is removed. New answers to a question submitted in
  the current page are read aloud by default when the browser allows playback.
  Old answers do not play merely because the page opens. Read answer and Stop
  reading remain; typed edits and transcript review are retained.
- A UPS poll no longer rejects manual Araknis identity, login/help, diagnostic,
  discovery or report reads. They use a separate device/session. UPS polls still
  defer once another operation starts; outlet actions remain protected. Full
  service checks and pending outlet confirmations still block conflicting work.
  This fixes a possible app-side blocker, not an independently verified switch
  authentication issue. Wrong credentials, changed keys and connectivity errors
  still require the actual error message and device-side verification.

## Historical power logs

The ADR power journal begins with enabled observations and cannot reconstruct an
outage before monitoring, or an event wholly between polls. No historical-log
request has been added to the WattBox SSH client: documented status queries
return current values, not a historical event list.

OvrC Power Insights has its own UPS Power lost history and may contain older
source data. Its graphs use periodic summarized uploads and can smooth short
blips, so absence is not proof that no event occurred. Local WattBox firmware
also documents device logs/export, but which entries survive and whether the
specific outage is present require inspecting the actual device or an export.
Do not invent an HTTP endpoint, retrieve arbitrary system files, clear logs or
change log settings. No live history has been downloaded by this build.

References:
https://help.snapone.com/ovrc-ug/Content/WB-OVRC/Power%20Insights.htm
https://help.snapone.com/wb-8x0-fw/Content/FW%20RN/8x0/8x0%20series%20FW%20RN.htm

## Validation

269 Python tests and the Node voice/dashboard checks pass offline, including
configurable quotas, existing-counter continuity, zero-cap behavior, removed
checkboxes, explicit button submissions, automatic readout and the Araknis/UPS
regression guard. Live browser audio, real switch login and source log retrieval
remain unverified. Main remains 0.22.0 pending publication approval.

---

# UPS monitoring and conversational power history — 0.22.0 development

One Service Agent app now has optional read-only UPS polling through the existing
WattBox SSH connection and approved host identity. It uses documented
`?UPSConnection` and (only when connection=1) `?UPSStatus` queries. The protocol
fields are battery charge/load percentages, battery health, input-power-lost flag,
estimated runtime in minutes, and alarm enabled/muted state. It never sends UPS
settings, shutdown, alarm, load-shedding or battery-test commands.

## Enable and verify

- Set `ups_monitoring_enabled: true`, save and restart Service Agent only.
- `ups_poll_interval_seconds` defaults to 15 and accepts 5–300. The app wakes its
  existing monitor loop every five seconds, so actual intervals are approximate.
- Run All Checks to inspect the first response. Outlet inventory is preserved if
  UPS status is unsupported, incomplete or fails. No UPSStatus request is sent
  unless UPSConnection explicitly reports a connected UPS.
- Ask ADR: “Are there any power issues?” or “What power events were observed?”
  Ask ADR receives current power status directly, including when the last full
  service report is older. The latest 12 events are included in cloud analysis
  only after the existing user confirmation.
- Ask ADR displays a brief UPS line and expandable recent event list. No new
  graphs or separate app are required. Observed times use the viewer's time zone.

## Sampling and event semantics

The fast poll runs without an open dashboard and may run even if recurring full
system checks are disabled. It uses the same operation guards and monitor loop,
with a 60-second startup delay. Full reports and pending device confirmations,
checks, voice transcription and power verification defer fast polls. A failed
fast read backs off for five minutes; there is no immediate command retry.
The next scheduled sample is a new observation, never a repeated power action.

Transitions are recorded as *observed* input power loss or availability. The first
sample establishes a baseline and does not invent an outage. Startup, a failed
read or an excessive interval between samples marks a coverage gap. A later
sample after a gap is recorded as an observation after a gap, rather than an exact
restoration event. Loss of UPS communication is never interpreted as loss of AC.
The UPS input-power flag cannot establish whether the cause was utility failure,
a voltage fluctuation or a disconnected input cable.

**Polling cannot guarantee capture of very brief interruptions.** Events wholly
between polls, during other operations or while Green/network equipment is down
may be missed. No exact start time, duration or complete outage count is claimed.
This build does not download historical OvrC events or retroactively recover the
previous outage. Source event retrieval or another verified event feed remains
necessary for complete brief-event coverage.

## Persistence, trust and existing controls

`/data/power_events.json` holds at most 512 events from 30 days and the most recent
successful sample. State-change events save immediately; unchanged samples save
at most once per five minutes to limit routine flash writes. On restart, saved
history is retained but current status remains unverified until a fresh read.
Samples older than twice the configured interval (minimum 45 seconds) become
unverified. Failed reads also invalidate current status immediately; previous
successful data remains explicitly historical.

History is isolated by a hash of WattBox host, SSH port and approved host-key
file. A changed target starts a new history; previous-target events are excluded.
Invalid saved history is blocked for local review and is not deleted or repaired
silently. Files are private and contain normalized fields, never raw console
responses, passwords or API keys. Unsupported results remain unverified.

Existing outlet actions remain explicit and confirmed. This version adds no
voice execution, automatic resets or remote Proxmox access. UniFi alert/event
ingestion and the existing Proxmox voice-control connection remain separate next
steps; current UniFi speed and AP collectors are unchanged.

## Validation and rollout

265 Python tests plus the existing Node UI checks pass offline. Coverage includes
conditional queries, strict field validation, state transitions, restart/gap
semantics, staleness, target isolation, corrupt data, file permissions, bounded
retention, reduced writes, failed reads, deferred jobs and unchanged controls.
Live UPS firmware compatibility, Docker image building and the actual UPS Link
read have not been verified from development tooling. Verify a read on Green
before relying on alerts. No actual outage or battery transfer was triggered.

Protocol reference: Snap One WattBox Integration Protocol (UPSConnection and
UPSStatus):
https://www.snapav.com/wcsstore/ExtendedSitesCatalogAssetStore/attachments/documents/PowerManagement/SupportDocuments/SnapAV_Wattbox_API_V2.2.pdf

---

# Ask ADR voice — 0.21.0 development build

Voice is part of the existing Service Agent app. It does not install another app
or change the working connection settings. Main remains 0.20.0 while this build
is reviewed and tested.

## Enable and use

1. In Service Agent Configuration, set `voice_enabled: true`. Keep the working
   cloud analysis settings and API key. Save and restart Service Agent only.
2. If your key is restricted to Responses, also allow creating audio
   transcriptions. The fixed transcription model is `gpt-transcribe`. Do not
   grant unrelated permissions or share the key in chat.
3. In Ask ADR, check the recording consent box. Tap Speak, allow microphone
   access, then tap Stop recording. The browser stops after 20 seconds.
4. Review the transcription in Question and tap Ask ADR. Nothing is submitted
   for analysis automatically. If you typed while transcribing, the typed text
   is kept and Use transcript offers the spoken text separately.
5. Tap Read answer for spoken output, or select Read replies aloud for new
   answers. Stop reading interrupts speech. Speech uses the device/browser voice;
   the operating system may use its own voice service. There is no OpenAI speech
   output request or additional OpenAI speech-output permission.

Microphone capture requires HTTPS, browser/app permission and permission from
any containing frame. The Home Assistant app and Safari may behave differently;
this must be tested on the actual phone. The app does not change parent-frame
security settings. Keyboard dictation and typed questions remain available.
The recording-file picker is removed; use Speak or keyboard dictation.

## Privacy and behavior

- Voice defaults off. Microphone access and sending audio require explicit action
  and consent. There is no wake word or background listening.
- Cancel discards an unsent recording. Leaving or hiding the page stops microphone
  tracks and speech. Once sent, an upload cannot be recalled from the provider.
- Audio is held in memory only and is not written to app files or logs. A browser
  recording is capped at 20 seconds and 2 MB.
- One fixed HTTPS transcription request is sent with a 45-second connection
  timeout and bounded response. No automatic transcription retry. Daily voice
  attempts are limited to 10 per UTC day, separately from the 10 analysis attempts.
  Failed attempts count; quotas persist before sending.
- The API key stays on Green. Only a random request identifier correlates the
  transcript back to the requesting page. The latest transcript is kept in app
  memory until another voice request or restart, never in history files.
- Transcription cannot run diagnostics, send an Ask ADR question, or change an
  outlet. Existing checks and power confirmation guards apply to voice uploads.
- Spoken replies stay within Ask ADR's existing evidence and action limitations.

## Validation

248 Python tests and the two Node UI checks pass offline. They cover consent,
ingress/CSRF, size/type bounds, no audio persistence, quotas, busy guards,
cancellation, late microphone permission, transcript review, typed edits and
speech controls. No live OpenAI transcription, phone microphone, Docker image
build or on-device audio playback has been verified from development tooling.
Next acceptance is Speak/Stop on the actual iPhone, review/send, read/stop reply,
permission rejection and existing outlet action confirmation behavior.

---

# Unified Service Agent 0.20.0 — development build

This branch combines the Service Agent dashboard, bounded local diagnostics and
Ask ADR in **one installed app** (`adr_service_agent`), one process, one ingress
port and one configuration. It does not install a second diagnostic app.
Main remains 0.19.1 until the combined build is approved and tested on Green.

## Configuration and transition

Existing WattBox, UniFi, Ruckus, monitoring and certificate settings remain in the
same Service Agent app data. New diagnostic and cloud settings default off.

In Service Agent Configuration, add the settings already tested in Diagnostics
Preview: `allowed_subnets`, `targets`, `dns_name`, `cloud_analysis_enabled`,
`openai_api_key`, and `openai_model`. Enable `diagnostics_enabled` to include local
connectivity checks in Run All Checks and the existing recurring system check.
Use private IPv4 networks with /24 or smaller scope; add each target separately
inside an allowed network. No client addresses or credentials are shipped.

The separate preview app's private settings and history are not automatically
accessible to Service Agent. Transfer settings locally; never paste API keys in
chat, reports or GitHub. Keep the preview stopped while testing the combined
installation. Retire it only after validating checks and Ask ADR in Service Agent.
Preview history is retained by its own app until that app is removed; this build
does not copy or delete it.

## Dashboard

- Network contains connectivity, DNS, manual discovery and observed addresses.
- Ask ADR uses the current combined report directly; no import or second app.
- Existing Power and AV/Automation views, graphs and outlet confirmations remain.
- Diagnostic polling changes result nodes only. Questions, consent, category and
  expanded tiles are retained; typing an Ask ADR question does not pause service
  updates. Observed-device timestamps use the viewer's local time zone.

## Checks, cloud access and controls

Local checks use fixed ICMP commands and TCP connects to ports 80 and 443 only.
The configured WattBox target is checked on HTTP (80) only. Set the optional
`diagnostics_wattbox_https: true` only if that WattBox supports HTTPS. Other
targets retain both service checks.
Discovery is manual, restricted to explicit local subnets and four workers.
It never infers Wi-Fi association or treats a missing reply as an offline device.
The image includes iputils-ping and requests NET_RAW only; host networking and
Supervisor API access remain disabled. Existing system data API access is retained.

Diagnostics and Ask ADR share operation guards with service checks and outlet
confirmations. Scheduled local checks run sequentially inside the existing report;
there is no second scheduler, automatic subnet discovery or automatic AI call.

Ask ADR requires enabling cloud analysis and checking consent on the page. It
sends the question, latest local measurements, one previous target check and
bounded, allowlisted service health readings/summaries with original timestamps.
Raw console responses, credentials, configuration, and internal entity identifiers
are excluded. Report values matching configured secrets are redacted. The model
has no tools and cannot change devices. Answers request concise plain text.

The API key stays server-side. One TLS-verified Responses request is allowed per
ask, with no automatic retries, `store: false` and a 10-attempt UTC-day limit
persisted before the call. Provider processing still applies. Use the working
model and Responses write permission from your preview setup. No audio permission
or microphone feature is added in this build; voice follows core consolidation.

Existing confirmed WattBox actions and post-action outlet verification remain.
Ask ADR does not bypass their confirmations or run any power action. Discovery
and analysis status GETs never initiate checks.

## Validation and rollout

Offline tests cover configuration, probe bounds, evidence filtering, quota,
actual ingress peer/CSRF, operation guards, existing controls and upgrade defaults.
Docker image building and live Green acceptance must still be completed before
publishing main. Live acceptance: upgrade the existing Service Agent (same slug),
verify saved connection trust, run combined checks, verify current gateway speed
and AP statistics, test Ask ADR, verify open views while refreshing, then test one
noncritical outlet action and automatic readback. Do not uninstall the working app
to install this build. Diagnostics Preview should stay stopped during acceptance.

Central team access, remote chat connectors and voice are future work.

---

# Outlet status verification: 0.19.1

After an On or Off action the app automatically reads WattBox at 2, 5 and 15
seconds, stopping when the requested state is observed. Restart reads occur at
15, 30 and 60 seconds. These are read-only status checks, including after an
ambiguous power-command response; the action is never repeated automatically.
The WattBox tile updates with the new state and read time. Other systems and the
original combined-run timestamp remain unchanged. A failed read leaves power
unverified. An On reading does not confirm connected equipment has recovered.

# Current setup: 0.19.0

## Update to 0.19.0

In the app store, check for updates, open ADR Service Agent and install 0.19.0.
Use the update backup option when offered. Reopen the Web UI after the update so
it loads the new dashboard script. Existing saved connection settings are retained.

- Background display updates preserve the selected category, expanded tiles and
  scroll position. Checks still run every 20 minutes by default.
- Internet results now come directly from the UniFi gateway. The old Speedtest.net
  source is no longer used. Until configured, speed graphs show a setup message.
- In app Configuration, set `unifi_gateway_host`, `unifi_gateway_api_key`, HTTPS
  port (normally 443), site (`default`) and WAN (`WAN`). Create a local Network
  API key; do not paste it into chat. Save and restart ADR Service Agent only.
  Under Connections, inspect and independently verify the gateway certificate,
  approve it, then run a check. Firmware compatibility needs a live check.
- For Ruckus, use Read Access Points, compare the listed APs with the controller's
  dashboard and use Verify AP status only when every listed AP is Online.
- WattBox controls are off by default. Enable `allow_outlet_control` in Configuration
  to expose On / Off / Restart actions. Each action confirms the fresh outlet name
  and mapping. Off or Restart on network equipment can interrupt remote access.
- Speed tests keep their six-hour schedule once configured. Speed history covers
  seven days; gateway latency history covers 24 hours. Graphs fill with real samples.

The versioned sections below describe earlier behavior. This 0.19.0 setup and the
Dashboard changes section take precedence for refresh, speed tests and outlet controls.

## Earlier setup and feature history

# Setup

Enter the WattBox's **local** SSH credentials under this app's Configuration.
Set `wattbox_host`, `ssh_port` (normally 22), `test_outlet`,
`expected_outlet_label` (exact label from WattBox), and `device_alias`.

Start the app and choose **Open Web UI**. Inspect and approve its SSH identity,
then read the labels and status. The app sends no device requests at startup.

To run a reboot test, enable `allow_reboot`, save and restart this app only.
Read status, confirm the device/outlet mapping, and select the reboot button.
An off outlet, different label, unexpected model, expired confirmation, or active
cooldown blocks the action. RESET only is supported; no global reset or manual
off/on action is exposed. Five minutes must pass between reset attempts.

The saved SSH identity cannot be silently replaced. If it changes, investigate
the device identity before resetting app data. If the reboot acknowledgement
times out, its outcome is unknown and the command will not be resent.

Use Refresh results to see the outlet verification after 15, 30, and 60 seconds.
Check the TV itself to confirm the Apple TV is operating normally. Outlet status
does not establish device reachability, successful boot, HDMI output or playback.

## Sonos checks (0.2.0)

Select **Check Sonos** in the Web UI. The app reads the player states reported
by your installed Sonos integration without sending playback, volume, mute,
grouping, or other Sonos control commands. No extra configuration is required.

This update enables Home Assistant Core API access, using an internally supplied
token. Home Assistant's grant is broader than read-only; this Sonos connector
uses only entity discovery and individual state reads. The app has no Supervisor
administration API access. No manually entered token is needed.

Source, title and artist appear when reported. Missing values show Not reported.
Group members are displayed by player name where available. A failed read clears
old Sonos results; failure of one player does not erase readings for the others.

These readings may be cached by Home Assistant. The snapshot retrieval time and
HA last-update time are displayed separately; neither proves audible output.
No Sonos playback history or track details are written to the app service log.

This version has no phone voice connection or external cloud access.

## Ruckus checks (0.3.0)

Select **Check Ruckus**, choose the current integration from the discovered-entry
list, then select **Check Ruckus** again. The app remembers that selection locally.
It does not disable, delete, reload or modify any Home Assistant integration.

Only the selected entry supplies readings. Its setup state is displayed first;
failed/disabled entries cannot contribute retained readings as if they were
current. Device inventory is saved HA metadata. Entity values are HA-reported
states, mainly presence for the standard Ruckus integration. They do not prove
AP operational health, Wi-Fi signal, throughput, internet connectivity or root
cause. Deeper controller diagnostics require a later direct integration.

No extra credentials or API permissions are needed beyond the existing Core API
grant. Entry metadata, entity/device registries and individual entity states are
read; no HA configuration actions or Ruckus control commands are implemented.

## UniFi checks (0.4.0)

Select **Check UniFi**, choose the integration if requested, select the gateway
in the device menu, then select **Check UniFi** again. The app remembers the
selection in its own data. No UniFi credentials need to be entered in this app.

Values are read only from enabled sensor/binary_sensor entities on the selected
device and integration. CPU, memory, uptime, temperature and WAN latency appear
when available in HA. Disabled sensors are listed separately; the app never
enables them, controls network entities, changes firewall rules or restarts gear.

Values include units and HA timestamps. Last state update is not a heartbeat.
Unknown/unavailable/failed readings are retained as such, not converted to zero.
WAN latency is not bandwidth or download speed. Non-UniFi switch internals and
Ruckus Wi-Fi health require separate diagnostics. No new API permissions are used.
No Home Assistant configuration, automations or existing integrations are edited.
All credentials stay in app configuration; include them only in protected backups.

If the first check fails, verify local credentials, SSH availability, and the
saved identity. WattBox SSH implementations/firmware can vary; do not weaken
SSH or enable Telnet to work around a failure before reviewing it.

## Araknis SSH inspection (0.5.1)

Configure `araknis_host`, `araknis_ssh_port` (22), `araknis_username` and
`araknis_password` in the app Configuration tab, save and restart only this app.
Existing WattBox configuration is unchanged. These optional fields do not need
values to use the other connectors. Credentials remain in app options and its
protected backups; no switch credentials are committed to this repository.

Open the Web UI, inspect the Araknis SSH identity and explicitly approve its
first-use key. Compare the fingerprint independently when available. A changed
saved key is rejected. Then test login and read CLI help. This opens a bounded
SSH session and sends `show ?` without a newline to request context help.
It advances only explicit `--More--` pager prompts with a space, with an
eight-page and 32 KB content limit. It does not send configuration,
restart, PoE or arbitrary user-entered commands. No automatic retries or startup
connections occur. This stage verifies login and discovers the CLI, not switch
health. The separate diagnostic snapshot requests fixed show commands; their live syntax
and responses still require verification.
Responses are bounded, HTML escaped, kept in memory only and not logged. The
SSH key is saved separately from the WattBox key. Live switch validation is
pending; automated tests use simulated SSH clients.

## Araknis diagnostic snapshot (0.7.0)

After SSH identity approval, select **Read Araknis diagnostics**. The check runs
in the background; use **Refresh Araknis results** to view completion. Starting
another check while one is running does not create a second job. Requests are
fixed: `show system information`, `show env all`, `show interfaces`,
`show power detail`, `show power inline`, `show poe port`. Each uses a fresh authenticated SSH connection and requires a recognized
command prompt. An incomplete/invalid command requests that same fixed command's `?`
context help on a new SSH connection. There is no arbitrary command field, retry,
privilege escalation, running-config read, or switch control.

Most responses have an eight-page/32 KB content limit. Interface details allow
96 pages/256 KB so the full switch can fit. Reads have a four-second limit per
page and a 90-second pagination deadline. Truncation is displayed explicitly. All six categories retain raw responses,
including errors or failed reads. Interface links, speeds and counters also appear in a parsed table, preserving
missing values as not reported. Counters are cumulative; no rates or diagnoses
are inferred. The root `show cpu` query was removed because it reports mirroring,
not utilization. Environment output may report resource thresholds rather than
current utilization. No CPU values, port states or PoE watts are fabricated; a successful SSH request is not a device-health verdict.
A failed help request preserves the original response and its status. Other
requests use independent connections. Authentication and host-key failures abort
the snapshot. Responses stay in memory; logs contain completion and response count.
The pilot has verified SSH login and root show-help; diagnostic commands need
live validation on the selected firmware.

The 0.7.0 command suffixes come from the pilot's own CLI help. Live validation
of those requests and completion of the full interface read are still pending.
The supported interface output format has been tested with sanitized fixtures
based on the observed switch format; other formats remain available as raw text.

## One-run discovery report (0.8.0)

Select **Run discovery and prepare report** once, allow a few minutes, and
refresh results. Select **Download Araknis report** and attach the JSON file in
the support conversation. There is no automatic upload or external API setup.
The report is generated on demand from memory, with no stored report file.

Discovery walks only allowlisted system/environment/interfaces/power/PoE show
paths actually advertised in CLI help. It uses at most 24 help probes, 12 reads,
and a 180-second deadline checked between requests (an in-flight bounded request
may finish after that deadline). Only paths explicitly advertising `<cr>` are
executed. No numeric arguments are inferred, arbitrary input is accepted, or
configuration/control requests are sent. The result records failures, captured
syntax help and discovery limits, so one report can be inspected without
repeated screen copying. Authentication/key failures abort the run.

The download contains switch responses (including possible addresses, device
names and identifiers), parsed port data and discovery metadata. It excludes app
options, credentials, saved SSH keys and other connectors' data. The configured
Araknis password is redacted from response values. The download requires HA
ingress and a valid CSRF-protected form. No new app permissions are required.
Live discovery/export validation remains pending.

### Discovery collection fixes (0.8.1)

Normalize terminal escape/control characters before detecting pager and help
prompts, avoiding a full receive timeout at each formatted prompt. Prioritize
system, environment-all, power/PoE and concise interface-status paths. Defer
verbose interface details/counters until the other discovery paths finish.
Avoid redundant individual CPU/RAM/fan/flash reads once env all is captured.
The same read-only allowlist, authentication and collection limits apply.
Live validation of the timing changes remains pending.

## Crestron discovery through Home Assistant (0.9.0)

Select **Discover Crestron readings**, allow the background check to complete,
then refresh. One configured Crestron entry is selected automatically; if there
are multiple entries, select the one to inspect and check again. An invalid
saved selection is not silently replaced. Select **Download Crestron report**
to save one JSON file containing inventory and exposed readings.

This uses the existing Home Assistant integration, not a new direct processor
connection. It queries config-entry metadata, device/entity registries and only
GET states for the selected entry and matching Crestron platform. Crestron-like
domains are identified by domain, not guessed from names. Failed/disabled
entries do not supply retained states as current data. Disabled devices/entities
are inventoried but not queried. Unsupported entities (buttons, scenes, etc.)
are inventoried without state reads. No service calls or configuration writes
are sent; no new HA permissions or processor credentials are needed.

The bounded check reads up to 128 enabled supported entities with eight workers,
prioritizing sensors/binary sensors. Device inventory is limited to 512 records
and entity inventory to 1024; limits and total counts are explicit. Device
registry software versions are metadata, not fresh firmware queries. Only
allowlisted scalar state attributes are retained, including exposed connectivity,
battery/sensor states, HVAC, shade position, light level and volume/mute/source
where available. No blanket online/offline or functional-health verdict is
inferred from entity state, missing values or update timestamps.

Selection is saved only in this app's data. Snapshots remain in memory; logs
contain the reading count, not device names or state history. Downloads require
HA ingress and CSRF, include only the selected connector's discovery data, and
exclude config-entry data/options, HA tokens and processor credentials. Reports
may include private room/device names and operational states. Live discovery on
the pilot's installed integration remains pending.

## CP4-R direct processor diagnostics (0.10.0)

Set optional `cp4_host`, `cp4_ssh_port` (22), `cp4_username` and `cp4_password`
in app Configuration, save and restart only ADR Service Agent. Use a working
processor SSH/Toolbox account; do not reset processor credentials or regenerate
its Home API token for this connector. Existing HA integration is unchanged.
Inspect and explicitly approve a separate CP4-R SSH key, then read diagnostics
and download the CP4-R report. A changed key is rejected; no agent/key login
fallbacks or automatic credential retries are used.

The fixed command allowlist is VER -V, HELP ALL, IPTABLE -p:all -t, UPTIME,
CPULOAD, RAMFREE, and the last three commands with a literal ? help argument.
After the version response identifies CP4-R, help is read to confirm supported
commands. IP table is read only if advertised. CPU/memory/uptime syntax is
captured first; a query is attempted only when help describes a display query
and does not indicate start/stop/reset or other control operations. Unsupported
or ambiguous metrics remain unverified. An incomplete response stops subsequent
commands. Output is bounded to 256 KB/64 pager advances/20 seconds per response.
The current CPU response alone is not a sustained-load assessment.

IP-table coverage is limited to configured IP-table entries, not all Crestron
Home devices, batteries or the Home Device Health dashboard. Firmware output,
resource readings and compatibility need live validation on the pilot processor.
Reports contain raw read responses and missing-command notes, not inferred
metrics. They stay in memory; logs contain only counts. The configured password
is redacted; credentials and keys are excluded from the report. Downloads use
HA ingress and CSRF protection. No new app API/network permissions are added.

Primary references:
- https://docs.crestron.com/en-us/8559/Content/Topics/Reference/Architecture.htm
- https://www.crestron.com/Products/Catalog/Control-and-Management/Control-System/Residential/CP4-R
- https://docs.crestron.com/en-us/8559/content/topics/Reference/Program-Management.htm

### CP4-R observed console syntax (0.10.1)

Use bare IPTABLE instead of all-program options rejected by the Home processor.
Classify Bad or Incomplete Command / Invalid Application Id responses as errors.
Accept the actual UPTIME no-parameters help and RAMFREE optional-flag help using
read-only purpose confirmed by HELP ALL. Add advertised SYSTEMREADY, PROGUPTIME,
REPORTCRESNET and TOP display queries. TOP is last and bounded; an incomplete
process snapshot closes the connection without sending further commands.
CPU, uptime, RAM, program-uptime and TOP help are captured before execution.
CPULOAD remains unattempted when absent. No arbitrary arguments are accepted.

The output now distinguishes hardware, control-engine and Home setup-program
versions. The pilot verified CP4-R HW2, control-engine 2.8000.00057 and Home
setup program 4.012.0168. Do not confuse those version namespaces or infer update
availability. New display queries still need live validation.

## Control4 discovery (0.11.0)

Configure the official Control4 integration in HA first. Select **Discover
Control4 readings**, wait for the background check, refresh, and download the
Control4 JSON report. No customer account credentials are copied into this app.
A single configured entry is selected automatically; multiple entries require
selection. Selection is saved separately from Crestron and other connectors.

The shared HA reader inventories only devices/entities associated with the
selected control4 entry and platform. Failed/disabled entries do not supply
retained states as current readings. Disabled entities/devices are inventoried
without queries. Supported enabled sensor, binary_sensor, light, cover, climate,
lock, media_player and switch states are GET-only snapshots. Scenes/buttons are
inventory only. Limits remain 128 readings (diagnostic sensors first), eight
workers, 512 devices and 1024 inventory entities, with explicit truncation.

No controller commands, scenes, device services, account changes, reloads or
config writes are implemented. This does not establish CORE CPU, RAM, uptime,
Zigbee health or full device connectivity. Those require independently verified
diagnostic interfaces. Registry software version is metadata, not a fresh
firmware query. Last-update time is not a heartbeat, and off is not offline.
The report retains only allowlisted state attributes, excludes credentials and
config-entry data/options, and requires ingress plus CSRF to download. Snapshots
remain in memory; logs include only reading counts. Live Control4 discovery and
report compatibility remain pending.

Reference: https://www.home-assistant.io/integrations/control4/


## Combined service report (0.12.0)

Click **Run All Checks**, wait several minutes, then **Refresh service report**.
Download **ADR-Service-Report.json** for a single report covering WattBox, Sonos,
Ruckus, UniFi, Crestron, Control4, Araknis and CP4-R. It runs sequentially in the
background and continues when one system fails or needs setup. Existing SSH
identities must already be approved. Existing saved HA selections are used;
Ruckus requires explicit selection. If an HA integration/device needs selection,
run its individual check first. Combined results do not replace individual panels.

“Verified” means the specified read succeeded, not full equipment health.
Unavailable/unknown states, partial reads and invalid volume values are findings
to review. Missing setup or failed reads remain unverified. Per-check timestamps
record the collection window; HA state age is not a heartbeat. Off/idle is not
offline. CPU/Zigbee/full connectivity limits of the individual checks still apply.

A second successful run compares Araknis RX discard counters and CP4-R system
and program start times against the preceding successful comparable result for
the same target in this app session. A decreased counter can mean wrap/reset;
no packet-loss rate or cause is inferred. Changed start times mean a possible
restart or clock adjustment. Unread metrics are not carried into current results.
Reports and comparison baselines remain in memory and clear on app restart.
Logs contain only timestamps and check counts, not raw reports or credentials.

Duplicate clicks do not launch extra runs. Other device operations are blocked
while the combined run is active. Combined WattBox checks read inventory only:
they never create reboot confirmations or issue power commands. Scheduling and
alerts are not included. Live combined-report verification remains pending.


## Bluesound / BluOS (0.13.0)

Use Home Assistant's Bluesound integration, then click **Discover Bluesound readings**,
refresh, and download **ADR-Bluesound-Discovery.json**. If not configured, add Bluesound
under HA Settings > Devices & services > Add Integration. The agent does not install,
reload or change integrations. Reference: https://www.home-assistant.io/integrations/bluesound/

All configured Bluesound entries are included, up to 32, with exact platform and
entry matching. Only loaded enabled entries supply states. Supported enabled
media_player, sensor and binary_sensor entities are GET-only reads, bounded to
128 readings (diagnostic sensors first), eight workers, 512 device records and
1024 inventory entities. Limits are explicit. Disabled devices/entities and buttons
are not queried. HA registry model/software metadata and allowlisted state
attributes (volume, mute, source, exposed diagnostic fields) are retained; credentials,
config-entry options/data, raw media URLs and playback titles are excluded.

Bluesound is also the ninth **Run All Checks** item. Off/idle is not offline;
unavailable/unknown/read failures remain findings to review, not proof of physical
failure. HA timestamps are not a heartbeat; CPU/RAM/uptime/full network diagnostics
remain unverified. No playback, grouping, volume, sleep-timer, reboot or configuration
actions are added. Reports remain in memory, with count-only audit events.
Live integration/report compatibility requires verification.


## Summary dashboard and historical observations (0.14.0)

Run All Checks fills nine responsive summary cards. Key readings come from the
latest combined snapshot. Expand Service findings, Data limitations, or Observations
and comparisons beneath each card. Read completed means the query succeeded; it
is not a full health verdict. Unavailable/read-failed entities remain service
findings to review, with physical connectivity/cause unverified. Unknown states,
invalid volumes and unsupported/partial diagnostics are data limitations. Changed
counters/start times are observations to review, not confirmed equipment faults.
Full raw evidence remains available in the combined JSON download. Individual
checks and connection setup are in a separate expandable section; these remain
separate snapshots. Confirmation panels automatically open their container.

Add historical observation saves an explicit note for the currently selected
system/target. Enter the subject (such as a port) and the confirmed operating
context, then Save. Notes are local app data, not public repository content.
They persist across app restarts, are bounded to 50 records, and are included in
matching-target cards and reports. They do not suppress findings or establish the
cause of a counter. Console history requires an already approved SSH identity;
a changed target/key does not inherit the old target's note. The form requires
Home Assistant ingress plus CSRF and cannot run during combined checks.

Existing chat history is not automatically copied into the installed app. Enter
any desired operating context once using this form. Do not enter credentials.
Raw diagnostic reports and comparison baselines remain in memory and clear on
restart; local historical annotations persist. Logs record annotation events
without note text. No scheduled checks, device writes or new power actions are added.



### USB UPS event monitoring (0.24.0 development)

Configure the supporting Network UPS Tools app and its integration first. Confirm
that the UPS supplies a raw status sensor (OL on line power; OB on battery).
Then add both options to the ADR Service Agent configuration:

```yaml
nut_status_entity: sensor.my_ups_status_data
nut_ups_name: myups
```

Use the exact raw Status data sensor ID and the device name configured in the NUT
app. Leave both empty to disable this listener. No NUT password is copied into ADR.
Save and restart ADR. The listener verifies the selected sensor belongs to the NUT
integration, subscribes to that sensor and to matching `nut.ups_event` notifications,
and sends no device actions. A startup sensor reading is a baseline, never an
invented restoration. Communication failures and reconnects mark gaps rather than
outages. Unknown/unavailable states do not establish power loss.

When USB monitoring is configured, it takes priority over `ups_monitoring_enabled`:
ADR stops its separate WattBox UPS polling. WattBox outlet inventory and confirmed
outlet control continue unchanged. Existing WattBox power history remains on disk.
If USB setup fails, ADR reports it as unverified; it does not silently fall back to
a potentially conflicting power source.

The separate USB journal retains up to 512 events for 30 days across app restarts.
Ask ADR receives the latest 16 records; the report download includes the latest 50.
The ingress diagnostic state includes listener status and history. Source-reported
and receipt timestamps remain separate. An initial on-battery observation is not
an exact outage start. Repeated native/sensor reports of the same transition do not
create duplicate outage/restoration entries. Brief changes may still be missed if
NUT notifications do not arrive, the integration does not update, or a connection
is interrupted. No previous device history is recovered by installing the listener.
A local event stream connection is not a fresh USB heartbeat.

Acceptance: confirm listener connection, then verify a controlled battery/line
transition at a suitable maintenance time against both the NUT status and saved ADR
events. Do not use outlet-off or UPS-shutdown commands for this test. Native NUT
notification delivery and actual outage capture remain unverified until this test.
USB communication and status/voltage/load readings were observed in the lab via
user-provided integration screenshots; other UPS models remain untested.


### Client site timezone

Set `site_timezone` to the installed system's IANA timezone, for example
`America/New_York`, `America/Chicago` or `America/Los_Angeles`. Existing sites default
to America/New_York for compatibility. Save and restart ADR after changing it.
Dashboard timestamps, browser event history and Ask ADR use this site setting,
independently of the technician's phone/browser or the server timezone. Daylight
saving and local date rollover are automatic. Invalid zone names require a
configuration correction; timestamps without a known timezone are not guessed.
Original journal/report timestamps remain intact for correlation and export.
