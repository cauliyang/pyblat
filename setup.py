from __future__ import annotations
from setuptools import setup

import os
import shlex
import sys
import typing
import subprocess
from contextlib import contextmanager
from ctypes.util import find_library
from functools import wraps
from pathlib import Path

import distutils
import setuptools
from pybind11.setup_helpers import auto_cpp_level
from pybind11.setup_helpers import ParallelCompile
from pybind11.setup_helpers import Pybind11Extension
from setuptools import Distribution
from setuptools import Extension
from setuptools.command.build_ext import build_ext as _build_ext


DEBUG = False

class PxblatExtensionBuilder(_build_ext):
    def build_extension(self, extension: setuptools.extension.Extension) -> None:  # type: ignore
        extension.library_dirs.append(self.build_lib)  # type: ignore
        super().build_extension(extension)

    def build_extensions(self) -> None:
        """
        Build extensions, injecting C++ std for Pybind11Extension if needed.
        """

        for ext in self.extensions:
            if hasattr(ext, "_cxx_level") and ext._cxx_level == 0:
                ext.cxx_std = auto_cpp_level(self.compiler)

        super().build_extensions()


def _get_pxblat_libname():
    builder = setuptools.command.build_ext.build_ext(Distribution())  # type: ignore
    full_name = builder.get_ext_filename("libpxblat")
    without_lib = full_name.split("lib", 1)[-1]
    without_so = without_lib.rsplit(".so", 1)[0]
    return without_so


def remove_env(key: str):
    """Remove environment variable."""
    env_cflags = os.environ.get("CFLAGS", "")
    env_cppflags = os.environ.get("CPPFLAGS", "")
    flags = shlex.split(env_cflags) + shlex.split(env_cppflags)

    for flag in flags:
        if flag.startswith(key):
            raise RuntimeError(f"Please remove {key} from CFLAGS and CPPFLAGS.")


@contextmanager
def change_dir(path: str):
    """Change directory."""
    save_dir = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(save_dir)


def change_env(key: str, value: str):
    """Change environment variable."""

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            old_env = os.environ.get(key, None)
            os.environ[key] = old_env + " " + value if old_env else value
            func(*args, **kwargs)
            os.environ[key] = old_env if old_env else " "

        return wrapper

    return decorator


def get_files_by_suffix(
    path: typing.Union[Path, str], suffix: typing.List[str]
) -> typing.Iterator[str]:
    """Get bindings."""
    if isinstance(path, str):
        path = Path(path)

    for file in path.iterdir():
        if file.is_dir():
            yield from get_files_by_suffix(file, suffix)
        if file.suffix in suffix:
            yield file.as_posix()


def filter_files(files, exclude=None):
    if exclude is None:
        exclude = []

    for file in files:
        file_name = Path(file).name
        if file_name not in exclude:
            yield file


# Optional multithreaded build
def get_thread_count():
    try:
        import multiprocessing

        return multiprocessing.cpu_count()
    except (ImportError, NotImplementedError):
        pass
    return 1


def _get_cxx_compiler():
    cc = distutils.ccompiler.new_compiler()  # type: ignore
    distutils.sysconfig.customize_compiler(cc)  # type: ignore
    return cc.compiler_cxx[0]  # type: ignore


def find_lib_in_conda(lib_name: str):
    conda_prefix = os.environ.get("CONDA_PREFIX", None)
    if conda_prefix is not None:
        conda_lib_dir = Path(conda_prefix) / "lib"

        if (conda_lib_dir / f"lib{lib_name}.a").exists():
            return conda_lib_dir

        if (conda_lib_dir / f"lib{lib_name}.so").exists():
            return conda_lib_dir

        if (conda_lib_dir / f"lib{lib_name}.dylib").exists():
            return conda_lib_dir

    return None


