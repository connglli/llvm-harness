---
name: llvm-patchreview-correctness-miscompilation
description: >
  Review an LLVM patch specifically for miscompilation regressions — silent
  semantic errors where the patched optimizer produces IR that computes a
  different result. Pass the previously reviewed patch and prior miscompilation
  report as `argument`, if present, so the reviewer can check for regressions
  from earlier attempts.
context: fork
---

# Miscompilation Regression Review

You are hunting for **miscompilations** — silent semantic errors where the
patched `opt` produces IR that computes a different result than the input
— introduced by an LLVM patch. A miscompilation is a regression if it lives
in a transform introduced or modified by the patch. The LLVM source tree you
now see already has the patch applied. so does `opt`.

**Crucial Safety Rule:** Keep the LLVM source code **read-only**. Create new
`.ll` and `.smt2` files under `/tmp/` only.

## Exit Rules

- If you confirm a miscompilation → report it with the reproducer.
- If all proofs come back correct (or timeout/error) → **stop**. State that
  no miscompilation regression was found.
- Do NOT keep iterating just to use up the budget.

## Time Management

Read the patch and source to understand the transform, then write generalized
proofs. Focus on the patched function and any helpers it calls directly — do
not read unrelated infrastructure. If a proof times out or errors, move on to
the next candidate.

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

### Step 4 — Hack the optimization

#### When alive2 is applicable

If the patch is fixing a transform, understand the fixed transform and write a
**generalized** `@src`/`@tgt` pair to prove it with alive2 when necessary.
Use symbolic parameters rather than hardcoded constants, and express preconditions
with `@llvm.assume`. This proves or disproves the transform for **all** inputs
within the stated constraints — not just the specific case the patch author had in mind.

**Use small bit-widths** (`i8`, `half`, `bfloat`) to keep alive2's search
space small. Only widen if the transform requires it, for example, the transform
is width-sensitive or relies on overflow behavior that differs across widths.
For example:
```llvm
; Example: fold (X sdiv C) slt X  →  X sgt 0
define i1 @src(i8 %x, i8 %C) {
  %precond = icmp ne i8 %C, 1
  call void @llvm.assume(i1 %precond)
  %div = sdiv i8 %x, %C
  %cmp = icmp slt i8 %div, %x
  ret i1 %cmp
}
define i1 @tgt(i8 %x, i8 %C) {
  %cmp = icmp sgt i8 %x, 0
  ret i1 %cmp
}
```

For pointer proofs, reduce the pointer width to keep the search space tractable:
```llvm
target datalayout = "p:8:8:8"
```

**Use necessary preconditions** Make preconditions as tight as necessary and no
tighter. Over-constraining with @llvm.assume can make alive2 verify a transform
that is actually unsound on inputs the assume rules out — the goal is to capture
exactly the conditions the patch author relies on, then check whether those
conditions are sufficient. If alive2 finds a counterexample despite your
preconditions, the precondition is either missing a constraint or the transform
is wrong.

**Run alive2** Run `alive-tv` with the above `@src`/`@tgt` in a single file.

- **"Transformation is correct"** — holds for all inputs within preconditions.
  Move to the next candidate.
- **"Transformation is INCORRECT"** — alive2 found a miscompilation. Read the
  counterexample for the specific violating values. Proceed to step 5.
- **Inconclusive / error** — simplify the IR (fewer ops, smaller types, avoid
  vectors or unusual intrinsics) and retry once. If it still fails, move on.

**Validate the counterexample**  The counterexample gives specific input values.
Inline those as constants, remove `@llvm.assume` calls, and produce a single
`@fun` function using `@src` (not `@tgt`). After that, optimize `@fun` with `opt`
(with the transform enabled) to obtain the optimized `@src` — the one after
optimiation should be similar to `@tgt`. Then run `alive-tv` again to confirm
the miscompilation.

```
opt -S -passes=<pass> fun.ll -o fun.opt.ll
alive-tv --smt-to=30000 --disable-undef-input fun.ll fun.opt.ll
# If the result is "Transformation is INCORRECT", the counterexample is valid.
```

**Loop proofs:** alive2 supports `-src-unroll=N -tgt-unroll=N` (max 128).
Pass these via the `args` parameter of `alive-tv`.

#### Otherwise

For cases that are difficult to express in alive2 — certain floating-point transforms,
loop-carried dependencies, complex memory aliasing, or preconditions that depend on
type structure rather than value constraints — analyze the patch line by line to
identify what the transform assumes on entry and what it guarantees on exit. Then
construct a **concrete LLVM IR** test case that sits at the boundary: valid by the
patch's own preconditions, but designed to expose the specific value range or
structural condition that might be wrong.

Run it through `opt` with the pass enabled, then compare execution
with `lli`. Any output divergence is a confirmed miscompilation.

