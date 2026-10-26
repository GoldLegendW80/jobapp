# Updating pypdfium2

How to take a PDFium security release of `pypdfium2`.

`pypdfium2` (the `ingestion` dependency group of `api/pyproject.toml`) bundles PDFium itself, the library that renders scanned pages inside the parser's sandbox. After a PDFium security release, take the `pypdfium2` release that carries it: change the pin, run `uv lock`, check that the lock holds a wheel for `manylinux_2_17_aarch64`, rebuild the worker image and run `pytest -m docker tests/ingestion/parser/test_image_container.py`, which measures the child again against its limits.
