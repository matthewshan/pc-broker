# Debug plan: PC ignores Wake-on-LAN from full shutdown

**Audience:** a Claude Code session running **on the gaming PC itself** (Windows),
in an **elevated (Administrator) PowerShell**. If the shell isn't elevated, stop
and ask the user to restart it as Administrator — most checks below need it.

## Incident context (read first)

- 2026-07-16 07:00 ET: the daily-briefing agent's wake request timed out; the
  PC (off since the prior evening) never woke.
- Same day, a controlled test from the broker (v0.3.2) re-broadcast the magic
  packet every 30s for 5 minutes — the PC still never woke. The packets
  verifiably leave the broker on the LAN segment.
- The day before, a **single** packet woke the PC fine. So WoL is configured
  and partially working — it fails from a *prolonged full shutdown (S5)*.
  Prime suspects: Windows Fast Startup, NIC deep-sleep/EEE, BIOS ErP.

**Known-good facts** (do not re-derive, do not change):

| Fact | Value |
|---|---|
| This PC's NIC MAC | `04:7c:16:15:cc:33` |
| This PC's LAN IP (DHCP reservation) | `192.168.1.77` |
| Magic packet | UDP **port 9**, broadcast `192.168.1.255`, standard 6×FF + 16×MAC |
| Broker (sender) | pc-broker pod, hostNetwork on the k3s node `192.168.1.163:8000`; also `https://pc.mattshan.dev` via Twingate |
| Broker resend cadence while waking | every 30s (v0.3.2+) |
| Shutdown agent on this PC | `:8001` (`agent/agent.py`, installed via `agent/install.ps1`) |
| Ollama on this PC | `:11434`, headless (installed via `agent/install-ollama.ps1`) |

## Ground rules

- Phases 1–2 are **read-only** — run them without asking.
- Phase 3 changes system settings: apply **one fix at a time**, tell the user
  what each command changes and how to revert it, and record the before-value
  first.
- Do **not** touch the shutdown agent, Ollama, firewall rules, or anything
  else on this machine — the problem is strictly power/NIC configuration.
- Do not run `powercfg /h off` (it disables hibernation wholesale); use the
  registry toggle for Fast Startup instead (Phase 3).

## Phase 1 — evidence gathering (read-only)

Run all of these and keep the output for the final report.

```powershell
# 1. Identify the wired adapter (match MAC 04-7C-16-15-CC-33)
Get-NetAdapter | Format-Table Name, InterfaceDescription, MacAddress, Status, LinkSpeed

# 2. Fast Startup state — 1 = ON (bad for WoL). Windows Updates re-enable this.
Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" -Name HiberbootEnabled

# 3. NIC wake config (use the adapter Name from step 1 everywhere below)
Get-NetAdapterPowerManagement -Name "<ADAPTER>"

# 4. NIC advanced properties — the ones that matter for S5 wake
Get-NetAdapterAdvancedProperty -Name "<ADAPTER>" |
  Where-Object { $_.DisplayName -match "wake|WOL|shutdown|energy|green|EEE|sleep|link speed" } |
  Format-Table DisplayName, DisplayValue

# 5. What is currently allowed to wake the machine
powercfg /devicequery wake_armed
powercfg /devicequery wake_programmable
powercfg /lastwake

# 6. Power/boot history — how was the PC last turned off? (1074 = shutdown
#    initiated, 41 = dirty reboot, 107 = resume, 1 = kernel boot w/ boot type:
#    0x0 = cold boot, 0x1 = fast startup, 0x2 = resume from hibernate)
Get-WinEvent -FilterHashtable @{LogName='System'; Id=1074,41,107} -MaxEvents 15 |
  Format-Table TimeCreated, Id, Message -Wrap
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='Microsoft-Windows-Kernel-Boot'; Id=27} -MaxEvents 5 |
  Format-Table TimeCreated, Message -Wrap

# 7. Did a Windows Update land recently (settings-reset suspect)?
Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 5

# 8. Motherboard model — needed for the BIOS phase
Get-CimInstance Win32_BaseBoard | Format-List Manufacturer, Product
```

**Interpretation guide:**
- `HiberbootEnabled = 1` → Fast Startup is on. On many boards, "shutdown" then
  isn't a real S5 and/or the NIC doesn't get armed for WoL. Strong candidate.
- Boot type `0x1` in the Kernel-Boot events confirms the machine has been
  fast-starting rather than cold-booting.
- Missing/disabled `Shutdown Wake-On-Lan` (Realtek) or `Wake on Magic Packet`
  advanced property → NIC won't listen in S5.