```
opt -S -passes=<pass> fun.ll -o fun.opt.ll
lli fun.ll
lli fun.opt.ll
# If the outputs differ, the patch is miscompiling.
```

This is a weaker form of validation than alive2 — it witnesses a single concrete
execution rather than quantifying over all inputs — but it has different reach:
it can catch bugs in transforms that alias analysis, memory models, or loop
structure make hard to encode symbolically. Treat a passing concrete test as
"not obviously wrong on this input", not as a proof of correctness.

### Step 5 — Refine into a concrete reproducer

A reproducer is a minimal self-contained LLVM IR program that triggers
the miscompilation when optimized with the patch, with the exact `opt` command
used to optimize it, and if necessary, additional `alive-tv` or `lli` commands
to validate the counterexample, depending on whether you come from the alive2
branch in Step 4 or the concrete test branch. Combine all of these into a
single bash script that can be copy-pasted and run by the patch author.
For example:

```bash
# Minimal IR that violates the precondition
cat > /tmp/fun.ll <<'EOF'
; reproducer
define i32 @fun(i32 %x) {
  %shl = shl i32 %x, 31
  %cmp = icmp ult i32 %shl, 0
  ret i32 %cmp
}
EOF

# Optimize with the patch
opt -S -passes=instcombine<no-verify-fixpoint> /tmp/fun.ll -o /tmp/fun.opt.ll

# Validate the miscompilation with alive2
alive-tv --smt-to=30000 --disable-undef-input /tmp/fun.ll /tmp/fun.opt.ll
```

### Step 6 — Report

Use the output format below. If no miscompilation is found, produce an APPROVE
report with the point-by-point justification.

## Miscompilation Heuristics

### 1. Poison-Generating Instruction Flags

When a fold replaces operands, removes guarding conditions, or changes semantics,
you **MUST** check whether these flags are still valid and drop them if not:

| Flag | Applies to | Implication | When to drop |
|------|-----------|-------------|--------------|
| `nuw` | add, sub, mul, shl | poison if unsigned overflow | operand widened or new operand may wrap |
| `nsw` | add, sub, mul, shl | poison if signed overflow | same |
| `exact` | sdiv, udiv, ashr, lshr | poison if not exact division | new divisor may not divide evenly |
| `disjoint` | or | poison if operands share set bits | operand replaced, fold merges bits |
| `samesign` | icmp | poison if operands differ in sign | operands changed, pred inverted |
| `inbounds` | getelementptr | poison if address out of bounds | address recomputed |
| `nneg` | zext | poison if src is negative | src semantics changed |

**Key rules for flag handling:**
- `replaceOperand()` **retains** the old flags — if the new operand makes them invalid, drop them.

### 2. Poison-Generating / UB-Implying Attributes and Metadata

| Attribute | Applies to | UB implication | When to drop |
|-----------|-----------|----------------|--------------|
| `range(S,E)` | ctlz, cttz, ctpop intrinsics | result is poison if outside range | guard removed, operand changed |
| `noundef` | any instruction / call arg | returning undef/poison is immediate UB | `is_zero_poison` set, operand may be poison |
| `align N` | load, store, call args | UB if pointer misaligned | address recomputed, aliasing changed |
| `nonnull` | call args, return | UB if pointer is null | operand may become null through fold |
| `dereferenceable(N)` | call args | UB if <N bytes readable | memory access transformed away |
| `dereferenceable_or_null(N)` | call args | UB if non-null but <N bytes readable | same |

**Metadata on load instructions** (also carry UB/poison semantics):

| Metadata | Implication |
|----------|-------------|
| `!range` | result is poison if loaded value is outside range |
| `!nonnull` | result is poison if loaded value is null |
| `!align` | immediate UB if pointer is misaligned |
| `!dereferenceable` | immediate UB if <N bytes readable at pointer |
| `!dereferenceable_or_null` | immediate UB if non-null and <N bytes readable |

### 3. Fast-Math Flags

**You may only use `nnan` and `ninf`.** Never use `fast`, `nsz`, `arcp`, `contract`,
`afn`, or `reassoc`. Only `nnan` and `ninf` carry poison semantics relevant to
correctness bugs; the other fast-math flags relax ordering/precision guarantees and
have no poison implication.

| Flag | Implication | Allowed in IR? |
|------|-------------|----------------|
| `nnan` | fadd/fsub/fmul/fdiv/frem: poison if any operand is NaN | **Yes** |
| `ninf` | same ops: poison if any operand is ±Inf | **Yes** |
| `fast` | composite — implies all flags below | **No** |
| `nsz` | ±0 treated as identical (no poison) | **No** |
| `arcp` | division reciprocal approx (no poison) | **No** |
| `contract` | FMA allowed (no poison) | **No** |
| `afn` | approximate functions allowed (no poison) | **No** |
| `reassoc` | reassociation allowed (no poison) | **No** |

