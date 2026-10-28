"""Advanced bounded static analyses over caller-supplied Python sources.

Pure standard library; never executes supplied source, never touches the
filesystem or network. Reuses the validation, parsing and finding contract of
``static`` so basename-collision protection, byte limits and redaction rules
stay identical. All exceptions carry machine codes only. Findings reference
sanitized file labels, line numbers and content hashes; they never contain
source snippets, secret values or paths.
"""
import ast
import copy
import hashlib
import json
import re
from .static import _validated_files, _Ctx, _parse, _finding
ADV_ADV_MAX_AST_NODES = 20000
ADV_MAX_FINDINGS = 300
ADV_OUTPUT_LIMIT = 120000
ADV_MIN_CLONE_NODES = 8
ADV_MAX_FLOW_ENTRIES = 200
ADV_MAX_CLONE_GROUPS = 100
ADV_MAX_CLONE_REFS = 3
ADV_MAX_GROUP_MEMBERS_IN_SUMMARY = 8
ADV_ADV_BASE_LIMIT = 'All findings are static-analysis candidates; none are confirmed exploitable vulnerabilities'
ADV_ADV_NEEDS_TOOLCHAIN = 'No Python sources supplied; analysis of other languages requires an external toolchain'
ADV_DATA_FLOW_LIMITS = ('Taint tracking is intra-function over assignments and branches; cross-function, closure, aliasing, container and comprehension-internal flows are not modeled', 'Sanitizer and transform recognition is pattern-based and unverified; absence of a finding is not proof of safety', 'Bound SQL parameters passed separately from statement text are assumed not to be concatenated into the statement')
ADV_CONTROL_FLOW_LIMITS = ('Control-flow summaries are structural approximations computed from statement structure; no symbolic execution is performed',)
ADV_DEAD_LIMITS = ('Dead-code detection covers statements after unconditional terminators, fully terminating branches and constant-false conditions; reachability through loops, try and dynamic branches is not proven', 'No source is modified or removed; findings are informational only')
ADV_CLONE_LIMITS = ('Clone detection compares function bodies after location and identifier normalization with literals preserved; semantically similar but structurally different code is not detected',)
_CLEAN = 0
_UNCERTAIN = 1
_TAINTED = 2
ADV_REQUEST_SOURCES = {'args', 'form', 'GET', 'POST'}
ADV_SANITIZER_KEYS = frozenset({('int',), ('float',), ('bool',), ('len',), ('abs',), ('escape',), ('html', 'escape'), ('bleach', 'clean'), ('markupsafe', 'escape'), ('secure_filename',), ('werkzeug', 'utils', 'secure_filename')})
ADV_PASS_FUNC_KEYS = frozenset({('str',), ('repr',), ('list',), ('tuple',), ('set',), ('frozenset',), ('sorted',), ('bytes',), ('bytearray',), ('format',), ('enumerate',), ('iter',), ('next',)})
ADV_PASS_METHODS = frozenset({'get', 'getlist', 'items', 'values', 'keys', 'pop', 'strip', 'lstrip', 'rstrip', 'lower', 'upper', 'title', 'capitalize', 'casefold', 'encode', 'decode', 'split', 'rsplit', 'splitlines', 'join', 'format', 'format_map', 'replace', 'ljust', 'rjust', 'zfill', 'expandtabs', 'removeprefix', 'removesuffix', '__getitem__'})
ADV_SUBPROCESS_NAMES = {'run', 'call', 'check_call', 'check_output', 'Popen'}
ADV_SQL_SINKS = {'execute', 'executemany', 'executescript'}
ADV_TERMINALS = (ast.Return, ast.Raise, ast.Break, ast.Continue)
ADV_MATCH = getattr(ast, 'Match', None)
ADV_TRY_T = (ast.Try,) + ((ast.TryStar,) if hasattr(ast, 'TryStar') else ())
ADV_COND_CTX = (ast.If, ast.While, ast.For, ast.AsyncFor, ast.ExceptHandler) + ADV_TRY_T + ((ADV_MATCH,) if ADV_MATCH is not None else ())
ADV_SAFE_NAME_RE = re.compile('^[A-Za-z_][A-Za-z0-9_.-]{0,63}$')
ADV_TAINT_MSG = {'eval': 'User-controlled source propagates to an eval/exec sink within the same function (intra-procedural taint candidate)', 'cmd': 'User-controlled source propagates to a shell command sink (os.system/os.popen/subprocess shell=True)', 'sql': 'User-controlled source propagates into a SQL execute call'}
ADV_UNVERIFIED_MSG = 'Tainted-derived value passes through an unverified transform or sanitizer before reaching a sensitive sink; effectiveness not proven'

