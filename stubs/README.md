# Type stubs

Hand-written type stubs for third-party packages that ship no type
information, so `mypy --strict` checks our calls instead of treating the
library as `Any`. They declare only the parts this project uses; extend them
when you call something new. Configured by `mypy_path = "stubs"` in
`pyproject.toml`.