def find_available_library(lib_name: str, *, ignores=[]):
    lib_path = find_library(lib_name)

    if lib_path is None:
        lib_path = find_lib_in_conda(lib_name)

    print(f"{lib_name} lib_path: {lib_path}")

    if not lib_path:
        if lib_name not in ignores:
            raise RuntimeError(f"Cannot find {lib_name} library.")
        return Path.cwd(), Path.cwd()

    header_path = Path(lib_path).parent.parent / "include"

    return Path(lib_path).parent, header_path


def find_openssl_libs():
    openssl_dir = subprocess.getoutput('openssl version -d')
    openssl_dir = openssl_dir.replace('OPENSSLDIR: "', '').replace('"', '').strip()

    lib_paths = [f"{openssl_dir}/lib"]

    print(f"find openssl lib_paths: {lib_paths}")

    return lib_paths

def _extra_compile_args_for_libpxblat():
    return [
        "-D_FILE_OFFSET_BITS=64",
        "-D_LARGEFILE_SOURCE",
        "-D_GNU_SOURCE",
        "-DMACHTYPE_$(MACHTYPE)",
        "-DPXBLATLIB",
    ]


def _include_dirs_for_libpxblat():
    return [
        "src/pxblat/extc/include/core",
        "src/pxblat/extc/include/aux",
        "src/pxblat/extc/include/net",
    ]


def _include_dirs_for_pxblat():
    return [
        "src/pxblat/extc/bindings",
    ]


def _extra_compile_args_for_pxblat():
    flag = []
    if not DEBUG:
        flag.append("-DDBG_MACRO_DISABLE")
    return flag


ParallelCompile(f"{get_thread_count()}").install()

extra_compile_args = ["-pthread"]
hidden_visibility_args = []
include_dirs: list[str] = []
library_dirs: list[str] = [] + find_openssl_libs()
python_module_link_args = []
base_library_link_args: list[str] = []
external_libraries = [
    "ssl",
    "crypto",
    "m",
]

for lib in external_libraries:
    lib_library_dir, lib_include_dir = find_available_library(lib, ignores=["m"])
    library_dirs.append(lib_library_dir.as_posix())
    include_dirs.append(lib_include_dir.as_posix())

if sys.platform == "win32":
    raise RuntimeError("Windows is not supported.")
elif sys.platform == "darwin":
    # See https://conda-forge.org/docs/maintainer/knowledge_base.html#newer-c-features-with-old-sdk
    extra_compile_args.append("-D_LIBCPP_DISABLE_AVAILABILITY")
    extra_compile_args.append("-undefined dynamic_lookup")
    hidden_visibility_args.append("-fvisibility=hidden")
    config_vars = distutils.sysconfig.get_config_vars()  # type: ignore
    config_vars["LDSHARED"] = config_vars["LDSHARED"].replace("-bundle", "")  # type: ignore
    python_module_link_args.append("-bundle")
    builder = setuptools.command.build_ext.build_ext(Distribution())  # type: ignore
    full_name = builder.get_ext_filename("libpxblat")
    print(f"full_name: {full_name}")
    base_library_link_args.append(
        f"-Wl,-dylib_install_name,@loader_path/../{full_name}"
    )
    base_library_link_args.append("-dynamiclib")
else:
    hidden_visibility_args.append("-fvisibility=hidden")
    python_module_link_args.append("-Wl,-rpath,$ORIGIN/..")


