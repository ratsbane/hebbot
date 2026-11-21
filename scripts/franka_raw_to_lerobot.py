#!/usr/bin/env python3
"""Build a LeRobot dataset from ASPIRE franka_real run directories.

Each run directory holds ``episode/raw`` as written by
``cap_harness.franka_real.recorder`` (arm log at 1 kHz, camera samples at
20 Hz with mp4 colour in ``media/videos``, gripper samples, command events) plus
the program result and success report. This resamples everything onto the
camera clock and writes one LeRobot episode per run:

    observation.images.<role>   the camera frame at that sample
    observation.state           q (7) + gripper width (1), held-last from the arm log
    observation.ee_position     O_T_EE translation (3)
    observation.force           O_F_ext_hat_K (6)
    action                      the next sample's q (7) + gripper width (1)

Episodes are kept when the program reported ok (``--include-failed`` keeps all;
the per-episode label is stored either way). Run where LeRobot is installed, e.g.
the Spark:

    ~/venvs/lerobot/bin/python scripts/franka_raw_to_lerobot.py \
        --root ~/datasets/franka_round_trip --repo-id hebbot/franka_round_trip \
        ~/episodes/franka_real/stack_blocks/00-stack-blocks/*/run-*
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np

STATE_NAMES = [f"q{i}" for i in range(7)] + ["gripper_width_m"]


def load_episode(run_dir: Path) -> dict | None:
    raw = run_dir / "episode" / "raw"
    if not (raw / "metadata.json").is_file():
        return None
    meta = json.loads((raw / "metadata.json").read_text())
    arm_q = np.load(raw / "arm-q.npy")
    if arm_q.shape[0] == 0:
        return None
    arm_time_ms = np.load(raw / "arm-time_ms.npy").reshape(-1)
    arm_ee = np.load(raw / "arm-O_T_EE.npy")
    arm_force = np.load(raw / "arm-O_F_ext_hat_K.npy")
    cam_t = np.load(raw / "gripper-t_s.npy")
    gripper = np.load(raw / "gripper-width_m.npy")
    # Host time of each arm-log row. The first row is the start clock pair's
    # own read, so it sits at the recorder's zero; the robot clock runs from
    # there, scaled by the two clock pairs when both exist -- live, the robot
    # clock gained about 1 % on the host over a 225 s episode, two seconds of
    # misalignment by the end if taken at face value.
    pairs = [p for p in (meta.get("clock_pairs") or []) if p.get("robot_time_ms") is not None]
    scale = 1.0
    if len(pairs) >= 2:
        host_span = float(pairs[-1]["host_t_s"]) - float(pairs[0]["host_t_s"])
        robot_span = (float(pairs[-1]["robot_time_ms"]) - float(pairs[0]["robot_time_ms"])) / 1000.0
        if robot_span > 0:
            scale = host_span / robot_span
    arm_host = (arm_time_ms - arm_time_ms[0]) / 1000.0 * scale

    # Gripper width is NaN until the first read; fill backwards, then forwards.
    gripper = gripper.astype(np.float64)
    if np.all(np.isnan(gripper)):
        gripper[:] = 0.0
    else:
        first = np.flatnonzero(~np.isnan(gripper))[0]
        gripper[:first] = gripper[first]
        for i in range(1, gripper.shape[0]):
            if np.isnan(gripper[i]):
                gripper[i] = gripper[i - 1]

    videos = {}
    for role in meta.get("cameras", []):
        frame_t = np.load(raw / f"camera-{role}-frame_t_s.npy")
        mp4 = run_dir / "media" / "videos" / f"{role}-images-rgb.mp4"
        if not mp4.is_file():
            continue
        # Sample i's frame is the (number of non-NaN stamps up to i)-th frame.
        # The video is decoded lazily in frames_of(): an episode's two streams
        # held in memory are ~7 GB and the converter was killed for it.
        index = np.cumsum(~np.isnan(frame_t)) - 1
        videos[role] = (mp4, index)

    label = None
    colour = None
    result = run_dir / "source" / "program-result.json"
    if result.is_file():
        payload = json.loads(result.read_text())
        verdict_ok = (payload.get("result") or {}).get("ok", True)
        label = bool(payload.get("ok")) and bool(verdict_ok)
        colour = (payload.get("result") or {}).get("colour")
    report = run_dir / "logs" / "success-report.json"
    verdict = None
    if report.is_file():
        verdict = json.loads(report.read_text()).get("verdict")
    dq_path = raw / "arm-dq.npy"
    arm_speed = np.abs(np.load(dq_path)).max(axis=1) if dq_path.is_file() else None
    episode = {
        "meta": meta,
        "arm_host": arm_host,
        "arm_q": arm_q,
        "arm_ee": arm_ee,
        "arm_force": arm_force,
        "cam_t": cam_t,
        "gripper": gripper,
        "videos": videos,
        "program_ok": label,
        "verdict": verdict,
        "colour": colour,
        "run": run_dir.name,
    }
    if arm_speed is not None and arm_speed.shape[0] == arm_host.shape[0]:
        episode["arm_speed"] = arm_speed
    return episode


def hold_last(times: np.ndarray, values: np.ndarray, at: np.ndarray) -> np.ndarray:
    """``values`` sampled at ``times``, read at ``at`` with hold-last (nearest previous)."""
    index = np.searchsorted(times, at, side="right") - 1
    return values[np.clip(index, 0, values.shape[0] - 1)]


LAG_SEARCH_S = (0.0, 3.0, 0.05)  # the image-to-arm lag searched, seconds: from, to, step
ALIGN_MIN_CORR = 0.12  # below this the motion does not pin the lag down, and the episode is left out


def estimate_lag(episode: dict, role: str = "wrist") -> tuple[float, float]:
    """The lag, in seconds, by which the images trail the arm log on the
    converter's clocks, from motion: the shift that best lines up how much
    the wrist image changes between samples with how fast the joints move.

    The recorder's camera and arm clocks do not share a zero: the arm log
    starts well after the camera (2.6-3.3 s after the recorder's start pair,
    2026-11-20), and the clock pairs that should map one onto the other are
    themselves unreliable (a 4.5 % "drift" in a 30 s run). Measured from the
    images, the pairing was off by about a second in every run checked (0.8 s
    from AprilTags seen across survey looks, 1.0 s from motion onsets): every
    image was paired with the arm state of about a second later, so a policy
    learned "moving" joints beside a still image. Returns (lag, correlation).
    """
    import av

    if role not in episode["videos"] or "arm_speed" not in episode:
        return 0.0, 0.0
    mp4, index = episode["videos"][role]
    change = []
    previous = None
    with av.open(str(mp4)) as container:
        for frame in container.decode(video=0):
            small = frame.reformat(width=160, height=120, format="gray").to_ndarray().astype(np.float32)
            change.append(0.0 if previous is None else float(np.mean(np.abs(small - previous))))
            previous = small
    change = np.asarray(change)
    t = episode["cam_t"]
    sample_change = change[np.clip(index, 0, len(change) - 1)]
    sample_change[index < 0] = 0.0
    # a repeated frame shows no change whatever the arm does: judge only fresh samples
    fresh = np.concatenate([[False], index[1:] != index[:-1]])
    if fresh.sum() < 50 or np.std(sample_change[fresh]) == 0:
        return 0.0, 0.0
    a = (sample_change[fresh] - sample_change[fresh].mean()) / sample_change[fresh].std()
    best = (0.0, -1.0)
    start, stop, step = LAG_SEARCH_S
    for lag in np.arange(start, stop + 1e-9, step):
        speed = hold_last(episode["arm_host"], episode["arm_speed"], t - lag)[fresh]
        if np.std(speed) == 0:
            continue
        b = (speed - speed.mean()) / speed.std()
        corr = float(np.mean(a * b))
        if corr > best[1]:
            best = (float(lag), corr)
    return best


def frames_of(episode: dict):
    """Yield one LeRobot frame per camera sample, decoding the videos as it goes.

    The arm is read ``episode["lag_s"]`` earlier than each camera sample, so
    that the joints beside an image are the joints the image shows."""
    import av  # PyAV ships with lerobot[dataset]

    t = episode["cam_t"]
    t_arm = t - float(episode.get("lag_s", 0.0))
    q = hold_last(episode["arm_host"], episode["arm_q"], t_arm)
    ee = hold_last(episode["arm_host"], episode["arm_ee"], t_arm)
    force = hold_last(episode["arm_host"], episode["arm_force"], t_arm)
    state = np.concatenate([q, episode["gripper"].reshape(-1, 1)], axis=1).astype(np.float32)
    action = np.concatenate([state[1:], state[-1:]], axis=0)  # next sample, hold at the end
    decoders = {}
    for role, (mp4, index) in episode["videos"].items():
        container = av.open(str(mp4))
        decoders[role] = {"container": container, "frames": container.decode(video=0),
                          "index": index, "at": -1, "last": None}
    try:
        for i in range(t.shape[0]):
            frame = {
                "observation.state": state[i],
                "observation.ee_position": ee[i, 12:15].astype(np.float32),  # column-major 4x4
                "observation.force": force[i].astype(np.float32),
                "action": action[i],
            }
            complete = True
            for role, d in decoders.items():
                want = int(d["index"][i])
                if want < 0:
                    complete = False
                    break
                while d["at"] < want:
                    try:
                        d["last"] = next(d["frames"]).to_ndarray(format="rgb24")
                    except StopIteration:
                        break
                    d["at"] += 1
                if d["last"] is None:
                    complete = False
                    break
                frame[f"observation.images.{role}"] = d["last"]
            if complete:
                yield frame
    finally:
        for d in decoders.values():
            d["container"].close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("runs", nargs="+", type=Path)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--repo-id", default="hebbot/franka_episodes")
    parser.add_argument("--task", default=None, help="override the task string")
    parser.add_argument("--include-failed", action="store_true")
    parser.add_argument("--no-align", action="store_true",
                        help="pair images with the arm by the recorder's clocks as they are (the old behaviour)")
    parser.add_argument(
        "--program",
        default=None,
        help="keep only runs whose provenance names a program file with this basename "
        "(e.g. round_trip.py); without it every ok run under the suite is an episode, and "
        "a bench suite holds touch calibrations, declutters and eye checks too (found "
        "2026-11-19: the first checkpoint had trained on that mix)",
    )
    parser.add_argument("--fps", type=int, default=None, help="override the recorder fps")
    args = parser.parse_args(argv)

    episodes = []
    for run in args.runs:
        episode = load_episode(run)
        if episode is None:
            print(f"skip {run}: no recorded episode", file=sys.stderr)
            continue
        if not args.include_failed and not episode["program_ok"]:
            print(f"skip {run}: program_ok={episode['program_ok']}", file=sys.stderr)
            continue
        if args.program is not None:
            provenance = Path(run) / "source" / "provenance.json"
            program = ""
            if provenance.is_file():
                program = str(json.loads(provenance.read_text()).get("input_path") or "")
            if Path(program).name != args.program:
                print(f"skip {run}: program {Path(program).name or '?'}", file=sys.stderr)
                continue
        if not args.no_align:
            lag, corr = estimate_lag(episode)
            if corr < ALIGN_MIN_CORR:
                print(f"skip {run}: image-to-arm lag not measurable (best {lag:.2f} s, r={corr:.2f})",
                      file=sys.stderr)
                continue
            episode["lag_s"] = lag
            print(f"{run}: images trail the arm log by {lag:.2f} s (r={corr:.2f})", file=sys.stderr)
        episodes.append(episode)
    if not episodes:
        print("nothing to convert", file=sys.stderr)
        return 1

    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    first = episodes[0]
    fps = args.fps or int(first["meta"]["fps"])
    features = {
        "observation.state": {"dtype": "float32", "shape": (8,), "names": STATE_NAMES},
        "observation.ee_position": {"dtype": "float32", "shape": (3,), "names": ["x", "y", "z"]},
        "observation.force": {
            "dtype": "float32",
            "shape": (6,),
            "names": ["fx", "fy", "fz", "tx", "ty", "tz"],
        },
        "action": {"dtype": "float32", "shape": (8,), "names": STATE_NAMES},
    }
    import av

    for role, (mp4, _) in first["videos"].items():
        with av.open(str(mp4)) as container:
            stream = container.streams.video[0]
            shape = (stream.height, stream.width, 3)
        features[f"observation.images.{role}"] = {
            "dtype": "video",
            "shape": shape,
            "names": ["height", "width", "channels"],
        }
    dataset = LeRobotDataset.create(
        args.repo_id, fps, features, root=args.root, robot_type="franka_panda", use_videos=True,
        image_writer_threads=4,  # frames go to disk as they arrive, not into memory
    )
    kept = 0
    for episode in episodes:
        task = args.task or episode["meta"].get("task") or "franka_real"
        if episode.get("colour") and "{colour}" in task:
            task = task.replace("{colour}", episode["colour"])
        elif episode.get("colour") and "blue" in task:
            task = task.replace("blue", episode["colour"])
        count = 0
        for frame in frames_of(episode):
            frame["task"] = task
            dataset.add_frame(frame)
            count += 1
        if count < 2:
            print(f"skip {episode['run']}: {count} frames", file=sys.stderr)
            continue
        dataset.save_episode()
        kept += 1
        print(
            f"{episode['run']}: {count} frames, program_ok={episode['program_ok']}, "
            f"verdict={episode['verdict']}"
        )
    dataset.finalize()
    print(f"wrote {kept} episodes to {args.root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