For `nnan`/`ninf`: does the fold turn a NaN/Inf result into a finite one, or vice versa?

### 4. Overly Relaxed Preconditions

The patch may optimize a pattern previously guarded by a stricter condition. Feed input that
satisfies the new (looser) precondition but violates the old (correct) assumption.

### 5. ConstantExpr

Does the patch match on `Constant` but neglect `ConstantExpr`?  A constant expression can appear
where a plain constant is expected.

### 6. Refinement / Replacement

If the patch replaces expression `A` with `B` based on `simplify(A) == simplify(B)`, check whether
`simplify(B)` introduces poison/UB that `A` did not have. Look for `replaceAllUsesWith` versus
single-use optimizations: the replacement must be safe for **every** user, not just the current one.

### 7. In-Place Modification

When the patch modifies an existing instruction in-place — via `setOperand()`, `mutateType()`,
or any method that changes the instruction's semantics without creating a new `Instruction` —
the old flags and metadata **persist** on the modified instruction. You MUST check:

- Does the new operand/type satisfy the existing flags?  If not, drop them.
  Example: `setOperand(0, NewOp)` on `or disjoint` — if `NewOp` may share bits, drop `disjoint`.
- Does the new operand satisfy existing metadata constraints?
  Example: narrowing the type of an `add nsw` to a smaller width that may overflow — drop `nsw`.
- Could poison that was previously impossible now become possible?
  Example: replacing a `zext nneg` operand with one that may be negative — drop `nneg`.

The principle: **any in-place mutation must re-validate all flags and attributes on the instruction.**

### 8. Narrow Type Arithmetic

Arithmetic transforms that are sound for `i8`/`i16`/`i32`/`i64` may be **unsound for `i1` and
`i2`**, especially when the transform involves flag propagation (poison-generating flags like
`nuw`, `nsw`, `exact`). Narrow types have small value ranges where overflow, truncation, and
wrapping semantics differ from wider types.

## Common Pitfalls

Refer to `references/<pass>.md` for pass-specific pitfalls in the module being
fixed. Do **not** assume the patch has the same issues as prior attempts.

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

### `alive-tv`

When use `alive-tv`, use it with 30s timeout and no undef input.

```
alive-tv --smt-to=30000 --disable-undef-input src.ll tgt.ll
```

or

```
alive-tv --smt-to=30000 --disable-undef-input srctg.ll
```

For the rest flags, you control exactly what flags are used.

**Limitations:** alive2 cannot analyze all IR. It will error on:
- Vector operations, shufflevector, extractelement/insertelement
- Some intrinsics (e.g. `@llvm.experimental.*`)
- Very large functions or modules
- Floating-point operations in certain modes
- Memory operations without proper `data layout` in the module

Note: `@llvm.assume` and `@llvm.ctpop` **are** supported by alive2 and should be
used to express preconditions in generalized proofs.

If alive2 errors out, the result is NOT a confirmed miscompilation. Simplify the
IR to avoid the unsupported feature, or move on to the next candidate.

## Tool Timeouts

If a tool times out or returns an error: do NOT retry with the same inputs.
Simplify the IR or try a different approach. If all WEAK rows are exhausted,
report clean.

## Output Format

Produce your report in the format below. Do not wrap it in ` ```markdown ``` `.
Start from the YAML frontmatter and end with "## Verdict".

------------------------------ BEGIN FORMAT ------------------------------
---
verdict: <APPROVE | REVISE | REJECT>
---

# Miscompilation Regression Review

## Summary
<One-paragraph overall assessment.>

## Findings

### [REGRESSION] <Short title>   ← only if a prior-attempt issue recurs
**Location:** <file>:<line range>
**Description:** <Which prior attempt had this issue and how it recurs here>
**Reproducer:**
  ```llvm
  <minimal IR — single @f function>
  ```
  ```
  llvm_optimize_ir(input_path="/tmp/repr.ll", args="<args>")
  ```
  alive2 proof: `llvm_verify_ir(src_path="/tmp/src.ll", tgt_path="/tmp/tgt.ll")`
**Recommendation:** <What to do>

### [HIGH] <Short title>
**Location:** <file>:<line range>
**Description:** <What the problem is>
**Reproducer:** <IR + llvm_optimize_ir call + alive2 proof call>
**Recommendation:** <What to do>

## Justification (APPROVE only)
<Point-by-point argument that each candidate transform is correct and general.>

## Verdict
- APPROVE — no miscompilation regressions found; justification above.
- REVISE — miscompilation(s) found that are fixable in place; reproducer(s) above.
- REJECT — miscompilation invalidates the fix approach; reproducer above.
------------------------------ END FORMAT ------------------------------

If no issues are found, omit the Findings section.
