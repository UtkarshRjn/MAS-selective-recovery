# AWS clean pilot: NRP gpt-oss

Five MBPP tasks (2, 3, 4, 7, 8), five workers, no faults, no recovery.

- Task correctness: 5/5.
- Final assertions: 11/11.
- Batch wall time: 39.133 seconds.
- Model calls: 35.
- Input tokens: 7626; output tokens: 8761.
- Auxiliary README-name check: 3/5; not part of task correctness.

Instance: m7i.xlarge, us-west-2; 4 vCPUs, 16 GiB RAM.
One trial per task; this is a small custom MBPP subset, not full-benchmark accuracy.
First assertion is feedback; remaining assertions are final-only.
Raw rows, configuration, manifest, and task traces are preserved alongside this report.
