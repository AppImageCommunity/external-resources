#! /usr/bin/env python
import hashlib
import os
import parse_cmake.parsing as cmp
import requests
import rfc6266
import shutil
import subprocess
import sys

from collections import namedtuple
from dulwich import porcelain
from tempfile import TemporaryDirectory
from tqdm import tqdm
from urllib.parse import urlparse


TarballURL = namedtuple("URL", ["url", "hash"])
PatchURL = namedtuple("PatchURL", ["url"])
GitRepository = namedtuple("GitRepository", ["url", "tag"])

EXTERNALPROJECT_OPTIONS = (
    "DEPENDS",
    "PREFIX",
    "LIST_SEPARATOR",
    "TMP_DIR",
    "STAMP_DIR",
    "EXCLUDE_FROM_ALL",
    "DOWNLOAD_NAME",
    "DOWNLOAD_DIR",
    "DOWNLOAD_COMMAND",
    "DOWNLOAD_NO_PROGRESS",
    "CVS_REPOSITORY",
    "CVS_TAG",
    "SVN_REPOSITORY",
    "SVN_REVISION",
    "SVN_USERNAME",
    "SVN_PASSWORD",
    "SVN_TRUST_CERT",
    "GIT_REPOSITORY",
    "GIT_TAG",
    "GIT_REMOTE_NAME",
    "GIT_SUBMODULES",
    "GIT_SHALLOW",
    "GIT_PROGRESS",
    "GIT_CONFIG",
    "HG_REPOSITORY",
    "HG_TAG",
    "URL",
    "URL_HASH_ALGO",
    "URL_MD5",
    "HTTP_USERNAME",
    "HTTP_PASSWORD",
    "HTTP_HEADER",
    "TLS_VERIFY",
    "TLS_CAINFO",
    "TIMEOUT",
    "DOWNLOAD_NO_EXTRACT",
    "UPDATE_COMMAND",
    "UPDATE_DISCONNECTED",
    "PATCH_COMMAND",
    "SOURCE_DIR",
    "SOURCE_SUBDIR",
    "CONFIGURE_COMMAND",
    "CMAKE_COMMAND",
    "CMAKE_GENERATOR",
    "CMAKE_GENERATOR_PLATFORM",
    "CMAKE_GENERATOR_TOOLSET",
    "CMAKE_ARGS",
    "CMAKE_CACHE_ARGS",
    "CMAKE_CACHE_DEFAULT_ARGS",
    "BINARY_DIR",
    "BUILD_COMMAND",
    "BUILD_IN_SOURCE",
    "BUILD_ALWAYS",
    "BUILD_BYPRODUCTS",
    "INSTALL_DIR",
    "INSTALL_COMMAND",
    "TEST_BEFORE_INSTALL",
    "TEST_AFTER_INSTALL",
    "TEST_EXCLUDE_FROM_MAIN",
    "TEST_COMMAND",
    "LOG_DOWNLOAD",
    "LOG_UPDATE",
    "LOG_CONFIGURE",
    "LOG_BUILD",
    "LOG_TEST",
    "LOG_INSTALL",
    "USES_TERMINAL_DOWNLOAD",
    "USES_TERMINAL_UPDATE",
    "USES_TERMINAL_CONFIGURE",
    "USES_TERMINAL_BUILD",
    "USES_TERMINAL_TEST",
    "USES_TERMINAL_INSTALL",
    "STEP_TARGETS",
    "INDEPENDENT_STEP_TARGETS",
    # the following entries are used to specify multiple items for options
    # like PATCH_COMMAND etc.
    "COMMAND",
    "URL",
)


def log(message):
    bold = '\033[1m'
    yellow = '\033[93m'
    endc = '\033[0m'
    print("".join([bold, yellow, message, endc]), file=sys.stderr)


def parse_cmake_dependencies():
    url = "https://raw.githubusercontent.com/AppImage/AppImageKit/" \
          "appimagetool/master/cmake/dependencies.cmake"

    response = requests.get(url)
    response.raise_for_status()

    script = cmp.parse(response.text)

    for statement in script:
        if hasattr(statement, "name"):
            if statement.name.lower() == "externalproject_add":
                args = statement.body

                skip_args = 0

                for i, arg in enumerate(args):
                    while skip_args > 0:
                        skip_args -= 1
                        continue

                    if not hasattr(arg, "contents"):
                        continue

                    lc = arg.contents.lower()

                    if lc == "url":
                        # next "arg"'s contents are the URL
                        skip_args = 1
                        # TODO: support for URL hash
                        yield TarballURL(args[i + 1].contents, None)

                    elif lc == "git_repository":
                        # next "arg"'s contents are the repository URL
                        skip_args = 1
                        repo_url = args[i+1].contents

                        tag = None

                        # look for tag in following arguments
                        for j, potential_tag in enumerate(args[i+2:]):
                            if not hasattr(arg, "contents"):
                                continue

                            if potential_tag.contents.lower() == "git_tag":
                                tag = args[(i+2)+(j+1)].contents
                                break

                        yield GitRepository(repo_url, tag)

                    # search for patches to download
                    elif lc in ("patch_command"):
                        skip_args = 1

                        # check if command contains URL
                        for i in map(lambda x: getattr(x, "contents", None), args[i+1:]):
                            if i is None:
                                continue

                            if i in EXTERNALPROJECT_OPTIONS:
                                break

                            i = i.replace("$<SEMICOLON>", ";").strip("\"")

                            # check if i is a URL
                            parsed = urlparse(i)
                            if not (parsed.scheme and parsed.netloc):
                                continue

                            yield PatchURL(i)


