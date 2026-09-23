# Landing design assets

The landing layout, styles, motion modules and agent marks come from
[treg-design/landing](https://github.com/l527497426-cyber/treg-design/tree/484066dd9d37a85eba24257ac9edb401b7710357/landing),
revision `484066dd9d37a85eba24257ac9edb401b7710357`, supplied as the implementation reference.

`../../landing.html` remains the server-rendered entry. These assets use the existing `/media`
mount, with no separate frontend build or runtime CDN dependency for the animation libraries.
Integration retains same-origin authentication and navigation, serving-origin setup instructions,
current signup-credit semantics, attribution scripts and deployment-configured support chat.
The Three.js font URL resolves relative to its module; failed WebGL initialization releases the
opening sequence and shows the existing monochrome treg mark. The mark stays hidden during
normal module loading, and the page retains the registry favicon.

## Third-party files

| Files | Version | Source and license |
|---|---|---|
| `vendor/three/three.module.js`, `three.core.js` | 0.180.0 | Unmodified `three@0.180.0` npm build; MIT, `vendor/three/LICENSE` |
| `vendor/three/RoomEnvironment.js`, `FontLoader.js`, `helvetiker_regular.typeface.json` | Reference revision above | Three.js helpers and font from the design repository; retained license and font metadata |
| `vendor/lenis.min.js`, `lenis.css` | 1.3.26 | Design repository's vendored Lenis; MIT, `vendor/LICENSE` |
| `assets/*` | Reference revision above | Agent marks; `assets/LOBEHUB-LICENSE` retained. The prototype's orange treg mark is omitted. |

To update the design, compare against the pinned source before importing changes. Keep the
production integration behavior described above and run `frontend/e2e/landing.spec.ts` plus the
SEO and site-tracking tests. Copy library builds verbatim; do not hand-edit them.
