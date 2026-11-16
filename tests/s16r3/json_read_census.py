"""AST census of decoder and ledger calls, including local helper call chains.

All JSON calls are seeds, including aliases: no file-reader heuristic can discard a decoder helper.
Ledger operations include constructors without a path: a future path must not silently escape the census.
"""
import ast


LIMITS = (RecursionError, MemoryError)
LEDGER_READS = {'entries', 'head', 'verify', 'reload', 'audit', 'append', 'get', 'entry'}


def census(source):
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    functions = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]

    def owner(node):
        while node in parents:
            node = parents[node]
            if node in functions:
                return node
        return None

    def qualified(fn):
        names = [fn.name]
        node = fn
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                names.append(node.name)
        return '.'.join(reversed(names))

    names = {qualified(fn): fn for fn in functions}
    json_modules, json_functions, ledger_names = {'json'}, set(), {'ChainLedger'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            json_modules.update(a.asname or a.name for a in node.names if a.name == 'json')
        if isinstance(node, ast.ImportFrom):
            if node.module == 'json':
                json_functions.update(a.asname or a.name for a in node.names if a.name in {'load', 'loads'})
            if node.module == 'g6.core.ledger':
                ledger_names.update(a.asname or a.name for a in node.names if a.name == 'ChainLedger')

    ledger_receivers = set()
    ledger_factories = {qualified(fn) for fn in functions if fn.returns is not None and any(
        isinstance(n, ast.Name) and n.id in ledger_names for n in ast.walk(fn.returns))}
    for node in ast.walk(tree):
        if isinstance(node, ast.arg) and node.annotation is not None and any(
                isinstance(n, ast.Name) and n.id in ledger_names for n in ast.walk(node.annotation)):
            ledger_receivers.add(node.arg)
    # Track constructor results through aliases, including attributes on a wrapper instance.
    while True:
        before = set(ledger_receivers)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                value = ast.unparse(node.value)
                constructor = isinstance(node.value, ast.Call) and (
                    ast.unparse(node.value.func) in ledger_names | ledger_factories or
                    value.split('(')[0].endswith('.ChainLedger'))
                if constructor or value in ledger_receivers:
                    for target in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                        ledger_receivers.add(ast.unparse(target))
        if ledger_receivers == before:
            break
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    seeds, links = {}, {}
    for call in calls:
        target = ast.unparse(call.func)
        attr = call.func.attr if isinstance(call.func, ast.Attribute) else ''
        is_json = target in json_functions or any(target == f'{mod}.{op}'
                   for mod in json_modules for op in ('load', 'loads'))
        is_ledger = target in ledger_names or target.endswith('.ChainLedger') or (attr in LEDGER_READS and
                    (ast.unparse(call.func.value) in ledger_receivers or
                     ast.unparse(call.func.value).split('.')[-1] in {'ledger', '_ledger', '_chain', 'adoptions'}))
        if is_json or is_ledger:
            seeds[call] = 'ledger' if is_ledger else 'json'
        possible = set()
        if isinstance(call.func, ast.Name):
            possible.update(f for name, f in names.items() if name == target or name.endswith('.' + target))
            possible.update(f for name, f in names.items() if name == target + '.__init__')
        elif isinstance(call.func, ast.Attribute):
            possible.update(f for name, f in names.items() if name == target or
                            target.startswith(('self.', 'cls.')) and name.endswith('.' + attr))
        if possible:
            links[call] = possible

    def protected(call):
        node = call
        while node in parents:
            parent = parents[node]
            if isinstance(parent, ast.Try) and node in parent.body:
                caught = set()
                for failure in LIMITS:
                    for handler in parent.handlers:
                        classes = [BaseException] if handler.type is None else [
                            {'RecursionError': RecursionError, 'RuntimeError': RuntimeError,
                             'MemoryError': MemoryError, 'Exception': Exception,
                             'BaseException': BaseException}.get(name.id)
                            for name in ast.walk(handler.type) if isinstance(name, ast.Name)]
                        if any(cls is not None and issubclass(failure, cls) for cls in classes):
                            rethrows = any(isinstance(n, ast.Raise) and (n.exc is None or
                                isinstance(n.exc, ast.Name) and n.exc.id == handler.name)
                                for body in handler.body for n in ast.walk(body))
                            if not rethrows:
                                caught.add(failure)
                            break
                if set(LIMITS) <= caught:
                    return True
            if parent is owner(call):
                break
            node = parent
        return False

    relevant = set(seeds)
    wrapped = {owner(call) for call in relevant}
    while True:
        before = set(wrapped)
        for call, targets in links.items():
            if targets & wrapped:
                relevant.add(call)
                wrapped.add(owner(call))
        if wrapped == before:
            break
    unsafe = {owner(call) for call in seeds if not protected(call)}
    while True:
        before = set(unsafe)
        for call, targets in links.items():
            if targets & unsafe and not protected(call):
                unsafe.add(owner(call))
        if unsafe == before:
            break
    return [(qualified(owner(call)) if owner(call) is not None else '<module>', call.lineno,
             ast.unparse(call.func), protected(call) or call not in seeds and not links[call] & unsafe)
            for call in sorted(relevant, key=lambda c: c.lineno)]
