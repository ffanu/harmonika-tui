---
name: harmonika-release
description: Prepare a manual Harmonika release with preflight checks, backups, checksums and an explicit rollback plan.
---

Use when preparing or reviewing a Harmonika release or manual update. Read the project README and actual root CLI help/source before choosing commands; do not invent updater subcommands or replace the user's patched Harmonika/Harmonika binary. Skills do not implement an updater.

Inspect version, platform/architecture, current installation path, working-tree state and declared package contents. Run the project's actual release checks. Prepare reviewable artifacts in the project and compute SHA-256 checksums with an available local utility. Check package contents for credentials and unintended config before handoff without printing their values.

For an explicitly authorized manual installation, first back up the exact existing artifact with its version and checksum into a user-approved location. Document precise restore instructions, then use only the root CLI's supported manual path. Do not automatically update, poll for updates, mutate live binaries/configuration, alter accounts/server settings or fetch executable releases merely because this skill is selected. Existing branding patches and update-banner behavior must be preserved.

Return preflight results, artifact paths, checksums, manual install steps and rollback steps. Mark a release verified only for checks actually run; a prepared archive is not an installed or published release. Publishing, replacing an installation and changing live settings require the user's explicit instruction.
