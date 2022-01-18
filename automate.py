"""Automates Python scripts formatting, linting and Mkdocs documentation."""

import ast
import importlib
import re
from collections import defaultdict
import pathlib
import os
from pathlib import Path
from typing import Union, get_type_hints
import inspect


def automate_mkdocs_from_docstring(
    mkdocs_dir: Union[str, Path], mkgendocs_f: str, repo_dir: Path, match_string: str
) -> str:
    """Automates the -pages for mkgendocs package by adding all Python functions in a directory to the mkgendocs config.
    Args:
        mkdocs_dir (typing.Union[str, pathlib.Path]): textual directory for the hierarchical directory & navigation in Mkdocs
        mkgendocs_f (str): The configurations file for the mkgendocs package
        repo_dir (pathlib.Path): textual directory to search for Python functions in
        match_string (str): the text to be matches, after which the functions will be added in mkgendocs format
    Example:
        >>>
        >>> automate_mkdocs_from_docstring('scripts', repo_dir=Path.cwd(), match_string='pages:')
    Returns:
        str: feedback message
    """
    p = repo_dir.glob("**/*.py")
    scripts = [x for x in p if x.is_file()]

    if Path.cwd() != repo_dir:  # look for mkgendocs.yml in the parent file if a subdirectory is used
        repo_dir = repo_dir.parent

    functions = defaultdict(list)
    classes = defaultdict(list)
    for script in scripts:

        with open(script, "r") as source:
            tree = ast.parse(source.read())

        for child in ast.iter_child_nodes(tree):
            if isinstance(child, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
                if child.name not in ["main"]:

                    spec = importlib.util.spec_from_file_location(script.stem, script)
                    module = spec.loader.load_module()
                    f_ = getattr(module, child.name)
                    f_name = f_.__name__
                    if inspect.isclass(f_):
                        classes[script].append(f_name)
                    else:
                        functions[script].append(f_name)

    with open(f"{repo_dir}/{mkgendocs_f}", "r+") as mkgen_config:
        insert_string = ""
        paths = list(set(list(classes)+list(functions)))
        for path in paths:
            relative_path = pathlib.Path(repo_dir).resolve()
            insert_string += (
                f'  - page: "{os.path.relpath(path.parent, relative_path)}/{path.stem}.md"\n    '
                f'source: "{os.path.relpath(path.parent, relative_path)}/{path.stem}.py"\n'
            )
            if path in functions:
                insert_string +="    functions:\n"

                f_string = ""
                for f in functions[path]:
                    insert_f_string = f"      - {f}\n"
                    f_string += insert_f_string

                insert_string += f_string

            if path in classes:
                insert_string +="    classes:\n"

                f_string = ""
                for f in classes[path]:
                    insert_f_string = f"      - {f}\n"
                    f_string += insert_f_string

                insert_string += f_string

        contents = mkgen_config.readlines()
        if match_string in contents[-1]:
            contents.append(insert_string)
        else:

            for index, line in enumerate(contents):
                if match_string in line and insert_string not in contents[index + 1]:

                    contents = contents[: index + 1]
                    contents.append(insert_string)
                    break

    with open(f"{repo_dir}/{mkgendocs_f}", "w") as mkgen_config:
        mkgen_config.writelines(contents)

    return f"Added to {mkgendocs_f}: {tuple(functions.values())}."


def main():
    """Execute when running this script."""
    repo_dir = Path.cwd().joinpath("pytorch_widedeep")

    automate_mkdocs_from_docstring(
        mkdocs_dir="docs_mk",
        mkgendocs_f="mkgendocs.yml",
        repo_dir=repo_dir,
        match_string="pages:\n",
    )


if __name__ == "__main__":
    main()