def adv_safe_name(name):
    return name if type(name) is str and ADV_SAFE_NAME_RE.match(name) else '[redacted]'

def adv_dotted(func):
    parts = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
        return list(reversed(parts))
    return None

def adv_syntax_finding(f):
    return _finding('PY.SYNTAX.ERROR', 'medium', None, 'proven', f, f.error_line or 1, 'Python syntax error prevents AST parsing')

def adv_parse_guarded(ctx, f):
    tree = _parse(ctx, f)
    if ctx.node_count > ADV_ADV_MAX_AST_NODES:
        raise ValueError('ast_limit_exceeded')
    return tree

def adv_dedupe(findings):
    seen = set()
    out = []
    for finding in findings:
        key = (finding['rule_id'], finding['evidence']['file'], finding['evidence']['line'], finding['evidence']['sha256'])
        if key in seen:
            continue
        seen.add(key)
        out.append(finding)
    return out

def adv_scope_nodes(scope):
    nodes = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
            continue
        nodes.append(node)
        stack.extend(ast.iter_child_nodes(node))
    nodes.sort(key=lambda n: (getattr(n, 'lineno', 0), getattr(n, 'col_offset', 0)))
    return nodes

def adv_parent_map(tree):
    parents = {}
    todo = [tree]
    while todo:
        parent = todo.pop()
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent
            todo.append(child)
    return parents

def adv_is_conditional(node, parents, scope):
    cur = parents.get(node)
    while cur is not None:
        if cur is scope:
            return False
        if isinstance(cur, ADV_COND_CTX):
            return True
        cur = parents.get(cur)
    return False

def adv_expr_taint(node, state, depth=0):
    if depth > 16 or node is None:
        return _UNCERTAIN
    if isinstance(node, ast.Constant):
        return _CLEAN
    if isinstance(node, ast.Name):
        return state.get(node.id, _CLEAN)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Name) and node.value.id == 'request' and (node.attr in ADV_REQUEST_SOURCES):
            return _TAINTED
        return adv_expr_taint(node.value, state, depth + 1)
    if isinstance(node, ast.Call):
        return adv_call_taint(node, state, depth)
    if isinstance(node, ast.BinOp):
        return max(adv_expr_taint(node.left, state, depth + 1), adv_expr_taint(node.right, state, depth + 1))
    if isinstance(node, ast.BoolOp):
        return max((adv_expr_taint(v, state, depth + 1) for v in node.values))
    if isinstance(node, ast.JoinedStr):
        return max([adv_expr_taint(v, state, depth + 1) for v in node.values], default=_CLEAN)
    if isinstance(node, ast.FormattedValue):
        return adv_expr_taint(node.value, state, depth + 1)
    if isinstance(node, ast.IfExp):
        return max(adv_expr_taint(node.body, state, depth + 1), adv_expr_taint(node.orelse, state, depth + 1))
    if isinstance(node, ast.UnaryOp):
        return adv_expr_taint(node.operand, state, depth + 1)
    if isinstance(node, ast.Subscript):
        return max(adv_expr_taint(node.value, state, depth + 1), adv_expr_taint(node.slice, state, depth + 1))
    if isinstance(node, (ast.Starred, ast.Await, ast.NamedExpr)):
        return adv_expr_taint(node.value, state, depth + 1)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return max([adv_expr_taint(e, state, depth + 1) for e in node.elts], default=_CLEAN)
    if isinstance(node, ast.Dict):
        return max([adv_expr_taint(v, state, depth + 1) for v in node.values if v is not None], default=_CLEAN)
    if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
        return max([adv_expr_taint(node.elt, state, depth + 1)] + [adv_expr_taint(g.iter, state, depth + 1) for g in node.generators], default=_CLEAN)
    if isinstance(node, ast.DictComp):
        return max([adv_expr_taint(node.key, state, depth + 1), adv_expr_taint(node.value, state, depth + 1)] + [adv_expr_taint(g.iter, state, depth + 1) for g in node.generators], default=_CLEAN)
    if isinstance(node, ast.Lambda):
        return adv_expr_taint(node.body, state, depth + 1)
    return _CLEAN