def get_extension_modules():
    extension_modules = []

    """
    Extension module which is actually a plain C++ library without Python bindings
    """
    libpxblat_sources = (
        list(filter_files(get_files_by_suffix("src/pxblat/extc/src/core", [".c"])))
        + list(
            filter_files(
                get_files_by_suffix("src/pxblat/extc/src/aux", [".c"]),
                exclude=["net.c"],
            )
        )
        + list(filter_files(get_files_by_suffix("src/pxblat/extc/src/net", [".c"])))
    )

    pxblat_library = Extension(
        "libpxblat",
        language="c",
        sources=libpxblat_sources,
        include_dirs=include_dirs + _include_dirs_for_libpxblat(),
        extra_compile_args=_extra_compile_args_for_libpxblat() + extra_compile_args,
        extra_link_args=base_library_link_args,
        libraries=external_libraries,
        library_dirs=library_dirs,
    )

    pxblat_libs = [_get_pxblat_libname()]
    extension_modules.append(pxblat_library)

    """
    An extension module which contains the main Python bindings for libblat
    """
    pxblat_python_sources = [
        "src/pxblat/extc/bindings/faToTwoBit.cpp",
        "src/pxblat/extc/bindings/twoBitToFa.cpp",
        "src/pxblat/extc/bindings/gfServer.cpp",
        "src/pxblat/extc/bindings/pygfServer.cpp",
        "src/pxblat/extc/bindings/gfClient.cpp",
    ] + list(
        filter_files(get_files_by_suffix("src/pxblat/extc/bindings/binder", [".cpp"]))
    )

    pxblat_python = Pybind11Extension(
        "pxblat._extc",
        language="c++",
        sources=pxblat_python_sources,
        include_dirs=include_dirs
        + _include_dirs_for_libpxblat()
        + _include_dirs_for_pxblat(),
        extra_compile_args=extra_compile_args
        + hidden_visibility_args
        + _extra_compile_args_for_pxblat(),
        libraries=external_libraries + pxblat_libs,
        extra_link_args=python_module_link_args,
        library_dirs=library_dirs,
    )

    extension_modules.append(pxblat_python)
    return extension_modules


def build(setup_kwargs):
    """Build cpp extension."""
    ext_modules = get_extension_modules()
    setup_kwargs.update(
        {
            "ext_modules": ext_modules,
            "cmdclass": {"build_ext": PxblatExtensionBuilder},
            "zip_safe": False,
            "package_data": {"pxblat": ["py.typed", "*so"]},
        }
    )
package_dir = {"": "src"}

packages = ["pxblat", "pxblat.cli", "pxblat.extc", "pxblat.server", "pxblat.toolkit"]

package_data = {
    "": ["*"],
    "pxblat.extc": [
        "bindings/*",
        "bindings/binder/*",
        "include/*",
        "include/aux/*",
        "include/core/*",
        "include/net/*",
        "src/*",
        "src/aux/*",
        "src/core/*",
        "src/net/*",
    ],
}

install_requires = [
    "biopython>=1.81,<2.0",
    "deprecated>=1.2.13,<2.0.0",
    "loguru>=0.7.0,<0.8.0",
    "mashumaro>=3.7,<4.0",
    "pybind11>=2.10.4,<3.0.0",
    "pysimdjson>=5.0.2,<6.0.0",
    "rich>=13.3.5,<14.0.0",
    "setuptools>=68.2.2,<69.0.0",
    "typer>=0.9.0,<0.10.0",
    "urllib3==2.0.7",
]

entry_points = {"console_scripts": ["pxblat = pxblat.cli.cli:app"]}

