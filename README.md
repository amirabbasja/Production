# Distributed CNN Training Worker

This repository contains a GPU worker for distributed CNN training
on cloud CUDA hosts — the same int8 (DP4A) quantized GEMM math used by
production quantized-CNN training and inference stacks, coordinated across workers with
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
   int8 (DP4A) matrix multiplication that make up one distributed training
   step of the quantized CNN (the quantized convolution and dense-layer
   GEMMs of each step).
4. Each completed batch is cryptographically attested (commitment trees over the
   operands, digest transcripts per output tile) so the coordinator can verify
   every worker's contribution before accepting it, like any verifiable-
   computation distributed training stack.
5. The notebook shows progress as
   `trainer_name | interval | job | GPU% | time left | rate`.

The training schedule (`training.job_sequence`) cycles through the scheduled
phases of one training run, per user configuration:

- **job-0**: the main distributed-training pass of the quantized CNN — the int8 (DP4A) matrix-
  multiplication blocks that make up each training step of the quantized
  network, pulled batch by batch from the coordinator and aggregated into
  the global step. Runs under a configured `max_gpu_utilization` pacing
  target, which keeps a long quantized-GEMM round inside a steady thermal
  and power envelope instead of pinning the GPU at 100% for hours.
- **job-1**: the fine-tuning phase (`side_job`) — a CNN pass over a Hugging
  Face image-classification dataset that trains real weights (checkpointed
  under `data/side-job/`), run during the scheduled intervals between job-0
  rounds, like any heavy-pass/fine-tune alternation in one training pipeline.
- **job-2**: a mixed round — one interval in which the main distributed-
  training pass and the `side_job` fine-tuning pass run together on the same
  GPUs, each paced at half of the interval's `max_gpu_utilization`. The status
  line reports the main-pass training rate alongside the current CNN epoch.

An interval with `max_gpu_utilization` set to `-1` marks a clean
stop-and-exit point for the run.

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
through a VPN. Set `telegram.enabled`, `telegram.tgBot_key`, and `telegram.tgChatID`
to receive training error and VPN connection-loss alerts. Job start/finish events
and periodic progress reports are not sent to Telegram. The legacy
`training_stats_tgBot_key` setting is accepted but unused;
`update_interval_seconds` controls retries for failed alerts.

## Native CUDA performance

Large 8×8 and 8×16 workloads use vectorized DP4A kernels. Native WR batches
reuse the header-dependent right operand across batch seeds and rebuild it
when the header changes. A cached tree path recomputes the left commitment
from the 16-byte batch-seed tail; the left commitment and noise still change
with each batch seed.
The notebook and CLI select this path automatically; restart a running worker
to load changed Python code and bundled native libraries. Production loads
precompiled CPU, CUDA, and benchmark libraries from `src/workrunner/bin/`.
It needs no C++ compiler or `nvcc` at runtime. Native C++ and CUDA sources live
in `WR-trainer/native/production/`; rebuild and deploy them from WR-trainer with
`python tools/build_production_native.py`. The adjacent `compute_manifest.json`
records the supported GPU architectures, source fingerprints, and binary hashes.
Use `--check` with that build command to verify the deployed compute artifacts.

Benchmark complete batches offline, including preparation and candidate
filtering, without reading your config or connecting to the coordinator:

```bash
python tools/benchmark_worker.py
python tools/benchmark_worker.py --uncached --variant 7  # previous large 8×8 path
```

The default shape is 4096×4096×16384 with 8×8 tiles. Three
warmup batches are excluded; 20 measured batches use the runtime's `m*n*k`
rate accounting. Use `--iterations 100` for a longer run or `--stages` to
inspect costs (stage instrumentation adds synchronization). Offline rates
exclude coordinator latency, stale jobs, scheduling pauses, and CPU-side
verification.

On the GTX 1660 SUPER used for validation, 100 complete offline batches improved
from 4.72 TOPS to about 6.23 TOPS (m*n*k int8 operations per second). [Benchmark results](tools/benchmarks/gtx1660-super.json)
record both checkouts and the comparison commands.

Run native build, GPU transcript, and preparation checks with:

```bash
PYTHONPATH=src python -m unittest discover -s tests -p 'test_native*.py'
```

<!-- native-word-codec:start -->
## Native word codec

