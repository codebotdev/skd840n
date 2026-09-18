# SPDX-License-Identifier: GPL-2.0-only
"""Restricted interpreter for IDM header tests, not a C compiler.

Models the used u32/u64 arithmetic, short circuiting, bounded arrays and output
pointers. Unsupported AST or undefined shifts fail closed. Does not model ABI,
concurrency, alias analysis, hardware, DMA or all of ISO C.
"""
from dataclasses import dataclass
import re

from pycparser import c_ast, c_parser


@dataclass
class Number:
    value: int
    bits: int = 32
    unsigned: bool = False

    def __post_init__(self):
        if self.unsigned:
            self.value %= 1 << self.bits
        elif not -(1 << (self.bits - 1)) <= self.value < (1 << (self.bits - 1)):
            raise ValueError("signed overflow outside the test model")


@dataclass
class Cell:
    value: object
    kind: object


@dataclass
class Pointer:
    cells: list
    index: int = 0

    def ref(self, index=0):
        pos = self.index + index
        if not 0 <= pos < len(self.cells):
            raise IndexError("out-of-bounds C-model access")
        return self.cells[pos]


class Returned(Exception):
    def __init__(self, value):
        self.value = value


def kind(node):
    if isinstance(node, c_ast.TypeDecl):
        return kind(node.type)
    if isinstance(node, c_ast.IdentifierType):
        names = tuple(node.names)
        if names == ("u32",):
            return (32, True)
        if names == ("u64",):
            return (64, True)
        if names == ("int",):
            return (32, False)
    if isinstance(node, c_ast.PtrDecl):
        return ("pointer", kind(node.type))
    raise ValueError("unsupported C-model declaration " + type(node).__name__)


def convert(value, target):
    if target[0] == "pointer":
        if value is None or isinstance(value, Pointer):
            return value
        raise ValueError("unsupported pointer conversion")
    if not isinstance(value, Number):
        raise ValueError("number required")
    return Number(value.value, *target)


def truth(value):
    return value.value != 0 if isinstance(value, Number) else value is not None


def common(a, b):
    # The only mixed ranks used by this header are int, u32 and u64.
    bits = max(a.bits, b.bits)
    unsigned = (a.unsigned if a.bits == bits else False) or (b.unsigned if b.bits == bits else False)
    return Number(a.value, bits, unsigned), Number(b.value, bits, unsigned)