- `Energy Efficient Ethernet` / `Green Ethernet` / `Advanced EEE` enabled →
  known to break S5 wake on some Realtek/Intel NICs.

## Phase 2 — live packet-arrival test (read-only)

Prove the magic packets reach this NIC while Windows is running:

```powershell
# Terminal A: listen on UDP 9 for one packet (magic packet = 102 bytes)
$u = New-Object System.Net.Sockets.UdpClient(9)
$ep = New-Object System.Net.IPEndPoint([System.Net.IPAddress]::Any, 0)
$u.Client.ReceiveTimeout = 90000
try { $b = $u.Receive([ref]$ep); "GOT $($b.Length) bytes from $($ep.Address)" }
catch { "NO PACKET within 90s" } finally { $u.Close() }
```

While it listens, trigger a wake from this PC (the broker no-ops the state
change when the PC is up, but v0.3.2 still logs/sends the packet on a fresh
request only if state is offline/timeout — so instead send one locally
from the k3s node's perspective by asking the broker):

```powershell
# Terminal B — request a wake via the broker (harmless while PC is on)
Invoke-RestMethod -Method Post -Uri "https://pc.mattshan.dev/api/power/on"
# If Twingate isn't connected, use the LAN address:
# Invoke-RestMethod -Method Post -Uri "http://192.168.1.163:8000/api/power/on"
```

> Note: while the PC is `ready`, `request_wake` early-returns without sending.
> If the listener gets nothing, that is expected in `ready` state — in that
> case skip this test's conclusion, or ask the user to run it right after the
> PC boots while the broker still shows `waking` (resends continue until the
> broker notices the PC).

If a packet **does** arrive: L2 delivery is fine (as expected), and the failure
is purely the NIC/board not listening in S5 → the fixes below are the answer.

## Phase 3 — fixes (apply one at a time, record before-values)

```powershell
# FIX 1 — disable Fast Startup (keeps hibernate available; takes effect on
# the next shutdown). Revert: set value back to 1.
Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power" -Name HiberbootEnabled -Value 0

# FIX 2 — arm the NIC for magic-packet wake
Set-NetAdapterPowerManagement -Name "<ADAPTER>" -WakeOnMagicPacket Enabled

# FIX 3 — NIC advanced properties (exact names vary by driver; use the
# DisplayName strings found in Phase 1 step 4)
Set-NetAdapterAdvancedProperty -Name "<ADAPTER>" -DisplayName "Shutdown Wake-On-Lan" -DisplayValue "Enabled"
Set-NetAdapterAdvancedProperty -Name "<ADAPTER>" -DisplayName "Wake on Magic Packet" -DisplayValue "Enabled"
Set-NetAdapterAdvancedProperty -Name "<ADAPTER>" -DisplayName "Energy Efficient Ethernet" -DisplayValue "Disabled"
# Also disable, if present: "Green Ethernet", "Advanced EEE", "Gigabit Lite",
# "Power Saving Mode", any "...deep sleep..." option.
```

## Phase 4 — BIOS (manual, if Phases 1–3 found nothing or don't hold)

Software can't set these; give the user this checklist with the motherboard
model from Phase 1 step 8:

- **ErP / EuP Ready: Disabled** (this cuts NIC standby power in S5 — the
  classic cause when the NIC's link LED is dark while the PC is off).
- **Power On By PCI-E / Wake On LAN: Enabled.**
- If a "Deep Sleep" option exists (some boards): Disabled.

Tell-tale the user can check without entering BIOS: with the PC shut down,
look at the Ethernet port — **link LED dark = NIC unpowered in S5** = BIOS
setting, not Windows.

## Phase 5 — verification protocol

1. Apply fixes → **full shutdown** (`shutdown /s /f /t 0` — not sleep, not
   restart; a restart re-arms differently than a shutdown).
2. Confirm the NIC link LED is lit while off.
3. Wake remotely: phone → `https://pc.mattshan.dev` → Wake, or from any LAN
   machine `Invoke-RestMethod -Method Post -Uri "http://192.168.1.163:8000/api/power/on"`.
   The broker resends every 30s; the PC should be up well inside 2 minutes.
4. **The real test:** leave the PC shut down overnight (the failure only
   showed after ~16h off) and let the 07:00 briefing wake it — or trigger a
   wake the next morning before 07:00 and watch `GET /api/status` go
   `waking → ollama_starting → ready`.

## Final report format

Summarize for the user: (1) which suspects were confirmed (before-values),
(2) which fixes were applied and how to revert each, (3) what remains manual
(BIOS items), (4) verification results. The cluster-side Claude session
tracks this as **bug-033** in the ADK workspace buglog — mention that ID so
the fix gets recorded there.
