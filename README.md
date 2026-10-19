# lab-eval: Opentrons Flex Protocol Generation & Safety Benchmark

`lab-eval` benchmarks an LLM agent on translating natural-language molecular biology requests into executable Opentrons Flex Python APIv2 protocols, handling under-specified tasks, and refusing physically impossible operations.

Grading relies on an independent, deterministic multi-stage checker:
1. **Static AST Analysis**: Enforces deck immutability and valid hardware definitions.
2. **Deterministic Sandbox Simulation**: Runs protocols via `opentrons.simulate`.
3. **Trace & Ledger Audit**: Validates microliter transfer volumes and tip policies.

---

## Benchmark Design & Empirical Results

The agent operates in a multi-turn tool-calling loop (`run_simulation`, `submit_protocol`, `refuse`).

Evaluated on Claude 3.5 Haiku (`claude-haiku-5-5`), 3 runs per case:

| Test Suite | Total Cases | Total Runs | Pass Rate | Tool Calls (`run_simulation`) | Silent Guesses | Over-Caution |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Baseline Suite** | 31 | 93 | **98.9%** (92/93) | 8 / 45 submit runs | 0 / 48 | 0 / 45 |
| **Hard Stress Suite** | 10 | 30 | **100.0%** (30/30) | 0 / 9 submit runs | 0 / 21 | 0 / 9 |
| **Combined** | **41** | **123** | **99.2%** (122/123) | **8 / 54 submit runs** | **0 / 69** | **0 / 54** |

---

## Key Findings

### 1. Zero-Shot Premature Submissions
Across 123 runs, exactly one run failed:
* **Case**: `std-01` (Run 2)
* **Outcome**: `fail_protocol`
* **Error**: `TipNotAttachedError: Cannot perform aspirate without a tip attached.`

The model bypassed `run_simulation` and called `submit_protocol` directly on turn 1. In all 8 runs where `run_simulation` was invoked proactively, runtime errors were caught and corrected in subsequent turns.

### 2. Prompt Enum Leakage & The Checklist Effect
Across both suites, Haiku saturated performance with 1-turn completions. Providing structured refusal enums in the system prompt served as an explicit in-context decision tree. The model executed static arithmetic checks in-memory and called `refuse` directly, bypassing protocol generation and simulation entirely.

### 3. Implications for Agentic Evals
To evaluate genuine debugging and recovery loops in lab automation, benchmarks should:
* Ablate discrete refusal reason enums in favor of open-ended explanations graded semantically.
* Introduce latent dynamic constraints (e.g., liquid viscosity blow-out delays, geometry clashes) that cannot be deduced from static prompt math alone.
