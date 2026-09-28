# Security policy

Narsika stores device credentials and can change network configuration, so security reports are taken seriously.

## Supported versions

Only the latest code on the `main` branch receives security fixes during the Public Beta.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Include:

- what the problem is and its impact;
- steps to reproduce, or a proof of concept;
- the Narsika commit and install method (native, Docker, WSL2);
- any suggested fix.

Remove passwords, keys, device configurations and other secrets from your report.

You should receive an acknowledgement within a few days. Once a fix is available, the issue will be disclosed in the changelog, with credit if you would like it.

## Deployment guidance

- Run Narsika on a trusted management network. It serves plain HTTP by default; use [HTTPS](docs/OPERATIONS.md#https-with-nginx) when traffic crosses untrusted segments, and never expose it directly to the Internet.
- Limit client access with the management networks chosen at install time.
- Protect `/etc/narsika`, `/var/lib/narsika` and platform backups: they contain the encryption keys.
- Uploaded Playbooks are trusted administrator code with the service account's access. Only upload Playbooks you have reviewed.
- Keep the host updated and give device accounts only the privileges your operations need.
