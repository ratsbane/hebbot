#!/bin/bash
# Relay recorded franka_real run directories from omen to the Spark through this
# machine (omen's key is not yet authorized on the Spark). Each argument is a
# seed number under the stack_blocks task; the run directory lands under
# ~/episodes/stack_blocks/<seed>/ on the Spark. Uncompressed tar: omen may be
# recording the next run while this copies, and gzip would cost it CPU.
#
#   scripts/relay_episodes.sh 8 9 10
set -euo pipefail
base=franka_runs/franka_real/stack_blocks/00-stack-blocks
for seed in "$@"; do
  dir=$(printf '%04d' "$seed")
  ssh -o BatchMode=yes omen "cd $base/$dir && tar cf - run-*" 2>/dev/null \
    | ssh -o BatchMode=yes spark "mkdir -p ~/episodes/stack_blocks/$dir && tar xf - -C ~/episodes/stack_blocks/$dir" 2>/dev/null
  echo "relayed seed $seed"
done
