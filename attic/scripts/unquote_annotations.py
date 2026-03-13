#!/usr/bin/env python3
"""
Remove quotes from forward references in type annotations.

Python 3.14 (PEP 649) evaluates annotations lazily, so forward references
no longer need to be quoted.

Generated with Claude Code (https://claude.ai/claude-code)
"""
import ast
from pathlib import Path


def collect_annotation_strings(source: str) -> list[ast.Constant]:
    tree = ast.parse(source)
    results = []

    def visit_annotation(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            # Verify the string is a valid Python expression (a type expression)
            try:
                ast.parse(node.value, mode='eval')
            except SyntaxError:
                return
            results.append(node)
        elif isinstance(node, ast.Subscript):
            value = node.value
            is_literal = (
                (isinstance(value, ast.Name) and value.id == 'Literal')
                or (isinstance(value, ast.Attribute) and value.attr == 'Literal')
            )
            is_annotated = (
                (isinstance(value, ast.Name) and value.id == 'Annotated')
                or (isinstance(value, ast.Attribute) and value.attr == 'Annotated')
            )
            visit_annotation(value)
            if not is_literal:
                if is_annotated:
                    # Only recurse into the first element (the type); skip metadata
                    slice_node = node.slice
                    if isinstance(slice_node, ast.Tuple) and slice_node.elts:
                        visit_annotation(slice_node.elts[0])
                else:
                    visit_annotation(node.slice)
        elif isinstance(node, ast.BinOp):
            visit_annotation(node.left)
            visit_annotation(node.right)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                visit_annotation(elt)
        # ast.Name, ast.Attribute, ast.IfExp, etc. — no strings inside

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                visit_annotation(node.returns)
            all_args = (
                node.args.posonlyargs
                + node.args.args
                + node.args.kwonlyargs
                + ([node.args.vararg] if node.args.vararg else [])
                + ([node.args.kwarg] if node.args.kwarg else [])
            )
            for arg in all_args:
                if arg.annotation is not None:
                    visit_annotation(arg.annotation)
        elif isinstance(node, ast.AnnAssign):
            visit_annotation(node.annotation)

    return results


def unquote_annotations(source: str) -> str:
    try:
        constants = collect_annotation_strings(source)
    except SyntaxError:
        return source

    if not constants:
        return source

    lines = source.splitlines(keepends=True)

    # Sort descending so later replacements don't shift earlier positions
    for node in sorted(
        constants,
        key=lambda n: (n.end_lineno, n.end_col_offset),
        reverse=True,
    ):
        line_idx = node.lineno - 1
        end_line_idx = node.end_lineno - 1

        if line_idx != end_line_idx:
            # Multi-line string annotations are too unusual to handle safely
            continue

        line = lines[line_idx]
        quoted = line[node.col_offset:node.end_col_offset]

        # Skip triple-quoted strings
        if quoted.startswith(('"""', "'''")):
            continue

        # Confirm it really is a single-character-quoted string
        if not quoted.startswith(('"', "'")):
            continue

        lines[line_idx] = (
            line[:node.col_offset] + node.value + line[node.end_col_offset:]
        )

    return ''.join(lines)


def process_file(path: Path, dry_run: bool = False) -> bool:
    source = path.read_text()
    new_source = unquote_annotations(source)
    if new_source != source:
        if not dry_run:
            path.write_text(new_source)
        return True
    return False


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', nargs='?', default='.', help='Root directory')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    root = Path(args.root)
    skip_dirs = {'.venv', '__pycache__', '.git', '.mypy_cache', 'node_modules'}

    modified = []
    for py_file in sorted(root.rglob('*.py')):
        if any(part in skip_dirs for part in py_file.parts):
            continue
        if process_file(py_file, dry_run=args.dry_run):
            modified.append(py_file)
            print(f'{"Would modify" if args.dry_run else "Modified"}: {py_file}')

    print(f'\nTotal: {len(modified)} files')
