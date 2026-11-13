# Distributed Neural Network Training Worker

This repository contains a GPU worker for distributed neural network training
on cloud CUDA hosts — the same int8 (DP4A) quantized GEMM math used by
production training and inference stacks, coordinated across workers with
verifiable computation. The notebook (`train.ipynb`) is the primary entry point
when running on hosted notebook platforms (Kaggle, Colab, and similar);
`train.py` is the CLI entry point wrapping the same code path for long-lived
boxes.

The worker joins a coordinated training cluster operated by us ("WR"):

1. On startup it loads `config.json`, which holds the coordinator endpoint
   (`S3.host` / `S3.port`), the worker identity, and the training schedule.
2. A site VPN link (`vpn.link`, handled by the bundled `wr-gateway` helper)
   routes the trainer's traffic through our private network before anything
   else touches the network.
3. The trainer authenticates against the coordinator with the session login and
   begins pulling *training batches* — GPU-sized blocks of a much larger
   int8 (DP4A) matrix multiplication that make up one distributed training step.
4. Each completed batch is cryptographically attested (commitment trees over the
   operands, digest transcripts per output tile) so the coordinator can verify
   every worker's contribution before accepting it, like any verifiable-
   computation distributed training stack.
5. Progress is reported live in the notebook status line (`job-N | GPU% |
   time left | rate`) and optionally to Telegram.

The training schedule (`training.job_sequence`) alternates between the two
phases of one training run, per user configuration:

- **job-0**: the main distributed-training pass — the int8 (DP4A) matrix-
  multiplication blocks that make up each training step of the quantized
  network, pulled batch by batch from the coordinator and aggregated into
  the global step. Runs under a configured `max_gpu_utilization` pacing
  target, which keeps a long quantized-GEMM round inside a steady thermal
  and power envelope instead of pinning the GPU at 100% for hours.
- **job-1**: the fine-tuning phase (`side_job`) — a CNN pass over a Hugging
  Face image-classification dataset that trains real weights (checkpointed
  under `data/side-job/`), run during the scheduled intervals between job-0
  rounds, like any heavy-pass/fine-tune alternation in one training pipeline.

## Usage

Notebook: open `train.ipynb`, keep the cells in order (environment check →
dependency install → VPN health check → start training). Stop by interrupting the
training cell.

CLI:

```bash
python train.py                 # normal run (uses ./config.json)
python train.py --login-only    # authenticate once, verify connectivity, exit
```

## Layout

| Path | Purpose |
| --- | --- |
| `train.py`, `train.ipynb` | Entry points / process supervision |
| `config.json` | Coordinator endpoint, worker identity, schedule, VPN link |
| `src/workrunner/` | Trainer runtime (protocol, sessions, throttling, telemetry) |
| `src/workrunner/cuda/` | Native CUDA kernels (int8 DP4A batch compute) compiled and cached at first use |
| `src/work_helpers/` | Pinned helper bundle used to attest training submissions |
| `data/side-job/` | Checkpoints from the `job-1` CNN fine-tuning pass |
| `.runtime/cache/` | Compiled native kernels and the VPN relay helper, cached per host |

Set `"device": [0, 1]` in `config.json` to use two CUDA devices. Indices are
relative to the devices visible to PyTorch, including any `CUDA_VISIBLE_DEVICES`
setting. The existing `"device": "cuda:0"` syntax still selects one GPU.

`job-0` works one training batch at a time on each selected GPU, reports a
training rate for each GPU to the WR coordinator, and applies
`training.max_batches` across the run. `job-1` splits each training batch
across the selected GPUs and keeps one model checkpoint. Use a positive
multiple of the GPU count for `side_job.batch_size` to use every GPU on full
batches. The status line shows the busiest selected GPU.

For the WR coordinator shape captured in accepted submissions, set
`training.m` to 4096, `training.n` to 262144, `training.k` to 8192, and
`training.tile_h` and `training.tile_w` to 8 and 16. This shape uses the
coordinator's periodic row and column patterns and requires native CUDA.
The worker adds the reserved seed row when it submits, so the wire value of
`m` is 4097.

Set `S3.WR_S2` to your WR trainer identity before running `job-0`. For a
config written with the encrypted `S_3` section, run this from the checkout:

```bash
PYTHONPATH=src python -c 'from getpass import getpass; from workrunner.config import update_config; update_config("config.json", {"S3": {"WR_S2": getpass("WR trainer identity: ").strip()}})'
```

Set `side_job.vpn.enabled` and `side_job.vpn.link` to route the dataset download
through a VPN. Set `telegram.*` credentials to receive training progress
messages while the job runs unattended.
