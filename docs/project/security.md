# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x (pre-release) | Yes |
| < 0.1.0 | No |

---

## Reporting a Vulnerability

If you discover a security vulnerability, report it privately. **Do NOT report through public GitHub issues.**

Send an email to **opensource@openagenthq.com** with:

- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)
- Your contact information

### Response Timeline

| Step | Timeline |
|------|----------|
| Acknowledgment | Within 48 hours |
| Initial Assessment | Within 1 week |
| Fix Released | Within 30 days (critical issues) |

---

## Security Best Practices

### For Users

- Keep ModelDock up to date (`pip install -U modeldock`)
- Use environment variables for secrets; never hardcode
- Download models only from trusted runtimes/registries
- Check a model's provenance before installing — `search`/`info` show the
  `Source` it came from, and `modeldock sources` lists every active source with
  its trust level (`official` / `verified` / `community` / `bundled` /
  `custom`). ModelDock never hides where an artifact originates.

### For Contributors

- Follow secure coding practices
- Never commit secrets or `.env` files
- Raise typed `ModelDockError` subclasses — never swallow silently
- Run `bandit -r src` as part of local checks
- Treat output from adapters as untrusted; see [Prompt-Injection & Untrusted Model Output](#prompt-injection--untrusted-model-output) below.

---

## Prompt-Injection & Untrusted Model Output

### Threat Model

ModelDock runtime adapters (Ollama, LM Studio, llama.cpp, etc.) relay data
from external processes and HTTP APIs. **All data returned by a model or
runtime API is untrusted by definition.** It may contain:

- Prompt-injection payloads designed to mislead downstream tooling.
- Malformed or adversarial strings that exploit parsers.
- Content crafted to escape the intended output context (HTML, shell, SQL).

### Rules

1. **Never execute model output.** Do not pass adapter responses to `exec()`,
   `eval()`, `subprocess`, `os.system()`, or any code-execution primitive.
2. **Never treat model output as trusted instructions.** If your application
   uses ModelDock within an agent or tool pipeline, model output must be
   validated and sandboxed before it influences control flow.
3. **Parse, don't interpret.** Adapter output should be parsed into typed
   domain objects (`ModelRef`, `PullResult`, `RunResult`) — not consumed as
   free-form text that drives logic.

### Guidance for Users

- Treat any text returned by a model as **untrusted user input**.
- Sanitise model output before displaying it in HTML, terminal, or log
  contexts to prevent injection (e.g., terminal escape-sequence attacks).
- Do not copy-paste model output into a shell without reviewing it.

### Guidance for Contributors

- Validate and sanitise all data at the adapter boundary before constructing
  domain objects.
- Never pass raw adapter responses to shell commands or string-interpolated
  queries.
- Add explicit `# Security: untrusted input` comments at trust boundaries in
  adapter code.
- See `src/modeldock/adapters/runtimes/base.py` for the canonical trust-
  boundary documentation.

### Guidance for Integrators

If you embed ModelDock in a larger agent, copilot, or automation pipeline:

- Apply output-escaping appropriate to your downstream context (HTML, SQL,
  shell, etc.).
- Do not grant model output the authority to invoke tools, modify files, or
  make network requests without an explicit human-in-the-loop approval step.
- Assume every string originating from an adapter response could be adversarial.

---

## Model Execution & Native Code

### Threat Model

ModelDock does not perform inference itself. `load()` and `run()` hand a model
to a runtime — Ollama, LM Studio, llama.cpp and others — which loads the
weights and executes them as **native machine code**, in a process owned by
the user who invoked ModelDock, with that user's full privileges.

A model artifact is therefore not inert data:

- **Weight files are parsed by native C/C++ loaders.** A malformed GGUF header
  is a memory-safety bug in that loader, not a Python exception you can catch.
- **Some formats carry executable content by construction.** Pickle-based
  `.bin` checkpoints deserialize arbitrary Python objects; repositories that
  ship custom operators or conversion scripts execute code by design.
- **Model metadata drives the runtime.** Chat templates and tokenizer
  configuration embedded in a model file are interpreted by the runtime, not
  validated by ModelDock.
- **A runtime is a separate program.** Once ModelDock has asked it to load a
  model, ModelDock has no further control over what that process reads,
  writes, or connects to.
- **Installed plugins run inside ModelDock itself.** Any distribution that
  advertises a `modeldock.runtimes`, `modeldock.model_sources`, or
  `modeldock.catalog_providers` entry point is imported *and instantiated* in
  ModelDock's own process the moment a registry is built. Installing such a
  package is equivalent to granting it arbitrary code execution.

**ModelDock cannot sandbox any of this.** Python cannot confine a native
library already mapped into its address space, and it cannot restrain a server
process it does not supervise. Real containment comes from the operating
system. What ModelDock *can* do is decline to take part, and tell you when it
is about to — which is what the setting below controls.

### The `execution_policy` Setting

Set it in `config.toml`, or as `MODELDOCK_EXECUTION_POLICY`:

| Value | Warns about native execution | Third-party plugins | Backends that load models in-process |
|-------|------------------------------|---------------------|--------------------------------------|
| `unrestricted` | No | Loaded | Allowed |
| `warn` (default) | Once per session | Loaded | Allowed |
| `strict` | Once per session | **Not imported or executed** | **Refused** |

Be clear about what `strict` does and does not buy you. It is not a sandbox.
It restricts what *ModelDock's own process* will execute: no third-party
plugin code, and no backend that maps model weights into that process. A
runtime server such as Ollama or llama-server still runs your model with your
full privileges — `strict` does not change that, and cannot. Confine the
runtime with the operating system, as below.

### Rules

1. **Treat a model file as a program, not a document.** Apply the same
   scrutiny to its origin that you would to an executable you downloaded.
2. **Prefer runtimes that execute out-of-process.** A separate server process
   can be confined by the OS; a native library inside your own interpreter
   cannot.
3. **Never install a ModelDock plugin you would not accept as arbitrary code.**
   Entry-point discovery grants it exactly that. Use `execution_policy =
   "strict"` when running untrusted or unaudited environments.
4. **Do not rely on ModelDock for isolation.** It reports and refuses; it does
   not contain.

### Running a Model in a Restricted Context

Confine the *runtime*, not ModelDock. A reasonable baseline, using Ollama as
the example — the same shape applies to `llama-server` and LM Studio:

```bash
docker run --rm \
  --user "$(id -u):$(id -g)" \
  --read-only --tmpfs /tmp \
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$PWD/models:/models:ro" \
  -p 127.0.0.1:11434:11434 \
  ollama/ollama
```

What each part is for:

- `--user` — never run the runtime as root.
- `--read-only` plus a read-only model mount — the model directory is the only
  filesystem the runtime needs, and it does not need to write to it.
- `--cap-drop ALL`, `--security-opt no-new-privileges` — inference needs no
  capabilities.
- `-p 127.0.0.1:...` — bind the API to loopback so it is not exposed to the
  network. Add `--network none` once the model is downloaded if the runtime
  does not need to fetch anything at inference time.

Then point ModelDock at it (`ollama_host`, `lmstudio_host`, or
`MODELDOCK_OLLAMA_HOST`) and set `execution_policy = "strict"` so ModelDock
itself executes nothing beyond its own shipped code.

If you launch `llama-server` directly, note that ModelDock only ever *suggests*
that command in an error hint — it never runs it for you. Apply the same
confinement to the command you actually run.

### Guidance for Contributors

- A runtime adapter must not spawn a model process without documenting it.
  Today every shipped adapter is an HTTP client to a server the user started.
- Declare `executes_in_process = True` on any adapter that loads weights into
  ModelDock's interpreter, so `strict` can refuse it.
- The execution policy is decided once, in `core/execution.py`. Do not
  re-implement or bypass it in an adapter or in the CLI.

---

## Contact

- **Email**: opensource@openagenthq.com
