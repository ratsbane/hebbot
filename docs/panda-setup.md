# Franka Panda Setup Notes

*Lessons from setting up the Franka Emika Panda controller with a new Linux host (omen, Ubuntu 26.04). Written 2026-06-04 after a full day's debugging.*

This document captures everything specific to **getting the Panda controller online and talking to a Linux host** — both the network plumbing and the FCI activation. The general OS setup (NVIDIA drivers, RT kernel, etc.) lives in `omen-setup-checklist.md`.

---

## TL;DR — what made it work

For a fresh Linux host (Ubuntu 26.04 in our case) to talk to the Franka over direct Ethernet:

1. **Real-time kernel** on the host (`sudo apt install ubuntu-realtime`, reboot)
2. **`realtime` group + scheduling limits** for the user (libfranka FCI needs them)
3. **NetworkManager Ethernet profile with `ipv4.method=shared`**, not `manual` — the Franka is a **DHCP client**, and the host needs to **run a DHCP server (dnsmasq) on the link**
4. **Activate FCI in the Franka Desk web UI** (`https://<robot-ip>/` → Settings → activate)
5. `pip install panda-python>=0.8.0` (bundles libfranka on x86_64 — no source build needed)

Test:

```python
import panda_py
panda = panda_py.Panda("192.168.0.253")
state = panda.get_state()
print(state.q)  # joint positions
```

---

## The big surprise: the Franka is a DHCP client

The single most important and least-obvious thing about Franka networking:

**On the link between the host and the Franka controller, the Franka is the DHCP client and the host must be the DHCP server.** A simple static-IP-on-both-ends configuration *does not work*. The Franka boots, finds no DHCP server, gets no IP, and broadcasts essentially nothing on the wire.

### How the symptom presents

When the host has a static IP and the Franka has no DHCP server to talk to:

- `ping <franka-ip>` → `Destination Host Unreachable`
- `ip neigh show dev <iface>` → entry exists but `(incomplete)` / `FAILED`
- `sudo tcpdump -i <iface>` → **zero packets in 10+ seconds** (this is the giveaway)
- `arp-scan --localnet` → 0 responses
- mDNS lookup (`avahi-resolve -n panda.local`) → timeout
- Host's link LEDs blink, full-duplex 1 Gbps link detected — but no data passes

It looks exactly like a damaged cable or a hung Franka. It is neither.

### How we discovered it

The Pi (robot2) was running `franka-direct` NetworkManager profile with `method=shared`. When the Pi's SD card eventually went read-only and dnsmasq could no longer write its lease file, the Franka silently stopped responding — same symptom we hit on omen after a fresh static-IP config. Once we set omen to `shared` mode, the Franka picked up `.253` (its remembered DHCP-assigned address) within seconds.

### Host network config (omen, Ubuntu 26.04)

```bash
# Create or edit the connection profile for the Franka Ethernet
sudo nmcli con add con-name franka-link ifname enp8s0 type ethernet \
    ipv4.addresses 192.168.0.2/24 \
    ipv4.method shared

sudo nmcli con up franka-link
```

`method=shared` does three things in NetworkManager:

1. Sets the static address on the interface (`192.168.0.2/24`)
2. Spawns `dnsmasq` as a DHCP+DNS server bound to that interface (range `192.168.0.11–.254`)
3. Adds an iptables rule to NAT traffic from that subnet out the default route (so the Franka could in principle reach the internet through us — this is harmless even if not needed)

### Verification

```bash
# Confirm DHCP server is up and bound to the right interface
ps -ef | grep dnsmasq | grep -v grep
# Expected: /usr/sbin/dnsmasq --listen-address=192.168.0.2 --dhcp-range=192.168.0.11,192.168.0.254,...

# Watch for the Franka to grab a lease
sudo cat /var/lib/NetworkManager/dnsmasq-<iface>.leases
# Expected: a line with the Franka's MAC and IP, e.g.:
# 1780630027 90:1b:0e:fd:8d:1d 192.168.0.253 * ...

# MAC vendor (sanity check it's actually the Franka NIC)
sudo arp-scan --interface=<iface> --localnet
# Expected: 90:1B:0E:FD:8D:1D    Fujitsu Technology Solutions GmbH
```

### Why the same IP every time

dnsmasq preserves lease assignments by MAC across restarts (lease file is persistent). So once the Franka has been assigned `192.168.0.253`, subsequent power-cycles get the same IP back unless you wipe the lease file. The original assignment is essentially random within the DHCP range, but you can force a specific IP via a static lease in `/etc/NetworkManager/dnsmasq-shared.d/`:

```conf
# /etc/NetworkManager/dnsmasq-shared.d/franka.conf
dhcp-host=90:1b:0e:fd:8d:1d,192.168.0.253
```

This is the safer config — the Franka always gets `.253` no matter what dnsmasq's lease file does.

---

## Real-time kernel + scheduling

libfranka's FCI runs a 1 kHz control loop and requires hard real-time scheduling. On Ubuntu 26.04 the RT kernel is in the main archive (no Ubuntu Pro subscription required):

```bash
sudo apt install ubuntu-realtime
sudo reboot
# pick the realtime kernel from GRUB
uname -r   # should show 7.0.0-22-realtime or similar
```

Set up scheduling permissions for your user:

```bash
sudo groupadd -r realtime 2>/dev/null
sudo usermod -a -G realtime doug

sudo tee /etc/security/limits.d/99-realtime.conf > /dev/null << 'EOF'
@realtime soft rtprio 99
@realtime soft priority 99
@realtime soft memlock 102400
@realtime hard rtprio 99
@realtime hard priority 99
@realtime hard memlock 102400
EOF
```

