"""Kronos accuracy backtesting against MetaTrader 5 (or CSV) data."""


def line_buffer_stdout():
    """Flush stdout on every newline, even when it is a pipe.

    Every entry point here is normally watched through `| tee run.log`, and a
    pipe makes stdout block-buffered: a healthy run can print nothing for
    hours and look hung. Line buffering costs nothing at these volumes.
    """
    import sys
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:  # pragma: no cover - very old Python
        pass
