# Applications

Deployable user-facing process entry points live here. Applications may depend on shared
packages and domain adapters; shared packages must never import application entry points.

`apps/web` contains browser-delivered static surfaces. The PRD-004 review queue intentionally uses
plain HTML, CSS, and JavaScript so it adds no second build toolchain while the workbench architecture
is still evolving.