After logout/login, `ulimit -r` should return `99` and `ulimit -l` should return `102400`.

### NVIDIA + PREEMPT_RT

The NVIDIA open kernel module refuses to build on PREEMPT_RT kernels by default — it has a sanity-check target in its Kbuild that aborts compilation. The workaround is to remove the check from `Kbuild` files and rebuild via DKMS. See `omen-setup-checklist.md` Phase 7 for the full patch.

Net effect: you can run NVIDIA + PREEMPT_RT + libfranka on a single kernel, but the NVIDIA driver is technically unsupported in this config. In practice it works fine for everything we do.

---

## panda-python (no libfranka source build needed on x86_64)

The project's `requirements.txt` notes that ARM64 needs libfranka built from source. **On x86_64 the panda-python pip wheel bundles libfranka** — installs in seconds:

```bash
# In the project venv
pip install panda-python>=0.8.0
```

### Python version constraint

As of mid-2026, panda-python only has wheels up to **Python 3.12**. Ubuntu 26.04 ships Python 3.14, which has no wheels. Use `uv` to manage a separate Python 3.12 venv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
cd ~/panda-mcp
~/.local/bin/uv venv --python 3.12 .venv
source .venv/bin/activate
~/.local/bin/uv pip install -r requirements.txt
```

uv auto-downloads Python 3.12 if missing.

---

## Activating FCI in Franka Desk

After the network is up and you can reach `https://<robot-ip>/` in a browser, you need to enable FCI before `panda_py.Panda(...)` will succeed.

1. Open `https://192.168.0.253/` in Chromium (or Firefox via Settings → Privacy → Certificates → Add Exception — note the Firefox snap on Ubuntu 26.04 makes the standard "Accept the Risk and Continue" path harder to find)
2. Accept the self-signed certificate
3. Log in with your Desk credentials
4. **Unlock the brakes** — click the lock icon (top right)
5. **Settings → Activate FCI** (or "Activate Workstation Mode" depending on firmware)

After FCI is active, `panda_py.Panda("192.168.0.253")` should connect in under a second and `get_state()` returns live joint positions / EE pose.

---

## Symptom → fix table

| What you see | What's wrong | Fix |
|---|---|---|
| `ping <ip>` → Destination Host Unreachable, `ip neigh` shows `incomplete` | No DHCP server on link → Franka has no IP | Switch host profile to `ipv4.method=shared` |
| `tcpdump` shows zero packets | Franka has no IP, isn't doing anything on the link | Same as above |
| dnsmasq log: `cannot open or create lease file: Read-only file system` | Host filesystem is read-only (SD card failure on a Pi, or similar) | Move to a host with writable storage |
| Link LEDs only on host side, none on Franka end | Either the Franka port doesn't expose LEDs, or cable issue. Verify by swapping cable | Replace cable to rule out |
| Desk reachable, `panda_py.Panda(...)` hangs/fails | FCI not activated | Desk Settings → Activate FCI |
| Desk reachable, FCI active, `Panda(...)` fails with timeout | Brakes still engaged | Unlock brakes in Desk (top right lock icon) |
| Robot connects but commands fail | RT scheduling not granted | Add user to `realtime` group, check `/etc/security/limits.d/99-realtime.conf`, re-login |
| Firefox won't let you accept self-signed cert | Firefox snap on Ubuntu 26.04 quirk | Use Chromium, or Firefox Settings → Privacy → Certificates → Add Exception |
| `pip install panda-python` fails: no matching distribution | Python 3.13+ has no wheels | Use uv with Python 3.12 |

---

## Useful diagnostic commands

```bash
# Network state of the Franka link
ip addr show <iface>
ip neigh show dev <iface>            # ARP / neighbor cache
sudo cat /var/lib/NetworkManager/dnsmasq-<iface>.leases   # DHCP leases
sudo arp-scan --interface=<iface> --localnet              # active sweep
ping -c 3 <franka-ip>

# DNSmasq status
ps -ef | grep dnsmasq
sudo journalctl -u NetworkManager --since "5 min ago" | grep -iE "dnsmasq|dhcp|lease"

# Confirm full duplex link
ethtool <iface> | grep -E "Speed|Duplex|Link detected"

# Watch the wire for any activity
sudo tcpdump -i <iface> -nn -c 50

# Desk reachable?
curl -sIk https://<franka-ip>/ | head -5

# Real-time scheduling permissions (in a fresh login shell)
ulimit -r    # should be 99 for realtime group members
ulimit -l    # should be 102400 (memlock KB)

# panda-py end-to-end smoke test
python -c "
import panda_py
p = panda_py.Panda('192.168.0.253')
s = p.get_state()
print('q =', list(s.q))
"
```

---

## Open questions / things we haven't tested

- The new host's behavior under **prolonged FCI control** (data collection, episode recording) hasn't been verified. The RT kernel + NVIDIA-via-Kbuild-patch combination is unsupported by NVIDIA; if we see GPU hangs or kernel oopses during heavy concurrent GPU + FCI workload, the dual-kernel-boot fallback (generic kernel for GPU-heavy work, RT kernel for FCI) is the escape hatch.
- We have not yet tried `franka_rt` (the ZMQ proxy server) on the new host — only direct `panda_py.Panda` access. Should be identical but worth confirming.
- The Franka's brakes / motion test (`move_to_joint_position`, etc.) — only state-read was confirmed.

---

## Provenance

Built up over a single setup day, 2026-06-04, with help from Claude Code while migrating from the Pi (robot2) to the OMEN. The DHCP-shared finding came from inspecting robot2's working NetworkManager profile (`franka-direct.nmconnection`) and noticing `method=shared` after a long detour through static-IP configs that all failed identically.
