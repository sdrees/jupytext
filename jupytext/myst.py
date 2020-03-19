"""
This module contains round-trip conversion between
myst formatted text documents and notebooks.
"""
import json

import nbformat as nbf
import yaml

MYST_FORMAT_NAME = "myst"
CODE_DIRECTIVE = "code-cell"
RAW_DIRECTIVE = "raw-cell"


def is_myst_available():
    """Whether the myst-parser package is available."""
    try:
        import myst_parser  # noqa
    except ImportError:
        return False
    return True


def myst_version():
    """The major version of myst parser."""
    from myst_parser import __version__

    return ".".join(__version__.split(".")[:2])


def myst_extensions(no_md=False):
    """The allowed extensions for the myst format."""
    if no_md:
        return [".myst", ".mystnb", ".mnb"]
    return [".md", ".myst", ".mystnb", ".mnb"]


def matches_mystnb(
    text,
    ext=None,
    requires_meta=True,
    require_non_md=True,
    return_nb=False,
    store_line_numbers=False,
):
    """Attempt to distinguish a file as mystnb, only given its extension and content.

    :param ext: the extension of the file
    :param requires_meta: requires the file to contain top matter metadata
    :param require_non_md: whether to require that a non-markdown cell is present
    :param return_nb: if a match is found, return the notebook
    :param store_line_numbers: add a `_source_lines` key to cell metadata,
        mapping to the source text.
    """
    if ext and "." + ("." + ext).rsplit(".", 1)[1] in myst_extensions(no_md=True):
        return (
            myst_to_notebook(
                text, ignore_bad_meta=True, store_line_numbers=store_line_numbers
            )
            if return_nb
            else True
        )
    if requires_meta and not text.startswith("---"):
        return False
    try:
        nb = myst_to_notebook(
            text, ignore_bad_meta=True, store_line_numbers=store_line_numbers
        )
    except Exception:
        return False

    from jupytext.formats import format_name_for_ext

    # Is the format information available in the jupytext text representation?
    try:
        format_name = format_name_for_ext(nb.metadata, ext or ".md")
    except AttributeError:
        pass
    else:
        if format_name == MYST_FORMAT_NAME:
            return nb if return_nb else True

    if require_non_md and not any(c.cell_type != "markdown" for c in nb.cells):
        return False

    return nb if return_nb else True


class CompactDumper(yaml.SafeDumper):
    """This YAML dumper creates a more compact style for lists"""


def represent_list(self, data):
    flow_style = not any(isinstance(i, dict) for i in data)
    return self.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=flow_style)


def represent_dict(self, data):
    return self.represent_mapping("tag:yaml.org,2002:map", data, flow_style=False)


CompactDumper.add_representer(list, represent_list)
CompactDumper.add_representer(dict, represent_dict)


def dump_yaml_blocks(data, compact=True):
    """Where possible, we try to use a more compact metadata style.

    For blocks with no nested dicts, the block is denoted by starting colons::

        :other: true
        :tags: [hide-output, show-input]

    For blocks with nesting the block is enlosed by ``---``::

        ---
        other:
            more: true
        tags: [hide-output, show-input]
        ---
    """
    string = yaml.dump(data, Dumper=CompactDumper)
    lines = string.splitlines()
    if compact and all(l and l[0].isalpha() for l in lines):
        return "\n".join([":{}".format(l) for l in lines]) + "\n\n"
    return "---\n{}---\n".format(string)


def from_nbnode(value):
    """Recursively convert NotebookNode to dict."""
    if isinstance(value, nbf.NotebookNode):
        return {k: from_nbnode(v) for k, v in value.items()}
    return value


class MockDirective:
    option_spec = {"options": True}
    required_arguments = 0
    optional_arguments = 1
    has_content = True


class MystMetadataParsingError(Exception):
    """Error when parsing metadata from myst formatted text"""


def _fmt_md(text):
    text = text.rstrip()
    while text and text.startswith("\n"):
        text = text[1:]
    return text


