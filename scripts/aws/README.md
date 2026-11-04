# CPU evaluation host

Provisioned with the explicitly selected `agent-recovery` AWS profile in `us-west-2`.
Instance: `i-0a683bfb52ba03356`, Ubuntu 24.04, `m7i.xlarge` (4 vCPUs / 16 GiB),
30 GiB encrypted gp3 root volume. Existing instances were not modified.
The existing security group `sg-0f21593d5fd211ed1` is used unchanged.
No IAM instance profile or model credentials were attached.

Local private SSH key: `~/.ssh/agent-recovery-ec2`; AWS key-pair name:
`a3nair-agent-recovery-ec2`. Never commit the private key or model API keys.

Fetch the current public address (it can change after stop/start):

```sh
aws ec2 describe-instances --profile agent-recovery --region us-west-2 \
  --instance-ids i-0a683bfb52ba03356 \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text
```

Connect, replacing INSTANCE_IP with that result:

```sh
ssh -i ~/.ssh/agent-recovery-ec2 -o IdentitiesOnly=yes \
  -o UserKnownHostsFile=~/.ssh/agent-recovery-known-hosts ubuntu@INSTANCE_IP
cd ~/agent-recovery
uv run pytest -q
```

Code is a source snapshot of the local `expand-benchmark-examples` worktree, including
uncommitted changes, not a clone of its base commit. The upload is an explicit allowlist
of project source, tests, manifests, and the pinned public MBPP dataset. Local deployment
records, source archive, and SHA-256 inventory are under ignored `results/aws-deployment/`.
The remote inventory is `~/source-manifest.json`. Previous experiment results, AWS
credentials, model credentials, and the SSH private key were not uploaded.

`bootstrap.sh` is the cloud-init script; check `cloud-init status` and
`/var/log/cloud-init-output.log` for installation status. Docker runs natively on Linux;
no Docker Desktop required. Model inference remains remote. See `evals/EXPANSION.md`
for endpoint and credential options. `--workers 16` controls task concurrency; containers
share four CPUs. A 16-container offline smoke check is not a model throughput guarantee.

Before a live run, supply the chosen project model key via its environment variable or
an external protected key file. Run under tmux to survive SSH disconnects. There is no
automatic restart/resume after instance interruption, and no automatic idle shutdown.

Stop when idle (retains EBS data; storage charges continue):

```sh
aws ec2 stop-instances --profile agent-recovery --region us-west-2 \
  --instance-ids i-0a683bfb52ba03356
```

Start again:

```sh
aws ec2 start-instances --profile agent-recovery --region us-west-2 \
  --instance-ids i-0a683bfb52ba03356
```

Do not terminate without first retrieving any needed results: root storage is configured
to be deleted on termination. Public IPv4 charges apply while an address is allocated.
