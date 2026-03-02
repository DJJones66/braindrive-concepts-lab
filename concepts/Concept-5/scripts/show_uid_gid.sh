#!/usr/bin/env bash
set -euo pipefail

uid="$(id -u)"
gid="$(id -g)"
user_name="$(id -un)"
group_name="$(id -gn)"

echo "Host user: ${user_name}"
echo "Host group: ${group_name}"
echo "HOST_UID=${uid}"
echo "HOST_GID=${gid}"
echo
echo "Set these in .env:"
echo "HOST_UID=${uid}"
echo "HOST_GID=${gid}"
echo
echo "Apply automatically (GNU sed):"
echo "sed -i \"s/^HOST_UID=.*/HOST_UID=${uid}/; s/^HOST_GID=.*/HOST_GID=${gid}/\" .env"
echo
echo "Run compose once with explicit IDs:"
echo "HOST_UID=${uid} HOST_GID=${gid} docker compose up -d --build"
