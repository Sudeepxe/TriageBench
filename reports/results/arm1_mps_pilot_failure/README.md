# Arm 1 MPS pilot -- diagnostic evidence (EXP-004)

See `docs/EXPERIMENT_LOG.md` (EXP-004) for the full narrative, including
the correction: the initial "hung process" diagnosis was retracted once
killing the process revealed it had actually completed training and two
of three evaluations, just far too slowly for a practical sweep.

- `mps_pilot_stack_sample_2026-09-10T1934.txt` -- excerpt of a
  non-destructive 2-second `sample` call taken while the pilot appeared
  stalled, showing the main thread blocked in MPS synchronization
  (`waitUntilCompleted`) and a Metal command-queue thread actively
  submitting GPU commands.
- `final_ps_snapshot.txt` -- `ps` output for the process and its parent
  immediately before termination.
- `interrupted_run_full_output.txt` -- the pilot's full stdout, released
  only once the process was terminated (it had been buffered by a
  `tail`-piped invocation). Contains the real, measured training/eval
  numbers from the interrupted run -- kept here as diagnostic context
  only, NOT as an official Arm 1 result (the run never reached its
  result-JSON write step).