def myst_to_notebook(
    text,
    code_directive=CODE_DIRECTIVE,
    raw_directive=RAW_DIRECTIVE,
    ignore_bad_meta=False,
    store_line_numbers=False,
):
    """Convert text written in the myst format to a notebook.

    :param text: the file text
    :param code_directive: the name of the directive to search for containing code cells
    :param raw_directive: the name of the directive to search for containing raw cells
    :param ignore_bad_meta: ignore metadata that cannot be parsed as JSON/YAML
    :param store_line_numbers: add a `_source_lines` key to cell metadata,
        mapping to the source text.

    NOTE: we assume here that all of these directives are at the top-level,
    i.e. not nested in other directives.
    """
    from mistletoe.base_elements import SourceLines
    from mistletoe.parse_context import (
        ParseContext,
        get_parse_context,
        set_parse_context,
    )
    from mistletoe.block_tokens import Document, CodeFence

    from myst_parser.block_tokens import BlockBreak
    from myst_parser.parse_directives import DirectiveParsingError, parse_directive_text
    from myst_parser.docutils_renderer import DocutilsRenderer

    code_directive = "{{{0}}}".format(code_directive)
    raw_directive = "{{{0}}}".format(raw_directive)

    original_context = get_parse_context()
    parse_context = ParseContext(
        find_blocks=DocutilsRenderer.default_block_tokens,
        find_spans=DocutilsRenderer.default_span_tokens,
    )

    if isinstance(text, SourceLines):
        lines = text
    else:
        lines = SourceLines(text, standardize_ends=True)

    try:
        set_parse_context(parse_context)
        doc = Document.read(lines, front_matter=True)
        metadata_nb = {}
        try:
            metadata_nb = doc.front_matter.get_data() if doc.front_matter else {}
        except (yaml.parser.ParserError, yaml.scanner.ScannerError) as error:
            if not ignore_bad_meta:
                raise MystMetadataParsingError("Notebook metadata: {}".format(error))
        nbf_version = nbf.v4
        kwargs = {"metadata": nbf.from_dict(metadata_nb)}
        notebook = nbf_version.new_notebook(**kwargs)

        current_line = 0 if not doc.front_matter else doc.front_matter.position.line_end
        md_metadata = {}

        for item in doc.walk(["CodeFence", "BlockBreak"]):
            if isinstance(item.node, BlockBreak):
                token = item.node  # type: BlockBreak
                source = _fmt_md(
                    "".join(lines.lines[current_line:token.position.line_start - 1])
                )
                if source:
                    md_metadata = nbf.from_dict(md_metadata)
                    if store_line_numbers:
                        md_metadata["_source_lines"] = [
                            current_line,
                            token.position.line_start - 1,
                        ]
                    notebook.cells.append(
                        nbf_version.new_markdown_cell(
                            source=source, metadata=md_metadata,
                        )
                    )
                if token.content:
                    md_metadata = {}
                    try:
                        md_metadata = json.loads(token.content.strip())
                    except Exception as err:
                        if not ignore_bad_meta:
                            raise MystMetadataParsingError(
                                "markdown cell {0} at {1} could not be read: {2}".format(
                                    len(notebook.cells) + 1, token.position, err
                                )
                            )
                    if not isinstance(md_metadata, dict):
                        if not ignore_bad_meta:
                            raise MystMetadataParsingError(
                                "markdown cell {0} at {1} is not a dict".format(
                                    len(notebook.cells) + 1, token.position
                                )
                            )
                else:
                    md_metadata = {}
                current_line = token.position.line_start
            if isinstance(item.node, CodeFence) and item.node.language in [
                code_directive,
                raw_directive,
            ]:
                token = item.node  # type: CodeFence
                # Note: we ignore anything after the directive on the first line
                # this is reserved for the optional lexer name
                # TODO: could log warning about if token.arguments != lexer name

                options, body_lines = {}, []
                try:
                    _, options, body_lines = parse_directive_text(
                        directive_class=MockDirective,
                        argument_str="",
                        content=token.children[0].content,
                        validate_options=False,
                    )
                except DirectiveParsingError as err:
                    if not ignore_bad_meta:
                        raise MystMetadataParsingError(
                            "Code cell {0} at {1} could not be read: {2}".format(
                                len(notebook.cells) + 1, token.position, err
                            )
                        )

                md_source = _fmt_md(
                    "".join(lines.lines[current_line:token.position.line_start - 1])
                )
                if md_source:
                    md_metadata = nbf.from_dict(md_metadata)
                    if store_line_numbers:
                        md_metadata["_source_lines"] = [
                            current_line,
                            token.position.line_start - 1,
                        ]
                    notebook.cells.append(
                        nbf_version.new_markdown_cell(
                            source=md_source, metadata=md_metadata,
                        )
                    )
                current_line = token.position.line_end
                md_metadata = {}

                cell_metadata = nbf.from_dict(options)
                if store_line_numbers:
                    cell_metadata["_source_lines"] = [
                        token.position.line_start,
                        token.position.line_end,
                    ]
                if item.node.language == code_directive:
                    notebook.cells.append(
                        nbf_version.new_code_cell(
                            source="\n".join(body_lines), metadata=cell_metadata,
                        )
                    )
                if item.node.language == raw_directive:
                    notebook.cells.append(
                        nbf_version.new_raw_cell(
                            source="\n".join(body_lines), metadata=cell_metadata,
                        )
                    )

        # add the final markdown cell (if present)
        if lines.lines[current_line:]:
            md_metadata = nbf.from_dict(md_metadata)
            if store_line_numbers:
                md_metadata["_source_lines"] = [current_line, len(lines.lines)]
            notebook.cells.append(
                nbf_version.new_markdown_cell(
                    source=_fmt_md("".join(lines.lines[current_line:])),
                    metadata=md_metadata,
                )
            )

    finally:
        set_parse_context(original_context)

    return notebook


