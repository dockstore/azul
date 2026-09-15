This repository hosts just the AWS inspector script. Thanks to the main azul project which created it.


# 1. Getting Started


## 2.1 Development Prerequisites

- The `bash` shell

- git 2.36.0 or newer

- AWS credentials configured in `~/.aws/credentials` and/or `~/.aws/config`

  
## 2 Project configuration

4. Create a Python virtual environment and activate it:

   ```
   python3.12 -m venv .venv
   source .venv/bin/activate
   ```

5. Install the requirements:

   ```
   pip install -r requirements.txt
   ```

   Linux users whose distribution does not offer the required Python version
   should consider installing [pyenv] first, then Python using `pyenv install
   x.y.z` and setting `PYENV_VERSION` to `x.y.z`, where `x.y.z` is `3.12.11`.
   You may need to update [pyenv] itself before it recognizes the given Python
   version. Even if a distribution provides the required minor version of
   Python natively, using [pyenv] is generally preferred because it offers
   every patch-level release of Python, supports an arbitrary number of
   different Python versions to be installed concurrently and allows for
   easily switching between them.

   Ubuntu users using their system's default Python installation must
   install `python3-dev` before any wheel requirements can be built.

   ```
   sudo apt install python3-dev
   ```

   [pyenv]: https://github.com/pyenv/pyenv

This should be sufficient to run the script via

```
   python scripts/export_inspector_findings.py
```
