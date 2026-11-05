orchdoc_parts/ holds the parts of orchdoc.py.

Each <name>.py here is a block of lines moved out of orchdoc.py unchanged. orchdoc.py runs it in
its own module namespace with a line `_include("<name>")` at the place the lines had; see
`_include` in orchdoc.py. A part is not importable on its own, because the names it uses are
defined in orchdoc.py.

A copy of orchdoc.py is complete only with this folder beside it. tool_drift.py and orchdoc.py's
own _refuse_if_tool_is_stale compare the *.py files in this folder.
