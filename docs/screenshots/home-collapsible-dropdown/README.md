# Home — collapsible lists + the New menu

Before/after screenshots for the change that made "Your paths" and "Your beats"
collapsible and turned home's top-right CTA into a menu.

Captured against the app's own MSW fakes (`src/aleph/web/frontend/src/mocks/`)
in Chromium at 1280×900 and 390×844, with the `analyst` flag on.

| Shot | What it shows |
| ---- | ------------- |
| `before-desktop.png` / `before-phone.png` | The screen this change replaced. |
| `after-desktop.png` / `after-phone.png` | Both section kickers are now disclosure toggles; the CTA reads "New". |
| `after-desktop-new-menu.png` / `after-phone-new-menu.png` | The New menu open. |
| `after-desktop-collapsed.png` | Both sections collapsed — each header keeps its count. |
