# Publishing checklist

## What's already prepared in this repo
- `custom_components/fireboard/` — the integration (HACS expects this nesting).
- `hacs.json` — HACS metadata.
- `manifest.json` — `codeowners`, `documentation`, `issue_tracker` set to `@Ltek` / `github.com/Ltek/fireboard`. **Change these if your username/repo differ.**
- `LICENSE` — MIT (edit the copyright holder if needed).
- `.github/workflows/validate.yml` — runs hassfest + HACS validation on every push/PR.
- `README.md` — public docs.

## Steps you do on GitHub

1. **Create the repo** `Ltek/fireboard` (public) and push this tree to the default branch.
2. **Add repo topics** (helps discovery / required-ish for default store): `home-assistant`, `hacs`, `integration`, `fireboard`.
3. **Create a release / tag** — HACS surfaces versioned releases. Tag e.g. `2026.09.03.28` (match `manifest.json` "version"). HACS can also use the default branch, but releases are recommended.
4. Confirm the **Validate** GitHub Action passes (green) — hassfest and HACS both.

## Install as a custom repository (works immediately, no HACS-store review)
Anyone can add `https://github.com/Ltek/fireboard` via HACS → Custom repositories → Integration.

## Getting into the HACS *default* store (optional, reviewed)
1. **Brand assets** — open a PR to [home-assistant/brands](https://github.com/home-assistant/brands) adding:
   - `custom_integrations/fireboard/icon.png` (256×256, transparent)
   - `custom_integrations/fireboard/logo.png` (optional, wider)
   These must be approved before the default-store PR.
2. **HACS inclusion** — open a PR to [hacs/default](https://github.com/hacs/default) adding `Ltek/fireboard` to the `integration` list. The HACS Action must be passing.
3. Requirements the reviewers check (we already satisfy): config flow, unique IDs, `iot_class`, `manifest.json` completeness, a description + topics on the repo, and a passing validation workflow.

## Notes
- The **experimental Drive control** entities write to physical hardware via an
  undocumented FireBoard endpoint. They are opt-in / mostly disabled by default
  and clearly labeled; keep that framing in any public description.
- Bump `manifest.json` "version" and `const.py` VERSION together for each release
  (format `YYYY.MM.DD.N`, increment never resets), and cut a matching GitHub release/tag.
