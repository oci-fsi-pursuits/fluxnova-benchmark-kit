"""REST adapter. Acceptance is never inferred from HTTP success alone."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path, PurePosixPath
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import ssl
import xml.etree.ElementTree as ET


def workload_resources(directory):
    """Include embedded deployment forms, retaining their workload-relative paths."""
    root = Path(directory).resolve()
    definitions = sorted(root.rglob('*.bpmn'))
    resources = set(definitions)
    for definition in definitions:
        for element in ET.parse(definition).iter():
            for attribute, value in element.attrib.items():
                if attribute.rsplit('}', 1)[-1] != 'formKey' or not value.startswith('embedded:deployment:'):
                    continue
                name = value.removeprefix('embedded:deployment:')
                relative = PurePosixPath(name)
                if (not name or relative.is_absolute() or '..' in relative.parts
                        or any(char in name for char in '\\:"\r\n')):
                    raise ValueError('Embedded form must be a safe workload-relative resource')
                form = (root / name).resolve()
                if not form.is_relative_to(root) or not form.is_file():
                    raise ValueError('Embedded form resource is missing or outside workloads')
                resources.add(form)
    return sorted(resources)

class FluxnovaClient:
    def __init__(self, base_url, username=None, password=None, ca_file=None):
        self.base_url = base_url.rstrip("/")
        parsed = urllib.parse.urlparse(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
            raise ValueError("base_url must be an HTTP(S) endpoint without credentials")
        self.auth = None
        if username is not None and password is not None:
            self.auth = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self.context = ssl.create_default_context(cafile=ca_file)

    @classmethod
    def from_config(cls, config):
        r = config["runtime"]
        return cls(r["base_url"], os.environ.get(r.get("auth_username_env", "BENCH_USERNAME")),
                   os.environ.get(r.get("auth_password_env", "BENCH_PASSWORD")), r.get("ca_file"))

    def request(self, method, path, body=None, content_type="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = content_type
        if self.auth:
            headers["Authorization"] = self.auth
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30, context=self.context) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            # Upstream messages can echo submitted data; keep credentials/payloads out of logs.
            raise RuntimeError(f"Fluxnova {method} {path.split('?')[0]} returned HTTP {exc.code}") from None

    def deploy(self, files, resource_root=None):
        boundary = "bench-" + uuid.uuid4().hex
        parts = []
        def field(name, value):
            parts.extend([f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n".encode(), value.encode(), b"\r\n"])
        field("deployment-name", "fluxnova-benchmark-v1")
        field("enable-duplicate-filtering", "true")
        for file in files:
            path = Path(file)
            name = path.resolve().relative_to(Path(resource_root).resolve()).as_posix() if resource_root is not None else path.name
            if any(char in name for char in '\\"\r\n'):
                raise ValueError('Invalid deployment resource name')
            content_type = {'.bpmn': 'application/xml', '.html': 'text/html'}.get(path.suffix, 'application/octet-stream')
            parts.extend([f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{name}\"\r\nContent-Type: {content_type}\r\n\r\n".encode(), path.read_bytes(), b"\r\n"])
        parts.append(f"--{boundary}--\r\n".encode())
        return self.request("POST", "/deployment/create", b"".join(parts), f"multipart/form-data; boundary={boundary}")

    def start(self, key, business_key, variables):
        return self.request("POST", "/process-definition/key/" + urllib.parse.quote(key, safe="") + "/start",
                            {"businessKey": business_key, "variables": variables})

    def observe(self, process_id, timeout_s=30, poll_s=0.1):
        deadline = time.monotonic() + timeout_s
        path = "/history/process-instance/" + urllib.parse.quote(process_id, safe="")
        while time.monotonic() < deadline:
            instance = self.request("GET", path)
            if instance and instance.get("endTime"):
                variables = self.request("GET", "/history/variable-instance?processInstanceId=" + urllib.parse.quote(process_id, safe=""))
                return {"instance": instance, "variables": {v["name"]: v.get("value") for v in variables}}
            time.sleep(poll_s)
        return None

    def readiness(self):
        return self.request("GET", "/version")
