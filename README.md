# RRA Population Covariates

[![PyPI](https://img.shields.io/pypi/v/rra-population-covariates?style=flat-square)](https://pypi.python.org/pypi/rra-population-covariates/)
[![PyPI - Python Version](https://img.shields.io/pypi/pyversions/rra-population-covariates?style=flat-square)](https://pypi.python.org/pypi/rra-population-covariates/)
[![PyPI - License](https://img.shields.io/pypi/l/rra-population-covariates?style=flat-square)](https://pypi.python.org/pypi/rra-population-covariates/)

---

**Documentation**: [https://MFStark.github.io/rra-population-covariates](https://MFStark.github.io/rra-population-covariates)

**Source Code**: [https://github.com/MFStark/rra-population-covariates](https://github.com/MFStark/rra-population-covariates)

**PyPI**: [https://pypi.org/project/rra-population-covariates/](https://pypi.org/project/rra-population-covariates/)

---

Proccessing pipeline for population model covariates

## Installation

```sh
pip install rra-population-covariates
```

## Development

* Clone this repository
* Requirements:
  * [Poetry](https://python-poetry.org/)
  * Python 3.10+
* Create a virtual environment and install the dependencies

```sh
poetry install
```

* Activate the virtual environment

```sh
poetry shell
```

### Testing

```sh
pytest
```

### Documentation

The documentation is automatically generated from the content of the `docs` directory and from the docstrings
 of the public signatures of the source code. The documentation is updated and published as a [Github project page
 ](https://pages.github.com/) automatically as part each release.

### Releasing

Trigger the [Draft release workflow](https://github.com/MFStark/rra-population-covariates/actions/workflows/draft_release.yml)
(press _Run workflow_). This will update the changelog & version and create a GitHub release which is in _Draft_ state.

Find the draft release from the
[GitHub releases](https://github.com/MFStark/rra-population-covariates/releases) and publish it. When
 a release is published, it'll trigger [release](https://github.com/MFStark/rra-population-covariates/blob/master/.github/workflows/release.yml) workflow which creates PyPI
 release and deploys updated documentation.

### Pre-commit

Pre-commit hooks run all the auto-formatting (`ruff format`), linters (e.g. `ruff` and `mypy`), and other quality
 checks to make sure the changeset is in good shape before a commit/push happens.

You can install the hooks with (runs for each commit):

```sh
pre-commit install
```

Or if you want them to run only for each push:

```sh
pre-commit install -t pre-push
```

Or if you want e.g. want to run all checks manually for all files:

```sh
pre-commit run --all-files
```

---
