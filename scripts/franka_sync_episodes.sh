#!/bin/bash
# Push the run archive to the Spark (omen initiates: the Spark cannot reach
# omen behind the mesh router). Incremental; safe to run after every run.
rsync -a --quiet ~/franka_runs/franka_real/stack_blocks/00-stack-blocks/ spark:~/episodes/stack_blocks/ 2>>/tmp/sync_episodes.err