The trainer runtime and its native libraries share one canonical name
table, defined in `native/word_codec.cpp` and compiled into the bundled
`libword_codec.so`. It fixes the exact names used for the tensor operands,
tile geometry, signal baselines and regression fixtures of the distributed
CNN pass, so the Python runtime, the CUDA kernels, diagnostics and fixtures
always agree on every constant.
Each C++ table row contains exactly two fields: the canonical field name
and its standard padded Base64 form. On a name lookup, C++ decodes that
Base64 inside native memory and returns the text or binary bytes a caller
uses. Encoding compares candidate bytes against a temporarily decoded
value and returns the matching name. The Python API and the coordinator's
wire format stay unchanged; Base64 keeps the table compact and binary-safe.

The native wire encoder/decoder translates only the selected JSON request
fields and methods and handles the coordinator's frame format. It uses the
same encoded table; ordinary message strings are preserved.

```python
from workrunner.native_text_codec import transform_word, word_buffer

transform_word("training.field.matrix_tile_height", "decode")
transform_word("tile_h", "encode")
with word_buffer("training.tensor.left_operand_label") as tensor_a:
    consume(tensor_a)
```

The corresponding standalone import is `workrunner.native_text_codec`.
`decode_word` and `encode_word` match structural protocol aliases exactly and
pass unlisted text through unchanged. `encode_fixed` and `fixed_buffer` with
a `FixedAlias` perform exact lookup across the full constant table. An
unknown fixed alias or value raises `ValueError`. Fixed values can include
arbitrary binary bytes. The codec performs no substring replacement.

### Updating and distributing mappings

1. In ANN-Trainer, run `python3 tools/build_word_codec.py --encode-value NAME VALUE`
   to print a name/Base64 row; add `--binary` when VALUE is hexadecimal
   binary data. Add the resulting `make_base64_entry` or
   `make_base64_binary_entry` row to `native/word_codec.cpp`.
   Names and original values must each remain unique. For an explicit constant,
   add the row to `kConstants` and use `FixedAlias` in Python. For a new
   request field or command, also select it in the appropriate `key_entry` or
   `Parser::translation` profile in `native/wire_codec.cpp`. Profiles distinguish
   request fields, session fields, command names and ordinary string data; adding a
   word-table entry alone does not rewrite arbitrary message values.
2. Run `python3 tools/build_word_codec.py`. It validates the table, compiles the
   word and wire libraries, regenerates this table in all three READMEs, and atomically
   installs identical libraries and Python bridges. This includes
   `native_text_codec.py`, `wire_io.py` and `rpc_fields.py`. The default standalone
   checkout is the sibling `WR-trainer` (or `LRAEP-WR-Trainer` when present);
   the production checkout is the sibling `Production-LREAP` when present.
   Use `--standalone PATH` and `--production PATH` to choose other checkouts,
   or `--local-only` for an author-only build. Only compiled artifacts and
   Python bridges are copied; native C++ source stays in the trainer checkouts.
3. Run `python3 tools/build_word_codec.py --check` and the protocol/native tests.
   The check detects stale README content, source fingerprints, differing
   binaries, or differing Python bridges. Restart running trainer/notebook
   processes to load a newly installed library; loaded libraries are not
   hot-swapped in place.

The current build targets the host's Linux architecture (`x86_64` or
`aarch64`) and requires a C++17 compiler. The output is
`src/LRAEP_trainer/bin/<platform>/libword_codec.so` and, when distributing,
`src/workrunner/bin/<platform>/libword_codec.so`. The native message layer is
installed alongside it as `libwire_codec.so`. Rebuild on each target
architecture. No compiler or C++ decoder source is needed in the standalone
runtime. A missing or incompatible library produces an explicit error.

### Buffer lifetime

`word_buffer` and `fixed_buffer` copy values into mutable buffers and clear
the buffers on normal and exception exits. `word_matches` compares ASCII
protocol values with a scoped buffer. Native Base64 and decoded comparison
buffers are also cleared immediately on success and exception exits with volatile
writes, including wire/frame temporaries. The native table retains
only the Base64 bytes; plaintext is recovered separately for each call.

`transform_word`, `decode_word`, and `encode_word` return ordinary immutable
Python strings. Callers must release those references after use; their physical
bytes cannot be guaranteed erased, and serializers, sockets, exception text,
or caller-retained objects may create copies. The framing-only
`customEncoder`/`customDecoder` API implements the coordinator's frame format.
<!-- native-word-codec:end -->