def adv_call_taint(node, state, depth):
    arg_t = _CLEAN
    for a in node.args:
        arg_t = max(arg_t, adv_expr_taint(a, state, depth + 1))
    for kw in node.keywords:
        arg_t = max(arg_t, adv_expr_taint(kw.value, state, depth + 1))
    parts = adv_dotted(node.func)
    key = tuple(parts) if parts else None
    if parts == ['input']:
        return _TAINTED
    if key is not None and key in ADV_SANITIZER_KEYS:
        return _UNCERTAIN if arg_t else _CLEAN
    recv = _CLEAN
    if isinstance(node.func, ast.Attribute):
        recv = adv_expr_taint(node.func.value, state, depth + 1)
    if recv == _TAINTED:
        return _TAINTED
    if recv == _UNCERTAIN:
        return _UNCERTAIN
    if key is not None and key in ADV_PASS_FUNC_KEYS or (isinstance(node.func, ast.Attribute) and node.func.attr in ADV_PASS_METHODS):
        return arg_t
    return _UNCERTAIN if arg_t else _CLEAN

def adv_taint_finding(f, node, rule, cwe, kind_key):
    return _finding(rule, 'high' if kind_key != 'unverified' else 'info', cwe, 'heuristic_candidate', f, node.lineno, ADV_TAINT_MSG[kind_key] if kind_key != 'unverified' else ADV_UNVERIFIED_MSG)

def adv_check_sink(f, node, state, out):
    parts = adv_dotted(node.func)
    if not parts or not node.args:
        return
    if parts in (['eval'], ['exec']):
        t = _CLEAN
        for a in node.args:
            t = max(t, adv_expr_taint(a, state))
        for kw in node.keywords:
            t = max(t, adv_expr_taint(kw.value, state))
        if t == _TAINTED:
            out.append(adv_taint_finding(f, node, 'ADV.PY.TAINT.EVAL_EXEC', 95, 'eval'))
        elif t == _UNCERTAIN:
            out.append(adv_taint_finding(f, node, 'ADV.PY.TAINT.UNVERIFIED_TRANSFORM', None, 'unverified'))
        return
    shell_kw = any((kw.arg == 'shell' and isinstance(kw.value, ast.Constant) and (kw.value.value is True) for kw in node.keywords))
    is_cmd = parts in (['os', 'system'], ['os', 'popen']) or (len(parts) >= 2 and parts[0] == 'subprocess' and (parts[-1] in ADV_SUBPROCESS_NAMES) and shell_kw)
    if is_cmd:
        t = adv_expr_taint(node.args[0], state)
        if t == _TAINTED:
            out.append(adv_taint_finding(f, node, 'ADV.PY.TAINT.COMMAND', 78, 'cmd'))
        elif t == _UNCERTAIN:
            out.append(adv_taint_finding(f, node, 'ADV.PY.TAINT.UNVERIFIED_TRANSFORM', None, 'unverified'))
        return
    if parts[-1] in ADV_SQL_SINKS:
        first = node.args[0]
        if isinstance(first, ast.Constant):
            return
        t = adv_expr_taint(first, state)
        if t == _TAINTED:
            out.append(adv_taint_finding(f, node, 'ADV.PY.TAINT.SQLI', 89, 'sql'))
        elif t == _UNCERTAIN:
            out.append(adv_taint_finding(f, node, 'ADV.PY.TAINT.UNVERIFIED_TRANSFORM', None, 'unverified'))

def adv_bind(target, t, state, cond):
    if isinstance(target, ast.Name):
        if cond:
            state[target.id] = max(state.get(target.id, _CLEAN), t)
        else:
            state[target.id] = t
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            adv_bind(elt, t, state, cond)
    elif isinstance(target, ast.Starred):
        adv_bind(target.value, t, state, cond)

