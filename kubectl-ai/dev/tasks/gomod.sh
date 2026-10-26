#!/usr/bin/env bash
# Copyright 2025 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

set -o errexit
set -o nounset
set -o pipefail


REPO_ROOT="$(git rev-parse --show-toplevel)"
cd ${REPO_ROOT}

# retry <max_attempts> <delay_seconds> <command...>
# Retries a command on failure with a fixed delay between attempts.
# Useful for transient Go proxy / network errors (e.g. HTTP/2 INTERNAL_ERROR).
retry() {
  local max=$1; shift
  local delay=$1; shift
  local attempt=1
  until "$@"; do
    if (( attempt >= max )); then
      echo "ERROR: command failed after ${max} attempts: $*" >&2
      return 1
    fi
    echo "WARNING: attempt ${attempt}/${max} failed, retrying in ${delay}s: $*" >&2
    sleep "${delay}"
    (( attempt++ ))
  done
}

for f in $(find ${REPO_ROOT} -name go.mod); do
  cd $(dirname ${f})
  rm go.sum
  retry 3 5 go mod tidy
done
