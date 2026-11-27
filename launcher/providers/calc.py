"""Calculator: sums, units, currencies, dates and time zones.

Uses qalc (libqalculate) when installed: `5 ft to cm`, `100 usd to eur`,
`today + 3 weeks`, `days until 2026-12-25`, `15:00 Tokyo to Amsterdam`.
Without it, plain arithmetic still works through a safe evaluator.
"""

from __future__ import annotations

import ast
import math
import operator
import re
import subprocess
import time

from . import Provider, action, close, item, toast
from .. import util

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
        ast.USub: operator.neg, ast.UAdd: operator.pos}
_NAMES = {k: getattr(math, k) for k in ("pi", "e", "tau", "sqrt", "sin", "cos", "tan", "asin", "acos", "atan",
                                         "log", "log10", "log2", "exp", "floor", "ceil", "factorial")}
_NAMES.update({"abs": abs, "round": round, "ln": math.log})

LOOKS_LIKE_MATH = re.compile(r"\d")
WORDY = re.compile(r"^[a-zA-Z\s]+$")
# qalc functions with side effects (run a program, write or read files); qalc has no flag to disable them.
# No word boundary: "2save(x)" would still parse as a product.
QALC_UNSAFE = re.compile(r"(?:command|export|load|plot|save)\s*\(", re.I)
DATE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}:\d{2}")


def _commas(expr: str) -> str:
    """A comma between digits is a decimal comma ("1,5"), except where it separates
    the arguments of a function call ("round(2.5, 1)", "log(8,2)"), or groups
    thousands in a number like 1,000,000 (two groups or more)."""
    expr = re.sub(r"\b\d{1,3}(?:,\d{3}){2,}\b", lambda m: m.group(0).replace(",", ""), expr)
    out, calls = [], []          # calls: for each open "(", whether it opens a function call
    for i, ch in enumerate(expr):
        if ch == "(":
            calls.append(bool(re.search(r"[A-Za-z_]\w*\s*$", expr[:i])))
        elif ch == ")" and calls:
            calls.pop()
        elif ch == "," and not (calls and calls[-1]):
            if 0 < i < len(expr) - 1 and expr[i - 1].isdigit() and expr[i + 1].isdigit():
                ch = "."
        out.append(ch)
    return "".join(out)


MAX_BITS = 100_000   # results larger than this are refused (a power tower would take minutes and gigabytes)


def _power(left, right):
    if right > 0 and left not in (0, 1, -1):
        bits = math.log2(abs(left)) * right if abs(left) > 1 else 0
        if bits > MAX_BITS or right > 1_000_000:
            raise ValueError("result too large")
    return left ** right


def safe_eval(expr: str):
    """Arithmetic only: numbers, + - * / // % ^ **, parentheses and math functions."""
    expr = _commas(expr.replace("^", "**").replace("×", "*").replace("÷", "/"))
    # "20%" and "50 * 10%" are percentages; "10 % 3" is the remainder.
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%(?!\s*[\d(.A-Za-z])", r"(\1/100)", expr)
    tree = ast.parse(expr, mode="eval")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = ev(node.left), ev(node.right)
            if isinstance(node.op, ast.Pow):
                return _power(left, right)
            return _OPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Name) and node.id in _NAMES and not callable(_NAMES[node.id]):
            return _NAMES[node.id]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and callable(_NAMES.get(node.func.id)) \
                and not node.keywords and len(node.args) <= 2:
            args = [ev(a) for a in node.args]
            if node.func.id == "factorial" and (args[0] > 500 or args[0] != int(args[0])):
                raise ValueError("factorial too large")
            return _NAMES[node.func.id](*args)
        raise ValueError("not arithmetic")

    return ev(tree)


def format_number(value) -> str:
    if isinstance(value, bool):
        raise ValueError
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return f"{value:.10g}"
    raise ValueError


class Calculator(Provider):
    id = "calc"
    name = "Calculator"
    prefix = "="
    slow = True
    description = "Sums, units, currencies, dates; qalc when installed"

    def __init__(self, ctx):
        super().__init__(ctx)
        self._rates_checked = 0.0

    def search(self, q, scoped):
        q = q.strip()
        if not q or (not scoped and (not LOOKS_LIKE_MATH.search(q) or WORDY.match(q))):
            return []
        if not scoped and re.fullmatch(r"[\d\s.,]+", q):
            return []   # a bare number is not a calculation
        result = None
        if not DATE.search(q):  # 2026-12-25 is a date, not a subtraction
            try:
                result = format_number(safe_eval(q))
            except Exception:
                result = None
        if result is None and util.have("qalc"):
            result = self._qalc(q)
        if result is None or result == q:
            return []
        return [item("calc", "result", result, subtitle=f"{q} =", icon="󰃬", accessory="Calculator",
                     recordable=False, score=990, _expr=q)]

    def _qalc(self, q: str) -> str | None:
        if QALC_UNSAFE.search(q):
            return None
        argv = ["qalc", "-t", "-m", "800"]
        if self.settings.get("calculator.currency") and time.time() - self._rates_checked > 86400:
            # -e: fetch fresh exchange rates (once a day; qalc caches them).
            argv.append("-e")
            self._rates_checked = time.time()
        # A leading space keeps a query like "-v" from reading as one of qalc's options.
        try:
            code, stdout, _ = util.communicate(argv + [" " + q if q.startswith("-") else q],
                                               timeout=6 if "-e" in argv else 2.5)
        except (OSError, subprocess.SubprocessError):
            return None
        text = stdout.strip().splitlines()
        if code != 0 or not text:
            return None
        res = text[-1].strip()
        if not res or res.lower().startswith(("error", "warning")) or res == q:
            return None
        # qalc echoes plain words back unchanged ("hello" → "hello").
        if re.fullmatch(r"[a-zA-Z ]+", res) and res.replace(" ", "").lower() == q.replace(" ", "").lower():
            return None
        return res

    def actions(self, it):
        return [action("default", "Copy result", "󰆏"),
                action("paste", "Paste result", "󰆒", "ctrl+enter"),
                action("copy-sum", "Copy sum and result", "󰆏", "ctrl+shift+c")]

    def run(self, it, aid, arg):
        if aid == "paste":
            util.paste(it["title"])
            return [close()]
        if aid == "copy-sum":
            util.copy(f"{it['_expr']} = {it['title']}")
        else:
            util.copy(it["title"])
        return [toast("Copied " + it["title"]), close()]