def adv_taint_scope(f, scope, parents, out, stats):
    state = {}
    for node in adv_scope_nodes(scope):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is not None:
                t = adv_expr_taint(node.value, state)
                targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
                cond = adv_is_conditional(node, parents, scope)
                for target in targets:
                    adv_bind(target, t, state, cond)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                state[node.target.id] = max(state.get(node.target.id, _CLEAN), adv_expr_taint(node.value, state))
        elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
            state[node.target.id] = max(state.get(node.target.id, _CLEAN), adv_expr_taint(node.value, state))
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            adv_bind(node.target, adv_expr_taint(node.iter, state), state, False)
        elif isinstance(node, ast.ExceptHandler) and isinstance(node.name, str):
            state[node.name] = max(state.get(node.name, _CLEAN), _UNCERTAIN)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            adv_bind(node.optional_vars, adv_expr_taint(node.context_expr, state), state, False)
        if isinstance(node, ast.Call):
            stats['sinks'] += 1
            adv_check_sink(f, node, state, out)

def adv_h_data_flow(ctx):
    out = []
    stats = {'sinks': 0}
    scopes = 0
    funcs = 0
    for f in ctx.python():
        tree = adv_parse_guarded(ctx, f)
        if tree is None:
            out.append(adv_syntax_finding(f))
            continue
        parents = adv_parent_map(tree)
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        funcs += len(functions)
        scopes += 1 + len(functions)
        for scope in [tree] + functions:
            adv_taint_scope(f, scope, parents, out, stats)
    return ('ok', out, list(ADV_DATA_FLOW_LIMITS), {'scopes_analyzed': scopes, 'function_scopes': funcs, 'sinks_inspected': stats['sinks']})

class adv_FR:
    __slots__ = ('blocks', 'edges', 'exits')

    def __init__(self, blocks, edges, exits):
        self.blocks = blocks
        self.edges = edges
        self.exits = exits

def adv_flow_seq(body, notes):
    blocks = 0
    edges = 0
    exits = {'fall'}
    for stmt in body:
        if 'fall' not in exits:
            break
        r = adv_flow_stmt(stmt, notes)
        blocks += r.blocks
        edges += r.edges
        exits = exits - {'fall'} | r.exits
    return adv_FR(blocks, edges, exits)

def adv_flow_stmt(stmt, notes):
    if isinstance(stmt, ast.Return):
        return adv_FR(0, 0, {'return'})
    if isinstance(stmt, ast.Raise):
        return adv_FR(0, 0, {'raise'})
    if isinstance(stmt, ast.Break):
        return adv_FR(0, 0, {'break'})
    if isinstance(stmt, ast.Continue):
        return adv_FR(0, 0, {'continue'})
    if isinstance(stmt, ast.If):
        b = adv_flow_seq(stmt.body, notes)
        blocks = b.blocks
        edges = b.edges
        exits = set(b.exits)
        nb = 1 if stmt.body else 0
        ne = 1 if stmt.body else 0
        if stmt.orelse:
            o = adv_flow_seq(stmt.orelse, notes)
            blocks += o.blocks
            edges += o.edges
            nb += 1
            ne += 1
            exits |= o.exits
        else:
            exits.add('fall')
        if 'fall' in exits:
            nb += 1
            ne += 1
        return adv_FR(blocks + nb, edges + ne, exits)
    if isinstance(stmt, (ast.While, ast.For, ast.AsyncFor)):
        b = adv_flow_seq(stmt.body, notes)
        o = adv_flow_seq(stmt.orelse, notes) if stmt.orelse else adv_FR(0, 0, set())
        blocks = b.blocks + 1
        edges = b.edges + 2
        if 'fall' in b.exits:
            edges += 1
        if 'break' in b.exits:
            edges += 1
        if 'continue' in b.exits:
            edges += 1
        if stmt.orelse:
            blocks += o.blocks
            edges += 1 + o.edges
        exits = set()
        if 'fall' in b.exits or 'fall' in o.exits or 'break' in b.exits:
            exits.add('fall')
        exits |= (b.exits | o.exits) & {'return', 'raise'}
        return adv_FR(blocks, edges, exits)
    if isinstance(stmt, ADV_TRY_T):
        body = adv_flow_seq(stmt.body, notes)
        blocks = body.blocks
        edges = body.edges
        exits = set(body.exits)
        for h in stmt.handlers:
            r = adv_flow_seq(h.body, notes)
            blocks += r.blocks + 1
            edges += r.edges + 1
            exits |= r.exits
        if stmt.orelse:
            r = adv_flow_seq(stmt.orelse, notes)
            blocks += r.blocks
            edges += r.edges + 1
            exits |= r.exits
        if stmt.finalbody:
            r = adv_flow_seq(stmt.finalbody, notes)
            blocks += r.blocks
            edges += r.edges
            exits |= r.exits
        notes.add('try')
        return adv_FR(blocks, edges, exits)
    if ADV_MATCH is not None and isinstance(stmt, ADV_MATCH):
        blocks = 0
        edges = 0
        exits = set()
        for case in stmt.cases:
            r = adv_flow_seq(case.body, notes)
            blocks += r.blocks + 1
            edges += r.edges + 1
            exits |= r.exits
        exits.add('fall')
        notes.add('match')
        return adv_FR(blocks, edges, exits)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        return adv_flow_seq(stmt.body, notes)
    return adv_FR(0, 0, {'fall'})

