#!/bin/bash
# Push the run archive to the Spark (omen initiates: the Spark cannot reach
# omen behind the mesh router). Incremental. Run it between runs, never
# during one: omen's LAN is a USB WiFi adapter, and the transfer's driver
# load stalled the robot's 1 kHz control loop for tens of milliseconds
# (communication_constraints_violation reflexes, 2026-11-19). Gentle on the
# link and the disk so that a run that does overlap suffers less.
nice -n 19 ionice -c3 rsync -a --quiet --bwlimit=15000 ~/franka_runs/franka_real/stack_blocks/00-stack-blocks/ spark:~/episodes/stack_blocks/ 2>>/tmp/sync_episodes.err
