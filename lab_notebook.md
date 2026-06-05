# Hebbot Lab Notebook

*A running log of experiments, findings, and reasoning for the Hebbot project. Started 2026-06-05 after renaming from `panda-mcp`. Prior 98 findings in the `panda-mcp` lab notebook remain valid as historical reference; new findings continue from #99.*

---

## 2026-06-05 — Project rename + OMEN setup complete

Hebbot started today as a fresh repo following the [2026-05-28 reset](https://github.com/ratsbane/panda-mcp/blob/main/lab_notebook.md) in the prior `panda-mcp` notebook. The decision to rename came after iterating on the thesis statement to its current form: *use agentic AI to bootstrap a system with continuous learning*. The name **Hebbot** honors Donald Hebb (Hebbian learning) + robot + the *good hebbot / good habit* pun, and is search-clean (no existing tech usage).

### What's working as of today

The new host machine (`omen`, an HP OMEN 16L with RTX 5060 Ti 16GB, Ubuntu 26.04, real-time kernel) is fully operational and connected to the Franka Panda. End-to-end chain verified yesterday:

- OMEN ↔ Linux 26.04 + PREEMPT_RT kernel ↔ NVIDIA driver 595 + CUDA 13.1 ↔ panda-python venv (Python 3.12 via uv) ↔ Ethernet w/ NetworkManager `shared` mode (dnsmasq DHCP) ↔ Franka FCI ↔ libfranka 1 kHz control loop ↔ **physical 7-DOF arm motion**

Full setup details in [`docs/panda-setup.md`](docs/panda-setup.md).

### Findings

| # | Finding |
|---|---------|
| 99 | The Franka's network interface is a **DHCP client**, not a static-IP peer. A Linux host on the direct-Ethernet link must run a DHCP server (NetworkManager `ipv4.method=shared` does this via dnsmasq). Static-IP on both ends fails silently — Franka has no IP and emits no packets. This was the morning's hardest debug; documented end-to-end in `docs/panda-setup.md`. |
| 100 | NVIDIA's open kernel module refuses to build on PREEMPT_RT kernels by default — sanity check at `Kbuild:305: preempt_rt_sanity_check`. Removing the check from the Kbuild and rebuilding via DKMS works; modules need to be signed with the MOK key and enrolled at first boot (Secure Boot kept on). NVIDIA + PREEMPT_RT then coexist on a single kernel. Officially unsupported by NVIDIA; works in practice. |
| 101 | D405 needs `dev.hardware_reset()` from librealsense at the start of every session — USB unplug alone doesn't reset the camera's internal state, and subsequent pipe.start() succeeds but never delivers frames. Calling hardware_reset + 5-second wait before `pipe.start()` solves it. |
| 102 | `panda-python` ships pre-built wheels for Python 3.10–3.12 only as of mid-2026; no wheels for Python 3.13+. Ubuntu 26.04 ships Python 3.14 by default, so the project venv must explicitly use Python 3.12 (uv handles this cleanly with `uv venv --python 3.12`). |

### Open threads

- **pyzed Python bindings fail to open the ZED 2i** on this system, despite C++ `ZED_Explorer` working fine. Same error from Stereolabs's own sample code. Probably a Stereolabs SDK 5.3.1 + Python 3.12 + Ubuntu 26.04 environment issue. Parked for later debug; not blocking robot work.
- **First end-to-end pick attempt** still to do. Wrist-camera-only depth detection works mechanically but the arm at `move_to_start` pose isn't looking at the block area — need a deliberate survey pose, or plug in the D435 scene camera to use the existing perception pipeline.
- The previously-collected ~100 skill episodes on `panda-mcp` are mostly from a single-viewpoint setup; whether to selectively port them or treat as discardable per the environment-diversity finding (#89 in `panda-mcp` notebook) is a near-term decision.
