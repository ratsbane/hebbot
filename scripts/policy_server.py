#!/usr/bin/env python
"""Serve a LeRobot checkpoint to the bench over HTTP.

One process holds the policy on the GPU; the ASPIRE harness on the bench
asks it for an action chunk per observation (``franka_real.policy_chunk``)
and executes the answer through its own guarded waypoint path. Runs in the
lerobot venv, on omen (localhost) or on the Spark:

    policy_server.py --checkpoint ~/policies/dream-20261117-1703/pretrained_model

Endpoints:
    GET  /health        -> {"policy", "chunk_size", "device", "features"}
    POST /reset         -> clears the policy's per-episode state
    POST /act           -> {"actions": [[q0..q6, gripper_width_m] x chunk]}
        body: {"state": [8], "ee_position": [3], "force": [6],
               "images": {"wrist": <base64 PNG>, "scene": <base64 PNG>}}

The request carries exactly what the dataset converter wrote
(scripts/franka_raw_to_lerobot.py): observation.state is q (7) + gripper
width, ee_position the O_T_EE translation, force the O_F_ext_hat_K wrench,
and the images RGB as the recorder saved them. Anything the checkpoint was
not trained on is ignored; anything it needs and the request lacks is a 400.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import torch
from PIL import Image


def load(checkpoint: Path, device: str):
    from lerobot.policies.act.modeling_act import ACTPolicy
    from lerobot.policies.factory import make_pre_post_processors

    policy = ACTPolicy.from_pretrained(str(checkpoint))
    policy.to(device)
    policy.eval()
    overrides = {"device_processor": {"device": device}}
    try:
        pre, post = make_pre_post_processors(
            policy.config, pretrained_path=str(checkpoint), preprocessor_overrides=overrides
        )
    except TypeError:
        pre, post = make_pre_post_processors(policy.config, pretrained_path=str(checkpoint))
    return policy, pre, post


def decode_image(b64: str) -> torch.Tensor:
    """Base64 PNG -> float CHW in [0, 1], as the dataset's video frames are decoded."""
    image = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGB")
    array = np.array(image, dtype=np.uint8)  # a copy: PIL's buffer is read-only
    return torch.from_numpy(array).permute(2, 0, 1).float() / 255.0


class Host:
    def __init__(self, checkpoint: Path, device: str) -> None:
        self.name = checkpoint.parent.name if checkpoint.name == "pretrained_model" else checkpoint.name
        self.device = device
        self.policy, self.pre, self.post = load(checkpoint, device)
        self.features = {k: list(v.shape) for k, v in self.policy.config.input_features.items()}
        self.chunk_size = int(self.policy.config.chunk_size)
        self.lock = threading.Lock()
        self.requests = 0
        self.dump_next = False
        self.dumps = 0

    def reset(self) -> None:
        with self.lock:
            self.policy.reset()
            # The first observation after a reset is saved for comparison with
            # the dataset (2026-11-20: the checkpoint reproduces its training
            # episodes offline and leans the wrong way on the bench).
            self.dump_next = True

    def _dump(self, body: dict, observation: dict) -> None:
        out = Path("/tmp/policy_probe")
        out.mkdir(exist_ok=True)
        arrays = {k.replace(".", "_"): v.detach().cpu().numpy() for k, v in observation.items()}
        np.savez_compressed(out / f"request_{self.dumps:03d}.npz", **arrays)
        self.dumps += 1

    def act(self, body: dict) -> list[list[float]]:
        observation: dict[str, torch.Tensor] = {}
        for key in self.features:
            if key == "observation.state":
                observation[key] = torch.tensor(body["state"], dtype=torch.float32)
            elif key == "observation.ee_position":
                observation[key] = torch.tensor(body["ee_position"], dtype=torch.float32)
            elif key == "observation.force":
                observation[key] = torch.tensor(body["force"], dtype=torch.float32)
            elif key.startswith("observation.images."):
                role = key.split(".")[-1]
                images = body.get("images") or {}
                if role not in images:
                    raise KeyError(f"the checkpoint needs an image from the {role!r} camera")
                observation[key] = decode_image(images[role])
            else:
                raise KeyError(f"the checkpoint wants {key!r}, which this server does not build")
        if self.dump_next:
            self.dump_next = False
            try:
                self._dump(body, observation)
            except Exception as exc:  # a probe must never cost a run
                sys.stderr.write(f"probe dump failed: {exc}\n")
        with self.lock, torch.no_grad():
            batch = self.pre(observation)
            actions = self.policy.predict_action_chunk(batch)
            actions = self.post(actions)
            self.requests += 1
        chunk = actions.detach().cpu().float().numpy()
        if chunk.ndim == 3:
            chunk = chunk[0]
        return [[float(v) for v in row] for row in chunk]


def make_handler(host: Host):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")

        def do_GET(self):  # noqa: N802
            if self.path == "/health":
                self._send(200, {"policy": host.name, "chunk_size": host.chunk_size,
                                 "device": host.device, "features": host.features,
                                 "requests": host.requests})
            else:
                self._send(404, {"error": "unknown path"})

        def do_POST(self):  # noqa: N802
            started = time.monotonic()
            try:
                body = self._body()
            except (ValueError, UnicodeDecodeError) as exc:
                self._send(400, {"error": f"bad json: {exc}"})
                return
            if self.path == "/reset":
                host.reset()
                self._send(200, {"ok": True, "policy": host.name})
            elif self.path == "/act":
                try:
                    actions = host.act(body)
                except KeyError as exc:
                    self._send(400, {"error": str(exc)})
                    return
                except Exception as exc:  # the policy's own failure is a 500 with a reason
                    self._send(500, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
                    return
                self._send(200, {"actions": actions, "policy": host.name,
                                 "chunk_size": host.chunk_size,
                                 "inference_s": round(time.monotonic() - started, 4)})
            else:
                self._send(404, {"error": "unknown path"})

        def log_message(self, format, *args):  # noqa: A002
            if "/act" not in str(args[0] if args else ""):
                sys.stderr.write("%s - %s\n" % (self.address_string(), format % args))

    return Handler


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--checkpoint", required=True, type=Path, help="a LeRobot pretrained_model directory")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = parser.parse_args(argv)
    host = Host(args.checkpoint.expanduser(), args.device)
    print(f"policy {host.name} on {host.device}: chunk {host.chunk_size}, features {host.features}",
          flush=True)
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(host))
    print(f"serving on http://{args.bind}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
