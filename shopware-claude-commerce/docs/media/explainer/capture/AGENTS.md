# Storefront

Source: http://localhost:3005

To create a video from this capture, use the `product-launch-video` skill.

## What's in This Capture

| File | Contents |
|------|----------|
| `screenshots/contact-sheet.jpg` | **View this first.** All scroll screenshots in labeled grid — see the entire page at a glance |
| `screenshots/scroll-*.png` | Individual viewport screenshots if you need detail on a specific section. |
| `extracted/tokens.json` | Design tokens: 15 colors, 2 fonts, 2 headings, 9 CTAs |
| `extracted/design-styles.json` | Computed styles from live DOM: typography hierarchy, button/card/nav styles, spacing scale, border-radius, box shadows. Primary data source for DESIGN.md. |
| `extracted/asset-descriptions.md` | One-line description of every downloaded asset. Read this for asset selection — only open individual files for safe-zone checking. |
| `extracted/visible-text.txt` | Page text in DOM order, prefixed with HTML tag (`[h1]`, `[p]`, `[a]`). Use as context — rephrase freely. |
| `assets/contact-sheet.jpg` | All downloaded images in one labeled grid. |
| `assets/` | Individual downloaded images, SVGs, and font files. |

## Brand Summary

- **Colors**: #23211C (surface-dark), #FFFFFF (bg-light), #F7F6F3 (bg-light), #189EFF (accent), #000000 (bg-dark), #6D685E (neutral), #EEECE7 (bg-light), #E8F5FF (bg-light), #25221B (surface-dark), #23211C24 (surface-dark)
- **Fonts**: __nextjs-Geist (400-600 variable), __nextjs-Geist Mono (400-600 variable)