setup_kwargs = {
    "name": "pxblat",
    "version": "1.1.8",
    "description": "A native python binding for blat suite",
    "long_description": '# <img src="https://raw.githubusercontent.com/cauliyang/pxblat/main/docs/_static/logo.png" alt="logo" height=100> **PxBLAT** [![social](https://img.shields.io/github/stars/cauliyang/pxblat?style=social)](https://github.com/cauliyang/pxblat/stargazers)\n\n_An Efficient and Ergonomic Python Binding Library for BLAT_\n\n[![python](https://img.shields.io/badge/Python-3776AB.svg?style=for-the-badge&logo=Python&logoColor=white)](https://www.python.org/)\n[![c++](https://img.shields.io/badge/C++-00599C.svg?style=for-the-badge&logo=C++&logoColor=white)](https://en.cppreference.com/w/)\n[![c](https://img.shields.io/badge/C-A8B9CC.svg?style=for-the-badge&logo=C&logoColor=black)](https://www.gnu.org/software/gnu-c-manual/)\n[![pypi](https://img.shields.io/pypi/v/pxblat.svg?style=for-the-badge)][pypi]\n[![conda](https://img.shields.io/conda/vn/bioconda/pxblat?style=for-the-badge)][conda]\n![Linux](https://img.shields.io/badge/-Linux-grey?logo=linux&style=for-the-badge)\n![macOS](https://img.shields.io/badge/-OSX-black?logo=apple&style=for-the-badge)\n[![pyversion](https://img.shields.io/pypi/pyversions/pxblat?style=for-the-badge)][pypi]\n[![tests](https://img.shields.io/github/actions/workflow/status/cauliyang/pxblat/tests.yml?style=for-the-badge&logo=github&label=Tests)](https://github.com/cauliyang/pxblat/actions/workflows/tests.yml)\n[![Codecov](https://img.shields.io/codecov/c/github/cauliyang/pxblat/main?style=for-the-badge)](https://app.codecov.io/gh/cauliyang/pxblat)\n[![docs](https://img.shields.io/readthedocs/pxblat?style=for-the-badge)](https://pxblat.readthedocs.io/en/latest/)\n[![download](https://img.shields.io/pypi/dm/pxblat?logo=pypi&label=pypi%20download&style=for-the-badge)][pypi]\n[![condadownload](https://img.shields.io/conda/dn/bioconda/pxblat?style=for-the-badge&logo=anaconda&label=Conda%20Download)][conda]\n[![precommit](https://img.shields.io/badge/pre--commit-enabled-brightgreen?style=for-the-badge&logo=pre-commit&logoColor=white)](https://github.com/pre-commit/pre-commit)\n[![ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg?style=for-the-badge)](https://github.com/charliermarsh/ruff)\n[![release](https://img.shields.io/github/release-date/cauliyang/pxblat?style=for-the-badge)](https://github.com/cauliyang/pxblat/releases)\n[![open-issue](https://img.shields.io/github/issues-raw/cauliyang/pxblat?style=for-the-badge)][open-issue]\n[![close-issue](https://img.shields.io/github/issues-closed-raw/cauliyang/pxblat?style=for-the-badge)][close-issue]\n[![activity](https://img.shields.io/github/commit-activity/m/cauliyang/pxblat?style=for-the-badge)][repo]\n[![lastcommit](https://img.shields.io/github/last-commit/cauliyang/pxblat?style=for-the-badge)][repo]\n[![opull](https://img.shields.io/github/issues-pr-raw/cauliyang/pxblat?style=for-the-badge)][opull]\n[![all contributors](https://img.shields.io/github/all-contributors/cauliyang/pxblat?style=for-the-badge)](#contributors)\n\n<!-- [![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)][colab] -->\n\n[repo]: https://github.com/ylab-hi/pxblat\n[open-issue]: https://github.com/cauliyang/pxblat/issues?q=is%3Aissue+is%3Aopen+sort%3Aupdated-desc\n[close-issue]: https://github.com/cauliyang/pxblat/issues?q=is%3Aissue+sort%3Aupdated-desc+is%3Aclosed\n[opull]: https://github.com/cauliyang/pxblat/pulls?q=is%3Apr+is%3Aopen+sort%3Aupdated-desc\n[conda]: https://bioconda.github.io/recipes/pxblat/README.html\n[pypi]: https://pypi.org/project/pxblat/\n[colab]: https://colab.research.google.com/drive/1TXb9GBmYa2EYezwBKbD-y9Xg6MC2gL36\n\n## Why PxBLAT?\n\nWhen conducting extensive queries, using the `blat` of `BLAT` suite can prove to be quite inefficient, especially if these operations aren\'t grouped. The tasks are allocated sporadically, often interspersed among other tasks.\nIn general, the choice narrows down to either utilizing `blat` or combining `gfServer` with `gfClient`.\nIndeed, `blat` is a program that launches `gfServer`, conducts the sequence query via `gfClient`, and then proceeds to terminate the server.\n\nThis approach is far from ideal when performing numerous queries that aren\'t grouped since `blat` repeatedly initializes and shuts down `gfServer` for each query, resulting in substantial overhead.\nThis overhead consists of the time required for the server to index the reference, contingent on the reference\'s size.\nTo index the human genome (hg38), for example, would take approximately five minutes.\n\nA more efficient solution would involve initializing `gfServer` once and invoking `gfClient` multiple times for the queries.\nHowever, `gfServer` and `gfClient` are only accessible via the command line.\nThis necessitates managing system calls (for instance, `subprocess` or `os.system`), intermediate temporary files, and format conversion, further diminishing performance.\n\nThat is why `PxBLAT` holds its position.\nIt resolves the issues mentioned above while introducing handy features like `port retry`, `use current running server`, etc.\n\n## 📚 **Table of Contents**\n\n- [ **PxBLAT** ](#-pxblat-)\n  - [📚 **Table of Contents**](#-table-of-contents)\n  - [🔮 **Features**](#-features)\n  - [📎 **Citation**](#-citation)\n  - [🚀 **Getting Started**](#-getting-started)\n  - [🤝 **Contributing**](#-contributing)\n  - [\U0001faaa **License**](#-license)\n  - [🤗 **Contributors**](#contributors)\n  - [🙏 **Acknowledgments**](#-acknowledgments)\n\n## 🔮 **Features**\n\n- **Zero System Calls**: Avoids system calls, leading to a smoother, quicker operation.<br>\n- **Ergonomics**: With an ergonomic design, `PxBLAT` aims for a seamless user experience.<br>\n- **No External Dependencies**: `PxBLAT` operates independently without any external dependencies.<br>\n- **Self-Monitoring**: No need to trawl through log files; `PxBLAT` monitors its status internally.<br>\n- **Robust Validation**: Extensively tested to ensure reliable performance and superior stability as BLAT.<br>\n- **Format-Agnostic:** `PxBLAT` doesn\'t require you to worry about file formats.<br>\n- **In-Memory Processing**: `PxBLAT` discards the need for intermediate files by doing all its operations in memory, ensuring speed and efficiency.<br>\n\n## 📎 **Citation**\n\nPxBLAT is scientific software, with a published paper in the BioRxiv.\nCheck the [published](https://www.biorxiv.org/content/10.1101/2023.08.02.551686v2) to read the paper.\n\n```bibtex\n@article {Li2023pxblat,\n\tauthor = {Yangyang Li and Rendong Yang},\n\ttitle = {PxBLAT: An Ergonomic and Efficient Python Binding Library for BLAT},\n\telocation-id = {2023.08.02.551686},\n\tyear = {2023},\n\tdoi = {10.1101/2023.08.02.551686},\n\tpublisher = {Cold Spring Harbor Laboratory},\n\turl = {https://www.biorxiv.org/content/10.1101/2023.08.02.551686v2},\n\tjournal = {bioRxiv}\n}\n```\n\n## 🚀 **Getting Started**\n\nWelcome to PxBLAT! To kickstart your journey and get the most out of this tool, we have prepared a comprehensive [documentation](https://pxblat.readthedocs.io/en/latest/installation.html).\nInside, you’ll find detailed guides, examples, and all the necessary information to help you navigate and utilize PxBLAT effectively.\n\n### Need Help or Found an Issue?\n\nIf you encounter any issues or if something is not clear in the documentation, do not hesitate to [open an issue](https://github.com/ylab-hi/pxblat/issues/new/choose).\nWe are here to help and appreciate your feedback for improving PxBLAT.\n\n### Show Your Support\n\nIf PxBLAT has been beneficial to your projects or you appreciate the work put into it, consider leaving a ⭐️ [Star](https://github.com/ylab-hi/pxblat/stargazers) on our GitHub repository.\nYour support means the world to us and motivates us to continue enhancing PxBLAT.\n\nLet’s embark on this journey together and make the most out of PxBLAT! 🎉\nPlease see the [document](https://pxblat.readthedocs.io/en/latest/installation.html) for details and more examples.\n\n## 🤝 **Contributing**\n\nContributions are always welcome! Please follow these steps:\n\n1. Fork the project repository. This creates a copy of the project on your account that you can modify without affecting the original project.\n2. Clone the forked repository to your local machine using a Git client like Git or GitHub Desktop.\n3. Create a new branch with a descriptive name (e.g., `new-feature-branch` or `bugfix-issue-123`).\n\n```bash\ngit checkout -b new-feature-branch\n```\n\n4. Take changes to the project\'s codebase.\n5. Install the latest package\n\n```bash\npoetry install\n```\n\n6. Test your changes\n\n```bash\npytest -vlsx tests\n```\n\n7. Commit your changes to your local branch with a clear commit message that explains the changes you\'ve made.\n\n```bash\ngit commit -m \'Implemented new feature.\'\n```\n\n8. Push your changes to your forked repository on GitHub using the following command\n\n```bash\ngit push origin new-feature-branch\n```\n\nCreate a pull request to the original repository.\nOpen a new pull request to the original project repository. In the pull request, describe the changes you\'ve made and why they\'re necessary.\nThe project maintainers will review your changes and provide feedback or merge them into the main branch.\n\n## \U0001faaa **License**\n\n**PxBLAT is modified from blat, the license is the same as blat. The source code and\nexecutables are freely available for academic, nonprofit, and personal use.\nCommercial licensing information is available on the Kent Informatics website\n(https://kentinformatics.com/).**\n\n## **Contributors**\n\n<!-- ALL-CONTRIBUTORS-LIST:START - Do not remove or modify this section -->\n<!-- prettier-ignore-start -->\n<!-- markdownlint-disable -->\n<table>\n  <tbody>\n    <tr>\n      <td align="center" valign="top" width="14.28%"><a href="https://yangyangli.top"><img src="https://avatars.githubusercontent.com/u/38903141?v=4?s=100" width="100px;" alt="yangliz5"/><br /><sub><b>yangliz5</b></sub></a><br /><a href="#maintenance-cauliyang" title="Maintenance">🚧</a></td>\n      <td align="center" valign="top" width="14.28%"><a href="https://github.com/mencian"><img src="https://avatars.githubusercontent.com/u/71105179?v=4?s=100" width="100px;" alt="Joshua Zhuang"/><br /><sub><b>Joshua Zhuang</b></sub></a><br /><a href="#infra-mencian" title="Infrastructure (Hosting, Build-Tools, etc)">🚇</a></td>\n    </tr>\n  </tbody>\n</table>\n\n<!-- markdownlint-restore -->\n<!-- prettier-ignore-end -->\n\n<!-- ALL-CONTRIBUTORS-LIST:END -->\n<!-- prettier-ignore-start -->\n<!-- markdownlint-disable -->\n\n<!-- markdownlint-restore -->\n<!-- prettier-ignore-end -->\n\n<!-- ALL-CONTRIBUTORS-LIST:END -->\n\n## 🙏 **Acknowledgments**\n\n- [BLAT](http://genome.ucsc.edu/goldenPath/help/blatSpec.html)\n- [UCSC](https://github.com/ucscGenomeBrowser/kent)\n- [pybind11](https://github.com/pybind/pybind11/tree/stable)\n\n<!-- github-only -->\n\n<br>\n<picture>\n  <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=cauliyang/pxblat&type=Date&theme=light" />\n  <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=cauliyang/pxblat&type=Date" />\n  <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=cauliyang/pxblat&type=Date" />\n</picture>\n',
    "author": "Yangyang Li",
    "author_email": "yangyang.li@northwestern.edu",
    "maintainer": "None",
    "maintainer_email": "None",
    "url": "https://github.com/ylab-hi/pxblat",
    "package_dir": package_dir,
    "packages": packages,
    "package_data": package_data,
    "install_requires": install_requires,
    "entry_points": entry_points,
    "python_requires": ">=3.9,<3.12",
}


build(setup_kwargs)

setup(**setup_kwargs)
