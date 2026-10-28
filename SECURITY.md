# Security reporting

This research toolkit is not a production payment processor. Use synthetic data, isolated resources and test identities. The maintained branch is `main`; historical evidence is not a supported deployed product.

Do not post secrets or customer information in public issues. Use GitHub private vulnerability reporting when enabled under this repository's Security tab. If unavailable, request a private route from a maintainer without including sensitive details.

Provide affected versions, a synthetic reproduction, impact and mitigation. No response-time SLA is promised. Secret scanning is a review step, not proof that every credential or vulnerability has been found.

Keep local configuration, private keys, environment files, Terraform state and generated evidence outside source control. Use least-privilege identities and TLS. Embedded-H2 demo credentials must not be reused in public or production services.
