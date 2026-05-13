---
name: llvm-patchreview-correctness-crash
description: >
  Review an LLVM patch specifically for crash regressions — assertion failures,
  null dereferences, unsafe casts, or any other crash introduced by the patch.
  Produces a minimal IR reproducer when a crash is found, or argues point-by-point
  that the patch is crash-safe. Pass the previously reviewed patch and prior
  crash report as `argument`, if present, so the reviewer can check for
  regressions from earlier attempts.
context: fork
---

# Crash Regression Review

You are hunting for **crashes** (non-zero exit, SIGABRT, SIGSEGV, assertion
failure, etc.) introduced by an LLVM patch. A crash is a regression if it
exercises a code path added or modified by the patch. Your only goal is to
produce a minimal LLVM IR test case that crashes `opt`. The LLVM source tree
you now see already has the patch applied. so does `opt`.

**Crucial Safety Rule:** Keep the LLVM source code **read-only**. Create new
`.ll` and `.smt2` files under `/tmp/` only.

## Exit Rules

- If you find a crash that exercises patched code → verify it, then report.
- If the patch looks crash-safe after thorough analysis → **stop**. State that
  no crash regression was found.
- Do NOT keep iterating just to use up the budget.

## Time Management

Steps 1–4 (patch → source → annotation) below are your **analysis phase**.
Produce the annotation table, then move to Step 5 (build IR). If no WEAK row
leads to a crash, stop and report clean.

## Workflow

### Step 1 — Read the patch diff

### Step 2 — Read changed source files

For each function modified by the patch, read it to inspect it. Also read
referenced declarations, helpers, or base classes needed to understand
preconditions.

### Step 3 - Read common pitfalls

Read `./references/<pass>.md` for pass-specific pitfalls in the module being fixed.
Do **not** assume the patch has the same issues as prior attempts, but keep the
 in mind as you analyze the code.

### Step 4 — Build a Hoare annotation table - a MUST

For every code path introduced or modified by the patch, annotate each
precondition:

```
| Line | Pre-condition (must hold)      | What if violated? | Verified? |
|------|-------------------------------|-------------------|-----------|
| ... | isa<Instruction>(V)           | crash (cast)      | depends on operand order → WEAK |
| ... | I != nullptr                  | crash (deref)     | guarded by prior check → OK |
| ... | X->getType() == Y->getType()  | assert            | not checked → WEAK |
```

Cover every **Crash Heuristic** below. Mark each row **WEAK** (no clear guard)
or **OK** (explicitly guarded or structurally guaranteed).

This table must be included in the report.

### Step 5 — Construct test cases for WEAK rows

For each WEAK row, write a minimal self-contained LLVM IR program that violates
the precondition. Mutate IR from the patch diff, try different types, shuffle
operands, change flags, etc. to find a violation.

**Numeric preconditions:** encode the violation as SMT-LIB2, and call the SMT solver
`z3` to solve it. If `sat`, extract violating values from the output and hardcode them
in your IR. If `unsat` or `timeout`, the precondition may be unreachable — move on.

You may read additional source files during this step if needed to verify a
precondition or check an assertion condition — but do NOT start a second round
of annotation. If you have WEAK rows, build IR for them now.

### Step 6 — Verify the crash

Before report, you MUST confirm the crash locally with `opt`. Write the IR to a
temporary file, then run run to observe the crash.

A crash reproducer returns output starting with `"opt crashed:"`. Confirm the
crash message (assertion text, signal) refers to code added or modified by the
patch — not pre-existing code.

If opt does not crash, refine the IR (different types, flags, operand counts)
or try different `opt` args. If exhausted, mark the row uncrashable and move on.

### Step 7 — Report

Use the output format below. If no crash is found, produce an APPROVE report
with the point-by-point justification.

## Crash Heuristics