def main():
    for item in parse_cmake_dependencies():
        if isinstance(item, GitRepository):
            repo_url, tag = item

            repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

            with TemporaryDirectory(prefix="AppImageKit-") as tempdir:
                log("Cloning Git repository: {}".format(repo_url))
                porcelain.clone(repo_url, tempdir)

                # TODO: replace with dulwich solution
                # maybe have a look at Pext source code
                version = subprocess.check_output([
                    "git", "describe", "--always", "--tags", tag,
                ], cwd=tempdir).decode().split("\n")[0]

                tarball_name = "{}-{}.tar.gz".format(repo_name, version)

                if os.path.exists(tarball_name):
                    log("Warning: {} exists, skipping".format(tarball_name))
                    continue

                tarball_path = os.path.join(tempdir, tarball_name)

                log("Creating tarball for tag/branch {}: {}".format(
                    tag, tarball_name
                ))

                # TODO: replace with dulwich call to remove dependency on Git
                # binary
                subprocess.check_call([
                    "git", "archive",
                    "--format", "tar.gz",
                    "-o", tarball_path,
                    "--prefix", "{}/".format(repo_name),
                    tag,
                ], cwd=tempdir)

                destination = os.path.join(os.getcwd(), "sources")

                shutil.copyfile(tarball_path,
                                os.path.join(destination, tarball_name))

        elif isinstance(item, TarballURL) or isinstance(item, PatchURL):
            if isinstance(item, TarballURL):
                url, hash = item
            elif isinstance(item, PatchURL):
                url, hash = item[0], None
            else:
                url, hash = str(item), None

            log("Downloading URL: {}".format(url))

            digest = None
            hash_algorithm = hash_value = None

            if hash is not None:
                hash_algorithm, hash_value = hash

                if hash_algorithm not in hashlib.algorithms_available:
                    log("Warning: hashing algorithm {} not supported by "
                        "Python interpreter".format(hash_algorithm))
                    hash_algorithm = None

                else:
                    digest = hashlib.new(hash_algorithm.upper())

            response = requests.get(url, stream=True)
            response.raise_for_status()

            content_disposition = rfc6266.parse_requests_response(response)

            ext = os.path.splitext(content_disposition.filename_unsafe)[-1]
            filename = content_disposition.filename_sanitized(ext.strip("."))

            if isinstance(item, TarballURL):
                path = os.path.join("sources", filename)
            elif isinstance(item, PatchURL):
                path = os.path.join("patches", filename)
            else:
                path = filename

            try:
                total = int(response.headers.get("Content-Length", None))
            except (ValueError, TypeError):
                total = None

            if os.path.exists(path):
                # if a hash value is available, use that to verify whether
                # file on system is up to date
                if hash_algorithm is not None:
                    local_digest = hashlib.new(hash_algorithm)

                    with open(path, "rb") as f:
                        data = f.read(4096)

                        if not data:
                            break

                        local_digest.update(data)

                    if hash_value == local_digest.hexdigest():
                        log("Warning: file {} exists, "
                            "skipping download".format(path))
                        continue

                if total is None:
                    log("Warning: size of file {} unknown, overwriting local "
                        "file".format(path))
                else:
                    if os.path.getsize(path) == total:
                        log("Warning: file {} exists, "
                            "skipping download".format(path))
                        continue

            os.makedirs(os.path.dirname(path), exist_ok=True)

            with open(path, "wb") as f:
                with tqdm(total=total) as pbar:
                    for chunk in response.iter_content():
                        f.write(chunk)

                        if digest is not None:
                            digest.update(chunk)

                        pbar.update(len(chunk))

            if digest is not None:
                if hash_value != digest.hexdigest():
                    log("Warning: could not verify file integrity: "
                        "expected digest: {}, received: {} "
                        "".format(hash_value, digest.hexdigest()))


if __name__ == "__main__":
    main()
