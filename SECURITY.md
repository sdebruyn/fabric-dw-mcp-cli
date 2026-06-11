# Security

We use [GitHub Private Vulnerability Reporting](https://docs.github.com/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability?WT.mc_id=MVP_310840) for security disclosures.

**To report a vulnerability**: [open a private advisory](https://github.com/sdebruyn/fabric-dw-mcp-cli/security/advisories/new). GitHub will deliver it directly and privately to the maintainers — no public issue or email needed.

We aim to acknowledge reports within **5 business days** and to publish a fix or advisory within **90 days** of confirmation.

## Supported versions

This project is in **pre-1.0 / alpha**. Only the **latest published release** on PyPI receives security fixes. No backports are made to older releases.

If you are running anything other than the latest published version, upgrade first before reporting.

## Scope

Bugs that put data, credentials, or workspace state at risk are in scope. Out of scope: bugs in third-party SDKs we depend on (file those upstream), denial-of-service against the Fabric API (file with Microsoft).