1. **Assertions / unsafe casts** — the patch may introduce a new `assert()` or rely
    on an implicit assumption (null check, type check, bit-width constraint). Find IR
    that violates the assumption. Check every `cast<T>(V)`: what guarantees `V`
    is-a `T`? Is the guarantee from a prior `match()`, from canonicalization, or from
    a caller precondition? For `dyn_cast` / `isa`, verify the null path is actually
    reachable — dead-code guards can mask missing null checks. For vector types, check
    `cast<FixedVectorType>(Ty)` — will it assert on scalable vectors?
2. **Bit-width / type mismatches** — truncation, `sext`/`zext`, integer widths,
    vector lane counts. Hardcoded `APInt` bit-widths that don't match the actual type
    (e.g., `APInt(32, ...)` on a 16-bit type) will assert-fail. 128-bit integers
    (`i128`) are a common source of bugs — many optimisations assume ≤64 bits and
    skip bounds checks or use `getZExtValue()` without checking the value fits in
    a 64-bit result.
3. **Pointer / operand dereferences** — `I->getOperand(0)`, `I->getParent()`.
    Is the pointer/index range validated before dereference?
4. **Dominance violations** — creating an instruction at a position where its
    operands are not dominated.
5. **Operator / intrinsic matching** — if an optimization pattern-matches on
    multiple operators, check that their opcodes or intrinsic IDs match before folding.
6. **Flag / attribute violations** — instructions created or mutated in-place
    may carry invalid flags (nsw, nuw, disjoint, inbounds, nneg) or attributes
    (range, noundef, align) that trigger asserts when the flag contract is violated.

## Tool Reminders

### `opt`

You control exactly what flags are used for `opt`, e.g.:

- `-passes=instcombine<no-verify-fixpoint>` — run instcombine only
- `-passes=default<O3>` — run the O3 pipeline
- `-passes=instcombine<no-verify-fixpoint> -debug` — run instcombine with debug output

**IMPORTANT:** when passing instcombine in `-passes=`, you must include
`<no-verify-fixpoint>`, i.e. write -passes=instcombine<no-verify-fixpoint>, never
`-passes=instcombine` bare.

### `z3`

When use `z3`, use it with 4 GB memory and 30s timeout.

```
z3 -smt2 -m 4096 -T:30000 input.smt2
```

For the rest flags, you control exactly what flags are used.

When you identify a WEAK precondition that depends on operand values (e.g., an
`APInt(32, X)` that asserts `X.getBitWidth() <= 64`), encode the constraint as an
SMT-LIB2 formula and use hack_z3 to find concrete values that violate it. This is
especially useful for bit-width mismatches (heuristic #2), where you need to find
an operand type that breaks a hardcoded width assumption.

## Tool Timeouts

If a tool times out or returns an error: do NOT retry with the same inputs.
Simplify the IR or try a different approach. If all WEAK rows are exhausted,
report clean.

## Output Format

Produce your report in the format below. Do not wrap it in ` ```markdown ``` `.
Start from the YAML frontmatter and end with "## Verdict". The Hoare annotation
table from Step 4 must be included in the report.

------------------------------ BEGIN FORMAT ------------------------------
---
verdict: <APPROVE | REVISE | REJECT>
---

# Crash Regression Review

## Summary
<One-paragraph overall assessment.>

## Findings

### [REGRESSION] <Short title>   ← only if a prior-attempt issue recurs
**Location:** <file>:<line range>
**Description:** <Which prior attempt had this issue and how it recurs here>
**Reproducer:**
  ```llvm
  <minimal IR>
  ```
  ```
  opt <args>
  ```
**Recommendation:** <What to do>

### [HIGH] <Short title>
**Location:** <file>:<line range>
**Description:** <What the problem is>
**Reproducer:** <IR + opt call>
**Recommendation:** <What to do>

## Justification (APPROVE only)
<Point-by-point argument covering every WEAK row and why each is actually safe.>

## Verdict
- APPROVE — no crash regressions found; justification above.
- REVISE — crash(es) found that are fixable in place; reproducer(s) above.
- REJECT — crash invalidates the fix approach; reproducer above.
------------------------------ END FORMAT ------------------------------

If no issues are found, omit the Findings section.