def adv_flow_entry(fn, notes):
    returns = raises = breaks = continues = loops = branch_points = 0
    for n in adv_scope_nodes(fn):
        if isinstance(n, ast.Return):
            returns += 1
        elif isinstance(n, ast.Raise):
            raises += 1
        elif isinstance(n, ast.Break):
            breaks += 1
        elif isinstance(n, ast.Continue):
            continues += 1
        elif isinstance(n, (ast.While, ast.For, ast.AsyncFor)):
            loops += 1
        elif isinstance(n, ast.If):
            branch_points += 1
        elif ADV_MATCH is not None and isinstance(n, ADV_MATCH):
            branch_points += 1
    r = adv_flow_seq(fn.body, notes)
    return {'function': adv_safe_name(fn.name), 'line': fn.lineno, 'kind': 'async' if isinstance(fn, ast.AsyncFunctionDef) else 'function', 'blocks': 1 + r.blocks, 'edges': r.edges, 'returns': returns, 'raises': raises, 'breaks': breaks, 'continues': continues, 'loops': loops, 'branch_points': branch_points}

def adv_h_control_flow(ctx):
    entries = []
    notes = set()
    for f in ctx.python():
        tree = adv_parse_guarded(ctx, f)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                entries.append(adv_flow_entry(node, notes))
    lims = list(ADV_CONTROL_FLOW_LIMITS)
    if 'try' in notes:
        lims.append('try/except/finally control-flow edges are approximated')
    if 'match' in notes:
        lims.append('match-statement edges are approximated')
    if len(entries) > ADV_MAX_FLOW_ENTRIES:
        entries = entries[:ADV_MAX_FLOW_ENTRIES]
        lims.append('Control-flow summary capped at %d functions' % ADV_MAX_FLOW_ENTRIES)
    return ('ok', [], lims, {'functions': entries})

def adv_always_terminates(body):
    for stmt in body:
        if isinstance(stmt, ADV_TERMINALS):
            return True
        if isinstance(stmt, ast.If) and stmt.orelse:
            if adv_always_terminates(stmt.body) and adv_always_terminates(stmt.orelse):
                return True
    return False

def adv_dead_list(f, body, out):
    term = False
    for (i, stmt) in enumerate(body):
        if term:
            out.append(_finding('ADV.PY.DEAD.UNREACHABLE', 'low', 561, 'proven', f, stmt.lineno, '%d statement(s) unreachable after an unconditional return/raise/break/continue or fully terminating branch' % (len(body) - i)))
            return
        adv_dead_stmt(f, stmt, out)
        if isinstance(stmt, ADV_TERMINALS) or (isinstance(stmt, ast.If) and stmt.orelse and adv_always_terminates(stmt.body) and adv_always_terminates(stmt.orelse)):
            term = True