class Model:
    def __init__(self, source, constants, sizes):
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
        source = re.sub(r"^#.*$", "", source, flags=re.M)
        tree = c_parser.CParser().parse("typedef unsigned int u32; typedef unsigned long long u64;\n" + source)
        self.functions = {n.decl.name: n for n in tree.ext if isinstance(n, c_ast.FuncDef)}
        self.globals = {name: Cell(Number(v, 64 if v > 0xffffffff else 32, True), None)
                        for name, v in constants.items()}
        for name, value in dict(EINVAL=22, EMSGSIZE=90, ERANGE=34, ENOSPC=28, EOVERFLOW=75).items():
            self.globals[name] = Cell(Number(value), None)
        cells = [Cell(Number(x, 32, True), (32, True)) for x in sizes]
        self.globals["skd840n_idm_region_bytes"] = Cell(Pointer(cells), None)

    def call(self, name, *args):
        node = self.functions[name]
        params = node.decl.type.args.params
        if len(params) != len(args):
            raise ValueError("argument count")
        env, arrays = dict(self.globals), []
        for param, value in zip(params, args):
            target = kind(param.type)
            if target[0] == "pointer":
                if isinstance(value, list):
                    cells = [Cell(Number(x, *target[1]), target[1]) for x in value]
                    arrays.append((value, cells))
                    value = Pointer(cells)
            elif isinstance(value, int):
                value = Number(value, *target)
            env[param.name] = Cell(convert(value, target), target)
        try:
            self.stmt(node.body, env)
            raise ValueError("function did not return")
        except Returned as result:
            answer = convert(result.value, kind(node.decl.type.type))
        for original, cells in arrays:
            original[:] = [x.value.value for x in cells]
        return answer.value

    def ref(self, node, env):
        if isinstance(node, c_ast.ID):
            return env[node.name]
        if isinstance(node, c_ast.ArrayRef):
            return self.expr(node.name, env).ref(self.expr(node.subscript, env).value)
        if isinstance(node, c_ast.UnaryOp) and node.op == "*":
            ptr = self.expr(node.expr, env)
            if ptr is None:
                raise ValueError("null dereference")
            return ptr.ref()
        raise ValueError("unsupported lvalue")

    def expr(self, node, env):
        if isinstance(node, (c_ast.ID, c_ast.ArrayRef)):
            return self.ref(node, env).value
        if isinstance(node, c_ast.Constant):
            text = node.value.lower()
            raw = re.sub(r"[ul]+$", "", text)
            value = int(raw, 16 if raw.startswith("0x") else 10)
            bits = 64 if "ll" in text else 32
            return Number(value, bits, "u" in text or (raw.startswith("0x") and value >= 1 << (bits - 1)))
        if isinstance(node, c_ast.UnaryOp):
            if node.op == "*":
                return self.ref(node, env).value
            a = self.expr(node.expr, env)
            if node.op == "!":
                return Number(int(not truth(a)))
            if node.op == "-":
                return Number(-a.value, a.bits, a.unsigned)
            if node.op == "~":
                return Number(~a.value, a.bits, a.unsigned)
            if node.op in ("p++", "++"):
                cell = self.ref(node.expr, env)
                cell.value = Number(a.value + 1, a.bits, a.unsigned)
                return a if node.op == "p++" else cell.value
            raise ValueError("unsupported unary " + node.op)
        if isinstance(node, c_ast.BinaryOp):
            a = self.expr(node.left, env)
            if node.op == "&&":
                return Number(int(truth(a) and truth(self.expr(node.right, env))))
            if node.op == "||":
                return Number(int(truth(a) or truth(self.expr(node.right, env))))
            b = self.expr(node.right, env)
            if node.op in ("<<", ">>"):
                if not 0 <= b.value < a.bits:
                    raise ValueError("undefined shift")
                return Number(a.value << b.value if node.op == "<<" else a.value >> b.value, a.bits, a.unsigned)
            a, b = common(a, b)
            comparisons = {"<": lambda: a.value < b.value, ">": lambda: a.value > b.value,
                           "<=": lambda: a.value <= b.value, ">=": lambda: a.value >= b.value,
                           "==": lambda: a.value == b.value, "!=": lambda: a.value != b.value}
            if node.op in comparisons:
                return Number(int(comparisons[node.op]()))
            operations = {"+": lambda: a.value + b.value, "-": lambda: a.value - b.value,
                          "*": lambda: a.value * b.value, "&": lambda: a.value & b.value,
                          "|": lambda: a.value | b.value}
            if node.op not in operations:
                raise ValueError("unsupported binary " + node.op)
            return Number(operations[node.op](), a.bits, a.unsigned)
        if isinstance(node, c_ast.FuncCall) and isinstance(node.name, c_ast.ID):
            args = [self.expr(x, env) for x in node.args.exprs]
            return Number(self.call(node.name.name, *args))
        if isinstance(node, c_ast.Assignment):
            cell = self.ref(node.lvalue, env)
            value = self.expr(node.rvalue, env)
            if node.op == "+=":
                value = Number(cell.value.value + value.value, *cell.kind)
            elif node.op == "|=":
                value = Number(cell.value.value | value.value, *cell.kind)
            elif node.op != "=":
                raise ValueError("unsupported assignment")
            cell.value = convert(value, cell.kind)
            return cell.value
        raise ValueError("unsupported expression " + type(node).__name__)

    def stmt(self, node, env):
        if node is None:
            return
        if isinstance(node, c_ast.Compound):
            for child in node.block_items or []:
                self.stmt(child, env)
        elif isinstance(node, c_ast.Decl):
            if isinstance(node.type, c_ast.ArrayDecl):
                count = self.expr(node.type.dim, env).value
                target = kind(node.type.type)
                if not isinstance(node.init, c_ast.InitList) or len(node.init.exprs) > count:
                    raise ValueError("unsupported array initializer")
                values = [self.expr(x, env) for x in node.init.exprs]
                values += [Number(0)] * (count - len(values))
                env[node.name] = Cell(Pointer([Cell(convert(x, target), target) for x in values]), None)
            else:
                target = kind(node.type)
                value = self.expr(node.init, env) if node.init else Number(0)
                env[node.name] = Cell(convert(value, target), target)
        elif isinstance(node, c_ast.If):
            self.stmt(node.iftrue if truth(self.expr(node.cond, env)) else node.iffalse, env)
        elif isinstance(node, c_ast.For):
            self.stmt(node.init, env)
            budget = 10000
            while truth(self.expr(node.cond, env)):
                budget -= 1
                if budget < 0:
                    raise ValueError("test loop limit")
                self.stmt(node.stmt, env)
                self.stmt(node.next, env)
        elif isinstance(node, c_ast.Return):
            raise Returned(self.expr(node.expr, env))
        else:
            self.expr(node, env)
