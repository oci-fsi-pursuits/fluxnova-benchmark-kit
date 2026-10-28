# Access the private Fluxnova web interface

The deployed Fluxnova 3.0.1 distribution serves Welcome, Monitoring, Tasklist and Admin on the app VM's port 8080. The VM has no public IP. Forward the UI through controller SSH over OCI Bastion; no new VCN or inbound network rule is needed. All four web pages returned HTTP 200 through this path on 13 October 2026. A web login with the CSRF cookie/header succeeded and authorized all four apps; a request without that header was rejected with HTTP 403.

Start the [Bastion controller tunnel](oci.md#forward-controller-ssh-through-oci-bastion) from the repository root. On the prepared workstation:

```powershell
& "$env:USERPROFILE\oci\Scripts\python.exe" .\scripts\bastion_forward.py `
  --contract .\dist\oci-plan-20261012-bastion\environment.local.json `
  --profile BENCHMARK --ttl 3600
```

Wait for `Ready: controller SSH responded` and keep that terminal open. Initial SSH startup failures are retried automatically; if the script exits, the UI tunnel cannot connect to port 2222. In another PowerShell terminal, run this command for the existing Phoenix cell:

```powershell
ssh -i "$env:USERPROFILE\.ssh\fluxnova_bench" `
  -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes `
  -o HostKeyAlias=fluxnova-controller-10.78.0.156 `
  -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 `
  -p 2222 -N -T -L 127.0.0.1:18080:10.78.0.28:8080 ubuntu@127.0.0.1
```

The IPs are from this cell's applied inventory; use refreshed Terraform outputs if the VMs are replaced. The workstation's verified controller host key must already be in `known_hosts`. Keep both terminals open. The localhost listener is available only on this machine, and expires when either tunnel closes or the Bastion session reaches its TTL.

- [Welcome](http://127.0.0.1:18080/fluxnova/app/welcome/default/)
- [Monitoring](http://127.0.0.1:18080/fluxnova/app/monitoring/default/)
- [Tasklist](http://127.0.0.1:18080/fluxnova/app/tasklist/default/)
- [Admin](http://127.0.0.1:18080/fluxnova/app/admin/default/)

Sign in as **`benchmark`** with the current value of Vault secret **`fluxnova-bench-app-admin`**. The OCI registry auth token is a separate credential. In the OCI Console, open the existing Vault, select this secret and view its active secret contents. Do not put the password in Terraform variables or Git.

On the prepared workstation, this optional PowerShell snippet copies the secret directly to the clipboard without printing it. It uses the ignored setup references and the current browser-session OCI CLI profile:

```powershell
$benchSecretId = (Get-Content .\dist\setup-20261013\application-secrets.local.json | ConvertFrom-Json).admin_password_secret_id
$benchBundle = oci secrets secret-bundle get --secret-id $benchSecretId `
  --profile BENCHMARK --auth security_token --stage CURRENT | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Vault retrieval failed; refresh the BENCHMARK OCI session.' }
$benchEncoded = $benchBundle.data.'secret-bundle-content'.content
[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($benchEncoded)) | Set-Clipboard
Remove-Variable benchEncoded, benchBundle, benchSecretId
```

After pasting into the login form, `Set-Clipboard -Value ''` clears the clipboard. A session-expired error requires `oci session authenticate --profile-name BENCHMARK` before retrying Vault access. The browser password remains the application secret, not the OCI Console password.

Monitoring is useful for process definitions, completed benchmark history and incident diagnosis. Tasklist shows waiting human tasks; the golden suite completes its simulated manual-review task, so an empty Tasklist is expected afterward. Do not create or modify workflows while a controlled baseline is running, because those changes affect the shared test database. Assigned synthetic payment approval/rejection (retained in the private research archive) now passes through actual browser interaction; other GUI business operations remain separate from page-load checks.

Upstream describes these web apps in the [Fluxnova platform README](https://github.com/finos/fluxnova-bpm-platform/blob/main/README.md).

The separate [payment approval lane](payment-approval.md) creates a `Review synthetic payment` task when started. Its driver supplies an explicit simulated review decision and records it in engine history. The nine-case lifecycle test ends with no waiting tasks. The subsequent signed-intent browser lane (retained in the private research archive) validates decision entry as a temporary non-admin reviewer with the persistent verifier. A subsequent [dedicated payment form](payment-review-form.md) is deployed and tested; human usability assessment remains open.
