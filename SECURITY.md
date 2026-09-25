# Security

4top runs as the current user. It is not a sandbox for an agent or a boundary
against malicious software running as that same user. No automatic elevation,
authentication upload, arbitrary shell command template, or telemetry exists.

For suspected command injection, unintended process control or data disclosure,
do not publish a working exploit with private data. Prefer GitHub's private
vulnerability reporting **when it has been enabled for this repository**. Otherwise
open an issue requesting a private contact channel without secret values or exploit
details. A private reporting endpoint has not been assumed to exist.

Include the exact 4top commit, OS/Python versions, a synthetic reproduction,
the affected boundary and expected versus observed behavior. Native agent vendor
vulnerabilities should also be reported to the relevant vendor.
