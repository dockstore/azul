This repository hosts just the AWS inspector script. Thanks to the main azul project which created it.


# 1. Getting Started


## 2.1 Development Prerequisites

- Python, the specific verson is defined in an environment variable called
  `azul_python_version` defined in [environment.py](environment.py)

- The `bash` shell

- GNU make 3.81 or newer

- git 2.36.0 or newer

- AWS credentials configured in `~/.aws/credentials` and/or `~/.aws/config`


- [jq](https://stedolan.github.io/jq/)

- The build process relies on numerous utilities that are pretty much standard 
  on any modern Unix. Things like `perl`, `sort`, `comm`, `uniq`, `sed`, `cp`, 
  `mv` and `rm`.

- For VPN support: OpenSSL (version 1.1.10 and 3.0.5 are known to work but other 
  versions should work, too). LibreSSL, which became the default on macOS at 
  some point, is an acceptible replacement. Version 2.8.3 is known to work.  

- Users of macOS 12 (Monterey) should follow additional steps outlined in 
  [Troubleshooting](#setting-up-the-azul-build-prerequisites-on-macos-12-monterey)

- Users of macOS 11 (Big Sur) should follow additional steps outlined in 
  [Troubleshooting](#installing-python-3812-on-macos-11-big-sur

  
## 2 Project configuration

Getting started without attempting to make contributions does not require AWS
credentials. A subset of the test suite passes without configured AWS
credentials. To validate your setup, we'll be running one of those tests at the
end.

1. Load the environment defaults

   ```
   source environment
   ```

2. Activate the `dev` deployment:

   ```
   _select dev
   ```

3. Load the environment:

   ```
   source environment
   ```

   The output should indicate that the environment is being loaded from the
   selected deployment (in this case, `dev`).

4. Create a Python virtual environment and activate it:

   ```
   make virtualenv
   source .venv/bin/activate
   ```

5. Install the development prerequisites:

   ```
   make requirements
   ```

   Linux users whose distribution does not offer the required Python version
   should consider installing [pyenv] first, then Python using `pyenv install
   x.y.z` and setting `PYENV_VERSION` to `x.y.z`, where `x.y.z` is the value of
   `azul_python_version` in [environment.py](environment.py). You may need to
   update [pyenv] itself before it recognizes the given Python version. Even if
   a distribution provides the required minor version of Python natively, using
   [pyenv] is generally preferred because it offers every patch-level release of
   Python, supports an arbitrary number of different Python versions to be
   installed concurrently and allows for easily switching between them.

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
