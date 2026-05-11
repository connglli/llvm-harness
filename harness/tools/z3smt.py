import shutil
from pathlib import Path
from subprocess import TimeoutExpired

from harness.lms.tool import FuncToolCallException, FuncToolSpec, StatelessFuncToolBase
from harness.utils.cmdline import getoutput

_TIMEOUT_SEC = 30
_MEMORY_MB = 4096


class SmtSolveTool(StatelessFuncToolBase):
  def __init__(self):
    z3 = shutil.which("z3")
    if z3 is None:
      raise FuncToolCallException("z3 not found on PATH. Ensure Z3 is installed.")
    self._z3 = z3

  def spec(self) -> FuncToolSpec:
    return FuncToolSpec(
      "smtsolve",
      "Run Z3 on an SMT-LIB2 file and return the result. "
      "Returns a result line (sat / unsat / unknown / timeout) followed by the raw Z3 output. "
      "Use this to check satisfiability, find counterexamples, or discharge proof obligations "
      "expressed in SMT-LIB2 syntax. "
      f"Z3 is limited to {_TIMEOUT_SEC} seconds and {_MEMORY_MB} MB of memory.",
      [
        FuncToolSpec.Param(
          "input_path",
          "string",
          True,
          "Path to the SMT-LIB2 file to solve. Example: '/tmp/formula.smt2'.",
        ),
      ],
      keywords=[
        "z3",
        "smt",
        "smt2",
        "satisfiability",
        "solver",
        "formula",
        "counterexample",
      ],
    )

  def _call(self, *, input_path: str, **kwargs) -> str:
    input_file = Path(input_path)
    if not input_file.is_file():
      raise FuncToolCallException(f"Input file not found: {input_path}")

    try:
      out = (
        getoutput(
          f"{self._z3} -T:{_TIMEOUT_SEC} -memory:{_MEMORY_MB} {input_file}",
          timeout=_TIMEOUT_SEC + 5,
        )
        or b""
      ).decode()
    except TimeoutExpired:
      out = "z3 process killed after timeout\n"

    timed_out = "timeout" in out or "killed" in out
    if timed_out:
      verdict = "timeout"
    elif "unsat" in out:
      verdict = "unsat"
    elif "sat" in out:
      verdict = "sat"
    else:
      verdict = "unknown"

    return f"Result: {verdict}\n\n{out}"