def adv_dead_stmt(f, stmt, out):
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        adv_dead_list(f, stmt.body, out)
        return
    if isinstance(stmt, ast.If):
        if isinstance(stmt.test, ast.Constant):
            if not stmt.test.value:
                if stmt.body:
                    out.append(_finding('ADV.PY.DEAD.CONST_BRANCH', 'low', 570, 'proven', f, stmt.body[0].lineno, 'Branch body cannot execute: condition is a constant false value'))
                if stmt.orelse:
                    adv_dead_list(f, stmt.orelse, out)
                return
            adv_dead_list(f, stmt.body, out)
            if stmt.orelse:
                out.append(_finding('ADV.PY.DEAD.CONST_BRANCH', 'low', 571, 'proven', f, stmt.orelse[0].lineno, 'Else branch cannot execute: condition is a constant true value'))
            return
        adv_dead_list(f, stmt.body, out)
        if stmt.orelse:
            adv_dead_list(f, stmt.orelse, out)
        return
    if isinstance(stmt, ast.While):
        if isinstance(stmt.test, ast.Constant) and (not stmt.test.value):
            if stmt.body:
                out.append(_finding('ADV.PY.DEAD.CONST_BRANCH', 'low', 570, 'proven', f, stmt.body[0].lineno, 'Loop body cannot execute: while condition is a constant false value'))
            if stmt.orelse:
                adv_dead_list(f, stmt.orelse, out)
            return
        adv_dead_list(f, stmt.body, out)
        if stmt.orelse:
            adv_dead_list(f, stmt.orelse, out)
        return
    if isinstance(stmt, (ast.For, ast.AsyncFor, ast.With, ast.AsyncWith)):
        adv_dead_list(f, stmt.body, out)
        if stmt.orelse:
            adv_dead_list(f, stmt.orelse, out)
        return
    if isinstance(stmt, ADV_TRY_T):
        adv_dead_list(f, stmt.body, out)
        for h in stmt.handlers:
            adv_dead_list(f, h.body, out)
        if stmt.orelse:
            adv_dead_list(f, stmt.orelse, out)
        if stmt.finalbody:
            adv_dead_list(f, stmt.finalbody, out)
        return
    if ADV_MATCH is not None and isinstance(stmt, ADV_MATCH):
        for case in stmt.cases:
            adv_dead_list(f, case.body, out)
        return

def adv_h_dead_code(ctx):
    out = []
    scopes = 0
    for f in ctx.python():
        tree = adv_parse_guarded(ctx, f)
        if tree is None:
            out.append(adv_syntax_finding(f))
            continue
        functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        scopes += 1 + len(functions)
        adv_dead_list(f, tree.body, out)
    return ('ok', out, list(ADV_DEAD_LIMITS), {'scopes_scanned': scopes})

class adv_CloneNorm(ast.NodeTransformer):

    def __init__(self):
        self.map = {}
        self.counters = {'p': 0, 'v': 0, 'f': 0}

    def _canon(self, name, role):
        if name not in self.map:
            self.map[name] = '%s%d' % (role, self.counters[role])
            self.counters[role] += 1
        return self.map[name]

    def visit_FunctionDef(self, node):
        node.name = self._canon(node.name, 'f')
        self.generic_visit(node)
        return node

    def visit_AsyncFunctionDef(self, node):
        node.name = self._canon(node.name, 'f')
        self.generic_visit(node)
        return node

    def visit_arg(self, node):
        node.arg = self._canon(node.arg, 'p')
        self.generic_visit(node)
        return node

    def visit_Name(self, node):
        if isinstance(node.ctx, (ast.Store, ast.Del)) and node.id not in self.map:
            self._canon(node.id, 'v')
        if node.id in self.map:
            node.id = self.map[node.id]
        return node

    def visit_ExceptHandler(self, node):
        if isinstance(node.name, str):
            node.name = self._canon(node.name, 'v')
        self.generic_visit(node)
        return node

def adv_clone_key(fn):
    clone = copy.deepcopy(fn)
    adv_CloneNorm().visit(clone)
    return hashlib.sha256(ast.dump(clone).encode('utf-8')).hexdigest()

