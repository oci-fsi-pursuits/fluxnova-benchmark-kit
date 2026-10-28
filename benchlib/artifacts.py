"""Resolve public Docker tags to recorded immutable digests, without running Docker."""
import json
import re
import urllib.request

ACCEPT = ", ".join(["application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json", "application/vnd.oci.image.manifest.v1+json", "application/vnd.docker.distribution.manifest.v2+json"])

def fetch_json(url, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": "fluxnova-benchmarks", **(headers or {})})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read()), dict(response.headers)

def resolve_dockerhub(reference):
    if "@sha256:" in reference:
        if not re.fullmatch(r"[a-zA-Z0-9._/:-]+@sha256:[a-f0-9]{64}", reference):
            raise ValueError("Invalid image digest")
        return reference
    repository, tag = reference.rsplit(":", 1)
    repository = repository.removeprefix("docker.io/")
    if "/" not in repository:
        repository = "library/" + repository
    token, _ = fetch_json("https://auth.docker.io/token?service=registry.docker.io&scope=repository:" + repository + ":pull")
    _, headers = fetch_json("https://registry-1.docker.io/v2/" + repository + "/manifests/" + tag,
                            {"Accept": ACCEPT, "Authorization": "Bearer " + token["token"]})
    digest = next((v for k, v in headers.items() if k.lower() == "docker-content-digest"), None)
    if not digest or not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise RuntimeError("Registry did not return an immutable digest")
    return repository + "@" + digest

def release_commit(release):
    data, _ = fetch_json("https://api.github.com/repos/finos/fluxnova-bpm-platform/git/ref/tags/v" + release)
    obj = data["object"]
    if obj["type"] == "tag":
        data, _ = fetch_json(obj["url"])
        obj = data["object"]
    if obj["type"] != "commit":
        raise RuntimeError("Release tag did not resolve to commit")
    return obj["sha"]