def notebook_to_myst(
    nb, code_directive=CODE_DIRECTIVE, raw_directive=RAW_DIRECTIVE, default_lexer=None,
):
    """Parse a notebook to a MyST formatted text document.

    :param nb: the notebook to parse
    :param code_directive: the name of the directive to use for code cells
    :param raw_directive: the name of the directive to use for raw cells
    :param default_lexer: a lexer name to use for annotating code cells
        (if ``nb.metadata.language_info.pygments_lexer`` is not available)
    """
    string = ""

    nb_metadata = from_nbnode(nb.metadata)

    # we add the pygments lexer as a directive argument, for use by syntax highlighters
    pygments_lexer = nb_metadata.get("language_info", {}).get("pygments_lexer", None)
    if pygments_lexer is None:
        pygments_lexer = default_lexer

    if nb_metadata:
        string += dump_yaml_blocks(nb_metadata, compact=False)

    last_cell_md = False
    for i, cell in enumerate(nb.cells):

        if cell.cell_type == "markdown":
            metadata = from_nbnode(cell.metadata)
            if metadata or last_cell_md:
                if metadata:
                    string += "\n+++ {}\n".format(json.dumps(metadata))
                else:
                    string += "\n+++\n"
            string += "\n" + cell.source
            if not cell.source.endswith("\n"):
                string += "\n"
            last_cell_md = True

        elif cell.cell_type in ["code", "raw"]:
            string += "\n```{{{}}}".format(
                code_directive if cell.cell_type == "code" else raw_directive
            )
            if pygments_lexer and cell.cell_type == "code":
                string += " {}".format(pygments_lexer)
            string += "\n"
            metadata = from_nbnode(cell.metadata)
            if metadata:
                string += dump_yaml_blocks(metadata)
            elif cell.source.startswith("---") or cell.source.startswith(":"):
                string += "\n"
            string += cell.source
            if not cell.source.endswith("\n"):
                string += "\n"
            string += "```\n"
            last_cell_md = False

        else:
            raise NotImplementedError("cell {}, type: {}".format(i, cell.cell_type))

    return string.rstrip() + "\n"