def adv_h_duplication(ctx):
    groups = {}
    total = 0
    findings = []
    for f in ctx.python():
        tree = adv_parse_guarded(ctx, f)
        if tree is None:
            findings.append(adv_syntax_finding(f))
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            total += 1
            if sum((1 for _ in ast.walk(node))) < ADV_MIN_CLONE_NODES:
                continue
            groups.setdefault(adv_clone_key(node), []).append((f, node))
    group_summaries = []
    dup_count = 0
    for members in groups.values():
        if len(members) < 2:
            continue
        dup_count += 1
        group_summaries.append({'size': len(members), 'members': [{'file': f.label, 'line': m.lineno} for (f, m) in members[:ADV_MAX_GROUP_MEMBERS_IN_SUMMARY]]})
        for (f, m) in members:
            refs = ['%s:%d' % (x.label, n.lineno) for (x, n) in members if n is not m][:ADV_MAX_CLONE_REFS]
            extra = len(members) - 1 - len(refs)
            ref_s = ', '.join(refs) + (' and %d more' % extra if extra > 0 else '')
            findings.append(_finding('ADV.PY.DUPLICATE.FUNCTION', 'low', None, 'proven', f, m.lineno, 'Structurally identical to %d other supplied function(s) after location and identifier normalization (literals preserved); duplicate(s) at %s' % (len(members) - 1, ref_s)))
    group_summaries.sort(key=lambda g: (-g['size'], g['members'][0]['file'], g['members'][0]['line']))
    lims = list(ADV_CLONE_LIMITS)
    if len(group_summaries) > ADV_MAX_CLONE_GROUPS:
        group_summaries = group_summaries[:ADV_MAX_CLONE_GROUPS]
        lims.append('Clone group summary capped at %d groups' % ADV_MAX_CLONE_GROUPS)
    return ('ok', findings, lims, {'functions_compared': total, 'clone_groups': dup_count, 'groups': group_summaries})
ADV_ADV_HANDLERS = {'sast.data.flow': adv_h_data_flow, 'sast.control.flow': adv_h_control_flow, 'sast.dead.code': adv_h_dead_code, 'sast.duplication': adv_h_duplication}
ADVANCED_STATIC_OPS = frozenset(ADV_ADV_HANDLERS)

def adv_fit_output(result):

    def size():
        try:
            return len(json.dumps(result).encode('utf-8'))
        except (TypeError, ValueError):
            return 0
    if size() <= ADV_OUTPUT_LIMIT:
        return result
    trimmed = False
    if result.get('findings'):
        result['truncated'] = True
        trimmed = True
        while result['findings'] and size() > ADV_OUTPUT_LIMIT - 512:
            result['findings'].pop()
    summary = result.get('summary')
    while isinstance(summary, dict) and size() > ADV_OUTPUT_LIMIT - 512:
        removed = False
        for key in list(summary):
            value = summary[key]
            if isinstance(value, list) and value:
                value.pop()
                removed = True
                trimmed = True
                break
        if not removed:
            result.pop('summary', None)
            trimmed = True
            break
    if size() > ADV_OUTPUT_LIMIT - 512:
        result.pop('summary', None)
        trimmed = True
    if trimmed:
        result['limitations'] = sorted(set(result.get('limitations', ())) | {'Output size limit reached; additional entries suppressed'})
    return result

def advanced_static_scan(operation, payload):
    if type(operation) is not str:
        raise ValueError('invalid_operation')
    if operation not in ADVANCED_STATIC_OPS:
        raise ValueError('unknown_operation')
    files = _validated_files(payload)
    ctx = _Ctx(files)
    (status, findings, lims, summary) = ADV_ADV_HANDLERS[operation](ctx)
    if ctx.syntax_failed:
        lims.append('Syntax errors prevented full AST analysis of %d Python file(s)' % ctx.syntax_failed)
    py = ctx.python()
    if files and (not py):
        status = 'needs_toolchain'
        lims.append(ADV_ADV_NEEDS_TOOLCHAIN)
    skipped = len(files) - len(py)
    if skipped:
        lims.append('%d non-Python file(s) skipped; advanced static analysis requires Python sources' % skipped)
    findings = adv_dedupe(findings)
    findings.sort(key=lambda x: (x['evidence']['file'], x['evidence']['line'], x['rule_id']))
    truncated = len(findings) > ADV_MAX_FINDINGS
    if truncated:
        findings = findings[:ADV_MAX_FINDINGS]
        lims.append('Finding limit (%d) reached; additional findings suppressed' % ADV_MAX_FINDINGS)
    if not files:
        lims.append('No source files supplied')
        status = 'no_input'
    lims.append(ADV_ADV_BASE_LIMIT)
    result = {'status': status, 'operation': operation, 'findings': findings, 'scanned_files': len(files), 'limitations': sorted(set(lims)), 'confirmed_vulnerabilities': False, 'source_executed': False, 'truncated': truncated}
    if summary is not None and summary != {}:
        result['summary'] = summary
    return adv_fit_output(result)
