# Connect to an existing engine

This mode provisions nothing and does not import resources into Terraform. Supply an endpoint, application account and trusted network path. OCI, another cloud and on premises use the same connection file. Fluxnova 3.0.1 was tested; other versions require qualification.

Copy `environment.example.json` to `environment.local.json`. Set `base_url` to the final `/engine-rest` endpoint. Remote endpoints require HTTPS with certificate verification; `ca_file` accepts a private CA. HTTP is accepted only on literal loopback IPs, for an SSH/Bastion forward. Redirects are rejected so credentials cannot follow them.

PowerShell, with password entered at a hidden prompt:

```powershell
$env:BENCH_USERNAME = 'lab-user'
$labPassword = Read-Host 'Fluxnova password' -AsSecureString
$env:BENCH_PASSWORD = [System.Net.NetworkCredential]::new('', $labPassword).Password
python -m benchlib.cli lab --connect providers/existing/environment.local.json
```

This starts **read-only**, checking the version endpoint with execution disabled. It is not a complete permission/history preflight. To enable isolated deployments on your authorized test engine:

```powershell
python -m benchlib.cli lab --connect providers/existing/environment.local.json --allow-deploy
```

After closing, clear credentials with `Remove-Item Env:BENCH_PASSWORD, Env:BENCH_USERNAME`.

The engine account needs deployment create/delete, instance create/read/delete, full activity/process/variable history read, and user/external-task read/completion rights for the lab definitions. Arrange scoped permissions with the administrator. Engine authorization must be enabled; the lab changes neither permissions nor history configuration. Use a test environment and synthetic inputs.

Uploaded process keys and external-task topics receive a unique session prefix. Starts use the exact returned definition ID; task actions use only instance IDs created here. The shared external-task queue is never fetched. Uploaded external tasks are completed manually, simulating a response rather than calling their original services.

On close, only deployments listed in `owned-deployments.local.json` are deleted, including their history. Exported/local run JSON is retained. Failed cleanup is recorded in `cleanup.local.json`; a terminated process or lost connection can leave test resources behind. No existing production definition is executed or changed. Models using scripts, delegates, connectors, forms, subprocesses, DMN or message/signal/timer events need dedicated adapters and established workload runners, beyond this first UI profile.
