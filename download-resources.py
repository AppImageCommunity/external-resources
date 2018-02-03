#! /usr/bin/env python

import os
import parse_cmake.parsing as cmp
import requests
import shutil
import subprocess
import sys

from dulwich import porcelain
from tempfile import TemporaryDirectory


def log(message):
    bold = '\033[1m'
    yellow = '\033[93m'
    endc = '\033[0m'
    print("".join([bold, yellow, message, endc]), file=sys.stderr)


def get_tarball_urls():
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
                        skip_args = 1
                        yield args[i+1].contents

                    elif lc == "git_repository":
                        skip_args = 1

                        repo_url = args[i+1].contents
                        tag = None

                        for j, potential_tag in enumerate(args[i+2:]):
                            if not hasattr(arg, "contents"):
                                continue

                            if potential_tag.contents.lower() == "git_tag":
                                tag = args[i+2+j+1].contents
                                skip_args += j+1
                                break

                        tag_f = "|{}".format(tag) if tag is not None else ""

                        yield "git|{}{}".format(repo_url, tag_f)


def main():
    for url in get_tarball_urls():
        if url.startswith("git|"):
            parts = url.split("|")

            repo_url = parts[1]
            tag = "master"

            repo_name = repo_url.rstrip("/").split("/")[-1].replace(".git", "")

            if len(parts) > 2:
                tag = parts[2]

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

                shutil.copyfile(tarball_path, os.path.join(os.getcwd(), tarball_name))

        else:
            log("Downloading URL: {}".format(url))
            retcode = subprocess.call(["wget", "-c", "-N", url, "--tries=3"])

            if retcode != 0:
                log("Warning: download failed, skipping")



if __name__ == "__main__":
    main()
